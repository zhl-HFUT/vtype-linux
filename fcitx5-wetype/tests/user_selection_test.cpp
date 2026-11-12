#include "user_selection.h"
#include <cassert>
#include <iostream>
#include <filesystem>

int main() {
    namespace fs = std::filesystem;
    const std::string testFile = "/tmp/test_user_selection.tsv";
    if (fs::exists(testFile)) fs::remove(testFile);

    wetype::UserSelection sel;
    sel.setPath(testFile);

    // 1. 空状态测试
    assert(sel.size() == 0);
    assert(sel.lookup("tb") == nullptr);

    // 2. 记录与查询
    sel.record("tb", "同步");
    assert(sel.size() == 1);
    assert(sel.lookup("tb") != nullptr);
    assert(*sel.lookup("tb") == "同步");

    // 3. 重复记录相同词（增加计数）
    sel.record("tb", "同步");
    assert(sel.size() == 1);
    assert(*sel.lookup("tb") == "同步");

    // 4. 覆盖记录新词
    sel.record("tb", "特别");
    assert(sel.size() == 1);
    assert(*sel.lookup("tb") == "特别");

    // 5. 忽略 raw pinyin 相同的情况
    sel.record("abc", "abc");
    assert(sel.lookup("abc") == nullptr);

    // 6. 存储并重新读取
    sel.record("nh", "你好");
    assert(sel.size() == 2);
    assert(sel.save());

    wetype::UserSelection sel2;
    sel2.setPath(testFile);
    assert(sel2.load());
    assert(sel2.size() == 2);
    assert(*sel2.lookup("tb") == "特别");
    assert(*sel2.lookup("nh") == "你好");

    // 7. 清理
    sel2.clear();
    assert(sel2.size() == 0);
    assert(sel2.lookup("tb") == nullptr);

    if (fs::exists(testFile)) fs::remove(testFile);
    std::cout << "PASS user_selection_test\n";
    return 0;
}
