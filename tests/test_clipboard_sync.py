"""在同步入口验证双向文本；只用合成数据，不需要账号或网络。"""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'cloud'))
from 剪贴板同步 import Synchronizer
from 剪贴板协议 import ClipboardImage, ClipboardMessage, GroupSnapshot, SendUnknown, NotSent

PNG = ClipboardImage(b"\x89PNG\r\n\x1a\n" + b"synthetic" + b"IEND\xaeB`\x82", "image/png")


class MemoryClipboard:
    def __init__(self, text='启动前已有文字'):
        self.text = text
        self.writes = []
        self.fail = False

    def read(self):
        return self.text

    def write(self, text, expected):
        if self.fail:
            raise OSError('synthetic failure')
        if self.text != expected:
            raise RuntimeError('clipboard changed')
        self.text = text
        self.writes.append(text)


class MemoryGroup:
    def __init__(self):
        self.snapshot = GroupSnapshot(20, 10, frozenset({10, 11}), None, 1000000)
        self.sent = []
        self.cos = {}
        self.images = {}
        self.downloads = 0

    def fetch(self):
        return self.snapshot

    def send(self, snapshot, text, trace):
        self.sent.append((text, trace))
        return 6

    def text(self, message):
        if message.kind == 2:
            value = self.cos[message.url]
            if isinstance(value, Exception):
                raise value
            return value
        return message.text

    def content(self, message):
        self.downloads += 1
        return self.images.get(message.version)

    def receive(self, text='手机复制的文本', version=5, source=11, expires=2000, kind=1, trace='remote', url=None):
        image = {'url': 'https://bucket.myqcloud.com/i'} if kind == 6 else None
        self.snapshot = GroupSnapshot(20, 10, frozenset({10, 11}),
                                      ClipboardMessage(version, source, expires, kind, text, trace, url, image), 1000000)


class ClipboardSyncTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.state = Path(self.temp.name) / 'clipboard-state.json'
        self.desktop = MemoryClipboard()
        self.group = MemoryGroup()
        self.sync = Synchronizer(self.desktop, self.group, self.state)

    def tearDown(self):
        self.temp.cleanup()

    def test_receive_text_without_uploading_existing_or_echoing_received_text(self):
        self.group.receive()
        self.sync.step()
        self.sync.step()
        self.assertEqual(self.desktop.text, '手机复制的文本')
        self.assertEqual(self.desktop.writes, ['手机复制的文本'])
        self.assertEqual(self.group.sent, [])
        self.assertEqual(self.sync.status()['received'], 1)

    def test_new_local_copy_sends_once_and_survives_restart_without_resending(self):
        self.sync.step()
        self.desktop.text = 'Linux 新复制\n末行\n'
        self.sync.step(poll=False)
        self.assertEqual(self.group.sent, [])
        self.sync.step()
        self.sync.step()
        resumed = Synchronizer(self.desktop, self.group, self.state)
        resumed.step()
        self.assertEqual([row[0] for row in self.group.sent], ['Linux 新复制\n末行\n'])
        self.assertEqual(self.sync.status()['sent'], 1)
        self.assertEqual(self.state.stat().st_mode & 0o777, 0o600)
        self.assertNotIn('Linux 新复制', self.state.read_text())

    def test_unknown_send_is_not_retried_and_old_remote_cannot_overwrite_it(self):
        self.group.receive()
        self.sync.step()
        self.desktop.text = '结果不明的本地文本'
        original = self.group.send
        def unknown(*args):
            original(*args)
            raise SendUnknown()
        self.group.send = unknown
        with self.assertRaises(SendUnknown):
            self.sync.step()
        self.sync.step()
        resumed = Synchronizer(self.desktop, self.group, self.state)
        resumed.step()
        self.assertEqual(len(self.group.sent), 1)
        self.assertEqual(self.desktop.text, '结果不明的本地文本')
        self.group.receive('结果不明的本地文本', version=6, source=10, trace=self.group.sent[0][1])
        resumed.step()
        self.assertFalse(resumed.status()['send_unknown'])
        self.assertNotIn('结果不明的本地文本', self.state.read_text())

    def test_receive_rejects_expired_foreign_self_non_text_and_zero_source(self):
        for options in ({'expires': 1000}, {'source': 12}, {'source': 10}, {'kind': 6},
                        {'text': ''}, {'text': '中' * 350000}, {'source': 0}, {'trace': ''}):
            with self.subTest(options=options.keys()):
                self.group.receive(**options)
                if options.get('source') == 0:
                    self.group.snapshot = GroupSnapshot(20, 10, frozenset({0, 10, 11}), self.group.snapshot.message, 1000000)
                self.sync.step()
                self.assertEqual(self.desktop.text, '启动前已有文字')
        self.assertEqual(self.group.sent, [])

    def test_long_text_is_received_inline_or_from_cos_but_never_sent(self):
        self.group.receive('长' * 100000)
        self.sync.step()
        self.assertEqual(self.desktop.text, '长' * 100000)
        url = 'https://bucket.cos.ap-shanghai.myqcloud.com/clip?sign=x'
        self.group.cos[url] = OSError('synthetic download failure')
        self.group.receive('', version=6, kind=2, url=url)
        with self.assertRaises(OSError):
            self.sync.step()
        self.assertEqual(self.desktop.text, '长' * 100000)
        self.group.cos[url] = '链接里的长文本'
        self.sync.step()
        self.sync.step()
        self.assertEqual(self.desktop.writes, ['长' * 100000, '链接里的长文本'])
        self.desktop.text = '本地超长' * 70000
        self.sync.step()
        self.sync.step()
        self.assertEqual(self.group.sent, [])

    def test_failed_desktop_write_retains_cursor_and_retries_after_recovery(self):
        self.group.receive()
        self.desktop.fail = True
        with self.assertRaises(OSError):
            self.sync.step()
        self.assertFalse(self.state.exists())
        self.desktop.fail = False
        self.sync.step()
        resumed = Synchronizer(self.desktop, self.group, self.state)
        resumed.step()
        self.assertEqual(self.desktop.writes, ['手机复制的文本'])

    def test_preflight_failure_retries_new_copy_without_replaying_old_contents(self):
        self.sync.step()
        self.desktop.text = '待发送'
        original = self.group.send
        self.group.send = lambda *args: (_ for _ in ()).throw(NotSent())
        with self.assertRaises(NotSent):
            self.sync.step()
        self.assertEqual(self.group.sent, [])
        self.group.send = original
        self.sync.step()
        self.assertEqual([row[0] for row in self.group.sent], ['待发送'])

    def test_cursor_save_failure_does_not_echo_or_consume_failed_receipt(self):
        self.group.receive()
        self.state.parent.chmod(0o500)
        try:
            with self.assertRaises(OSError):
                self.sync.step()
        finally:
            self.state.parent.chmod(0o700)
        self.sync.step()
        self.assertEqual(self.group.sent, [])
        self.assertEqual(self.sync.status()['received'], 1)
        resumed = Synchronizer(self.desktop, self.group, self.state)
        resumed.step()
        self.assertEqual(len(self.desktop.writes), 2)

    def test_group_change_discards_queued_copy_instead_of_sending_to_new_group(self):
        self.sync.step()
        self.desktop.text = '旧组的待发送文字'
        self.sync.step(poll=False)
        self.group.snapshot = GroupSnapshot(21, 10, frozenset({10, 12}), None, 1000000)
        self.sync.step()
        self.assertEqual(self.group.sent, [])
        self.desktop.text = '新组复制的文字'
        self.sync.step()
        self.assertEqual([row[0] for row in self.group.sent], ['新组复制的文字'])

    def test_write_that_changes_clipboard_but_fails_confirmation_is_never_echoed(self):
        self.sync.step()
        self.group.receive()
        original = self.desktop.write
        def write_without_confirmation(*args, **kwargs):
            original(*args, **kwargs)
            raise TimeoutError('synthetic confirmation failure')
        self.desktop.write = write_without_confirmation
        with self.assertRaises(TimeoutError):
            self.sync.step()
        self.desktop.write = original
        self.sync.step()
        self.assertEqual(self.group.sent, [])
        self.assertEqual(self.sync.status()['received'], 1)


class ClipboardSyncLogTest(unittest.TestCase):
    def test_events_are_logged_once_without_clipboard_contents(self):
        with tempfile.TemporaryDirectory() as directory:
            lines = []
            desktop, group = MemoryClipboard(), MemoryGroup()
            sync = Synchronizer(desktop, group, Path(directory) / 'state.json', images=True, log=lines.append)
            sync.step()
            desktop.text = '私密文字'
            sync.step()
            group.receive('过期内容', version=7, expires=500)
            sync.step()
            sync.step()
            group.images[8] = PNG
            group.receive('', version=8, kind=6)
            sync.step()
            self.assertEqual(lines, ['本机复制：文本 4 字', lines[1], '跳过远端 v7：已过期', lines[3]])
            self.assertRegex(lines[1], r'^已发送 v6：文本 4 字，用时 \d+\.\d 秒$')
            self.assertRegex(lines[3], r'^已收到 v8：图片 PNG 0\.0 MB，剩余有效期 1000 秒，下载与写入用时 \d+\.\d 秒$')
            self.assertFalse(any('私密' in line or '过期内容' in line for line in lines))


class ClipboardImageSyncTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.state = Path(self.temp.name) / 'clipboard-state.json'
        self.desktop = MemoryClipboard()
        self.group = MemoryGroup()

    def tearDown(self):
        self.temp.cleanup()

    def test_images_are_ignored_unless_enabled(self):
        sync = Synchronizer(self.desktop, self.group, self.state)
        self.group.images[5] = PNG
        self.group.receive('', kind=6)
        sync.step()
        self.assertEqual((self.desktop.writes, self.group.downloads), ([], 0))

    def test_remote_image_is_written_once_and_not_echoed(self):
        sync = Synchronizer(self.desktop, self.group, self.state, images=True)
        self.group.images[5] = PNG
        self.group.receive('', kind=6)
        sync.step()
        sync.step()
        self.assertEqual(self.desktop.writes, [PNG])
        self.assertEqual((self.group.sent, self.group.downloads, sync.status()['received']), ([], 1, 1))

    def test_local_image_is_sent_and_confirmed_by_trace_after_unknown_result(self):
        sync = Synchronizer(self.desktop, self.group, self.state, images=True)
        sync.step()
        self.desktop.text = PNG
        def unknown(snapshot, value, trace):
            self.group.sent.append((value, trace))
            raise SendUnknown('synthetic')
        self.group.send = unknown
        with self.assertRaises(SendUnknown):
            sync.step()
        trace = self.group.sent[0][1]
        self.assertEqual(self.group.sent[0][0], PNG)
        # 自己发出的图片回到组里时引用已重新加密，只能按 trace 认领，不再下载或写回。
        self.group.receive('', version=8, source=10, kind=6, trace=trace)
        sync.step()
        self.assertEqual((self.desktop.writes, self.group.downloads, sync.status()['sent']), ([], 0, 1))
        self.assertNotIn('intent', sync.state)

    def test_oversized_local_image_is_not_sent(self):
        sync = Synchronizer(self.desktop, self.group, self.state, images=True)
        sync.step()
        self.desktop.text = ClipboardImage(PNG.data + b'0' * (10 * 1024 * 1024), 'image/png')
        sync.step()
        self.assertEqual(self.group.sent, [])

    def test_invalid_remote_image_advances_cursor_instead_of_retrying(self):
        sync = Synchronizer(self.desktop, self.group, self.state, images=True)
        self.group.receive('', kind=6)
        sync.step()
        sync.step()
        self.assertEqual((self.desktop.writes, self.group.downloads), ([], 1))


if __name__ == '__main__':
    unittest.main()
