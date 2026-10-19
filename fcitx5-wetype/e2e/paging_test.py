#!/usr/bin/env python3
"""候选翻页 + 漏字(issue #5) + 跟随全局每页候选数(issue #4) 的隔离 e2e。
断言两条链路: ProcessKeyEvent 返回值(按键是否被吞掉) 与插件日志里的
'commit candidate index=N'(数字选词落到了第几个候选)。
"""
import os, re, sys, time
import dbus
from dbus.mainloop.glib import DBusGMainLoop
from gi.repository import GLib

PS = int(sys.argv[1])
LOG = os.environ["WETYPE_PAGETEST_LOG"]
KEY = {"a": 97, "i": 105, "h": 104, "n": 110, "o": 111, "1": 49, "2": 50, "3": 51, "0": 48,
       "kp1": 0xFFB1, "kp2": 0xFFB2,
       "eq": 61, "minus": 45, "esc": 0xFF1B, "down": 0xFF54,
       "up": 0xFF52, "right": 0xFF53, "left": 0xFF51, "tab": 0xFF09,
       "home": 0xFF50, "end": 0xFF57, "delete": 0xFFFF, "backspace": 0xFF08,
       "space": 32, "4": 52, "s": 115, "r": 114, "u": 117, "f": 102, "b": 98,
       "y": 121, "e": 101, "p": 112}

DBusGMainLoop(set_as_default=True)
bus = dbus.SessionBus()
CTL = dbus.Interface(bus.get_object("org.fcitx.Fcitx5", "/controller"),
                     "org.fcitx.Fcitx.Controller1")
IM = dbus.Interface(bus.get_object("org.fcitx.Fcitx5", "/org/freedesktop/portal/inputmethod"),
                    "org.fcitx.Fcitx.InputMethod1")
for _ in range(20):
    try:
        CTL.CurrentInputMethod()
        break
    except Exception:
        time.sleep(0.5)
else:
    print("FAIL: fcitx5 dbus 未就绪")
    sys.exit(1)

loop = GLib.MainLoop()
commits = []
ui_candidates = []


def record_ui(*args):
    ui_candidates[:] = [(str(c[0]), str(c[1])) for c in args[4]]


bus.add_signal_receiver(record_ui,
                        dbus_interface="org.fcitx.Fcitx.InputContext1",
                        signal_name="UpdateClientSideUI")
bus.add_signal_receiver(lambda *a, **k: commits.append(str(a[0]) if a else ""),
                        dbus_interface="org.fcitx.Fcitx.InputContext1",
                        signal_name="CommitString")
CTL.SetCurrentIM("wetype-im")
time.sleep(0.5)
path, _ = IM.CreateInputContext([("program", "wetype-pagetest"),
                                 ("capability", dbus.String(str(dbus.UInt64(1 | (1 << 39)))))])
ic = dbus.Interface(bus.get_object("org.fcitx.Fcitx5", path),
                    "org.fcitx.Fcitx.InputContext1")
# CreateInputContext's metadata does not set capabilities on all Fcitx5
# versions. Explicitly enable client-side candidate UI for signal assertions.
ic.SetCapability(dbus.UInt64(1 | (1 << 39)))
ic.FocusIn()
time.sleep(0.5)


def pump(seconds):
    end = time.time() + seconds
    while time.time() < end:
        loop.get_context().iteration(False)
        time.sleep(0.02)


def press(name, state=0):
    ok = ic.ProcessKeyEvent(dbus.UInt32(KEY[name]), dbus.UInt32(0), dbus.UInt32(state),
                            dbus.Boolean(False),
                            dbus.UInt32(int(time.time() * 1000) & 0xFFFFFFFF))
    pump(0.15)
    return bool(ok)


def indexes():
    with open(LOG, errors="ignore") as f:
        return [int(m) for m in re.findall(r"commit candidate index=(\d+)", f.read())]


def candidate_count(offset=0, buf_len=5):
    with open(LOG, errors="ignore") as f:
        f.seek(offset)
        pattern = r"parsed candidates=(\d+) buffer_len=%d preview=0" % buf_len
        m = re.findall(pattern, f.read())
    return int(m[-1]) if m else 0


def fresh():
    press("esc")
    commits.clear()
    offset = os.path.getsize(LOG)
    for ch in "nihao":
        press(ch)
    for _ in range(40):
        pump(0.25)
        if candidate_count(offset) >= 2 * PS + 1:
            break
    return candidate_count(offset)


def set_page_size(size):
    # The runner creates this isolated config; never write the desktop config.
    config = os.environ["WETYPE_PAGETEST_CONFIG"]
    with open(config, "w") as f:
        f.write("[Behavior]\nDefaultPageSize=%d\n" % size)
    CTL.ReloadConfig()
    pump(0.2)


results = []


def check(label, cond, detail=""):
    results.append((label, bool(cond), detail))
    print(("PASS " if cond else "FAIL ") + label + ("  " + detail if detail else ""), flush=True)


n = fresh()
check("engine returned enough candidates (>=2*%d+1)" % PS, n >= 2 * PS + 1, "count=%d" % n)

# Glosses decorate the candidate UI only, never its committed text.
with_glossary = os.environ.get("WETYPE_PAGETEST_GLOSSARY") == "1"
check("optional English gloss follows fixture presence",
      any(" hello" in text for _, text in ui_candidates) == with_glossary,
      "glossary=%s" % with_glossary)

# 1) 未翻页时按数字 1 -> 第一个候选
before = len(indexes())
press("1")
pump(0.8)
got = indexes()
check("page 1 digit-1 commits index 0", len(got) > before and got[-1] == 0,
      "index=%s" % (got[-1] if got else None))
check("digit-1 emits a nonempty CommitString", bool(commits) and bool(commits[-1]),
      "commits=%s" % commits)

if with_glossary:
    check("digit selection commits Chinese without gloss", commits == ["你好"],
          "commits=%s" % commits)
    fresh()
    before = len(indexes())
    ic.SelectCandidate(0)
    pump(0.8)
    got = indexes()
    check("candidate click commits Chinese without gloss",
          commits == ["你好"] and len(got) > before and got[-1] == 0,
          "commits=%s" % commits)

# 2) issue #5: 第一页按 '-' 无法后退, 但按键必须被吞掉且不产生上屏
fresh()
leak_before = len(commits)
consumed = press("minus")
pump(0.8)
check("issue#5 '-' on first page is consumed", consumed, "consumed=%s" % consumed)
check("issue#5 '-' leaks nothing into the document", len(commits) == leak_before,
      "commits=%s" % commits[leak_before:])

# 3) issue #4: 一次 '=' 前进 PS 个候选
fresh()
before = len(indexes())
press("eq")
press("1")
pump(0.8)
got = indexes()
check("one '=' then digit-1 commits index %d" % PS, len(got) > before and got[-1] == PS,
      "index=%s" % (got[-1] if got else None))

# 4) 两次 '=' 前进 2*PS
fresh()
before = len(indexes())
press("eq")
press("eq")
press("1")
pump(0.8)
got = indexes()
check("two '=' then digit-1 commits index %d" % (2 * PS),
      len(got) > before and got[-1] == 2 * PS, "index=%s" % (got[-1] if got else None))

# 5) 末尾页反复 '=' / '-' 不越界、不漏字, 回到首页后数字 1 仍是 index 0
n = fresh()
steps = (n + PS - 1) // PS + 2
tail = all(press("eq") for _ in range(steps))
check("last page shows the expected remaining candidates",
      len(ui_candidates) == n - ((n - 1) // PS) * PS)
check("paging forward leaks no CommitString", not commits)
before = len(indexes())
press("1")
pump(0.8)
got = indexes()
check("last page digit-1 selects the expected index",
      len(got) > before and got[-1] == ((n - 1) // PS) * PS)
fresh()
for _ in range(steps):
    press("eq")
head = all(press("minus") for _ in range(steps))
check("paging keys stay consumed at both boundaries", tail and head)
check("paging back leaks no CommitString", not commits)
before = len(indexes())
press("1")
pump(0.8)
got = indexes()
check("after paging back, digit-1 commits index 0",
      len(got) > before and got[-1] == 0, "index=%s" % (got[-1] if got else None))

# 6) 每页 10 个时, '0' 选第 10 个
if PS >= 10:
    fresh()
    before = len(indexes())
    press("0")
    pump(0.8)
    got = indexes()
    check("digit-0 commits the 10th candidate (index 9)",
          len(got) > before and got[-1] == 9, "index=%s" % (got[-1] if got else None))

# 7) Changing global configuration mid-composition must not produce negative
# indices (3 -> 10 -> Up), or leave the selected index outside the new page.
set_page_size(3)
fresh()
press("eq")  # windowStart = 3
set_page_size(10)
consumed = press("up")
before = len(indexes())
press("1")
pump(0.8)
got = indexes()
check("live page size 3->10 then Up stays consumed and selects index 0",
      consumed and len(got) > before and got[-1] == 0 and bool(commits))

set_page_size(10)
fresh()
press("eq")  # windowStart = 10
set_page_size(3)
press("up")  # align to 9, then previous page starts at 6
before = len(indexes())
press("1")
pump(0.8)
got = indexes()
check("live page size 10->3 normalizes page and selects index 6",
      len(got) > before and got[-1] == 6 and bool(commits))
# No navigation key between reload and selection: unchanged indices still
# require a UI refresh so displayed candidates match the digit-key limit.
set_page_size(10)
fresh()
check("page size 10 is rendered", len(ui_candidates) == 10)
set_page_size(3)
consumed = press("4")
check("live homepage 10->3 refreshes before direct digit handling",
      not consumed and len(ui_candidates) == 3 and not commits)

# 旧方向键选词模式继续验证原来的右键行为。
value, _ = CTL.GetConfig('fcitx://config/addon/wetype')
value['ArrowKeysMoveCursor'] = dbus.String('False', variant_level=1)
CTL.SetConfig('fcitx://config/addon/wetype', dbus.Dictionary(value, signature='sv', variant_level=1))
set_page_size(10)
fresh()
for _ in range(9):
    press("right")
set_page_size(3)
before = len(indexes())
press("space")
pump(0.8)
got = indexes()
check("live shrink clamps selected index before Space",
      len(got) > before and got[-1] == 2 and bool(commits))
set_page_size(PS)
value['ArrowKeysMoveCursor'] = dbus.String('True', variant_level=1)
CTL.SetConfig('fcitx://config/addon/wetype', dbus.Dictionary(value, signature='sv', variant_level=1))

# 自定义翻页键测试 (逗号 / 句号)
conf_dir = os.path.dirname(os.environ["WETYPE_PAGETEST_CONFIG"])
wetype_conf = os.path.join(conf_dir, "conf", "wetype.conf")
os.makedirs(os.path.join(conf_dir, "conf"), exist_ok=True)
with open(wetype_conf, "w") as f:
    f.write("[PrevPage]\n0=comma\n\n[NextPage]\n0=period\n")
CTL.ReloadAddonConfig("wetype")
pump(0.3)

KEY["comma"] = 44
KEY["period"] = 46

fresh()
consumed_period = press("period")
check("custom period翻页键组词时被吞掉", consumed_period)
check("custom period翻页不泄漏进文档", len(commits) == 0)
before = len(indexes())
press("1")
pump(0.8)
got = indexes()
check("按自定义句号翻页后选词为第二页候选", len(got) > before and got[-1] == PS, "index=%s" % (got[-1] if got else -1))

if os.path.exists(wetype_conf):
    os.remove(wetype_conf)
CTL.ReloadAddonConfig("wetype")
pump(0.2)

# 拼音行内光标移动与编辑测试 (shrufa -> 左移 4 次至 h 后 -> 插入 u -> shurufa -> 输入法)
press("esc")
commits.clear()
offset = os.path.getsize(LOG)
for ch in "shrufa":
    press(ch)
for _ in range(4):
    press("left")
press("u")
for _ in range(40):
    pump(0.2)
    if candidate_count(offset, 7) >= 1:
        break
press("space")
pump(0.8)
check("拼音光标左移插入修改 shrufa -> shurufa 并首选上屏",
      commits and commits[-1] == "输入法",
      "commit=%s" % (commits[-1] if commits else ""))

# 拼音行内 Delete 键删除测试 (nihao -> 左移 3 次至 i 后 -> Delete 删 h -> niao -> 鸟)
press("esc")
commits.clear()
offset = os.path.getsize(LOG)
for ch in "nihao":
    press(ch)
for _ in range(3):
    press("left")
press("delete")
for _ in range(40):
    pump(0.2)
    if candidate_count(offset, 4) >= 1:
        break
press("space")
pump(0.8)
check("拼音光标 Delete 键删除修改 nihao -> niao 并首选上屏",
      commits and commits[-1] in ("鸟", "尿"),
      "commit=%s" % (commits[-1] if commits else ""))

# Readline 快捷键测试 (Ctrl+A 跳至行首 + Delete 删 n -> ihao)
press("esc")
commits.clear()
offset = os.path.getsize(LOG)
for ch in "nihao":
    press(ch)
press("a", state=4)  # Ctrl+A
press("delete")       # 删掉 'n' -> 'ihao'
for _ in range(40):
    pump(0.2)
    if candidate_count(offset, 4) >= 1:
        break
press("space")
pump(0.8)
check("Readline Ctrl+A 跳至行首并删除首字母",
      bool(commits),
      "commit=%s" % (commits[-1] if commits else ""))

# Readline Alt+B 向左移动词语: 输入 shrufa，按 Alt+B 两次跳到 'h' 后面，插入 'u' 变成 shurufa 并首选上屏
press("esc")
commits.clear()
offset = os.path.getsize(LOG)
for ch in "shrufa":
    press(ch)
press("b", state=8)  # Alt+B 跳到 fa 前 (光标在 4: shru|fa)
press("b", state=8)  # Alt+B 跳到 ru 前 (光标在 2: sh|rufa)
press("u")           # 插入 'u' -> 'shurufa'
for _ in range(40):
    pump(0.2)
    if candidate_count(offset, 7) >= 1:
        break
press("space")
pump(0.8)
check("Readline Alt+B 向左移动词语并修改拼音 shrufa -> shurufa 并首选上屏",
      commits and commits[-1] == "输入法",
      "commit=%s" % (commits[-1] if commits else ""))

# Readline Alt+B 行内修改后按数字键选词: shrufa -> Alt+B 两次 -> 插入 u -> 按数字 2 选第 2 个候选词
press("esc")
commits.clear()
for ch in "shrufa":
    press(ch)
press("b", state=8)  # Alt+B 跳到 fa 前 (光标在 4: shru|fa)
press("b", state=8)  # Alt+B 跳到 ru 前 (光标在 2: sh|rufa)
press("u")           # 插入 'u' -> 'shurufa'
before = len(indexes())
press("2")           # 按数字 2 选第 2 候选，不应漏出数字 '2'
pump(0.8)
got = indexes()
check("Readline Alt+B 行内修改后按数字2选中第2候选且不泄漏数字",
      len(got) > before and got[-1] == 1 and commits and commits[-1] != "2",
      "index=%s commit=%s" % (got[-1] if got else -1, commits[-1] if commits else ""))

# 小键盘数字选词验证 (Keypad KP_2 选第 2 候选词)
fresh()
before = len(indexes())
press("kp2")
pump(0.8)
got = indexes()
check("小键盘KP_2选词成功命中index 1且不泄漏字符",
      len(got) > before and got[-1] == 1,
      "index=%s" % (got[-1] if got else -1))

# Readline Alt+F 向右移动词语: 从行首 (Ctrl+A) 跳过 'ni' (Alt+F)，删掉 'h' -> 'niao' (鸟)
press("esc")
commits.clear()
offset = os.path.getsize(LOG)
for ch in "nihao":
    press(ch)
press("a", state=4)  # Ctrl+A 跳至行首
press("f", state=8)  # Alt+F 跳过 'ni' (光标在 2: ni|hao)
press("delete")      # 删掉 'h' -> 'niao'
for _ in range(40):
    pump(0.2)
    if candidate_count(offset, 4) >= 1:
        break
press("space")
pump(0.8)
check("Readline Alt+F 向右移动词语并删除修改 nihao -> niao 并首选上屏",
      commits and commits[-1] in ("鸟", "尿"),
      "commit=%s" % (commits[-1] if commits else ""))

# 多行候选词测试 (MultiRowCandidates=True, CandidateRows=3, MultiRowShortcuts=True)
press("esc")
commits.clear()
with open(wetype_conf, "w") as f:
    f.write("MultiRowCandidates=True\nCandidateRows=3\nMultiRowShortcuts=True\n")
CTL.ReloadAddonConfig("wetype")
pump(0.3)

fresh()
check("多行候选模式显示指定行数", len(ui_candidates) == 3, "rows=%d" % len(ui_candidates))

# 默认第 0 行，按数字 1 选词为 index 0
before = len(indexes())
press("1")
pump(0.8)
got = indexes()
check("多行候选默认第0行数字1选词为index 0", len(got) > before and got[-1] == 0, "index=%s" % (got[-1] if got else -1))

# 按方向键 Down 切换到第 1 行（保持 3 行显示，不跳整页），按数字 1 选词为 index 10
fresh()
press("down")
check("多行候选方向键Down后保持指定行数", len(ui_candidates) == 3, "rows=%d" % len(ui_candidates))
before = len(indexes())
press("1")
pump(0.8)
got = indexes()
check("多行候选方向键Down移动到下一行并按1选词为index 10", len(got) > before and got[-1] == 10, "index=%s" % (got[-1] if got else -1))

# 按方向键 Down 再按 Up 返回第 0 行，按数字 1 选词为 index 0
fresh()
press("down")  # row 1
press("up")    # row 0
before = len(indexes())
press("1")
pump(0.8)
got = indexes()
check("多行候选方向键Up移动到上一行并按1选词为index 0", len(got) > before and got[-1] == 0, "index=%s" % (got[-1] if got else -1))

# 方向键 Down 连续按至末行后翻页到下一页第 0 行 (index 30)
fresh()
press("down")  # row 1
press("down")  # row 2 (当前页末行)
press("down")  # 翻至第 2 页第 0 行
before = len(indexes())
press("1")
pump(0.8)
got = indexes()
check("多行候选方向键Down在末行翻页到下一页首行选词为index 30", len(got) > before and got[-1] == 30, "index=%s" % (got[-1] if got else -1))

# 按 Ctrl+E 切换到第 1 行，按数字 1 选词为 index 10
fresh()
press("e", state=4)  # Ctrl+E
before = len(indexes())
press("1")
pump(0.8)
got = indexes()
check("多行候选Ctrl+E移动到下一行并按1选词为index 10", len(got) > before and got[-1] == 10, "index=%s" % (got[-1] if got else -1))

# 按 Ctrl+E 两次切换到第 2 行，按数字 1 选词为 index 20
fresh()
press("e", state=4)  # Ctrl+E -> row 1
press("e", state=4)  # Ctrl+E -> row 2
before = len(indexes())
press("1")
pump(0.8)
got = indexes()
check("多行候选Ctrl+E移动到第2行并按1选词为index 20", len(got) > before and got[-1] == 20, "index=%s" % (got[-1] if got else -1))

# 按 Ctrl+Y 从第 1 行返回第 0 行，按数字 1 选词为 index 0
fresh()
press("e", state=4)  # Ctrl+E -> row 1
press("y", state=4)  # Ctrl+Y -> row 0
before = len(indexes())
press("1")
pump(0.8)
got = indexes()
check("多行候选Ctrl+Y移动到上一行并按1选词为index 0", len(got) > before and got[-1] == 0, "index=%s" % (got[-1] if got else -1))

# Tab 键在多行模式下切换候选行并用空格上屏
fresh()
press("tab")  # row 1
before = len(indexes())
press("space")
pump(0.8)
got = indexes()
check("多行候选Tab切换激活行后Space上屏为index 10", len(got) > before and got[-1] == 10, "index=%s" % (got[-1] if got else -1))

# 自定义多行候选快捷键 (PrevRow=Control+p, NextRow=Control+n)
with open(wetype_conf, "w") as f:
    f.write("MultiRowCandidates=True\nCandidateRows=3\nMultiRowShortcuts=True\n\n[PrevRow]\n0=Control+p\n\n[NextRow]\n0=Control+n\n")
CTL.ReloadAddonConfig("wetype")
pump(0.3)

fresh()
press("n", state=4)  # Ctrl+N -> row 1
before = len(indexes())
press("1")
pump(0.8)
got = indexes()
check("多行候选自定义Ctrl+N切换至下一行选词为index 10", len(got) > before and got[-1] == 10, "index=%s" % (got[-1] if got else -1))

# 清理配置
if os.path.exists(wetype_conf):
    os.remove(wetype_conf)
CTL.ReloadAddonConfig("wetype")
pump(0.3)

failed = [r for r in results if not r[1]]
print("SUMMARY page_size=%d total=%d failed=%d" % (PS, len(results), len(failed)), flush=True)
print("E2E_RESULT =", "FAIL" if failed else "PASS", flush=True)
sys.exit(1 if failed else 0)
