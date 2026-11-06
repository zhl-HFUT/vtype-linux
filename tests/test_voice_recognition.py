"""验证语音识别的 OPUS_RAW 编码、请求字段和会话流程；不联网、不需要账号。"""
import ctypes
import ctypes.util
import json
import os
import threading
import time
from pathlib import Path
import struct
import sys
import tempfile
import unittest
import wave

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'cloud'))
import 官方同步探针 as p
import 语音识别 as v


def tone(seconds=1.0):
    import math
    count = int(v.SAMPLE_RATE * seconds)
    return b''.join(struct.pack('<h', int(8000 * math.sin(2 * math.pi * 440 * i / v.SAMPLE_RATE))) for i in range(count))


def response(voice_id=b'', seq=0, text='', end=0, interval=100, max_packet=3500, ret=0):
    base = p.number(1, ret & (1 << 64) - 1) + p.blob(2, p.blob(1, b''))
    inner = p.blob(1, base) + p.blob(2, voice_id) + p.number(3, seq) + p.blob(4, text) + p.number(5, end)
    inner += p.number(6, interval) + p.number(7, max_packet) + p.blob(13, text)
    return inner


@unittest.skipUnless(ctypes.util.find_library('opus'), '需要 libopus')
class EncodingTest(unittest.TestCase):
    def test_header_and_frames_decode(self):
        pcm = tone(0.55)
        data = v.encode_pcm(pcm)
        self.assertEqual(data[:16], b'#!OPUS_RAW_V1\x02\x01\x00')
        lib = ctypes.CDLL(ctypes.util.find_library('opus'))
        lib.opus_decoder_create.restype = ctypes.c_void_p
        error = ctypes.c_int()
        decoder = lib.opus_decoder_create(v.SAMPLE_RATE, 1, ctypes.byref(error))
        output = ctypes.create_string_buffer(v.FRAME_SAMPLES * 2)
        pos = 16
        frames = 0
        while pos < len(data):
            size, = struct.unpack_from('<H', data, pos)
            pos += 2
            self.assertEqual(lib.opus_decode(ctypes.c_void_p(decoder), data[pos:pos + size], size,
                                             output, v.FRAME_SAMPLES, 0), v.FRAME_SAMPLES)
            pos += size
            frames += 1
        lib.opus_decoder_destroy(ctypes.c_void_p(decoder))
        self.assertEqual(pos, len(data))
        self.assertEqual(frames, 28)   # 0.55 秒补齐到 28 个 20ms 帧

    def test_streaming_matches_single_call(self):
        pcm = tone(0.5)
        encoder = v.OpusEncoder()
        parts = encoder.encode(pcm[:999]) + encoder.encode(pcm[999:], final=True)
        encoder.close()
        self.assertEqual(v.OPUS_HEADER + parts, v.encode_pcm(pcm))

    def test_wav_format_is_checked(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'stereo.wav'
            with wave.open(str(path), 'wb') as stream:
                stream.setnchannels(2)
                stream.setsampwidth(2)
                stream.setframerate(v.SAMPLE_RATE)
                stream.writeframes(bytes(400))
            with self.assertRaises(ValueError):
                v.read_wav(path)


class RequestTest(unittest.TestCase):
    def test_request_has_only_audio_fields(self):
        outer = p.fields(v.recognize_request('123', 4, b'audio', 99, True))
        self.assertEqual(list(outer), [1])
        body = p.fields(p.one(outer, 1))
        self.assertEqual(sorted(body), [2, 4, 5, 6, 7, 11])
        self.assertEqual((p.one(body, 2), p.one(body, 4), p.one(body, 5), p.one(body, 6), p.one(body, 7), p.one(body, 11)),
                         (b'123', b'audio', 5, 1, 4, 99))
        # 不含前后文 13/18、最近上屏 12、应用名称 16、来源/场景 15/17 和整理选项 14。
        for field in (12, 13, 14, 15, 16, 17, 18):
            self.assertNotIn(field, body)

    def test_response_parsing(self):
        parsed = v.parse_response(response(b'7', 3, '你|好', 1), '7')
        self.assertEqual((parsed['text'], parsed['end'], parsed['seq']), ('你好', True, 3))
        with self.assertRaises(ValueError):
            v.parse_response(response(b'other'), '7')
        self.assertEqual(v.parse_response(response(ret=-1), '7')['ret'], -1)


class FakeTransport:
    """按顺序返回预设响应，记录发出的 ImeVoiceRecognizeRequest。"""

    def __init__(self, replies, default=None):
        self.replies = list(replies)
        self.default = default
        self.sent = []
        self.closed = False

    def uin(self):
        return 1234567

    def send(self, body):
        self.sent.append(p.fields(p.one(p.fields(body), 1)))
        if self.replies:
            return self.replies.pop(0)
        return self.default(self.sent[-1]) if self.default else response()

    def close(self):
        self.closed = True


def summary(transport):
    return [(p.one(item, 7), p.one(item, 6), len(p.one(item, 4, b'')), p.one(item, 11)) for item in transport.sent]


class RecognitionTest(unittest.TestCase):
    def test_chunks_end_packet_and_polling(self):
        transport = FakeTransport([response(), response(text='你'), response(text='你'),
                                   response(text='你好', interval=1), response(text='你好。', end=1)])
        seen = []
        recognition = v.Recognition(transport, seen.append)
        recognition.feed(b'0123456789')
        recognition.finish()
        self.assertEqual(recognition.run(), '你好。')
        # 文件头 16 字节加 10 字节音频，一包发完；结束包为空且序号递增，之后以 seq 0 轮询到结束标志。
        self.assertEqual(summary(transport), [(1, 0, 26, 26), (2, 1, 0, 26), (0, 1, 0, 26), (0, 1, 0, 26), (0, 1, 0, 26)])
        self.assertTrue(p.one(transport.sent[0], 4).startswith(v.OPUS_HEADER))
        self.assertEqual(seen, ['你', '你好', '你好。'])
        voice_ids = {p.one(item, 2) for item in transport.sent}
        self.assertEqual(len(voice_ids), 1)
        self.assertTrue(next(iter(voice_ids)).startswith(b'234567'))

    def test_packet_size_follows_server(self):
        transport = FakeTransport([response(max_packet=4)] + [response()] * 3 + [response(text='好', end=1)])
        recognition = v.Recognition(transport)
        recognition.feed(b'x' * 3952)
        recognition.finish()
        recognition.run()
        self.assertEqual([item[2] for item in summary(transport)], [3960, 4, 4, 0, 0])   # 结束包之后再轮询一次

    def test_streams_while_recording(self):
        transport = FakeTransport([], default=lambda item: response(text='好', end=p.one(item, 6)))
        recognition = v.Recognition(transport)
        result = []
        worker = threading.Thread(target=lambda: result.append(recognition.run()))
        worker.start()
        for _ in range(3):
            recognition.feed(b'a' * 100)
            time.sleep(.15)
        sent_before_finish = len(transport.sent)
        recognition.finish()
        worker.join(2)
        self.assertEqual(result, ['好'])
        # 录音中已按间隔陆续发送，不等录音结束。
        self.assertGreaterEqual(sent_before_finish, 2)
        self.assertEqual(sum(item[2] for item in summary(transport)), 16 + 300)
        self.assertEqual(summary(transport)[-1][1], 1)

    def test_cancel_stops_without_result(self):
        transport = FakeTransport([], default=lambda item: response())
        recognition = v.Recognition(transport)
        result = []

        def run():
            try:
                recognition.run()
            except v.Cancelled:
                result.append('cancelled')
        worker = threading.Thread(target=run)
        worker.start()
        recognition.feed(b'a')
        time.sleep(.05)
        recognition.cancel()
        worker.join(2)
        self.assertEqual(result, ['cancelled'])

    def test_server_error_stops(self):
        recognition = v.Recognition(FakeTransport([response(ret=-5)]))
        recognition.feed(b'abc')
        recognition.finish()
        with self.assertRaises(RuntimeError):
            recognition.run()

    def test_level(self):
        self.assertEqual(v.level(bytes(640)), 0)
        self.assertEqual(v.level(struct.pack('<320h', *([30000, -30000] * 160))), 8)


class Lines:
    def __init__(self):
        self.lines = []

    def write(self, text):
        self.lines.extend(text.splitlines())

    def flush(self):
        pass


@unittest.skipUnless(ctypes.util.find_library('opus'), '需要 libopus')
class ServeTest(unittest.TestCase):
    def setUp(self):
        # 替身录音：0.2 秒静音后结束，相当于录满时长上限。
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        script = Path(directory.name) / 'recorder.py'
        script.write_text('import sys, time\nfor _ in range(10):\n'
                          '    sys.stdout.buffer.write(bytes(640)); sys.stdout.flush(); time.sleep(.02)\n')
        os.environ['WETYPE_VOICE_RECORDER'] = '%s %s' % (sys.executable, script)
        self.addCleanup(os.environ.pop, 'WETYPE_VOICE_RECORDER', None)

    def run_serve(self, commands, transport):
        read, write = os.pipe()
        output = Lines()
        stdin = os.fdopen(read)
        worker = threading.Thread(target=v.serve, kwargs={'stdin': stdin, 'stdout': output, 'transport': transport})
        worker.start()
        with os.fdopen(write, 'w') as pipe:
            for command, delay in commands:
                pipe.write(command + '\n')
                pipe.flush()
                time.sleep(delay)
        worker.join(5)
        self.assertFalse(worker.is_alive())
        return output.lines

    def test_recording_until_limit_then_final(self):
        transport = FakeTransport([], default=lambda item: response(text='测试', end=p.one(item, 6)))
        lines = self.run_serve([('START 7', 1.0), ('Q', 0)], transport)
        self.assertEqual(lines[:2], ['READY', 'LISTENING 7'])
        self.assertIn('RECOGNIZING 7', lines)
        self.assertIn('PARTIAL 7 ' + '测试'.encode().hex(), lines)
        self.assertEqual(lines[-1], 'FINAL 7 ' + '测试'.encode().hex())
        self.assertTrue(any(line.startswith('LEVEL 7 ') for line in lines))
        self.assertTrue(transport.closed)

    def test_cancel_emits_nothing_more(self):
        transport = FakeTransport([], default=lambda item: response(text='测试'))
        lines = self.run_serve([('START 8', .05), ('CANCEL 8', .4), ('Q', 0)], transport)
        self.assertNotIn('FINAL 8', ' '.join(lines))
        self.assertFalse(any(line.startswith('ERROR') for line in lines), lines)

    def test_error_code(self):
        transport = FakeTransport([], default=lambda item: (_ for _ in ()).throw(TimeoutError('timed out')))
        lines = self.run_serve([('START 9', .6), ('STOP 9', .3), ('Q', 0)], transport)
        self.assertIn('ERROR 9 timeout', lines)


if __name__ == '__main__':
    unittest.main()
