#pragma once

#include <fcitx-config/configuration.h>
#include <fcitx-config/enum.h>
#include <fcitx-config/option.h>
#include <fcitx-utils/i18n.h>
#include <fcitx-utils/key.h>
#include <fcitx-utils/keysym.h>
#include "fuzzy_pinyin.h"

namespace fcitx {

enum class PinyinScheme { FullPinyin, Flypy };
FCITX_CONFIG_ENUM_NAME_WITH_I18N(PinyinScheme, N_("全拼"), N_("小鹤双拼"));

FCITX_CONFIGURATION(WeTypeConfig,
    OptionWithAnnotation<PinyinScheme, PinyinSchemeI18NAnnotation> pinyinScheme{
        this, "PinyinScheme", "拼音方案", PinyinScheme::FullPinyin};
    Option<bool> showPinyinInInput{
        this, "ShowPinyinInInput", "在输入框中显示拼音", false};
    Option<bool> alwaysEnglishPunctuation{
        this, "AlwaysEnglishPunctuation", "始终使用英文标点", false};
    Option<bool> arrowKeysMoveCursor{
        this, "ArrowKeysMoveCursor", "方向键左右移动拼音光标", true};
    Option<bool> readlineKeyBindings{
        this, "ReadlineKeyBindings", "启用 Readline 快捷键 (Ctrl+A/E/B/F/D/H/K/U, Alt+B/F)", true};
    KeyListOption prevPage{
        this, "PrevPage", "上一页",
        {Key(FcitxKey_minus), Key(FcitxKey_KP_Subtract), Key(FcitxKey_Up), Key(FcitxKey_Page_Up)},
        KeyListConstrain(KeyConstrainFlag::AllowModifierLess)};
    KeyListOption nextPage{
        this, "NextPage", "下一页",
        {Key(FcitxKey_equal), Key(FcitxKey_plus), Key(FcitxKey_KP_Add), Key(FcitxKey_Down), Key(FcitxKey_Page_Down)},
        KeyListConstrain(KeyConstrainFlag::AllowModifierLess)};

    // 多行候选词设置
    Option<bool> multiRowCandidates{
        this, "MultiRowCandidates", "启用多行候选词显示", false};
    Option<int, IntConstrain> candidateRows{
        this, "CandidateRows", "候选行数 (2-10)", 5, IntConstrain(2, 10)};
    Option<bool> multiRowShortcuts{
        this, "MultiRowShortcuts", "启用候选行切换快捷键 (Up/Down, Ctrl+Y/E)", true};
    KeyListOption prevRow{
        this, "PrevRow", "上一候选行",
        {Key(FcitxKey_Up), Key(FcitxKey_y, KeyState::Ctrl), Key(FcitxKey_Up, KeyState::Ctrl)},
        KeyListConstrain(KeyConstrainFlag::AllowModifierLess)};
    KeyListOption nextRow{
        this, "NextRow", "下一候选行",
        {Key(FcitxKey_Down), Key(FcitxKey_e, KeyState::Ctrl), Key(FcitxKey_Down, KeyState::Ctrl)},
        KeyListConstrain(KeyConstrainFlag::AllowModifierLess)};

    // 模糊音设置 (19 组，与微信输入法官方一致)
    Option<bool> fuzzyZ_ZH{this, "Fuzzy_Z_ZH", "模糊音：z = zh", false};
    Option<bool> fuzzyC_CH{this, "Fuzzy_C_CH", "模糊音：c = ch", false};
    Option<bool> fuzzyS_SH{this, "Fuzzy_S_SH", "模糊音：s = sh", false};
    Option<bool> fuzzyN_L{this, "Fuzzy_N_L", "模糊音：n = l", false};
    Option<bool> fuzzyH_F{this, "Fuzzy_H_F", "模糊音：h = f", false};
    Option<bool> fuzzyR_L{this, "Fuzzy_R_L", "模糊音：r = l", false};
    Option<bool> fuzzyG_K{this, "Fuzzy_G_K", "模糊音：g = k", false};
    Option<bool> fuzzyAN_ANG{this, "Fuzzy_AN_ANG", "模糊音：an = ang", false};
    Option<bool> fuzzyEN_ENG{this, "Fuzzy_EN_ENG", "模糊音：en = eng", false};
    Option<bool> fuzzyIN_ING{this, "Fuzzy_IN_ING", "模糊音：in = ing", false};
    Option<bool> fuzzyIAN_IANG{this, "Fuzzy_IAN_IANG", "模糊音：ian = iang", false};
    Option<bool> fuzzyUAN_UANG{this, "Fuzzy_UAN_UANG", "模糊音：uan = uang", false};
    Option<bool> fuzzyON_ONG{this, "Fuzzy_ON_ONG", "模糊音：on = ong", false};
    Option<bool> fuzzyENG_ONG{this, "Fuzzy_ENG_ONG", "模糊音：eng = ong", false};
    Option<bool> fuzzyUN_ONG{this, "Fuzzy_UN_ONG", "模糊音：un = ong", false};
    Option<bool> fuzzyUN_IONG{this, "Fuzzy_UN_IONG", "模糊音：un = iong", false};
    Option<bool> fuzzyAN_AI{this, "Fuzzy_AN_AI", "模糊音：an = ai", false};
    Option<bool> fuzzyHUI_FEI{this, "Fuzzy_HUI_FEI", "模糊音：hui = fei", false};
    Option<bool> fuzzyHUANG_WANG{this, "Fuzzy_HUANG_WANG", "模糊音：huang = wang", false};

    uint32_t fuzzyFlags() const {
        uint32_t flags = 0;
        if (*fuzzyZ_ZH) flags |= WETYPE_FUZZY_Z_ZH;
        if (*fuzzyC_CH) flags |= WETYPE_FUZZY_C_CH;
        if (*fuzzyS_SH) flags |= WETYPE_FUZZY_S_SH;
        if (*fuzzyN_L) flags |= WETYPE_FUZZY_N_L;
        if (*fuzzyH_F) flags |= WETYPE_FUZZY_H_F;
        if (*fuzzyR_L) flags |= WETYPE_FUZZY_R_L;
        if (*fuzzyG_K) flags |= WETYPE_FUZZY_G_K;
        if (*fuzzyAN_ANG) flags |= WETYPE_FUZZY_AN_ANG;
        if (*fuzzyEN_ENG) flags |= WETYPE_FUZZY_EN_ENG;
        if (*fuzzyIN_ING) flags |= WETYPE_FUZZY_IN_ING;
        if (*fuzzyIAN_IANG) flags |= WETYPE_FUZZY_IAN_IANG;
        if (*fuzzyUAN_UANG) flags |= WETYPE_FUZZY_UAN_UANG;
        if (*fuzzyON_ONG) flags |= WETYPE_FUZZY_ON_ONG;
        if (*fuzzyENG_ONG) flags |= WETYPE_FUZZY_ENG_ONG;
        if (*fuzzyUN_ONG) flags |= WETYPE_FUZZY_UN_ONG;
        if (*fuzzyUN_IONG) flags |= WETYPE_FUZZY_UN_IONG;
        if (*fuzzyAN_AI) flags |= WETYPE_FUZZY_AN_AI;
        if (*fuzzyHUI_FEI) flags |= WETYPE_FUZZY_HUI_FEI;
        if (*fuzzyHUANG_WANG) flags |= WETYPE_FUZZY_HUANG_WANG;
        return flags;
    }

    void setFuzzyFlags(uint32_t flags) {
        fuzzyZ_ZH.setValue(bool(flags & WETYPE_FUZZY_Z_ZH));
        fuzzyC_CH.setValue(bool(flags & WETYPE_FUZZY_C_CH));
        fuzzyS_SH.setValue(bool(flags & WETYPE_FUZZY_S_SH));
        fuzzyN_L.setValue(bool(flags & WETYPE_FUZZY_N_L));
        fuzzyH_F.setValue(bool(flags & WETYPE_FUZZY_H_F));
        fuzzyR_L.setValue(bool(flags & WETYPE_FUZZY_R_L));
        fuzzyG_K.setValue(bool(flags & WETYPE_FUZZY_G_K));
        fuzzyAN_ANG.setValue(bool(flags & WETYPE_FUZZY_AN_ANG));
        fuzzyEN_ENG.setValue(bool(flags & WETYPE_FUZZY_EN_ENG));
        fuzzyIN_ING.setValue(bool(flags & WETYPE_FUZZY_IN_ING));
        fuzzyIAN_IANG.setValue(bool(flags & WETYPE_FUZZY_IAN_IANG));
        fuzzyUAN_UANG.setValue(bool(flags & WETYPE_FUZZY_UAN_UANG));
        fuzzyON_ONG.setValue(bool(flags & WETYPE_FUZZY_ON_ONG));
        fuzzyENG_ONG.setValue(bool(flags & WETYPE_FUZZY_ENG_ONG));
        fuzzyUN_ONG.setValue(bool(flags & WETYPE_FUZZY_UN_ONG));
        fuzzyUN_IONG.setValue(bool(flags & WETYPE_FUZZY_UN_IONG));
        fuzzyAN_AI.setValue(bool(flags & WETYPE_FUZZY_AN_AI));
        fuzzyHUI_FEI.setValue(bool(flags & WETYPE_FUZZY_HUI_FEI));
        fuzzyHUANG_WANG.setValue(bool(flags & WETYPE_FUZZY_HUANG_WANG));
    }
);

}  // namespace fcitx
