#include "voice_input.h"

#include <cstdlib>
#include <iostream>

namespace {

int failures = 0;

void check(bool condition, const char *what) {
    if (!condition) {
        std::cerr << "FAIL: " << what << "\n";
        ++failures;
    }
}

}  // namespace

int main() {
    using namespace wetype;
    auto partial = parseVoiceLine("PARTIAL 12 e4bda0e5a5bd");
    check(partial && partial->kind == "PARTIAL" && partial->id == "12" && partial->payload == "e4bda0e5a5bd",
          "PARTIAL 拆出类型、会话和内容");
    check(partial && hexDecode(partial->payload) == std::optional<std::string>("你好"), "十六进制还原 UTF-8");
    auto empty = parseVoiceLine("PARTIAL 3 ");
    check(empty && empty->payload.empty() && hexDecode(empty->payload) == std::optional<std::string>(""),
          "空文字的 PARTIAL");
    auto ready = parseVoiceLine("READY");
    check(ready && ready->kind == "READY" && ready->id.empty(), "READY 没有会话号");
    auto error = parseVoiceLine("ERROR 7 network");
    check(error && error->payload == "network", "ERROR 带错误码");
    check(!parseVoiceLine("LEVEL  4"), "会话号不能为空");
    check(!parseVoiceLine("LEVEL " + std::string(33, '1') + " 4"), "会话号过长");
    check(!parseVoiceLine(""), "空行");
    check(!hexDecode("abc"), "奇数长度");
    check(!hexDecode("zz"), "非十六进制字符");
    check(!hexDecode("00"), "不接受 NUL");
    check(voiceMeter({}) == "▁▁▁▁▁▁▁▁", "无音量时全为最低");
    check(voiceMeter({8, 0, 4}, 4) == "▁█▁▄", "最近的音量靠右");
    check(voiceMeter({12, -3}, 2) == "█▁", "越界音量被截断");
    check(std::string(voiceErrorText("not_paired")).find("关联") != std::string::npos, "未关联提示");
    check(std::string(voiceErrorText("unknown")) == "识别失败，请重试", "未知错误码");
    if (failures) return EXIT_FAILURE;
    std::cout << "voice protocol ok\n";
    return EXIT_SUCCESS;
}
