#!/bin/bash
# 语音输入隔离 e2e：私有 dbus + 临时 XDG 目录，用替身辅助进程，不录音、不联网。
# 用法: bash fcitx5-wetype/e2e/run_voice_e2e.sh（先构建插件，见 run_paging_e2e.sh）
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
FIXTURE="$(mktemp -d /tmp/wetype-voice-e2e.XXXXXX)"
trap 'rm -rf "$FIXTURE"' EXIT
export WETYPE_VOICE_FIXTURE_DIR="$FIXTURE"
export WETYPE_VOICE_HELPER="$HERE/voice_helper_fixture.py"
WETYPE_E2E_SCRIPT="$HERE/voice_test.py" bash "$HERE/run_paging_e2e.sh"
