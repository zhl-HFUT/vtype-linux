"""Linux输入引擎宿主代理：复用既有身份，转发普通拼音云查询，以及（native_sync 开启时）官方引擎自身的词库同步请求。

同步请求只放行已核对的 4814 子命令：34 组信息/常用语/清理记录拉取（不含剪贴板位）、41 增量上传、42 增量拉取、
44 全量同步信息查询。native_snapshot_download 开启时允许已知快照及本身份的 COS 凭证查询（60），
宿主仅执行 HTTPS GET；JNI 上传任务异步失败，不发送文件。43 全量状态上报、91 上屏报告由宿主本地回复成功、
不联网：被拒时引擎会反复重试，91 的待发上屏队列（最多100条）也一直留在词库里。其他未核对请求继续拒绝。"""
import argparse,concurrent.futures,fcntl,json,os,selectors,socket,struct,subprocess,sys,threading,time,urllib.parse,urllib.request,tempfile
import urllib.error
import xml.etree.ElementTree as ET
from collections import deque
from pathlib import Path
MAGIC=0x57434c31
SYNC_MAGIC=0x57534e31
COS_MAGIC=0x57434f31
SYNC_ALLOWED={34,41,42,44}
SYNC_FILTER_BITS=1|4|8|32   # 设备信息/常用语/词典信息/清理记录；不含剪贴板
MAX_PUSH_OPS=1000           # 单次上传超过此数视为异常（如词库被重建），拒绝并登记

def cloud_timeout_ms(config):
    value=config.get('cloud_timeout_ms',1500)
    if type(value) is not int or not 200<=value<=5000:
        raise ValueError('cloud_timeout_ms 必须是 200 到 5000 的整数')
    return value

def sync_service(p,sock,uin,group,log_path,readonly=False,downloads=False):
    """在独立线程中服务官方引擎的 4814 同步请求；只记录统计，不落词条内容。"""
    client=None;download_keys=set()
    def read_exact(n):
        data=b''
        while len(data)<n:
            chunk=sock.recv(n-len(data))
            if not chunk:raise EOFError
            data+=chunk
        return data
    def note(entry):
        try:
            fd=os.open(log_path,os.O_WRONLY|os.O_CREAT|os.O_APPEND,0o600)
            with os.fdopen(fd,'a') as f:f.write(json.dumps(entry,ensure_ascii=False)+'\n')
        except OSError:pass
    def check(sub,body):
        f=p.fields(body)
        if sub==60 and downloads:
            if p.one(f,1)!=5 or p.one(f,3)!=7:return 'cos_write_or_type'
            extra=json.loads(p.one(f,2,b'{}'))
            if set(extra)!={'group_id','key'}:return 'cos_extra_fields'
            if str(extra.get('group_id'))!=str(group) or extra.get('key') not in download_keys:return 'cos_group_or_key'
            return None
        if sub==43:
            f43=p.fields(p.one(f,1,b''))
            if p.one(f43,1)!=uin or p.one(f,2)!=group:return 'local_ack_uin_or_group'
            return None
        if sub==91:return None
        if sub not in SYNC_ALLOWED:return 'subcommand'
        if readonly and sub==41:return 'isolated_readonly'
        if p.one(f,1)!=uin:return 'uin'
        if sub==34 and (p.one(f,3,0)&~SYNC_FILTER_BITS):return 'filter'
        if sub==42 and p.one(f,3)!=group:return 'group'
        if sub==44 and p.one(f,2)!=group:return 'group'
        if sub==41:
            if p.one(f,3)!=group:return 'group'
            ops=len(p.fields(p.one(f,2,b'')).get(1,[]))
            if ops>MAX_PUSH_OPS:return f'push_ops:{ops}'
        return None
    def request(sub,body):
        nonlocal client
        deadline=time.monotonic()+20
        with (p.STATE/'identity.lock').open('a+') as lock:
            while True:
                try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB);break
                except BlockingIOError:
                    if time.monotonic()>deadline:raise TimeoutError('identity.lock busy')
                    time.sleep(.2)
            identity=json.loads((p.STATE/'identity.json').read_text())
            if client is None or client.identity.get('server_key')!=identity.get('server_key'):
                client=p.Probe(identity);client.timeout=15
                started=int(time.time()*1000);stamp=p.one(p.fields(client.request('/timestamp',b'')),1)
                client.offset=stamp-(started+int(time.time()*1000))//2
            return client.api(sub,body)
    while True:
        try:
            magic,task,cmd,sub,size=struct.unpack('<5I',read_exact(20));body=read_exact(size)
        except (EOFError,OSError):return
        entry={'t':round(time.time(),3),'sub':sub,'size':size};code=-1;response=b''
        if magic!=SYNC_MAGIC or cmd!=4814:return
        try:
            refused=check(sub,body)
            if refused:entry['refused']=refused
            else:
                if sub==41:entry['ops']=len(p.fields(p.one(p.fields(body),2,b'')).get(1,[]))
                # 43 回 {status 0}；91 的响应字段1是嵌套消息，回 {1:0} 会解析失败、不清队列，空响应即全默认成功。
                if sub in (43,91):response=p.number(1,0) if sub==43 else b'';entry['local_ack']=True
                else:response=request(sub,body)
                code=0
                if sub==44:
                    metadata=p.fields(response)
                    if p.one(metadata,1,-1)==0:
                        download_keys.clear()
                        # 官方 COS 任务串行排队；允许本身份申请凭证，JNI 上传回调仍返回 -1。
                        # 凭证查询不传文件；拒绝上传后队列才能继续处理下载任务。
                        download_keys.add(f'{uin}_{p.one(metadata,3)}')
                        for raw in metadata.get(2,[]):
                            record=p.fields(raw)
                            detail=p.fields(p.one(record,2,b''))
                            # 原生引擎按设备编号和快照版本申请凭证；它可能与旧 URL 的文件名不同。
                            # 只登记同组成功响应明确返回的设备/版本，不接受任意拼接的键。
                            device,version=p.one(record,1),p.one(detail,6)
                            if isinstance(device,int) and device>0 and isinstance(version,int) and version>0:
                                download_keys.add(f'{device}_{version}')
                            url=p.one(detail,8,b'').decode()
                            key=urllib.parse.urlparse(url).path.rsplit('/',1)[-1]
                            if key:download_keys.add(key)
        except Exception as error:
            entry['error']=type(error).__name__
        entry['code']=code;entry['response']=len(response);note(entry)
        try:sock.sendall(struct.pack('<IIiI',SYNC_MAGIC,task,code,len(response))+response)
        except OSError:return

def cos_service(sock,work,log_path):
    """只读 HTTPS 文件下载，写入引擎工作目录；不解释词库格式，不执行上传。"""
    root=Path(work).resolve()
    def read_exact(n):
        data=b''
        while len(data)<n:
            chunk=sock.recv(n-len(data))
            if not chunk:raise EOFError
            data+=chunk
        return data
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self,*args,**kwargs):raise ValueError('COS redirect rejected')
    http=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())
    while True:
        try:
            magic,job,*sizes=struct.unpack('<6I',read_exact(24))
            if magic!=COS_MAGIC or any(n>16384 for n in sizes):return
            bucket,path,key,url=[read_exact(n).decode() for n in sizes]
        except (EOFError,OSError,UnicodeError):return
        code=-1;count=0;temporary=None;entry={'t':round(time.time(),3),'job':job}
        try:
            target=Path(path).resolve();parsed=urllib.parse.urlparse(url)
            assert target.is_relative_to(root) and target!=root and not Path(path).is_symlink()
            assert parsed.scheme=='https' and parsed.hostname and parsed.hostname.endswith('.myqcloud.com')
            assert not parsed.username and not parsed.password and parsed.port in (None,443)
            assert key and parsed.path.rsplit('/',1)[-1]==key.rsplit('/',1)[-1]
            assert parsed.query and len(url)<16384
            target.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
            fd,temporary=tempfile.mkstemp(prefix='.cos-download-',dir=target.parent)
            with os.fdopen(fd,'wb') as output,http.open(url,timeout=30) as response:
                length=int(response.headers.get('Content-Length',0));assert length<=128*1024*1024
                deadline=time.monotonic()+180
                while True:
                    data=response.read(256*1024)
                    if not data:break
                    count+=len(data);assert count<=128*1024*1024 and time.monotonic()<deadline
                    output.write(data)
                assert count>0 and (not length or count==length)
                output.flush();os.fsync(output.fileno())
            os.replace(temporary,target);temporary=None;code=0
        except urllib.error.HTTPError as error:
            entry['error']='HTTPError';entry['http_status']=error.code
            try:
                reason=ET.fromstring(error.read(16384)).findtext('Code')
                if reason in {'NoSuchKey','AccessDenied','SignatureDoesNotMatch','RequestTimeTooSkewed','ExpiredToken'}:
                    entry['cos_error_code']=reason
            except (OSError,ET.ParseError):pass
            finally:error.close()
        except Exception as error:
            entry['error']=type(error).__name__
            # 由用户检查网络/代理；不自动重传或换源。
        finally:
            if temporary:
                try:os.unlink(temporary)
                except OSError:pass
        entry.update(code=code,bytes=count)
        try:
            fd=os.open(log_path,os.O_WRONLY|os.O_CREAT|os.O_APPEND,0o600)
            with os.fdopen(fd,'a') as f:f.write(json.dumps(entry)+'\n')
            sock.sendall(struct.pack('<IIi',COS_MAGIC,job,code))
        except OSError:return

def main():
    os.umask(0o077)
    parser=argparse.ArgumentParser();parser.add_argument('--config',required=True);parser.add_argument('command',nargs=argparse.REMAINDER)
    args=parser.parse_args();command=args.command
    if command and command[0]=='--':command=command[1:]
    config=json.loads(Path(args.config).read_text())
    if not config.get('enabled'):os.execvpe(command[0],command,os.environ)
    timeout=cloud_timeout_ms(config)
    sys.path.insert(0,config['protocol_dir']);import 官方同步探针 as p
    if config.get('state_dir'):p.STATE=Path(config['state_dir']).expanduser().resolve()
    from 云响应 import candidates
    parent,child=socket.socketpair(socket.AF_UNIX,socket.SOCK_SEQPACKET)
    env=os.environ.copy();env['WETYPE_CLOUD_FD']=str(child.fileno());env['WETYPE_CLOUD_NATIVE']='1'
    env['WETYPE_CLOUD_TIMEOUT_MS']=str(timeout)
    passed=[child.fileno()];sync_child=None;cos_child=None
    if config.get('native_sync'):
        identity=json.loads((p.STATE/'identity.json').read_text())
        info=p.fields(p.one(p.fields((p.STATE/'group-sync-response.pb').read_bytes()),1,b''))
        group,switch=p.one(info,1),p.one(info,5,0);hot=p.one(p.fields(p.one(info,4,b'')),1,0)
        assert group and identity.get('uin')
        sync_parent,sync_child=socket.socketpair(socket.AF_UNIX,socket.SOCK_STREAM)
        env.update(WETYPE_SYNC_FD=str(sync_child.fileno()),WETYPE_GROUP_SYNC=f'{group},{switch},{hot}',WETYPE_UIN=str(identity['uin']))
        passed.append(sync_child.fileno())
        log_dir=Path(config.get('sync_log_dir',p.STATE/'native-sync-live'));log_dir.mkdir(mode=0o700,exist_ok=True)
        downloads=bool(config.get('native_snapshot_download'))
        threading.Thread(target=sync_service,args=(p,sync_parent,identity['uin'],group,log_dir/'requests.jsonl',bool(config.get('sync_readonly')),downloads),daemon=True).start()
        if downloads:
            assert env.get('WETYPE_WORK_DIR')
            cos_parent,cos_child=socket.socketpair(socket.AF_UNIX,socket.SOCK_STREAM)
            env['WETYPE_COS_FD']=str(cos_child.fileno());passed.append(cos_child.fileno())
            threading.Thread(target=cos_service,args=(cos_parent,env['WETYPE_WORK_DIR'],log_dir/'downloads.jsonl'),daemon=True).start()
    shim=str(Path(config['shim']).resolve())
    assert Path(shim).is_file()
    # 只设置guest的LD_PRELOAD，不能把ARM64库预加载到宿主Python。
    env['QEMU_SET_ENV']='LD_PRELOAD='+shim
    proc=subprocess.Popen(command,stdin=subprocess.PIPE,stdout=subprocess.PIPE,env=env,pass_fds=tuple(passed),bufsize=0)
    child.close()
    if sync_child:sync_child.close()
    if cos_child:cos_child.close()
    parent.setblocking(False)
    selector=selectors.DefaultSelector();selector.register(sys.stdin.buffer,selectors.EVENT_READ,'stdin')
    selector.register(proc.stdout,selectors.EVENT_READ,'stdout');selector.register(parent,selectors.EVENT_READ,'network')
    replies=deque();pending=None;future=None;active=None;latest=None;input_buffer=b'';backoff_until=0
    stats={'requests':0,'cancelled':0,'responses':0,'errors':0};counter=0
    pool=concurrent.futures.ThreadPoolExecutor(max_workers=1)
    def reply(task,code,body=b''):
        replies.append(struct.pack('<IIiI',MAGIC,task,code,len(body))+body)
    def sanitize(raw):
        outer=p.fields(raw);body=p.fields(p.one(outer,4,b''));sequences=body.get(3,[])
        query=b''.join(sequences).decode('ascii')
        assert 4<=len(query)<=128 and all(c in "abcdefghijklmnopqrstuvwxyz'" for c in query)
        assert p.one(outer,1)==2 and len(p.one(outer,2,b''))<=64
        context=p.fields(p.one(body,1,b''));safe_context=b''
        for k in [1,2,3,4,5,10,11,12,13,15]:
            for v in context.get(k,[]):safe_context+=p.number(k,v) if isinstance(v,int) else p.blob(k,v)
        # 不带前后文、选词历史、应用名称或地理信息。
        safe_body=p.blob(1,safe_context)+b''.join(p.blob(3,v) for v in sequences)+p.number(11,10)+p.number(12,0)
        safe=p.number(1,2)+p.blob(2,p.one(outer,2,b''))+p.blob(4,safe_body)+p.number(5,p.one(outer,5))
        allowed=config.get('test_queries')
        if allowed is not None:assert query in allowed
        return safe
    probe_cache={}
    def query_cloud(task,raw):
        with (p.STATE/'identity.lock').open('a+') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            # 复用 Probe（含其持久 HTTPS 连接）：省去每次的 openssl ECDH 派生与
            # TCP/TLS 握手；身份文件变化（重新配对）时按 mtime 重建。
            identity_path=p.STATE/'identity.json'
            stamp=identity_path.stat().st_mtime_ns
            if probe_cache.get('stamp')!=stamp:
                probe_cache['client']=p.Probe(json.loads(identity_path.read_text()))
                probe_cache['client'].timeout=1.2
                probe_cache['stamp']=stamp
            client=probe_cache['client']
            response=client.request('/api_v2',raw,2,service_command=4329)
        rows=candidates(response)
        assert len(response)<=65536
        evidence=config.get('test_evidence')
        if evidence:
            directory=Path(evidence);assert directory.resolve().is_relative_to((Path(config['protocol_dir'])/'private-state').resolve())
            directory.mkdir(exist_ok=True,parents=True);(directory/f'{os.getpid()}-{task}-request.pb').write_bytes(raw);(directory/f'{os.getpid()}-{task}-response.pb').write_bytes(response)
        return response
    try:
        while proc.poll() is None:
            for key,_ in selector.select(.01):
                if key.data=='stdin':
                    data=os.read(sys.stdin.fileno(),8192)
                    if not data:proc.stdin.close();selector.unregister(sys.stdin.buffer);continue
                    input_buffer+=data
                    while b'\n' in input_buffer:
                        line,input_buffer=input_buffer.split(b'\n',1)
                        if line.startswith((b'B ',b'L ',b'S ',b'C')):
                            latest=None
                            if pending:reply(pending[0],-2);stats['cancelled']+=1;pending=None
                        proc.stdin.write(line+b'\n')
                elif key.data=='stdout':
                    data=os.read(proc.stdout.fileno(),65536)
                    if not data:selector.unregister(proc.stdout);continue
                    sys.stdout.buffer.write(data);sys.stdout.buffer.flush()
                else:
                    packet=parent.recv(8192+20)
                    if not packet:
                        selector.unregister(parent);continue
                    if len(packet)<20:continue
                    magic,task,cmd,sub,size=struct.unpack('<5I',packet[:20])
                    if magic!=MAGIC or (cmd,sub)!=(4329,2) or len(packet)!=20+size:continue
                    try:raw=sanitize(packet[20:])
                    except Exception:reply(task,-2);continue
                    if pending:reply(pending[0],-2);stats['cancelled']+=1
                    pending=(task,raw,time.monotonic()+.045);latest=task
            if future and future.done():
                try:
                    body=future.result();code=0;stats['responses']+=1
                except Exception:
                    body=b'';code=-2;stats['errors']+=1;backoff_until=time.monotonic()+min(60,2**min(stats['errors'],6))
                    # 网络失败先核对已登记Clash与端口；不换源、不自动重传。
                    if stats['errors']==1:
                        subprocess.run(['systemctl','is-active','clash-verge-service.service'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
                        try:
                            with socket.create_connection(('127.0.0.1',7897),timeout=.1):pass
                        except OSError:pass
                reply(active,code if active==latest else -2,body if active==latest else b'');future=None;active=None
            if pending and not future and time.monotonic()>=pending[2]:
                task,raw,_=pending;pending=None
                if task!=latest or time.monotonic()<backoff_until:reply(task,-2)
                else:
                    active=task;future=pool.submit(query_cloud,task,raw);stats['requests']+=1
            while replies:
                try:parent.send(replies[0]);replies.popleft()
                except BlockingIOError:break
            counter+=1
            if counter%500==0 and config.get('test_evidence'):
                (Path(config['test_evidence'])/'stats.json').write_text(json.dumps(stats))
    finally:
        if proc.poll() is not None:
            remaining=proc.stdout.read()
            if remaining:sys.stdout.buffer.write(remaining);sys.stdout.buffer.flush()
        parent.close();selector.close()
        if proc.poll() is None:proc.terminate()
        try:proc.wait(timeout=.5)
        except subprocess.TimeoutExpired:proc.kill();proc.wait()
        pool.shutdown(wait=True,cancel_futures=True)
        if config.get('test_evidence'):(Path(config['test_evidence'])/'stats.json').write_text(json.dumps(stats))
    return proc.returncode

if __name__=='__main__':sys.exit(main())
