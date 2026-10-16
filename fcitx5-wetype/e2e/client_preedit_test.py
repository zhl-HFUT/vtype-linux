"""私有 D-Bus 会话验证预编辑位置、配置切换和应用能力回退。"""
import dbus
from dbus_test_client import Client, controller, pump

URI = 'fcitx://config/addon/wetype'


def configure(enabled):
    value, _ = controller.GetConfig(URI)
    value['ShowPinyinInInput'] = dbus.String('True' if enabled else 'False', variant_level=1)
    controller.SetConfig(URI, dbus.Dictionary(value, signature='sv', variant_level=1))
    pump()


def preedit(client):
    return ''.join(str(part[0]) for part in client.preedits[-1][0]) if client.preedits else ''


c = Client('client-preedit', capabilities=1 | (1 << 1) | (1 << 4) | (1 << 39))
c.focus()
value, _ = controller.GetConfig(URI)
assert str(value['ShowPinyinInInput']) == 'False'
c.type('nihao')
assert preedit(c) == '', c.preedits
configure(True)
assert preedit(c) == 'ni hao', c.preedits
assert int(c.preedits[-1][1]) == 0, c.preedits[-1]
assert not c.commits
c.press('space')
assert c.commits == ['你好'], c.commits
assert preedit(c) == '', c.preedits
c.commits.clear()
c.type('zhongguo')
assert preedit(c) == 'zhong guo'
assert int(c.preedits[-1][1]) == 0
c.press('esc')
assert preedit(c) == '' and not c.commits
c.type('nihao')
configure(False)
assert preedit(c) == '' and not c.commits
configure(True)
assert preedit(c) == 'ni hao'
c.press('enter')
assert c.commits == ['nihao'] and preedit(c) == '', c.commits
c.type('nihao')
c.press('backspace')
assert preedit(c) == 'ni ha', c.preedits
assert int(c.preedits[-1][1]) == 0
c.press('esc')
controller.ReloadAddonConfig('wetype')
pump()
value, _ = controller.GetConfig(URI)
assert str(value['ShowPinyinInInput']) == 'True'
fallback = Client('without-preedit', capabilities=1 | (1 << 39))
fallback.focus()
fallback.type('nihao')
assert preedit(fallback) == '' and fallback.candidates
fallback.press('space')
assert fallback.commits == ['你好']
print('PASS 预编辑位置、起点锚定、选词/回车/Esc 清空、退格、配置保存、无预编辑能力回退')
