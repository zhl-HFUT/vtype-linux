"""用户主动执行的官方客户端关联、云服务配置和定时刷新入口。"""
import argparse
import contextlib
import fcntl
import io
import json
import os
import platform
import re
import subprocess
import sys
from pathlib import Path
import 官方同步探针 as p
from 运行支持 import DATA, atomic, refresh_client, wxc_binary, write_doc


def config_path():
    return Path(os.environ.get('XDG_CONFIG_HOME', Path.home() / '.config')) / 'wetype-ime/cloud.json'


def query_group():
    with (p.STATE / 'identity.lock').open('a+') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        identity = json.loads((p.STATE / 'identity.json').read_text())
        # 元数据摘要不包含输入文本；仍仅留在私有账户目录。
        with (p.STATE / 'bootstrap.log').open('w') as log, contextlib.redirect_stdout(log):
            p.Probe(identity).group()
    if (p.STATE / 'common-words-payload.bin').exists():
        from 导出官方常用语 import export
        export(DATA / 'common-phrases.bin', False)


def quote_unit(value):
    return '"' + str(value).replace('%', '%%').replace('\\', '\\\\').replace('"', '\\"') + '"'


def configure_timer(runtime):
    units = Path(os.environ.get('XDG_CONFIG_HOME', Path.home() / '.config')) / 'systemd/user'
    units.mkdir(parents=True, exist_ok=True)
    # 同名用户文件不可静默覆盖其他命令。
    service = units / 'wetype-sync.service'
    if service.exists() and 'WeType' not in service.read_text():
        raise RuntimeError('wetype-sync.service 已被其他服务使用')
    body = ('[Unit]\nDescription=WeType session and common phrase refresh\n'
            '[Service]\nType=oneshot\nUMask=0077\nTimeoutStartSec=90\n'
            'Environment=' + quote_unit('WETYPE_STATE_DIR=' + str(p.STATE)) + '\n'
            'Environment=' + quote_unit('WETYPE_WXC=' + wxc_binary()) + '\n'
            'Environment=' + quote_unit('PATH=' + os.environ.get('PATH', '/usr/bin:/bin')) + '\n'
            + ('Environment=' + quote_unit('WETYPE_SNAPPY_LIBRARY=' + os.environ['WETYPE_SNAPPY_LIBRARY']) + '\n'
               if os.environ.get('WETYPE_SNAPPY_LIBRARY') else '')
            + 'ExecStart=' + quote_unit(sys.executable) + ' ' + quote_unit(Path(__file__).with_name('会话与常用语刷新.py')) + '\n'
            'NoNewPrivileges=yes\n')
    timer = ('[Unit]\nDescription=WeType session and common phrase refresh timer\n'
             '[Timer]\nOnStartupSec=1min\nOnUnitInactiveSec=5min\nRandomizedDelaySec=30\n'
             '[Install]\nWantedBy=timers.target\n')
    atomic(service, body.encode())
    atomic(units / 'wetype-sync.timer', timer.encode())
    subprocess.run(['systemctl', '--user', 'daemon-reload'], check=True)
    subprocess.run(['systemctl', '--user', 'enable', '--now', 'wetype-sync.timer'], check=True)


def main(argv=None):
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state', type=Path, default=p.STATE, help='私有设备身份目录；关联后保持不变')
    parser.add_argument('--runtime', type=Path, default=Path(os.environ.get(
        'WETYPE_ENGINE_DIR', Path.home() / '.local/lib/wetype-ime/arm64')))
    commands = parser.add_subparsers(dest='action', required=True)
    pair = commands.add_parser('pair', help='用官方客户端六位关联码加入设备组')
    pair.add_argument('--code', help='省略时交互输入')
    pair.add_argument('--name', default='Linux 输入法')
    enable = commands.add_parser('enable', help='启用云候选、原生增量同步及快照下载')
    enable.add_argument('--timer', action='store_true', help='同时安装五分钟会话/常用语刷新任务')
    commands.add_parser('refresh', help='刷新已有身份、组信息和常用语；不读写词库')
    commands.add_parser('status', help='显示配置状态，不输出密钥或个人词条')
    commands.add_parser('disable', help='关闭云服务及刷新任务，保留身份和个人词库')
    clipboard = commands.add_parser('clipboard', help='独立管理跨设备文本剪贴板同步', add_help=False)
    clipboard.add_argument('-h', '--help', dest='clipboard_help', action='store_true')
    clipboard.add_argument('arguments', nargs=argparse.REMAINDER)
    commands.add_parser('voice', help='在线语音识别（会把录音发给官方服务器）', add_help=False)
    args, extra = parser.parse_known_args(argv)
    if extra and args.action != 'voice':
        parser.error('无法识别的参数：' + ' '.join(extra))
    p.STATE = args.state.expanduser().resolve()
    os.environ['WETYPE_STATE_DIR'] = str(p.STATE)
    path = config_path()
    if args.action == 'clipboard':
        from 剪贴板同步 import main as clipboard_main
        return clipboard_main(['--help'] if args.clipboard_help else args.arguments,
                              state=p.STATE, entry=Path(sys.argv[0]).resolve())
    if args.action == 'voice':
        from 语音识别 import main as voice_main
        return voice_main(extra, state=p.STATE)
    if args.action == 'status':
        config = json.loads(path.read_text()) if path.exists() else {}
        print(json.dumps({'paired': (p.STATE / 'identity.json').is_file(),
                          'cloud_enabled': bool(config.get('enabled')),
                          'native_sync': bool(config.get('native_sync')),
                          'snapshot_download': bool(config.get('native_snapshot_download'))}, ensure_ascii=False))
        return 0
    if args.action == 'disable':
        from 剪贴板同步 import disable as disable_clipboard
        disable_clipboard()
        if path.exists():
            config = json.loads(path.read_text()); config['enabled'] = False
            write_doc(path, config)
        subprocess.run(['systemctl', '--user', 'disable', '--now', 'wetype-sync.timer'],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        print('云服务和剪贴板同步已关闭，身份与词库保留；引擎云服务在重启 Fcitx5 后生效。')
        return 0
    p.STATE.mkdir(parents=True, exist_ok=True, mode=0o700); p.STATE.chmod(0o700)
    if args.action == 'pair':
        if (p.STATE / 'identity.json').exists():
            raise RuntimeError('此目录已有身份；使用 enable，或为新关联选择另一个 --state 目录')
        code = args.code or input('官方微信输入法六位关联码：').strip()
        if not re.fullmatch(r'[0-9]{6}', code):
            raise ValueError('关联码必须为六位数字')
        with (p.STATE / 'pair-result.json').open('wb') as log:
            result = subprocess.run([wxc_binary(), '--state', str(p.STATE), '--name', args.name,
                                     '--json', 'pair', code], stdout=log, stderr=subprocess.STDOUT, timeout=130)
        if result.returncode:
            raise RuntimeError('关联未成功；请检查官方客户端确认及私有 pair-result.json')
        query_group()
        print('已关联官方客户端；执行 enable 才会启用引擎云服务。')
    elif args.action == 'refresh':
        from 会话与常用语刷新 import main as refresh
        return refresh()
    else:
        if platform.machine() != 'x86_64':
            raise RuntimeError('云扩展当前只验证并开放 x86_64/QEMU 路线；ARM64 原生扩展尚未验证')
        runtime = args.runtime.expanduser().resolve()
        for relative in ('cloud_proxy.py', 'libwetype-cloud.so', 'cloud/官方同步探针.py', 'wetype-harness'):
            if not (runtime / relative).is_file():
                raise RuntimeError('运行时缺少 ' + relative + '，请先安装本增强版运行时')
        refresh_client(); query_group()
        config = {'enabled': True, 'protocol_dir': str(Path(__file__).resolve().parent), 'state_dir': str(p.STATE),
                  'shim': str(runtime / 'libwetype-cloud.so'), 'native_sync': True,
                  'native_snapshot_download': True}
        if args.timer:
            configure_timer(runtime)
        write_doc(path, config)
        print('云候选、原生增量同步和快照下载已配置；重启 Fcitx5 后生效。')
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as error:
        print('操作未完成：' + str(error), file=sys.stderr)
        sys.exit(1)
