"""按住说话：按下开始、识别中显示预编辑、松开上屏；误触、组合键、Esc、错误和自动重复。

用替身辅助进程（voice_helper_fixture.py），不录音、不联网。由 run_voice_e2e.sh 调用。
"""
import os
from pathlib import Path

import dbus
from dbus_test_client import Client, bus, controller, pump

ROOT = Path(os.environ["WETYPE_VOICE_FIXTURE_DIR"])
CONTROL_R = 0xFFE4
CTRL = 1 << 2
controller.SetConfig("fcitx://config/addon/wetype", dbus.Dictionary(
    {"VoiceInputKey": dbus.Dictionary({"0": "Control_R"}, signature="sv")}, signature="sv"))
pump(0.5)


def set_mode(mode):
    (ROOT / "mode").write_text(mode)


def trace():
    path = ROOT / "trace"
    return path.read_text().split("\n")[:-1] if path.exists() else []


class Panel:
    """记录客户端界面：辅助文字和（不支持预编辑时）候选窗里的预编辑。"""

    def __init__(self, client):
        self.aux = ""
        self.preedit = ""
        bus.add_signal_receiver(self._ui, dbus_interface="org.fcitx.Fcitx.InputContext1",
                                signal_name="UpdateClientSideUI", path=client.path)

    def _ui(self, preedit, cursor, aux_up, *rest):
        self.preedit = "".join(str(part[0]) for part in preedit)
        self.aux = "".join(str(part[0]) for part in aux_up)


def hold(client, seconds=0.5):
    assert client.press(CONTROL_R, delay=seconds), "按下语音键应被输入法接管"
    return client.press(CONTROL_R, delay=0.3, states=CTRL, release=True)


c = Client("voice")
panel = Panel(c)
c.focus()

# 1. 按住：显示“正在听”和识别中的文字；松开：上屏最终结果，界面清空。
set_mode("normal")
assert c.press(CONTROL_R, delay=0.4)
assert "正在听" in panel.aux and "松开结束" in panel.aux, panel.aux
assert panel.preedit == "你好", panel.preedit
assert not c.commits
assert c.press(CONTROL_R, delay=0.4, states=CTRL, release=True), "松开语音键应被输入法接管"
assert c.commits == ["你好世界。"], c.commits
assert panel.aux == "" and panel.preedit == "", (panel.aux, panel.preedit)
assert trace()[-2:] == ["START 1", "STOP 1"], trace()
print("PASS 按住说话、识别中显示、松开上屏")

# 2. 轻点不到 0.3 秒：不识别、不上屏。
c.commits.clear()
assert c.press(CONTROL_R, delay=0.05)
assert c.press(CONTROL_R, delay=0.4, states=CTRL, release=True)
assert not c.commits and trace()[-1] == "CANCEL 2", (c.commits, trace())
assert panel.aux == "", panel.aux
print("PASS 轻点视为误触")

# 3. 按住时按了别的键（Ctrl+C）：放弃语音，按键交给应用。
assert c.press(CONTROL_R, delay=0.4)
consumed = c.press("c", delay=0.2, states=CTRL)
assert not consumed, "组合键不应被吞掉"
assert trace()[-1] == "CANCEL 3", trace()
c.press(CONTROL_R, delay=0.3, states=CTRL, release=True)
assert not c.commits and panel.aux == "", (c.commits, panel.aux)
print("PASS 组合键放弃语音")

# 4. 松开后识别较慢时 Esc 取消，迟到的结果被忽略。
set_mode("slow")
hold(c)
assert "识别中" in panel.aux, panel.aux
assert c.press("esc", delay=1.5)
assert trace()[-1] == "CANCEL 4", trace()
assert not c.commits and panel.aux == "", (c.commits, panel.aux)
print("PASS Esc 取消并忽略迟到结果")

# 5. 松开后等结果时直接打字：先上屏已识别的文字，按键照常处理。
hold(c)
c.press("n", delay=0.3)
assert c.commits == ["你好"], c.commits
assert trace()[-1] == "CANCEL 5", trace()
c.press("esc", delay=0.2)
c.commits.clear()
pump(1.2)
assert not c.commits, c.commits
print("PASS 等待结果时打字先上屏已识别部分")

# 6. 网络错误：显示提示，不上屏。
set_mode("error")
assert c.press(CONTROL_R, delay=0.3)
assert "网络连接失败" in panel.aux, panel.aux
c.press(CONTROL_R, delay=0.2, states=CTRL, release=True)
assert not c.commits
pump(2.6)
assert panel.aux == "", panel.aux
print("PASS 错误提示后自动消失")

# 7. 没识别出文字：提示“没有听清”。
set_mode("empty")
hold(c)
assert "没有听清" in panel.aux and not c.commits, (panel.aux, c.commits)
pump(2.6)
print("PASS 无结果提示")

# 8. X11 自动重复插入的松开/按下对不打断录音。
set_mode("normal")
before = len(trace())
assert c.press(CONTROL_R, delay=0.2)
c.press(CONTROL_R, delay=0, states=CTRL, release=True)
c.press(CONTROL_R, delay=0.3, states=CTRL)
assert "STOP" not in " ".join(trace()[before:]), trace()[before:]
assert c.press(CONTROL_R, delay=0.4, states=CTRL, release=True)
assert c.commits == ["你好世界。"], c.commits
print("PASS 自动重复不打断录音")

# 9. 正在拼写时语音键不接管。
c.commits.clear()
c.type("ni")
before = len(trace())
c.press(CONTROL_R, delay=0.3)
c.press(CONTROL_R, delay=0.2, states=CTRL, release=True)
assert len(trace()) == before, trace()[before:]
c.press("esc", delay=0.2)
print("PASS 拼写中不启动语音")

# 10. 支持预编辑的程序：识别中的文字直接显示在输入框里，带下划线。
e = Client("voice-inline", capabilities=1 | (1 << 1) | (1 << 4) | (1 << 39))
e.focus()
assert e.press(CONTROL_R, delay=0.4)
assert e.preedits, "应收到行内预编辑"
segments = e.preedits[-1][0]
assert "".join(str(s[0]) for s in segments) == "你好", segments
assert int(segments[0][1]) & (1 << 3), "预编辑应带下划线"
assert e.press(CONTROL_R, delay=0.4, states=CTRL, release=True)
assert e.commits == ["你好世界。"], e.commits
print("PASS 行内预编辑")

# 11. 失焦时放弃本次语音。
assert e.press(CONTROL_R, delay=0.3)
e.ic.FocusOut()
pump(0.3)
assert trace()[-1].startswith("CANCEL"), trace()
print("PASS 失焦取消")

# 12. 录满时长上限：键还按着时结果先上屏，之后的松开仍被吞掉，下一次按住照常。
c.focus()
c.commits.clear()
set_mode("limit")
assert c.press(CONTROL_R, delay=0.6)
assert c.commits == ["录满了"], c.commits
assert c.press(CONTROL_R, delay=0.3, states=CTRL, release=True), "录满后的松开也应被吞掉"
set_mode("normal")
hold(c)
assert c.commits == ["录满了", "你好世界。"], c.commits
print("PASS 录满时长上限")
