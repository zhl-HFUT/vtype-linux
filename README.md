# VType Linux

[![源码检查](https://github.com/zhl-HFUT/vtype-linux/actions/workflows/check.yml/badge.svg)](https://github.com/zhl-HFUT/vtype-linux/actions/workflows/check.yml)
[![License: GPL-3.0](https://img.shields.io/badge/license-GPL--3.0-blue.svg)](LICENSE)

在 Linux 上用**微信输入法**。Fcitx5 里跑的是官方 Android 引擎，候选和手机上同源；关联你的手机或 Mac 后，个人词库双向同步，云候选、常用语也都能用。

![功能一览](docs/截图/功能一览.png)

## 用 agent 安装（推荐）

把下面这段话发给你的 Codex、Claude Code 或其他编程 agent：

```text
帮我在这台 Linux 上安装 VType Linux：https://github.com/zhl-HFUT/vtype-linux
克隆仓库后按仓库根目录的 AGENTS.md 操作。需要 sudo、需要我输入关联码、或要改 Fcitx5 配置的时候，先停下来问我。
```

agent 会检查系统环境、编译、安装、启用输入法并跑一遍自检。关联手机时它会提示你在手机上取六位关联码。

想自己动手，看 [安装与使用](docs/安装与使用.md)。Nix/NixOS 用户见 [Nix 安装](docs/NixOS.md)。

## 能做什么

| | 说明 |
|---|---|
| **官方引擎** | 整句、乱序/键邻纠错、emoji 和颜文字，和安卓版同一个引擎 |
| **小鹤双拼** | 在 Fcitx5 配置中切换全拼/小鹤，`nihc` → 你好，`vsgo` → 中国 |
| **拼音显示位置** | 可在 Fcitx5 配置中选择把拼音显示在应用输入框里，候选窗停在本轮拼音起始位置；默认拼音显示在候选窗 |

| **模糊音** | 支持全部 19 组官方模糊音（平翘舌 z/c/s、鼻边音 n/l、前后鼻音等），可在 Fcitx5 配置中独立勾选 |
| **云候选与热词** | 网络热词、新梗由官方云端补充（如 `zheng yi zs` → 正义之手），断网自动退回本地 |
| **二级词库** | 下载官方更新的公共词库，补上 APK 发布之后出现的新词 |
| **个人词库互通** | 和手机、Mac 同步新学的词；支持手动恢复历史快照并补齐缺失拼音词条 |
| **常用语** | 同步官方常用语：输入编码或前三字首字母出候选；号码类打开头三位、回车补全 |
| **跨设备剪贴板** | 单独启用后，和关联的手机/Mac 自动双向同步纯文本（发送最多 256 KiB，接收最多 1 MiB）；GNOME Wayland 自动使用 XWayland 后端，避免临时窗口闪烁；见[剪贴板互通](docs/剪贴板互通.md) |
| **大小写混输** | 带大写的输入按英文处理，`OpenC` → OpenClaw，词库里的英文词保留你打的大小写 |
| **按键习惯** | 单独按 Shift 切中英，中文标点自动配对，数字里的小数点保持英文；可自定义翻页键（默认 -/=、方向键、PgUp/PgDn）；可配置“始终使用英文标点”，中文输入时也用英文符号 |

本项目基于 [yu1745/wetype-ime-linux](https://github.com/yu1745/wetype-ime-linux)：上游把引擎跑了起来（本地输入、纠错、emoji），这里补上了云候选、二级词库、个人词库同步、常用语、大小写混输和 Shift 中英切换。

不支持：语音、手写、图片常用语、在 Linux 上编辑常用语、图片剪贴板和文件互传。

## 环境

| | 状态 |
|---|---|
| x86_64 + Ubuntu 22.04 + KDE / X11 | 日常使用中 |
| x86_64 + Ubuntu 26.04 + GNOME / Wayland | 社区反馈全部功能可用，剪贴板走 XWayland（[#11](https://github.com/zhl-HFUT/vtype-linux/issues/11)） |
| Kubuntu 26.04 | 社区反馈安装成功、使用正常（[#9](https://github.com/zhl-HFUT/vtype-linux/issues/9)） |
| x86_64 + NixOS + Niri / Wayland | 社区反馈用 Nix 包安装，本地输入和云候选可用（[#4](https://github.com/zhl-HFUT/vtype-linux/pull/4)、[#5](https://github.com/zhl-HFUT/vtype-linux/issues/5)） |
| Ubuntu 24.04、其他发行版 | CI 能编译，还没有实机反馈 |
| ARM64 | 上游支持原生运行，本项目的云功能没验证 |

在其他环境装成功或失败了，都欢迎[开个 issue](https://github.com/zhl-HFUT/vtype-linux/issues/new/choose) 说一声，这是现在最缺的信息。

## 会发送什么

| 功能 | 发给官方服务器的内容 |
|---|---|
| 云候选 | 正在输入的拼音 |
| 词库同步 | 新学到的词条，以及拉取其他设备的变化 |
| 常用语 | 只读取 |
| 剪贴板同步（默认关闭） | 启用后新复制的纯文本，以及拉取同组设备的最新剪贴板 |

Linux不发送上屏报告或整库快照文件。手动请求快照重建时，官方来源设备可能把已有个人词库重新备份到腾讯服务器，供当前关联组同步。剪贴板默认关闭，启用云候选和词库同步也不会打开它；需要单独执行 `clipboard enable`。引擎内置的上屏报告会被本地代理拦下。只用本地输入、不关联设备时，不会联网。

设备身份保存在 `~/.local/state/wetype-ime/account`，相当于你的登录凭据，不要上传或在多台机器间共用。

## 已知限制

- 候选排序不保证和手机完全一致。
- 常见英文词（Linux、Python）引擎自带大小写；你自己打出来、被学进词库的英文词存成小写，要大写时在输入里带上大写字母。
- 公共词库不会自动更新，需要按 [更新公共词库](docs/更新公共词库.md) 手动更新。
- 设备长期离线、错过服务器保留的增量后，从快照自动恢复的流程还没有完整验证。

## 一起改进

这个项目适合用 agent 来维护：仓库根目录的 [AGENTS.md](AGENTS.md) 写明了结构、构建、测试和规矩，Codex、Claude Code 打开仓库就能读到。

- **报问题**：让你的 agent 按 AGENTS.md 里的“报告问题”一节收集环境信息，再[开 issue](https://github.com/zhl-HFUT/vtype-linux/issues/new/choose)。
- **提改进**：体验上的不顺手（按键习惯、候选显示、某个应用里不好用）都可以提，附上你期望的行为。
- **提 PR**：见 [参与贡献](CONTRIBUTING.md)。改动请附上你是怎么验证的。
- **跟进新版**：官方 APK 更新后，适配新版本是最需要人手的事，步骤见 AGENTS.md 的“升级官方版本”。

## 文档

- [安装与使用](docs/安装与使用.md)：手动安装、关联设备、按键、停用和卸载
- [工作原理](docs/工作原理.md)：组件结构、放行的请求、数据存放位置
- [剪贴板互通](docs/剪贴板互通.md)：和手机、Mac 双向同步复制的文字
- [更新公共词库](docs/更新公共词库.md)：下载二级词库等新版资源
- [恢复历史词库](docs/恢复历史词库.md)：检查失效快照、请求来源重建、隔离导入与拼音补齐
- [上游资料](docs/上游/)：上游原始 README（含 ARM64、AppImage 打包）、引擎泄漏分析、界面测试和性能对比

## 致谢

- [yu1745/wetype-ime-linux](https://github.com/yu1745/wetype-ime-linux)：把引擎在 Linux 上跑起来的上游项目。
- [mkdir700/wx-ime-sdk](https://github.com/mkdir700/wx-ime-sdk)：设备协议参考。
- [LINUX DO](https://linux.do)：项目在这里首发，欢迎来社区交流。

## 许可

GPL-3.0，与上游一致。设备协议参考了 [mkdir700/wx-ime-sdk](https://github.com/mkdir700/wx-ime-sdk)（Apache-2.0）。本项目是非官方实现，与腾讯无关；微信输入法的引擎和词库归腾讯所有，不随本项目分发，安装时从官方服务器下载。
