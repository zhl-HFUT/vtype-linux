#!/usr/bin/env python3
"""语音辅助进程替身，只供隔离 e2e 使用：不打开麦克风、不联网。

行为由 $WETYPE_VOICE_FIXTURE_DIR/mode 控制，收到的命令逐行记入同目录的 trace。
"""
import os
import sys
import threading

root = os.environ["WETYPE_VOICE_FIXTURE_DIR"]
lock = threading.Lock()


def mode():
    try:
        with open(os.path.join(root, "mode")) as stream:
            return stream.read().strip()
    except FileNotFoundError:
        return "normal"


def emit(*parts):
    with lock:
        print(" ".join(parts), flush=True)


def hexed(text):
    return text.encode().hex()


emit("READY")
active = {}
for line in sys.stdin:
    command, _, session = line.strip().partition(" ")
    with open(os.path.join(root, "trace"), "a") as stream:
        stream.write(line.strip() + "\n")
    if command == "Q":
        break
    if command == "START":
        current = mode()
        emit("LISTENING", session)
        if current == "error":
            emit("ERROR", session, "network")
            continue
        emit("LEVEL", session, "6")
        emit("PARTIAL", session, hexed("你好"))
        if current == "limit":
            # 录满时长上限：键还按着，辅助进程已自行结束录音并给出结果。
            def finish(s=session):
                emit("RECOGNIZING", s)
                emit("FINAL", s, hexed("录满了"))
            threading.Timer(0.3, finish).start()
            continue
        active[session] = current
    elif command == "STOP" and session in active:
        current = active.pop(session)
        emit("RECOGNIZING", session)
        if current == "slow":
            # 被取消后迟到的结果必须被插件忽略。
            threading.Timer(1.0, lambda s=session: emit("FINAL", s, hexed("迟到的结果"))).start()
        elif current == "empty":
            emit("FINAL", session, "")
        else:
            emit("PARTIAL", session, hexed("你好世界"))
            emit("FINAL", session, hexed("你好世界。"))
    elif command == "CANCEL":
        active.pop(session, None)
