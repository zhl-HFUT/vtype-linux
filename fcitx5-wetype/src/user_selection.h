#pragma once

#include <algorithm>
#include <cstdint>
#include <fstream>
#include <string>
#include <unordered_map>
#include <utility>
#include <vector>

namespace wetype {

struct UserSelectionEntry {
    std::string text;
    uint32_t count = 0;
};

// 记录用户明确选择的候选词。在下次输入相同拼音时，将其优先置顶，减少重复选词。
class UserSelection {
public:
    void setPath(std::string path) {
        path_ = std::move(path);
    }

    const std::string &path() const {
        return path_;
    }

    bool load() {
        if (path_.empty()) return false;
        std::ifstream input(path_);
        if (!input.is_open()) return false;

        entries_.clear();
        order_.clear();

        std::string line;
        while (std::getline(input, line)) {
            if (!line.empty() && line.back() == '\r') line.pop_back();
            if (line.empty() || line.front() == '#') continue;
            const auto tab1 = line.find('\t');
            if (tab1 == std::string::npos || tab1 == 0) continue;
            std::string pinyin = line.substr(0, tab1);
            const auto tab2 = line.find('\t', tab1 + 1);
            std::string text = (tab2 == std::string::npos)
                                   ? line.substr(tab1 + 1)
                                   : line.substr(tab1 + 1, tab2 - tab1 - 1);
            uint32_t count = 1;
            if (tab2 != std::string::npos) {
                count = static_cast<uint32_t>(std::strtoul(line.c_str() + tab2 + 1, nullptr, 10));
                if (count == 0) count = 1;
            }
            if (!pinyin.empty() && !text.empty()) {
                entries_[pinyin] = UserSelectionEntry{std::move(text), count};
                order_.push_back(pinyin);
            }
        }
        dirty_ = false;
        return true;
    }

    bool save() const {
        if (path_.empty() || !dirty_) return false;
        const std::string tempPath = path_ + ".tmp";
        std::ofstream output(tempPath);
        if (!output.is_open()) return false;

        for (const auto &pinyin : order_) {
            auto it = entries_.find(pinyin);
            if (it != entries_.end()) {
                output << pinyin << '\t' << it->second.text << '\t' << it->second.count << '\n';
            }
        }
        output.close();
        if (!output.good()) return false;
        if (std::rename(tempPath.c_str(), path_.c_str()) != 0) return false;
        dirty_ = false;
        return true;
    }

    void record(const std::string &pinyin, const std::string &text) {
        if (pinyin.empty() || text.empty() || pinyin == text) return;
        auto it = entries_.find(pinyin);
        if (it != entries_.end()) {
            if (it->second.text == text) {
                if (it->second.count < UINT32_MAX) ++it->second.count;
            } else {
                it->second.text = text;
                it->second.count = 1;
            }
            auto oit = std::find(order_.begin(), order_.end(), pinyin);
            if (oit != order_.end() && oit + 1 != order_.end()) {
                order_.erase(oit);
                order_.push_back(pinyin);
            }
        } else {
            if (order_.size() >= 2048) {
                const auto &oldest = order_.front();
                entries_.erase(oldest);
                order_.erase(order_.begin());
            }
            entries_[pinyin] = UserSelectionEntry{text, 1};
            order_.push_back(pinyin);
        }
        dirty_ = true;
    }

    const std::string *lookup(const std::string &pinyin) const {
        auto it = entries_.find(pinyin);
        return (it != entries_.end()) ? &it->second.text : nullptr;
    }

    void clear() {
        entries_.clear();
        order_.clear();
        dirty_ = true;
    }

    size_t size() const {
        return entries_.size();
    }

    bool isDirty() const {
        return dirty_;
    }

private:
    std::string path_;
    std::unordered_map<std::string, UserSelectionEntry> entries_;
    std::vector<std::string> order_;
    mutable bool dirty_ = false;
};

} // namespace wetype
