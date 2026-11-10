#pragma once
// 语音输入辅助进程的行协议（见 cloud/语音识别.py 的 serve）。纯逻辑，便于单元测试。
//
// 插件 → 辅助进程：START <id> / STOP <id> / CANCEL <id> / Q
// 辅助进程 → 插件：READY、LISTENING <id>、LEVEL <id> <0-8>、PARTIAL <id> <hex>、
//                  RECOGNIZING <id>、FINAL <id> <hex>、ERROR <id> <code>
// 文字是 UTF-8 的十六进制，空文字时 <hex> 为空串。

#include <cstddef>
#include <deque>
#include <optional>
#include <string>
#include <string_view>

namespace wetype {

struct VoiceMessage {
    std::string kind, id, payload;
};

inline std::optional<VoiceMessage> parseVoiceLine(std::string_view line) {
    if (line.empty() || line.size() > 70000) return std::nullopt;
    VoiceMessage message;
    const auto first = line.find(' ');
    message.kind = std::string(line.substr(0, first));
    if (first == std::string_view::npos) return message;
    const auto rest = line.substr(first + 1);
    const auto second = rest.find(' ');
    message.id = std::string(rest.substr(0, second));
    if (second != std::string_view::npos) message.payload = std::string(rest.substr(second + 1));
    if (message.id.empty() || message.id.size() > 32) return std::nullopt;
    return message;
}

inline std::optional<std::string> hexDecode(std::string_view hex) {
    if (hex.size() % 2 || hex.size() > 65536) return std::nullopt;
    auto value = [](char c) -> int {
        if (c >= '0' && c <= '9') return c - '0';
        if (c >= 'a' && c <= 'f') return c - 'a' + 10;
        if (c >= 'A' && c <= 'F') return c - 'A' + 10;
        return -1;
    };
    std::string out;
    out.reserve(hex.size() / 2);
    for (std::size_t i = 0; i < hex.size(); i += 2) {
        const int high = value(hex[i]), low = value(hex[i + 1]);
        if (high < 0 || low < 0) return std::nullopt;
        const char byte = static_cast<char>(high * 16 + low);
        if (byte == '\0') return std::nullopt;
        out.push_back(byte);
    }
    return out;
}

// 最近几次音量（0–8）画成一段波形；没有声音时显示最低的横条。
inline std::string voiceMeter(const std::deque<int> &levels, std::size_t width = 8) {
    static constexpr const char *bars[] = {"▁", "▁", "▂", "▃", "▄", "▅", "▆", "▇", "█"};
    std::string out;
    for (std::size_t i = 0; i < width; ++i) {
        const std::size_t offset = width - levels.size();
        int level = i < offset ? 0 : levels[i - offset];
        if (level < 0) level = 0;
        if (level > 8) level = 8;
        out += bars[level];
    }
    return out;
}

inline const char *voiceErrorText(std::string_view code) {
    if (code == "not_paired") return "尚未关联手机或 Mac，请先运行 wetype-cloud.py pair";
    if (code == "no_recorder") return "找不到录音程序（pw-record、parecord 或 arecord）";
    if (code == "no_opus") return "缺少 libopus，请安装后重试";
    if (code == "timeout") return "网络超时，请重试";
    if (code == "network") return "网络连接失败";
    if (code == "server") return "识别服务暂时不可用";
    return "识别失败，请重试";
}

}  // namespace wetype
