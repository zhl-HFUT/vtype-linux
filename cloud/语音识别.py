"""在线语音识别：把 16kHz 单声道录音编码为官方 OPUS_RAW 格式，经已关联身份的 4547/0 请求流式识别。

只发送音频和固定的识别选项；不发送前后文、最近上屏内容或应用名称，不保存录音、文字或响应。
`--serve` 供 Fcitx5 插件常驻调用：收到 START 才打开麦克风，STOP/CANCEL 后立即关闭。
"""
import ctypes
import ctypes.util
import fcntl
import http.client
import json
import math
import os
import secrets
import shutil
import struct
import subprocess
import sys
import threading
import time
import wave
from pathlib import Path

import 官方同步探针 as p

SAMPLE_RATE = 16000
FRAME_SAMPLES = SAMPLE_RATE // 50          # 官方编码器每帧 20ms
FRAME_BYTES = FRAME_SAMPLES * 2
BITRATE = 64000
OPUS_HEADER = b'#!OPUS_RAW_V1' + bytes([2, 1, 0])   # 采样率序号 2=16kHz、单声道
ENCODE_OPUS = 5
SERVICE = 4547
HOST = 'wetype.weixin.qq.com'
FIRST_PACKET = 3960                        # 服务器在响应里可调整单包上限
MAX_AUDIO_SECONDS = 60
MAX_RESPONSE_TEXT = 4096
FINISH_DEADLINE = 10                       # 录音结束后等待最终结果的上限（秒）


class Cancelled(Exception):
    pass


class OpusEncoder:
    """与官方 libvoiceopus 相同的参数：AUDIO 应用、音乐信号、复杂度 10、宽带。"""

    def __init__(self):
        name = os.environ.get('WETYPE_OPUS_LIBRARY') or ctypes.util.find_library('opus')
        if not name:
            raise RuntimeError('找不到 libopus；请安装 libopus0（Ubuntu）或 opus（Arch）')
        self.lib = lib = ctypes.CDLL(name)
        lib.opus_encoder_create.restype = ctypes.c_void_p
        lib.opus_encoder_create.argtypes = [ctypes.c_int32, ctypes.c_int, ctypes.c_int, ctypes.POINTER(ctypes.c_int)]
        lib.opus_encode.restype = ctypes.c_int32
        lib.opus_encode.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_int32]
        lib.opus_encoder_destroy.argtypes = [ctypes.c_void_p]
        error = ctypes.c_int()
        self.state = lib.opus_encoder_create(SAMPLE_RATE, 1, 2049, ctypes.byref(error))
        if not self.state or error.value:
            raise RuntimeError('Opus 编码器初始化失败')
        for request, value in ((4002, BITRATE), (4010, 10), (4024, 3002), (4008, 1103)):
            if lib.opus_encoder_ctl(ctypes.c_void_p(self.state), ctypes.c_int(request), ctypes.c_int32(value)):
                raise RuntimeError('Opus 编码器参数设置失败')
        self.pending = b''

    def encode(self, pcm, final=False):
        """输入 16 位小端 PCM，返回若干“2 字节长度 + Opus 包”的帧；末尾不足一帧时补静音。"""
        data = self.pending + pcm
        if final and len(data) % FRAME_BYTES:
            data += bytes(FRAME_BYTES - len(data) % FRAME_BYTES)
        out = bytearray()
        buffer = ctypes.create_string_buffer(1500)
        while len(data) >= FRAME_BYTES:
            frame, data = data[:FRAME_BYTES], data[FRAME_BYTES:]
            length = self.lib.opus_encode(ctypes.c_void_p(self.state), frame, FRAME_SAMPLES, buffer, 1500)
            if length < 0:
                raise RuntimeError('Opus 编码失败')
            out += struct.pack('<H', length) + buffer.raw[:length]
        self.pending = data
        return bytes(out)

    def close(self):
        if self.state:
            self.lib.opus_encoder_destroy(ctypes.c_void_p(self.state))
            self.state = None


def encode_pcm(pcm):
    encoder = OpusEncoder()
    try:
        return OPUS_HEADER + encoder.encode(pcm, final=True)
    finally:
        encoder.close()


def level(pcm):
    """一段 PCM 的音量，0–8 级，用于界面显示。"""
    count = len(pcm) // 2
    if not count:
        return 0
    samples = struct.unpack('<%dh' % count, pcm[:count * 2])
    rms = math.sqrt(sum(x * x for x in samples) / count)
    if rms < 1:
        return 0
    return max(0, min(8, int((20 * math.log10(rms / 32768) + 60) / 6)))


def read_wav(path):
    with wave.open(str(path), 'rb') as stream:
        if (stream.getframerate(), stream.getnchannels(), stream.getsampwidth()) != (SAMPLE_RATE, 1, 2):
            raise ValueError('需要 16kHz、单声道、16 位 WAV；可先用 ffmpeg -i 输入 -ar 16000 -ac 1 -sample_fmt s16 输出.wav 转换')
        if stream.getnframes() > SAMPLE_RATE * MAX_AUDIO_SECONDS:
            raise ValueError('录音超过 %d 秒' % MAX_AUDIO_SECONDS)
        return stream.readframes(stream.getnframes())


def recognize_request(voice_id, seq, data, total, is_end):
    """ImeVoiceTranslateReq{1: ImeVoiceRecognizeRequest}；只含识别必需字段。"""
    body = p.blob(2, voice_id) + p.blob(4, data) + p.number(5, ENCODE_OPUS)
    body += p.number(6, int(is_end)) + p.number(7, seq) + p.number(11, total)
    # scene、简繁转换、服务器推送、前后文、最近上屏、应用名称和整理选项都保持默认（不发送）。
    return p.blob(1, body)


def text(message, key):
    value = p.one(message, key, b'')
    if not isinstance(value, bytes) or len(value) > MAX_RESPONSE_TEXT:
        raise ValueError('识别响应格式不符')
    return value.decode('utf-8', 'replace')


def parse_response(raw, voice_id):
    """ImeVoiceRecognizeResponse；服务器不一定回显 voice_id，回显时必须与本次会话一致。"""
    message = p.fields(raw)
    if p.one(message, 2, b'') not in (b'', voice_id.encode()):
        raise ValueError('识别响应与本次会话不符')
    base = p.fields(p.one(message, 1, b'')) if isinstance(p.one(message, 1, b''), bytes) else {}
    ret = p.one(base, 1)
    if ret >= 1 << 63:
        ret -= 1 << 64
    polished, plain = text(message, 13), text(message, 4)
    return {'ret': ret, 'seq': p.one(message, 3), 'text': (polished or plain).replace('|', ''),
            'end': p.one(message, 5) > 0, 'interval': p.one(message, 6), 'max_packet': p.one(message, 7)}


class IdentityLock:
    """与云候选相同：每个请求持有身份锁，避免与 wxc 续期同时使用会话密钥。"""

    def __init__(self, state):
        self.path = Path(state) / 'identity.lock'

    def __enter__(self):
        self.file = self.path.open('a+')
        for _ in range(20):
            try:
                fcntl.flock(self.file, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return self
            except BlockingIOError:
                time.sleep(.1)
        self.file.close()
        raise RuntimeError('设备身份正被其他进程占用')

    def __exit__(self, *_):
        self.file.close()


class Transport:
    """复用关联身份和一条 HTTPS 长连接（每个请求约省 40ms 握手）。"""

    def __init__(self, state=None, timeout=5):
        self.state = Path(state or p.STATE)
        self.timeout = timeout
        self.client = None
        self.stamp = None
        self.connection = None
        self.mutex = threading.Lock()

    def _client(self):
        identity = self.state / 'identity.json'
        if not identity.is_file():
            raise RuntimeError('尚未关联官方客户端，请先执行 pair')
        stamp = identity.stat().st_mtime_ns
        if self.client is None or stamp != self.stamp:
            self.client = p.Probe(json.loads(identity.read_text()))
            self.stamp = stamp
        return self.client

    def uin(self):
        with self.mutex, IdentityLock(self.state):
            return self._client().identity['uin']

    def _post(self, client, body):
        payload, headers = client.prepare('/api_v2', body, 0, SERVICE)
        if self.connection is None:
            self.connection = http.client.HTTPSConnection(HOST, timeout=self.timeout)
        self.connection.request('POST', '/api_v2', payload, headers)
        response = self.connection.getresponse()
        data = response.read(2 * 1024 * 1024 + 1)
        if response.status != 200:
            raise ConnectionError('HTTP %d' % response.status)
        if (response.getheader('Connection') or '').lower() == 'close':
            self.close()
        return client.finish(data, response.getheader('Kb-CompressionType', '1'))

    def send(self, body):
        with self.mutex, IdentityLock(self.state):
            client = self._client()
            try:
                raw = self._post(client, body)
            except (http.client.RemoteDisconnected, http.client.CannotSendRequest, BrokenPipeError, ConnectionResetError):
                # 空闲长连接可能已被服务器关闭；重新连接后只重试这一次。
                self.close()
                raw = self._post(client, body)
            except Exception:
                self.close()
                raise
        if len(raw) > 64 * 1024:
            raise ValueError('识别响应过大')
        # 4547 不套 4814 的 api_v2 业务信封：请求体是 ImeVoiceTranslateReq，
        # 响应是 ImeVoiceTranslateResp{1: translate_result = ImeVoiceRecognizeResponse}。
        inner = p.one(p.fields(raw), 1, b'')
        if not isinstance(inner, bytes) or not inner:
            raise ValueError('识别响应为空')
        return inner

    def close(self):
        if self.connection is not None:
            self.connection.close()
            self.connection = None


class Recognition:
    """一次识别会话：feed() 追加编码后的音频帧，finish() 表示录音结束，run() 负责发送与轮询。"""

    def __init__(self, transport, on_text=lambda value: None, voice_id=None):
        self.transport = transport
        self.on_text = on_text
        self.voice_id = voice_id or (str(transport.uin() % 1000000) + str(secrets.randbits(32)) + str(secrets.randbits(32)))
        self.audio = bytearray(OPUS_HEADER)
        self.done = False
        self.cancelled = False
        self.condition = threading.Condition()
        self.text = ''

    def feed(self, data):
        with self.condition:
            self.audio += data
            self.condition.notify_all()

    def finish(self):
        with self.condition:
            self.done = True
            self.condition.notify_all()

    def cancel(self):
        with self.condition:
            self.cancelled = True
            self.condition.notify_all()

    def _send(self, seq, data, total, is_end):
        if self.cancelled:
            raise Cancelled()
        raw = self.transport.send(recognize_request(self.voice_id, seq, data, total, is_end))
        if self.cancelled:
            raise Cancelled()
        response = parse_response(raw, self.voice_id)
        if response['ret'] != 0:
            raise RuntimeError('识别服务返回错误 %d' % response['ret'])
        if response['text'] != self.text:
            self.text = response['text']
            self.on_text(self.text)
        return response

    def run(self):
        sent, seq, packet, interval, last = 0, 0, FIRST_PACKET, .1, 0.0
        while True:
            with self.condition:
                while True:
                    if self.cancelled:
                        raise Cancelled()
                    pending = len(self.audio) > sent
                    wait = last + interval - time.monotonic()
                    # 录音中按服务器给的间隔攒包；录音结束后连续发完剩余音频。
                    if pending and (self.done or wait <= 0 or len(self.audio) - sent >= packet):
                        break
                    if self.done and not pending:
                        break
                    self.condition.wait(max(wait, .01) if pending else None)
                if not pending:
                    break
                chunk = bytes(self.audio[sent:sent + packet])
            last = time.monotonic()
            seq += 1
            response = self._send(seq, chunk, sent + len(chunk), False)
            sent += len(chunk)
            if response['max_packet'] > 0:
                packet = min(response['max_packet'], 64 * 1024)
            interval = min(max(response['interval'], 30), 1000) / 1000
        seq += 1
        response = self._send(seq, b'', sent, True)
        limit = time.monotonic() + FINISH_DEADLINE
        while not response['end']:
            if time.monotonic() > limit:
                raise TimeoutError('等待识别结果超时')
            with self.condition:
                if not self.cancelled:
                    self.condition.wait(interval)
            response = self._send(0, b'', sent, True)
        return response['text']


def recorder_command():
    override = os.environ.get('WETYPE_VOICE_RECORDER')
    if override:
        return override.split()
    candidates = (['pw-record', '--rate', str(SAMPLE_RATE), '--channels', '1', '--format', 's16', '--latency', '20ms', '-'],
                  ['parecord', '--raw', '--rate', str(SAMPLE_RATE), '--channels', '1', '--format', 's16le', '--latency-msec', '20'],
                  ['arecord', '-q', '-t', 'raw', '-r', str(SAMPLE_RATE), '-c', '1', '-f', 'S16_LE'])
    for command in candidates:
        if shutil.which(command[0]):
            return command
    raise RuntimeError('找不到 pw-record、parecord 或 arecord')


class Microphone:
    """录音线程：读 20ms 一帧的 PCM，编码后交给识别会话，并回报音量。"""

    def __init__(self, recognition, on_level=lambda value: None, on_stopped=lambda: None):
        self.recognition = recognition
        self.on_level = on_level
        self.on_stopped = on_stopped
        self.stopping = threading.Event()
        self.frames = 0
        self.encoder = OpusEncoder()
        try:
            self.process = subprocess.Popen(recorder_command(), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                            stderr=subprocess.DEVNULL)
        except Exception:
            self.encoder.close()
            raise
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _run(self):
        last_level, loudest = 0.0, 0
        try:
            while not self.stopping.is_set() and self.frames < MAX_AUDIO_SECONDS * 50:
                pcm = self.process.stdout.read(FRAME_BYTES)
                if not pcm:
                    break
                self.frames += len(pcm) // FRAME_BYTES
                self.recognition.feed(self.encoder.encode(pcm))
                loudest = max(loudest, level(pcm))
                if time.monotonic() - last_level >= .08:
                    self.on_level(loudest)
                    last_level, loudest = time.monotonic(), 0
            self.recognition.feed(self.encoder.encode(b'', final=True))
        finally:
            self.encoder.close()
            self._close()
            self.recognition.finish()
            self.on_stopped()

    def _close(self):
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()

    def stop(self):
        # 立即关闭录音进程；读线程随后收到 EOF，补齐最后一帧并结束录音。
        self.stopping.set()
        self._close()


def error_code(error):
    message = str(error)
    if isinstance(error, TimeoutError) or 'timed out' in message:
        return 'timeout'
    if '尚未关联' in message:
        return 'not_paired'
    if 'pw-record' in message:
        return 'no_recorder'
    if 'libopus' in message:
        return 'no_opus'
    if '识别服务返回错误' in message:
        return 'server'
    if isinstance(error, (OSError, http.client.HTTPException)):
        return 'network'
    return 'failed'


def serve(state=None, stdin=None, stdout=None, transport=None):
    """供插件调用的行协议。

    输入：START <id> / STOP <id> / CANCEL <id> / Q
    输出：READY、LISTENING <id>、LEVEL <id> <0-8>、PARTIAL <id> <hex>、RECOGNIZING <id>、
          FINAL <id> <hex>、ERROR <id> <code>；文字以 UTF-8 十六进制传递，避免换行。
    """
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    lock = threading.Lock()
    transport = transport or Transport(state)
    sessions = {}

    def emit(*parts):
        with lock:
            stdout.write(' '.join(str(part) for part in parts) + '\n')
            stdout.flush()

    def stop_all():
        for recognition, microphone in list(sessions.values()):
            recognition.cancel()
            microphone.stop()

    def start(session_id):
        try:
            recognition = Recognition(transport, lambda value: emit('PARTIAL', session_id, value.encode().hex()))
            microphone = Microphone(recognition, lambda value: emit('LEVEL', session_id, value),
                                    lambda: emit('RECOGNIZING', session_id))
        except Exception as error:
            emit('ERROR', session_id, error_code(error))
            return
        sessions[session_id] = (recognition, microphone)
        emit('LISTENING', session_id)

        def network():
            try:
                final = recognition.run()
                emit('FINAL', session_id, final.encode().hex())
            except Cancelled:
                pass
            except Exception as error:
                # 只记录错误类别；异常正文可能包含响应内容。
                print('语音识别失败：' + type(error).__name__, file=sys.stderr, flush=True)
                emit('ERROR', session_id, error_code(error))
            finally:
                microphone.stop()
                sessions.pop(session_id, None)
        threading.Thread(target=network, daemon=True).start()

    emit('READY')
    try:
        for line in stdin:
            command, _, session_id = line.strip().partition(' ')
            if command == 'Q':
                break
            if not session_id or len(session_id) > 32:
                continue
            if command == 'START':
                stop_all()
                start(session_id)
            elif command in ('STOP', 'CANCEL') and session_id in sessions:
                recognition, microphone = sessions[session_id]
                if command == 'CANCEL':
                    recognition.cancel()
                microphone.stop()
    finally:
        stop_all()
        transport.close()
    return 0


def main(argv=None, state=None):
    import argparse
    parser = argparse.ArgumentParser(prog='wetype-cloud.py voice', description='在线语音识别（会把录音发给官方服务器）')
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--wav', type=Path, help='16kHz、单声道、16 位 WAV 文件')
    source.add_argument('--record', type=float, metavar='秒', help='从默认麦克风录制指定秒数')
    source.add_argument('--serve', action='store_true', help='供输入法插件调用的常驻模式')
    args = parser.parse_args(argv)
    if args.serve:
        return serve(state)
    if args.record is not None and not 0 < args.record <= MAX_AUDIO_SECONDS:
        parser.error('录音时长需在 0–%d 秒之间' % MAX_AUDIO_SECONDS)
    started = time.monotonic()
    transport = Transport(state)
    recognition = Recognition(transport, lambda value: print('…' + value, flush=True))
    microphone = None
    if args.wav:
        pcm = read_wav(args.wav)
        recognition.feed(encode_pcm(pcm)[len(OPUS_HEADER):])
        recognition.finish()
        seconds = len(pcm) / 2 / SAMPLE_RATE
    else:
        microphone = Microphone(recognition)
        threading.Timer(args.record, microphone.stop).start()
    try:
        final = recognition.run()
    finally:
        transport.close()
    if microphone:
        seconds = microphone.frames / 50
    print(json.dumps({'text': final, 'audio_seconds': round(seconds, 2),
                      'elapsed_seconds': round(time.monotonic() - started, 2)}, ensure_ascii=False))
    return 0
