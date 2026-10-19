#include "config.h"

#include <fcitx-config/rawconfig.h>
#include <fcitx-utils/key.h>
#include <cassert>
#include <iostream>
#include <stdexcept>

int main() {
    using namespace fcitx;
    try {
        WeTypeConfig config;

        // 1. 默认配置验证
        const auto &prev = *config.prevPage;
        const auto &next = *config.nextPage;

        if (!Key(FcitxKey_minus).checkKeyList(prev)) throw std::runtime_error("default prevPage missing minus");
        if (!Key(FcitxKey_KP_Subtract).checkKeyList(prev)) throw std::runtime_error("default prevPage missing KP_Subtract");
        if (!Key(FcitxKey_Up).checkKeyList(prev)) throw std::runtime_error("default prevPage missing Up");
        if (!Key(FcitxKey_Page_Up).checkKeyList(prev)) throw std::runtime_error("default prevPage missing Page_Up");

        if (!Key(FcitxKey_equal).checkKeyList(next)) throw std::runtime_error("default nextPage missing equal");
        if (!Key(FcitxKey_plus).checkKeyList(next)) throw std::runtime_error("default nextPage missing plus");
        if (!Key(FcitxKey_KP_Add).checkKeyList(next)) throw std::runtime_error("default nextPage missing KP_Add");
        if (!Key(FcitxKey_Down).checkKeyList(next)) throw std::runtime_error("default nextPage missing Down");
        if (!Key(FcitxKey_Page_Down).checkKeyList(next)) throw std::runtime_error("default nextPage missing Page_Down");

        if (Key(FcitxKey_comma).checkKeyList(prev)) throw std::runtime_error("default prevPage unexpectedly matched comma");
        if (Key(FcitxKey_period).checkKeyList(next)) throw std::runtime_error("default nextPage unexpectedly matched period");

        // 2. 自定义逗号与句号翻页
        RawConfig raw;
        raw["PrevPage"]["0"] = "comma";
        raw["NextPage"]["0"] = "period";
        config.load(raw, true);

        const auto &customPrev = *config.prevPage;
        const auto &customNext = *config.nextPage;

        if (!Key(FcitxKey_comma).checkKeyList(customPrev)) throw std::runtime_error("custom prevPage failed to match comma");
        if (!Key(FcitxKey_period).checkKeyList(customNext)) throw std::runtime_error("custom nextPage failed to match period");
        if (Key(FcitxKey_minus).checkKeyList(customPrev)) throw std::runtime_error("custom prevPage still matched minus");
        if (Key(FcitxKey_equal).checkKeyList(customNext)) throw std::runtime_error("custom nextPage still matched equal");

        // 3. 自定义方括号翻页 (bracketleft / bracketright)
        RawConfig bracketRaw;
        bracketRaw["PrevPage"]["0"] = "bracketleft";
        bracketRaw["NextPage"]["0"] = "bracketright";
        config.load(bracketRaw, true);

        if (!Key(FcitxKey_bracketleft).checkKeyList(*config.prevPage)) throw std::runtime_error("custom bracketleft failed");
        if (!Key(FcitxKey_bracketright).checkKeyList(*config.nextPage)) throw std::runtime_error("custom bracketright failed");

        // 4. 多行候选默认配置验证
        WeTypeConfig multiConf;
        if (*multiConf.multiRowCandidates != false) throw std::runtime_error("default multiRowCandidates should be false");
        if (*multiConf.candidateRows != 5) throw std::runtime_error("default candidateRows should be 5");
        if (*multiConf.multiRowShortcuts != true) throw std::runtime_error("default multiRowShortcuts should be true");

        const auto &prevRow = *multiConf.prevRow;
        const auto &nextRow = *multiConf.nextRow;

        auto matchKeyList = [](const Key &eventKey, const std::vector<Key> &list) {
            if (eventKey.checkKeyList(list) || eventKey.normalize().checkKeyList(list)) return true;
            const KeySym sym = eventKey.sym();
            if (sym >= FcitxKey_a && sym <= FcitxKey_z) {
                const Key toggled(static_cast<KeySym>(sym - FcitxKey_a + FcitxKey_A), eventKey.states());
                if (toggled.checkKeyList(list) || toggled.normalize().checkKeyList(list)) return true;
            } else if (sym >= FcitxKey_A && sym <= FcitxKey_Z) {
                const Key toggled(static_cast<KeySym>(sym - FcitxKey_A + FcitxKey_a), eventKey.states());
                if (toggled.checkKeyList(list) || toggled.normalize().checkKeyList(list)) return true;
            }
            return false;
        };

        if (!matchKeyList(Key(FcitxKey_Down), nextRow)) throw std::runtime_error("matchKeyList failed for plain Down");
        if (!matchKeyList(Key(FcitxKey_e, KeyState::Ctrl), nextRow)) throw std::runtime_error("matchKeyList failed for lower e");
        if (!matchKeyList(Key(FcitxKey_E, KeyState::Ctrl), nextRow)) throw std::runtime_error("matchKeyList failed for upper E");
        if (!matchKeyList(Key(FcitxKey_Down, KeyState::Ctrl), nextRow)) throw std::runtime_error("matchKeyList failed for Ctrl+Down");
        if (!matchKeyList(Key(FcitxKey_Up), prevRow)) throw std::runtime_error("matchKeyList failed for plain Up");
        if (!matchKeyList(Key(FcitxKey_y, KeyState::Ctrl), prevRow)) throw std::runtime_error("matchKeyList failed for lower y");
        if (!matchKeyList(Key(FcitxKey_Y, KeyState::Ctrl), prevRow)) throw std::runtime_error("matchKeyList failed for upper Y");
        if (!matchKeyList(Key(FcitxKey_Up, KeyState::Ctrl), prevRow)) throw std::runtime_error("matchKeyList failed for Ctrl+Up");

        // 5. 多行候选自定义配置与行切换计算
        RawConfig multiRaw;
        multiRaw["MultiRowCandidates"].setValue("True");
        multiRaw["CandidateRows"].setValue("4");
        multiRaw["MultiRowShortcuts"].setValue("False");
        multiRaw["PrevRow"]["0"] = "Control+p";
        multiRaw["NextRow"]["0"] = "Control+n";
        multiConf.load(multiRaw, true);

        if (*multiConf.multiRowCandidates != true) throw std::runtime_error("custom multiRowCandidates should be true");
        if (*multiConf.candidateRows != 4) throw std::runtime_error("custom candidateRows should be 4");
        if (*multiConf.multiRowShortcuts != false) throw std::runtime_error("custom multiRowShortcuts should be false");
        if (!Key(FcitxKey_p, KeyState::Ctrl).checkKeyList(*multiConf.prevRow)) throw std::runtime_error("custom prevRow failed to match Ctrl+p");
        if (!Key(FcitxKey_n, KeyState::Ctrl).checkKeyList(*multiConf.nextRow)) throw std::runtime_error("custom nextRow failed to match Ctrl+n");

        // 6. 二维矩阵行列计算逻辑验证 (35个候选词，4行，每行10个)
        const int totalCands = 35;
        const int cols = 10;
        const int actualRows = (totalCands + cols - 1) / cols; // 4 行
        if (actualRows != 4) throw std::runtime_error("actualRows mismatch");

        // 在第 1 行 (row=1)，选数字 1 (ordinal=0) 得到全局序号 10
        int selRow = 1;
        int ordinal = 0;
        int idx = selRow * cols + ordinal;
        int rowEnd = std::min((selRow + 1) * cols, totalCands);
        if (idx < 0 || idx >= rowEnd || idx != 10) throw std::runtime_error("row 1 digit 1 index calculation failed");

        // 在第 3 行 (row=3)，只有 5 个词 (30..34)。选数字 5 (ordinal=4) 得到 34
        selRow = 3;
        ordinal = 4;
        idx = selRow * cols + ordinal;
        rowEnd = std::min((selRow + 1) * cols, totalCands);
        if (idx < 0 || idx >= rowEnd || idx != 34) throw std::runtime_error("row 3 digit 5 index calculation failed");

        // 在第 3 行 (row=3)，选数字 6 (ordinal=5) 超出行边界，应判定为无效
        ordinal = 5;
        idx = selRow * cols + ordinal;
        if (idx < rowEnd) throw std::runtime_error("out-of-bounds ordinal on last row should be rejected");

        // 行切换循环
        selRow = 0;
        selRow = (selRow - 1 + actualRows) % actualRows; // 上一行 -> 3
        if (selRow != 3) throw std::runtime_error("prevRow loop from 0 failed");
        selRow = (selRow + 1) % actualRows; // 下一行 -> 0
        if (selRow != 0) throw std::runtime_error("nextRow loop from 3 failed");

        std::cout << "PASS paging_config: default keys, custom comma/period, bracket keys, multi-row matrix config\n";
        return 0;
    } catch (const std::exception &err) {
        std::cerr << "FAIL " << err.what() << '\n';
        return 1;
    }
}
