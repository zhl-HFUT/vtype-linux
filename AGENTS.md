# AGENTS.md

写给帮用户安装、排错和开发 VType Linux 的编程 agent（Codex、Claude Code 等）。人类读者看 [README](README.md) 和 [docs/](docs/) 即可。

## 项目是什么

在 Fcitx5 里运行微信输入法 Android 3.5.4 的官方引擎：

- `fcitx5-wetype/`：Fcitx5 插件（C++），负责按键、候选窗、中英切换、标点、常用语、大小写混输。
- `harness/`：JNI 宿主（C），模拟 Android 环境，把 `libwxhld.so` 跑起来。x86_64 上通过 QEMU 运行 ARM64 代码。
- `cloud/`：Python 代理和工具，负责关联设备、云候选、词库同步、常用语刷新。代理只放行核对过的请求，见 [docs/工作原理.md](docs/工作原理.md)。
- `scripts/`：取 APK、打补丁、编译、打包 AppImage、测试。
- `tests/`、`fcitx5-wetype/tests/`：不联网、不需要账号的单元测试。

APK、引擎库和词库都不在仓库里，安装时从腾讯官方服务器下载并校验 SHA256。

## 安装流程

按顺序做，每一步检查结果再往下走。遇到下面这些情况先停下来问用户：需要 sudo、需要关联码、要改 Fcitx5 配置、要重启 Fcitx5（会打断正在输入的内容）。

### 1. 检查环境

```sh
uname -m; . /etc/os-release; echo "$PRETTY_NAME"; echo "$XDG_SESSION_TYPE $XDG_CURRENT_DESKTOP"
fcitx5 --version; pgrep -a fcitx5; fcitx5-remote
```

- 维护者日常用 x86_64 + Ubuntu 22.04 + KDE/X11；社区另反馈过 Ubuntu 26.04 GNOME/Wayland、Kubuntu 26.04、NixOS Niri/Wayland、Arch Linux Niri/Wayland 可用，见 README 的“环境”表。不在表里的组合也可以装，但要告诉用户这是没验证过的，装完请他反馈。
- 没装 Fcitx5，或者 Fcitx5 不是当前输入法框架时（`fcitx5-remote` 报错或 `pgrep` 没结果）：Ubuntu 上是 `sudo apt install fcitx5 fcitx5-config-qt`，再 `im-config -n fcitx5`，需要**重新登录**。这会替换用户现有的输入法框架（如 ibus），务必先征得同意。

### 2. 安装依赖、编译

Ubuntu 22.04 / 24.04：

```sh
sudo apt install build-essential cmake libfcitx5core-dev file qemu-user-static e2fsprogs unzip \
  python3 python3-cryptography libsnappy1v5 openssl curl
git clone https://github.com/zhl-HFUT/vtype-linux.git && cd vtype-linux
bash scripts/prepare_ndk.sh
export ANDROID_NDK_HOME="$PWD/.deps/tools/android-ndk-r27c"
bash scripts/e2_img.sh
```

其他发行版按名字找对应的包。第一次编译会下载 Android NDK 和 AOSP 运行时（几百 MB 以上），要花些时间，之后复用缓存。成功后当前目录会有 `WeTypeIME-Engine-x86_64.AppImage`。

### 3. 安装

```sh
./WeTypeIME-Engine-x86_64.AppImage install --user
```

会下载官方 APK（约 214 MB）并校验。没有 FUSE 时加环境变量 `APPIMAGE_EXTRACT_AND_RUN=1`。

### 4. 在 Fcitx5 里启用“微信拼音”

推荐让用户自己在 Fcitx5 设置（`fcitx5-configtool`）里添加“微信拼音”。用户希望你代劳时：

1. `fcitx5-remote -e` 退出 Fcitx5（编辑期间 Fcitx5 不能在运行，否则会覆盖配置）。
2. 备份 `~/.config/fcitx5/profile`。
3. 在默认分组里追加一项，`N` 取现有最大序号加 1：

   ```ini
   [Groups/0/Items/N]
   Name=wetype-im
   Layout=
   ```

4. `fcitx5 -d` 启动，`fcitx5-remote -s wetype-im` 切换，`fcitx5-remote -n` 应输出 `wetype-im`。

### 5. 自检

```sh
python3 scripts/test_bionic_daemon.py --engine-dir ~/.local/lib/wetype-ime/arm64 --idle-seconds 85
```

这会启动已安装的引擎，测试输入、选词、学习和重启，使用临时词库，不碰用户的个人词库。通过后请用户在任意文本框里试打 `nihao`。

到这里本地输入就能用了，不联网。

### 6. 关联手机或 Mac（可选，需要用户参与）

云候选、词库同步、常用语都需要这一步。先确认用户想要，并告诉他：关联后会把正在输入的拼音发给官方云端，详见 README 的“会发送什么”。

```sh
npm install --prefix "$HOME/.local/share/wetype-ime/tools" @mkdir700/wxc@0.1.0   # 需要 Node.js 22+
```

然后请用户在手机或 Mac 的微信输入法里打开“关联设备”，拿到六位关联码告诉你（码很快过期，拿到就用）：

```sh
python3 ~/.local/lib/wetype-ime/arm64/wetype-cloud.py pair --name "Linux 输入法" --code 六位数字
```

用户需要在官方客户端上点确认。成功后：

```sh
python3 ~/.local/lib/wetype-ime/arm64/wetype-cloud.py enable --timer
python3 ~/.local/lib/wetype-ime/arm64/wetype-cloud.py status
```

重启 Fcitx5 后生效。`status` 里 `paired`、`cloud_enabled` 都应为 `true`。

### 7. 剪贴板互通（可选，需要用户同意）

启用后，用户之后复制的所有纯文本都会发给官方服务器，务必先说明并征得同意。需要 `xclip`（X11）或 `wl-clipboard`（Wayland），在图形桌面的终端执行 `wetype-cloud.py clipboard enable`，用 `clipboard status` 检查。图片同步要再加 `--images`，之后复制的截图等图片都会上传，须另行征得同意；X11 / XWayland 上还需要 `python3-gi gir1.2-gtk-3.0`。详见 [docs/剪贴板互通.md](docs/剪贴板互通.md)。

### 更新已有安装

已装过的用户更新到新版本时按这个顺序做，个人词库、关联身份和设置都会保留（Nix 安装按 [docs/NixOS.md](docs/NixOS.md) 更新）：

```sh
cd vtype-linux && git pull
export ANDROID_NDK_HOME="$PWD/.deps/tools/android-ndk-r27c"
bash scripts/e2_img.sh
```

1. 编译成功后，告诉用户接下来要退出 Fcitx5（会打断正在输入的内容），征得同意再执行 `fcitx5-remote -e`。
2. `./WeTypeIME-Engine-x86_64.AppImage install --user`，再 `fcitx5 -d` 启动。
3. 启用了剪贴板互通的，执行 `systemctl --user restart wetype-clipboard.service`。
4. 跑第 5 步的自检，请用户试打。

新版本新增的可选功能（如剪贴板图片）不会自动开启，先向用户说明，征得同意再启用。

## 必须遵守

- `~/.local/state/wetype-ime/account` 是用户的设备身份，相当于登录凭据。不要打印其中内容，不要复制到仓库、issue、网盘或其他机器。
- `~/.local/share/wetype-ime`（个人词库、常用语缓存）和 `~/.local/state/wetype-ime/harness.log`（可能含输入内容）同样不外传。需要日志排错时，只摘取报错行，并先给用户看。
- 不要修改 `cloud/实时云桥接.py` 的放行规则来“修好”某个请求。新增放行要单独讨论，见 [docs/工作原理.md](docs/工作原理.md)。
- 替换个人词库之前必须先退出 Fcitx5 并备份。

## 常见问题

| 现象 | 先查 |
|---|---|
| 候选窗不出现 | `fcitx5-remote -n` 是否为 `wetype-im`；`~/.local/state/wetype-ime/harness.log` 末尾是否有崩溃；重新跑自检 |
| 引擎启动即退出，日志里有 `Resource may not ready` | 词库清单和资源不配套，见 [docs/更新公共词库.md](docs/更新公共词库.md) |
| 剪贴板不同步 | `wetype-cloud.py clipboard status` 的 `running` 和 `error`；`journalctl --user -u wetype-clipboard.service` 的逐条收发记录（跳过原因、耗时）；官方客户端是否开启跨设备复制粘贴 |
| 云候选没反应 | `wetype-cloud.py status`；网络要能直连官方服务器，代理只认系统级 TUN，不读 `http_proxy` |
| 某个应用里打不了字 | 查该应用用的输入法模块（GTK/Qt 的 `*_IM_MODULE`、XIM、Wayland text-input），换个应用对比 |

## 报告问题

帮用户开 issue 时收集这些（不含任何个人数据）：

```sh
uname -m; . /etc/os-release; echo "$PRETTY_NAME"; echo "$XDG_SESSION_TYPE $XDG_CURRENT_DESKTOP"
fcitx5 --version; git -C vtype-linux rev-parse --short HEAD
python3 ~/.local/lib/wetype-ime/arm64/wetype-cloud.py status
```

再加上复现步骤、期望行为、实际行为，以及 `harness.log` 里相关的报错行（先让用户确认里面没有个人输入内容）。

## 开发

### 构建和测试

和 CI（`.github/workflows/check.yml`）一致，提 PR 前都要过：

```sh
cmake -S fcitx5-wetype -B build && cmake --build build && ctest --test-dir build --output-on-failure
python3 -m unittest discover -s tests -p 'test_*.py'
python3 tests/test_snapshot_transport.py
python3 scripts/check-public-source.py
python3 scripts/test_clipboard_desktop.py --backend x11   # 需要 Xvfb、xclip、python3-gi 和 GTK 3
```

插件改动还要在真实环境验证：重新编译后把 `build/libfcitx5-wetype.so` 装到 `~/.local/lib/fcitx5/`（先备份），重启 Fcitx5 试打。`fcitx5-wetype/e2e/` 下有基于 Xvfb 的界面测试，可以在不影响用户桌面的情况下验证按键行为。

### 约定

- 文档、注释、提交信息用中文。文档写给用户看：直接说能做什么、怎么做，不写内部过程和免责声明。
- 改动对应的文档一起更新，尤其是 README 的功能表和 `docs/安装与使用.md` 的按键表。
- 合并上游 [yu1745/wetype-ime-linux](https://github.com/yu1745/wetype-ime-linux) 时，注意 `harness/jinterop.c` 里 SessionConfig 的默认值不要覆盖本项目设置的云配置和设备组信息。
- `harness/dict_table.h` 必须和 APK 里的词库清单（`index.json`）一致，否则全新安装会启动失败。
- 修改历史词库恢复时，分别验证空白词库和已有词库副本的实际记录、候选与重启；下载成功或原生合并日志不能单独证明完整恢复。恢复入口见 [恢复历史词库](docs/恢复历史词库.md)，不直接替换正在使用的词库。

### 升级官方版本

官方 APK 更新时，下面这些地方写死了 3.5.4，需要一起核对：

- `scripts/fetch_apk.py`、`scripts/prepare_assets.sh`：版本号、下载地址、SHA256。
- `scripts/10_patch_libs.sh`、`scripts/1*_*.py`、`scripts/30_patch_leak.py`：针对具体二进制的补丁，偏移量多半要重新找。
- `harness/dict_table.h`：从新 APK 的 `index.json` 重新生成。
- `harness/jinterop.c`：SessionConfig 默认值（取自 APK 的 `SessionConfig` 类）、`appVer`。
- `cloud/官方同步探针.py`：`VERSION`；`cloud/resource_versions.json`。
- `scripts/pull-snapshot-copy.py`：只接受 3.5.4，确认新版本快照格式不变后再放开。

升级后至少跑完上面的测试和安装自检，并在真实账号上确认云候选和词库同步正常。
