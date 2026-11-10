"""官方已配对设备的协议传输；协议参考 wx-ime-sdk（Apache-2.0）。

不注册新身份、不改远端开关、不写词条。敏感响应只存入用户私有状态目录。
使用系统 Python（cryptography）；运行时独占 wxc 的身份锁。
"""
import fcntl
import ctypes
import ctypes.util
import hashlib
import json
import os
from pathlib import Path
import secrets
import subprocess
import tempfile
import time
import urllib.request
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.padding import PKCS7

ROOT = Path(__file__).resolve().parent
STATE = Path(os.environ.get("WETYPE_STATE_DIR", Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / "wetype-ime/account")).expanduser().resolve()
VERSION = "3.5.4.57423"
SALT = "aI0wC6qT1nB3nN5r"

def varint(n):
    out = bytearray()
    while n > 127:
        out.append((n & 127) | 128)
        n >>= 7
    return bytes(out + bytes([n]))

def number(k, n):
    return varint(k << 3) + varint(n)

def blob(k, value):
    if isinstance(value, str):
        value = value.encode()
    return varint(k << 3 | 2) + varint(len(value)) + value

def read_varint(data, pos):
    n = shift = 0
    while True:
        b = data[pos]
        pos += 1
        n |= (b & 127) << shift
        if b < 128:
            return n, pos
        shift += 7
        if shift > 70:
            raise ValueError("invalid varint")

def fields(data):
    result = {}
    pos = 0
    while pos < len(data):
        tag, pos = read_varint(data, pos)
        kind = tag & 7
        if kind == 0:
            value, pos = read_varint(data, pos)
        elif kind == 2:
            size, pos = read_varint(data, pos)
            value = data[pos:pos+size]
            if len(value) != size:
                raise ValueError("truncated protobuf")
            pos += size
        elif kind in (1, 5):
            size = 8 if kind == 1 else 4
            value = data[pos:pos+size]
            pos += size
        else:
            raise ValueError("unsupported protobuf wire")
        result.setdefault(tag >> 3, []).append(value)
    return result

def one(message, key, default=0):
    return message.get(key, [default])[0]

def aes(data, key, encrypt):
    cipher = Cipher(algorithms.AES(key), modes.ECB())
    if encrypt:
        pad = PKCS7(128).padder()
        padded = pad.update(data) + pad.finalize()
        enc = cipher.encryptor()
        return enc.update(padded) + enc.finalize()
    dec = cipher.decryptor()
    raw = dec.update(data) + dec.finalize()
    pad = PKCS7(128).unpadder()
    return pad.update(raw) + pad.finalize()

def shared_key(identity):
    with tempfile.TemporaryDirectory(dir=STATE) as tmp:
        private = Path(tmp)/"key.pem"
        private.write_text(identity["private_pem"])
        private.chmod(0o600)
        public = subprocess.check_output(["openssl", "pkey", "-in", str(private), "-pubout", "-outform", "DER"], stderr=subprocess.DEVNULL)
        peer = bytes.fromhex(identity["server_key"])
        assert len(peer) == 33 and peer[0] == 4
        peer_path = Path(tmp)/"peer.der"
        peer_path.write_bytes(public[:-33]+peer)
        secret = subprocess.check_output(["openssl", "pkeyutl", "-derive", "-inkey", str(private), "-peerkey", str(peer_path), "-peerform", "DER"], stderr=subprocess.DEVNULL)
        assert len(secret) == 16
        return secret.hex().upper().encode()

def save_private(name, data):
    path = STATE/name
    fd = os.open(path, os.O_WRONLY|os.O_CREAT|os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as output:
        output.write(data)

def snappy_decompress(data):
    # Nix 等平台可显式指定共享库，传统发行版仍使用系统库查找。
    library = os.environ.get("WETYPE_SNAPPY_LIBRARY") or ctypes.util.find_library("snappy")
    if not library:
        raise RuntimeError("找不到 Snappy 共享库；请安装 libsnappy 或设置 WETYPE_SNAPPY_LIBRARY")
    lib = ctypes.CDLL(library)
    lib.snappy_uncompressed_length.argtypes = [ctypes.c_char_p, ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
    lib.snappy_uncompressed_length.restype = ctypes.c_int
    lib.snappy_uncompress.argtypes = [ctypes.c_char_p, ctypes.c_size_t, ctypes.c_void_p, ctypes.POINTER(ctypes.c_size_t)]
    lib.snappy_uncompress.restype = ctypes.c_int
    size = ctypes.c_size_t()
    if lib.snappy_uncompressed_length(data, len(data), ctypes.byref(size)) != 0 or size.value > 2*1024*1024:
        raise ValueError("invalid snappy length")
    output = ctypes.create_string_buffer(size.value)
    if lib.snappy_uncompress(data, len(data), output, ctypes.byref(size)) != 0:
        raise ValueError("invalid snappy payload")
    return output.raw[:size.value]

class Probe:
    def __init__(self, identity):
        self.identity = identity
        self.key = shared_key(identity)
        self.offset = 0
        self.task = 1
        self.http = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def timestamp(self):
        return int(time.time()*1000)+self.offset

    def request(self, path, plain, command=None, service_command=4814):
        encrypted = path != "/timestamp"
        body, headers = self.prepare(path, plain, command, service_command)
        with self.http.open(urllib.request.Request("https://wetype.weixin.qq.com"+path, data=body, headers=headers), timeout=getattr(self,"timeout",20)) as response:
            data = response.read(2*1024*1024+1)
            compression = response.headers.get("Kb-CompressionType", "1")
        return self.finish(data, compression, encrypted)

    def prepare(self, path, plain, command=None, service_command=4814):
        """加密请求体并生成签名请求头；返回 (body, headers)。"""
        encrypted = path != "/timestamp"
        body = aes(plain, self.key, True) if encrypted else plain
        headers = {"Kb-OsType":"1", "Kb-Version":VERSION, "Kb-SystemVersion":"34",
                   "Kb-PackageType":"2", "Kb-TimeStamp":str(self.timestamp()),
                   "Kb-BodyMd5":hashlib.md5(body).hexdigest().upper(),
                   "Kb-TraceId":secrets.token_hex(8), "Kb-TaskId":str(self.task if encrypted else 0),
                   "Kb-Scene":"1", "Kb-CompressionType":"1", "Content-Type":"application/octet-stream", "Use_DebugNet":"0"}
        if command is not None:
            headers.update({"Kb-Uin":str(self.identity["uin"]), "Kb-CmdId":str(service_command), "Kb-SubCmdId":str(command), "Kb-SharedKeySuffix":self.key[-4:].decode()})
            order = ["Kb-OsType", "Kb-Version", "Kb-Scene", "Kb-CmdId", "Kb-SubCmdId", "Kb-TimeStamp", "Kb-BodyMd5", "Kb-Uin", "Kb-TraceId", "Kb-TaskId"]
            headers["Kb-Sign"] = hashlib.sha256(("".join(headers[k] for k in order)+SALT).encode()).hexdigest().upper()
        return body, headers

    def finish(self, data, compression, encrypted=True):
        """解密、解压响应，并推进任务序号。"""
        if len(data)>2*1024*1024:
            raise ValueError("response too large")
        if encrypted:
            data = aes(data, self.key, False)
        if compression == "2":
            data = snappy_decompress(data)
        elif compression != "1":
            raise ValueError("unknown response compression")
        self.task += 1
        return data

    def api(self, command, body, service_command=4814, *, persist_errors=True):
        uin = self.identity["uin"]
        device = "UniClipboard;research;standalone;"+self.identity["device_name"]+";sdk 34;PHONE"
        entries = {"vid":str(uin), "app_ver":VERSION, "platform":"1", "device":device, "channelid":"", "client_timestamp":str(self.timestamp())}
        header = b"".join(blob(1, blob(1,k)+blob(2,v)) for k,v in entries.items())
        envelope = number(2,uin)+number(3,command)+blob(4,header)+blob(5,body)
        raw = self.request("/api_v2",envelope,command,service_command)
        parsed = fields(raw)
        result = one(parsed,6,b"")
        if not result:
            if persist_errors:
                save_private('api-'+str(service_command)+'-'+str(command)+'-empty.pb',raw)
            raise ValueError("no business response: fields="+str(list(parsed)))
        if result.startswith(b'['):
            if persist_errors:
                save_private('api-'+str(command)+'-error.bin',result)
                raise ValueError('官方业务接口返回错误，响应已保存在账户状态目录')
            raise ValueError('官方业务接口返回错误')
        return result

    def group(self):
        started = int(time.time()*1000)
        stamp = one(fields(self.request("/timestamp",b"")),1)
        self.offset = stamp-(started+int(time.time()*1000))//2
        uin = self.identity["uin"]
        # 只请求设备/词典元数据；filter 不含剪贴板，当前常用语版本为 0。
        body = number(1,uin)+number(2,1)+number(3,13)+number(5,0)
        raw = self.api(34,body)
        save_private("group-sync-response.pb",raw)
        rsp = fields(raw)
        group = fields(one(rsp,1,b""))
        hot = fields(one(group,4,b""))
        data = one(hot,2,b"")
        if data:
            save_private("common-words-payload.bin",data)
        summary = {"status":one(rsp,2),"group_present":bool(group),"device_count":len(group.get(2,[])),
                   "function_switch":one(group,5),"common_words_version":one(hot,1),
                   "common_words_bytes":len(data),"group_field_numbers":list(group),
                   "response_field_numbers":list(rsp)}
        print(json.dumps(summary,ensure_ascii=False,indent=2))
        # 官方原生引擎：FetchUserDictFullSyncInfo，cmd44；请求为 uin1/group_id2。
        raw = self.api(44,number(1,uin)+number(2,one(group,1)))
        save_private("full-sync-response.pb",raw)
        parsed = fields(raw)
        assert one(parsed,1)==0,'查询个人词典元数据失败'
        print(json.dumps({'full_sync_status':one(parsed,1),'snapshot_count':len(parsed.get(2,[])),
                          'encryption_key_version':one(parsed,3),
                          'encryption_key_bytes':len(one(parsed,4,b''))},ensure_ascii=False,indent=2))

if __name__ == "__main__":
    os.umask(0o077)
    with (STATE/"identity.lock").open("a+") as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        identity = json.loads((STATE/"identity.json").read_text())
        Probe(identity).group()
