"""不联网、不读取设备身份：检查 Snappy 库查找与压缩响应。"""
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'cloud'))
import 官方同步探针 as p


class Response:
    def __init__(self, data, compression='2'):
        self.data = data
        self.headers = {'Kb-CompressionType': compression}

    def read(self, limit):
        return self.data[:limit]


class Connection:
    """注入 Probe 的持久连接接口（request/getresponse/read）。"""
    def __init__(self, response):
        self.response = response

    def request(self, *_args, **_kwargs):
        pass

    def getresponse(self):
        return self.response


class SnappyTransportTest(unittest.TestCase):
    def request(self, data, encrypted=False, compression='2'):
        # 使用合成密钥，跳过会读取设备身份的构造器。
        probe = p.Probe.__new__(p.Probe)
        probe.key = b'0123456789ABCDEF0123456789ABCDEF'
        probe.offset = 0
        probe.task = 1
        payload = p.aes(data, probe.key, True) if encrypted else data
        probe.conn = Connection(Response(payload, compression))
        return probe.request('/api_v2' if encrypted else '/timestamp', b'')

    def test_literal_compressed_response(self):
        # Snappy 原始块：长度为 5，literal 长度标签为 0x10。
        for encrypted in [False, True]:
            with self.subTest(encrypted=encrypted):
                self.assertEqual(self.request(b'\x05\x10hello', encrypted), b'hello')
                self.assertEqual(self.request(b'\x00', encrypted), b'')

    def test_missing_library_is_clear_error(self):
        with patch.dict(os.environ, {'WETYPE_SNAPPY_LIBRARY': ''}):
            with patch.object(p.ctypes.util, 'find_library', return_value=None):
                with patch.object(p.ctypes, 'CDLL') as load:
                    with self.assertRaisesRegex(RuntimeError, 'Snappy'):
                        p.snappy_decompress(b'\x00')
                    load.assert_not_called()

    def test_explicit_library_skips_lookup(self):
        actual = os.environ.get('WETYPE_SNAPPY_LIBRARY') or p.ctypes.util.find_library('snappy')
        self.assertIsNotNone(actual, '测试需要 libsnappy')
        with patch.dict(os.environ, {'WETYPE_SNAPPY_LIBRARY': actual}):
            with patch.object(p.ctypes.util, 'find_library', side_effect=AssertionError('不应查找系统库')):
                self.assertEqual(p.snappy_decompress(b'\x05\x10hello'), b'hello')

    def test_invalid_and_oversized_response(self):
        for data in [b'invalid snappy', b'\x81\x80\x80\x01']:
            with self.subTest(data=data):
                with self.assertRaises(ValueError):
                    self.request(data)

    def test_plain_and_unknown_compression(self):
        self.assertEqual(self.request(b'test', compression='1'), b'test')
        with self.assertRaises(ValueError):
            self.request(b'test', compression='3')


if __name__ == '__main__':
    unittest.main()
