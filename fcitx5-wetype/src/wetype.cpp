// fcitx5 微信输入法 addon — 异步事件驱动版 (2026-09-10)
//
// 架构: fcitx5 主线程 IO 事件驱动, 全程零阻塞:
//   keyEvent → 写 "B c" 到引擎 stdin(内核缓冲, 不等) + 本地即时回显 buf_
//   引擎响应(CAND)通过 EventLoop IO 事件到达 → parseCand → 更新候选面板
//   响应与命令按 FIFO handler 队列配对, 迟到/乱序不可能发生
//
// 引擎目录解析(按序): $WETYPE_ENGINE_DIR > ~/.local/lib/wetype-ime/arm64 >
//   /usr/lib/wetype-ime/arm64 >
//   ~/wetype-ime/squashfs-root/usr/lib/wetype-ime/arm64(开发回退)
#include <fcitx/instance.h>
#include <fcitx/addonfactory.h>
#include <fcitx/addonmanager.h>
#include <fcitx/inputmethodengine.h>
#include <fcitx/inputcontext.h>
#include <fcitx/inputcontextmanager.h>
#include <fcitx/inputcontextproperty.h>
#include <fcitx/surroundingtext.h>
#include <fcitx/inputpanel.h>
#include <fcitx/text.h>
#include <fcitx/candidatelist.h>
#include <fcitx-utils/keysym.h>
#include <fcitx-utils/key.h>
#include <fcitx-utils/event.h>
#include <fcitx-utils/trackableobject.h>
#include <fcitx-config/configuration.h>
#include <fcitx-config/enum.h>
#include <fcitx-config/iniparser.h>
#include <fcitx-config/option.h>
#include <fcitx-utils/i18n.h>

#include <algorithm>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <csignal>
#include <cerrno>
#include <cstdint>
#include <ctime>
#include <sys/stat.h>
#include <unistd.h>
#include <fcntl.h>
#include <sys/wait.h>
#include <sys/resource.h>
#include <sys/file.h>
#include <deque>
#include <functional>
#include <memory>
#include <string>
#include <stdexcept>
#include <vector>
#include <sstream>
#include <unordered_set>

#include "glossary.h"
#include "common_phrases.h"
#include "harness_log.h"
#include "punctuation.h"
#include "config.h"

namespace fcitx {

static constexpr int FALLBACK_PAGE_SIZE = 5;   // 取不到全局设置时的每页候选数
static constexpr uint64_t ENGINE_RESTART_DELAY_USEC = 200000;

// Display-only segmentation. The original unsegmented buffer is still sent to
// the WeType engine, so this never changes composition or candidate matching.
static std::string segmentPinyin(const std::string &raw) {
    static const std::unordered_set<std::string> syllables = [] {
        static const char *data =
            "a ai an ang ao e ei en eng er o ou "
            "ba bai ban bang bao bei ben beng bi bian biao bie bin bing bo bu "
            "pa pai pan pang pao pei pen peng pi pian piao pie pin ping po pou pu "
            "ma mai man mang mao me mei men meng mi mian miao mie min ming miu mo mou mu "
            "fa fan fang fei fen feng fo fou fu "
            "da dai dan dang dao de dei den deng di dia dian diao die ding diu dong dou du duan dui dun duo "
            "ta tai tan tang tao te teng ti tian tiao tie ting tong tou tu tuan tui tun tuo "
            "na nai nan nang nao ne nei nen neng ng ni nian niang niao nie nin ning niu nong nou nu nuan nue nuo nv nve "
            "la lai lan lang lao le lei leng li lia lian liang liao lie lin ling liu long lou lu luan lue lun luo lv "
            "ga gai gan gang gao ge gei gen geng gong gou gu gua guai guan guang gui gun guo "
            "ka kai kan kang kao ke kei ken keng kong kou ku kua kuai kuan kuang kui kun kuo "
            "ha hai han hang hao he hei hen heng hong hou hu hua huai huan huang hui hun huo "
            "za zai zan zang zao ze zei zen zeng zha zhai zhan zhang zhao zhe zhei zhen zheng zhi zhong zhou zhu zhua zhuai zhuan zhuang zhui zhun zhuo zi zong zou zu zuan zui zun zuo "
            "ca cai can cang cao ce cen ceng cha chai chan chang chao che chen cheng chi chong chou chu chua chuai chuan chuang chui chun chuo ci cong cou cu cuan cui cun cuo "
            "sa sai san sang sao se sen seng sha shai shan shang shao she shei shen sheng shi shou shu shua shuai shuan shuang shui shun shuo si song sou su suan sui sun suo "
            "ra ran rang rao re ren reng ri rong rou ru ruan rui run ruo "
            "ji jia jian jiang jiao jie jin jing jiong jiu ju juan jue jun "
            "qi qia qian qiang qiao qie qin qing qiong qiu qu quan que qun "
            "xi xia xian xiang xiao xie xin xing xiong xiu xu xuan xue xun "
            "ya yan yang yao ye yi yin ying yo yong you yu yuan yue yun "
            "wa wai wan wang wei wen weng wo wu "
            "bo bei ben beng bian bie bin bing "
            "ge gei gen geng gong gou gua guai guan guang gui gun guo "
            "jiong juan jue jun";
        std::unordered_set<std::string> result;
        std::istringstream input(data);
        std::string item;
        while (input >> item) result.insert(item);
        return result;
    }();

    std::string out;
    for (size_t i = 0; i < raw.size();) {
        if (raw[i] == '\'') {
            out += raw[i++];
            continue;
        }
        size_t match = 0;
        const size_t limit = std::min(raw.size(), i + 6);
        for (size_t end = limit; end > i; --end) {
            if (raw.find('\'', i) < end) continue;
            if (syllables.count(raw.substr(i, end - i))) {
                match = end - i;
                break;
            }
        }
        if (match) {
            if (!out.empty() && out.back() != '\'' && out.back() != ' ') out += ' ';
            out.append(raw, i, match);
            i += match;
        } else {
            if (!out.empty() && out.back() != '\'' && out.back() != ' ') out += ' ';
            out.append(raw, i, std::string::npos);
            break;
        }
    }
    return out;
}

static Text pinyinPreedit(const std::string &buffer, bool doublePin) {
    Text text;
    std::string display;
    if (doublePin) {
        // 双拼编码每两键一个音节；不按全拼词表切分，也不改动引擎输入。
        for (size_t i = 0; i < buffer.size(); i += 2) {
            if (i) display += ' ';
            display.append(buffer, i, 2);
        }
    } else {
        display = segmentPinyin(buffer);
    }
    if (!display.empty()) text.append(display, TextFormatFlag::NoFlag);
    return text;
}

struct GridColumnCandidate : public CandidateWord {
    GridColumnCandidate(Text text, std::function<void(InputContext *)> select)
        : CandidateWord(std::move(text)), select_(std::move(select)) {
        setCustomLabel(Text(""));
    }
    void select(InputContext *ic) const override {
        if (select_) select_(ic);
    }
private:
    std::function<void(InputContext *)> select_;
};
// 调试日志: 进 fcitx5 的 stderr, 不影响功能
#define WLOG(...) do { fprintf(stderr, "[wetype-addon] " __VA_ARGS__); fflush(stderr); } while (0)

// ---------------------------------------------------------------- 引擎目录解析
static void resolveDirs(std::string &eng, std::string &dicts, std::string &work,
                        std::string &qemu, std::string &sysroot) {
    auto existsEngine = [](const std::string &p) {
        return ::access((p + "/wetype-harness").c_str(), X_OK) == 0 &&
               ::access((p + "/lib/libwxhld_jni.so").c_str(), R_OK) == 0;
    };
    const char *home = getenv("HOME") ? getenv("HOME") : "/root";
    eng = getenv("WETYPE_ENGINE_DIR") ? getenv("WETYPE_ENGINE_DIR") : "";
    if (eng.empty() || !existsEngine(eng)) {
        eng = std::string(home) + "/.local/lib/wetype-ime/arm64";
        if (!existsEngine(eng)) {
            eng = "/usr/lib/wetype-ime/arm64";
            if (!existsEngine(eng)) {
                eng = std::string(home) + "/wetype-ime/squashfs-root/usr/lib/wetype-ime/arm64";  // 开发回退
            }
        }
    }
    dicts = getenv("WETYPE_DICT_DIR") ? getenv("WETYPE_DICT_DIR") : eng + "/dicts";
    // QEMU 随安装一起提供，缺失时回落到系统包；bionic 运行时只随安装提供。
    // ARM64 宿主没有 QEMU 时 qemu 为空，表示直接运行 harness。
    qemu = getenv("QEMU_AARCH64") ? getenv("QEMU_AARCH64") : "";
    if (qemu.empty()) {
        if (::access((eng + "/qemu-aarch64-static").c_str(), X_OK) == 0)
            qemu = eng + "/qemu-aarch64-static";
#if !defined(__aarch64__)
        else
            qemu = "qemu-aarch64-static";
#endif
    }
    const char *sr = getenv("WETYPE_SYSROOT");
    sysroot = sr && *sr ? sr : eng + "/sysroot";
    const char *xdg = getenv("XDG_DATA_HOME");
    std::string base = xdg && *xdg ? xdg : std::string(home) + "/.local/share";
    work = getenv("WETYPE_WORK_DIR") ? getenv("WETYPE_WORK_DIR") : base + "/wetype-ime/dict";
}

// ---------------------------------------------------------------- 引擎日志
// 解析、建目录与轮转都在父进程做完(纯逻辑见 harness_log.h), 好让 fork 之后、
// exec 之前只剩一次 open: 那段窗口里不做分配, 也不做 stat。
static std::string prepareHarnessLog() {
    std::string path = wetype::harnessLogPath(getenv("WETYPE_HARNESS_LOG"),
                                              getenv("XDG_STATE_HOME"), getenv("HOME"));
    const std::size_t slash = path.rfind('/');
    if (slash != std::string::npos) wetype::mkdirParents(path.substr(0, slash));
    wetype::capHarnessLog(path);
    return path;
}

// ---------------------------------------------------------------- 异步引擎进程
class EngineProc {
public:
    using Handler = std::function<void(const std::string &)>;   // "" = 失败/死亡

    explicit EngineProc(EventLoop &loop) : loop_(loop) {}
    ~EngineProc() { stop(); }

    bool alive() const { return pid_ > 0; }
    bool ready() const { return state_ == State::Ready; }
    bool idle() const { return ready() && pending_.empty(); }
    bool gaveUp() const { return gaveUp_; }
    bool cloudConfigured() const { return cloudConfigured_; }
    void setOnReady(std::function<void()> cb) { onReady_ = std::move(cb); }
    void setOnExit(std::function<void()> cb) { onExit_ = std::move(cb); }
    void setDoublePinSolution(int value) { doublePinSolution_ = value; }
    void setFuzzyFlags(uint32_t value) { fuzzyFlags_ = value; }

    // 非阻塞启动; 就绪走 IO 事件。指数退避防 fork 风暴(连续 5 次失败放弃)。
    bool start() {
        if (pid_ > 0) return true;
        if (failCount_ >= 5) {
            if (!gaveUp_) { WLOG("engine gave up after %d failures\n", failCount_); gaveUp_ = true; }
            return false;
        }
        std::string eng, dicts, work, qemu, sysroot;
        resolveDirs(eng, dicts, work, qemu, sysroot);
        const char *explicitCloud=getenv("WETYPE_CLOUD_CONFIG");
        const char *configHome=getenv("XDG_CONFIG_HOME");
        const std::string configBase=configHome && *configHome ? configHome : std::string(getenv("HOME"))+"/.config";
        const std::string cloudConfig=explicitCloud ? explicitCloud : configBase+"/wetype-ime/cloud.json";
        cloudConfigured_=!qemu.empty() && access(cloudConfig.c_str(),R_OK)==0 &&
            access((eng+"/cloud_proxy.py").c_str(),R_OK)==0;
        learningFile_ = work + "/../sync-control/learning-generation";
        // 路径解析与轮转都在父进程做完, 子进程只保留一次 open。
        const std::string harnessLog = prepareHarnessLog();
        const std::string doublePin = std::to_string(doublePinSolution_);
        const std::string fuzzyVal = std::to_string(fuzzyFlags_);

        int inP[2], outP[2];
        if (pipe2(inP, O_CLOEXEC) < 0) return false;
        if (pipe2(outP, O_CLOEXEC) < 0) {
            close(inP[0]);
            close(inP[1]);
            return false;
        }
        pid_t p = fork();
        if (p < 0) {
            close(inP[0]); close(inP[1]); close(outP[0]); close(outP[1]);
            return false;
        }
        if (p == 0) {
            setenv("WETYPE_DOUBLE_PIN", doublePin.c_str(), 1);
            setenv("WETYPE_FUZZY", fuzzyVal.c_str(), 1);
            if(cloudConfigured_ && setpgid(0,0)<0)_exit(127);
            // QEMU user mode writes guest core files in its current directory.
            // Keep an engine crash from leaving qemu_wetype-harness_*.core in HOME.
            const struct rlimit noCore = {0, 0};
            if (setrlimit(RLIMIT_CORE, &noCore) < 0) _exit(127);
            dup2(inP[0], 0);
            dup2(outP[1], 1);
            close(inP[0]);
            close(inP[1]);
            close(outP[0]);
            close(outP[1]);
            int logFd = open(harnessLog.c_str(), O_WRONLY | O_CREAT | O_APPEND, 0600);
            if (logFd >= 0) { dup2(logFd, 2); close(logFd); }
            std::string libPath = eng + "/lib";
            // Native run: bionic libc/liblog/libc++ come from the bundled sysroot.
            if (qemu.empty()) libPath += ":" + sysroot + "/system/lib64";
            setenv("LD_LIBRARY_PATH", libPath.c_str(), 1);
            setenv("WETYPE_DICT_DIR", dicts.c_str(), 1);
            setenv("WETYPE_ASSET_DIR", dicts.c_str(), 1);
            setenv("WETYPE_WORK_DIR", work.c_str(), 1);
            if (qemu.empty()) {
                // ARM64 host: exec the harness directly. Its ELF interpreter is set at
                // install time to the bundled sysroot/system/bin/linker64 (patchelf).
                execl((eng + "/wetype-harness").c_str(), (eng + "/wetype-harness").c_str(),
                      (eng + "/lib/libwxhld_jni.so").c_str(), "--daemon", (char *)nullptr);
            } else {
                // QEMU's default "max" CPU enables pointer authentication, which it
                // computes in software on every libc++ call: about 2x slower.
                setenv("QEMU_CPU", "cortex-a72", 0);
                if(cloudConfigured_) {
                    execl("/usr/bin/python3","/usr/bin/python3",(eng+"/cloud_proxy.py").c_str(),
                        "--config",cloudConfig.c_str(),"--",qemu.c_str(),"-L",sysroot.c_str(),
                        (eng+"/wetype-harness").c_str(),(eng+"/lib/libwxhld_jni.so").c_str(),"--daemon",(char *)nullptr);
                    _exit(127);
                }
                execlp(qemu.c_str(), qemu.c_str(), "-L", sysroot.c_str(),
                       (eng + "/wetype-harness").c_str(),
                       (eng + "/lib/libwxhld_jni.so").c_str(), "--daemon", (char *)nullptr);
            }
            _exit(127);
        }
        close(inP[0]);
        close(outP[1]);
        pid_ = p;  in_ = inP[1];  out_ = outP[0];
        rbuf_.clear();
        state_ = State::Starting;
        ++failCount_;
        ioSource_ = loop_.addIOEvent(
            out_, IOEventFlags(IOEventFlag::In),
            [this](EventSourceIO *, int fd, IOEventFlags) { onReadable(fd); return true; });
        // 启动看门狗: 90s 未 READY 判失败
        timeSource_ = loop_.addTimeEvent(
            CLOCK_MONOTONIC, nowUsec() + 90ull * 1000000ull, 0,
            [this](EventSourceTime *, uint64_t) { onStartTimeout(); return true; });
        WLOG("engine starting (attempt %d) eng=%s\n", failCount_, eng.c_str());
        return true;
    }

    bool stop() {
        bool graceful = true;
        WLOG("stop() called (pid=%d)\n", pid_);
        if (pid_ > 0) {
            if (in_ >= 0) {
                ssize_t w = write(in_, "Q\n", 2);
                (void)w;
                close(in_);
                in_ = -1;
            }
            int st = 0;
            bool exited = false;
            // Let the harness process Q, destroy its engine session and flush
            // learned words. Bound shutdown so a wedged QEMU cannot stall Fcitx.
            for (int i = 0; i < (cloudConfigured_ ? 200 : 100); ++i) {
                pid_t result = waitpid(pid_, &st, WNOHANG);
                if (result == pid_ || (result < 0 && errno == ECHILD)) {
                    exited = true;
                    break;
                }
                if (result < 0 && errno != EINTR) break;
                usleep(10000);
            }
            if (!exited) {
                graceful = false;
                kill(cloudConfigured_ ? -pid_ : pid_, SIGKILL);
                while (waitpid(pid_, &st, 0) < 0 && errno == EINTR) {}
            }
            pid_ = -1;
        }
        if (in_ >= 0)  { close(in_);  in_  = -1; }
        if (out_ >= 0) { close(out_); out_ = -1; }
        ioSource_.reset();
        timeSource_.reset();
        state_ = State::Dead;
        failPendingHandlers();
        return graceful;
    }

    // 异步发送: handler 在主线程(IO 事件)被调用一次; 死亡时以 "" 调用
    void send(const std::string &line, Handler h) {
        if (pid_ <= 0 || in_ < 0) { if (h) h(""); return; }
        WLOG("engine send command=%c bytes=%zu queued=%zu state=%d\n",
             line.empty() ? '?' : line[0], line.size(), pending_.size(),
             static_cast<int>(state_));
        std::string out = line + "\n";
        if (line.rfind("S ", 0) == 0 && !learningFile_.empty()) {
            const auto temp = learningFile_ + ".pending";
            std::ofstream changed(temp); changed << getpid() << ":" << nowUsec(); changed.close();
            chmod(temp.c_str(), 0600); rename(temp.c_str(), learningFile_.c_str());
        }
        ssize_t n = write(in_, out.data(), out.size());
        if (n < 0) {
            WLOG("write fail errno=%d → stop\n", errno);
            die();
            if (h) h("");
            return;
        }
        pending_.push_back(std::move(h));
    }

private:
    enum class State { Dead, Starting, Ready };

    static uint64_t nowUsec() {
        struct timespec ts; clock_gettime(CLOCK_MONOTONIC, &ts);
        return (uint64_t)ts.tv_sec * 1000000ull + ts.tv_nsec / 1000;
    }

    void onReadable(int fd) {
        char tmp[8192];
        ssize_t n = read(fd, tmp, sizeof tmp);
        if (n <= 0) {
            WLOG("engine EOF (n=%zd)\n", n);
            die();
            return;
        }
        rbuf_.append(tmp, n);
        size_t pos;
        while ((pos = rbuf_.find('\n')) != std::string::npos) {
            std::string line = rbuf_.substr(0, pos);
            rbuf_.erase(0, pos + 1);
            onLine(line);
        }
    }

    void onLine(const std::string &line) {
        WLOG("engine line bytes=%zu prefix=%.12s queued=%zu state=%d\n",
             line.size(), line.c_str(), pending_.size(), static_cast<int>(state_));
        if (state_ == State::Starting) {
            if (line == "READY") {
                state_ = State::Ready;
                failCount_ = 0;
                timeSource_.reset();
                WLOG("engine READY\n");
                if (onReady_) onReady_();
            }
            return;   // READY 前的杂音行丢弃
        }
        if (state_ != State::Ready) return;
        if (!line.empty() && !pending_.empty()) {
            Handler h = std::move(pending_.front());
            pending_.pop_front();
            if (h) h(line);
        }
    }

    void onStartTimeout() {
        WLOG("engine start timeout → stop\n");
        die();
    }

    // 引擎意外退出: 清理后通知上层重启(主动 stop() 不通知)
    void die() {
        stop();
        if (onExit_) onExit_();
    }

    // Handlers may restart the engine and queue new commands; those belong to
    // the new process and must not be failed here.
    void failPendingHandlers() {
        std::deque<Handler> failed;
        failed.swap(pending_);
        for (auto &h : failed)
            if (h) h("");
    }

    EventLoop &loop_;
    pid_t pid_ = -1;
    int in_ = -1, out_ = -1;
    std::string rbuf_;
    std::string learningFile_;
    State state_ = State::Dead;
    std::deque<Handler> pending_;
    std::unique_ptr<EventSourceIO> ioSource_;
    std::unique_ptr<EventSourceTime> timeSource_;
    std::function<void()> onReady_;
    std::function<void()> onExit_;
    int failCount_ = 0;
    bool gaveUp_ = false;
    bool cloudConfigured_ = false;
    int doublePinSolution_ = 0;
    uint32_t fuzzyFlags_ = 0;
};

// Punctuation history belongs to one input context and is never shared by
// Fcitx's ShareInputState option (the property's default needCopy() is false).
struct PunctuationState : InputContextProperty {
    wetype::TextLanguage language = wetype::TextLanguage::Unknown;
    bool lastDigit = false, doubleQuote = false, singleQuote = false;
};
struct ModeState : InputContextProperty {
    bool english=false, shiftSolo=false;
    KeySym shiftDown=FcitxKey_None;
};

// ---------------------------------------------------------------- 主引擎类
class WeTypeEngine final : public InputMethodEngineV2 {
public:
    explicit WeTypeEngine(Instance *instance)
        : instance_(instance), eng_(instance->eventLoop()) {
        readAsIni(config_, "conf/wetype.conf");
        eng_.setDoublePinSolution(*config_.pinyinScheme == PinyinScheme::Flypy ? 3 : 0);
        eng_.setFuzzyFlags(config_.fuzzyFlags());
        signal(SIGPIPE, SIG_IGN);
        if (!instance_->inputContextManager().registerProperty("wetype-punctuation-state", &punctuationFactory_)) {
            throw std::runtime_error("cannot register WeType punctuation state");
        }
        if (!instance_->inputContextManager().registerProperty("wetype-mode-state", &modeFactory_))
            throw std::runtime_error("cannot register WeType mode state");
        // 在 Fcitx 全局中英开关之前处理单独轻按的 Shift，仅作用于微信输入法；Ctrl+Space 交给 Fcitx。
        modeWatcher_=instance_->watchEvent(EventType::InputContextKeyEvent,EventWatcherPhase::PreInputMethod,
            [this](Event &event) {
                auto &key=static_cast<KeyEvent &>(event);
                auto *ic=key.inputContext();
                const auto *entry=instance_->inputMethodEntry(ic);
                if (!entry || entry->uniqueName()!="wetype-im" || ic->capabilityFlags().test(CapabilityFlag::Password)) return;
                auto *state=ic->propertyFor(&modeFactory_);
                const auto symbol=key.key().sym();
                if (symbol==FcitxKey_Shift_L || symbol==FcitxKey_Shift_R) {
                    if (!key.isRelease()) {
                        const bool solo=!key.key().states().testAny(KeyStates{KeyState::Ctrl,KeyState::Alt,KeyState::Super,KeyState::Super2,KeyState::Meta,KeyState::Hyper,KeyState::Mod5});
                        if (solo && state->shiftDown==FcitxKey_None) { state->shiftDown=symbol;state->shiftSolo=true; }
                        if (solo) key.filter(); // 修饰键仍交给应用，字母大写/组合键照常。
                    } else if (state->shiftDown==symbol) {
                        const bool solo=state->shiftSolo;
                        state->shiftDown=FcitxKey_None;state->shiftSolo=false;
                        if (solo) toggleMode(ic);
                        key.filter();
                    }
                    return;
                }
                if (!key.isRelease()) state->shiftSolo=false;
            });
        surroundingWatcher_ = instance_->watchEvent(
            EventType::InputContextSurroundingTextUpdated, EventWatcherPhase::Default,
            [this](Event &event) {
                auto *ic = static_cast<InputContextEvent &>(event).inputContext();
                if (!ic) return;
                const auto &surrounding = ic->surroundingText();
                auto *state = ic->propertyFor(&punctuationFactory_);
                state->language = wetype::TextLanguage::Unknown;
                state->lastDigit = false;
                if (surrounding.isValid() && surrounding.cursor() > 0) {
                    const auto &text = surrounding.text();
                    const auto bytes = utf8::ncharByteLength(text.begin(), surrounding.cursor());
                    state->language = wetype::endingLanguage(std::string_view(text.data(), bytes));
                    state->lastDigit = bytes && text[bytes-1]>='0' && text[bytes-1]<='9';
                }
            });
        const char *xdg = getenv("XDG_DATA_HOME");
        const char *home = getenv("HOME");
        const std::string dataHome = xdg && *xdg ? xdg :
            std::string(home ? home : "/root") + "/.local/share";
        commonPhrases_.setPath(dataHome + "/wetype-ime/common-phrases.bin");
        const std::size_t glossCount = glossary_.load(dataHome + "/wetype-ime/glossary-en.tsv");
        if (glossCount) WLOG("glossary loaded: %zu entries\n", glossCount);
        std::string eng, dicts, work, qemu, sysroot;
        resolveDirs(eng, dicts, work, qemu, sysroot);
        syncDir_ = work + "/../sync-control";
        wetype::mkdirParents(syncDir_);
        chmod(syncDir_.c_str(), 0700);
        syncLock_ = open((syncDir_ + "/lock").c_str(), O_RDWR | O_CREAT | O_CLOEXEC | O_NOFOLLOW, 0600);
        // Fcitx itself may be restarted while a worker holds a checkpoint.
        // Never eagerly reopen a database that is still being handed over.
        if (syncLock_ >= 0) {
            if (flock(syncLock_, LOCK_EX | LOCK_NB) == 0) flock(syncLock_, LOCK_UN);
            else {
                syncToken_ = syncRequest();
                if (!syncToken_.empty()) syncPaused_ = true;
            }
        }
        lastKeyTime_ = now(CLOCK_MONOTONIC);
        if (const char *delay = getenv("WETYPE_SYNC_IDLE_SECONDS"))
            syncIdle_ = std::max(1, atoi(delay)) * 1000000ull;
        syncTimer_ = instance_->eventLoop().addTimeEvent(CLOCK_MONOTONIC,
            now(CLOCK_MONOTONIC) + 100000, 0,
            [this](EventSourceTime *source, uint64_t) {
                syncTick(); source->setNextInterval(100000); source->setEnabled(true); return true;
            });
        WLOG("async addon init: eng=%s\n", eng.c_str());
        // Start eagerly so the first key never waits for the ~1.5 s engine
        // startup, and bring the engine back as soon as it exits.
        eng_.setOnExit([this] { scheduleRestart(); });
        cloudTimer_=instance_->eventLoop().addTimeEvent(CLOCK_MONOTONIC,now(CLOCK_MONOTONIC)+60000,0,
            [this](EventSourceTime *source,uint64_t){cloudTick();source->setNextInterval(60000);source->setEnabled(true);return true;});
        ensureEngine();
    }
    ~WeTypeEngine() override {
        syncTimer_.reset();
        cloudTimer_.reset();
        if (syncLock_ >= 0) close(syncLock_);
        cancelPending();
        restartSource_.reset();
        eng_.stop();
    }

    void keyEvent(const InputMethodEntry &, KeyEvent &event) override { processKey(event); }
    const Configuration *getConfig() const override { return &config_; }
    void setConfig(const RawConfig &raw) override {
        const auto previousScheme = *config_.pinyinScheme;
        const auto previousFuzzy = config_.fuzzyFlags();
        const bool previousPreedit = *config_.showPinyinInInput;
        config_.load(raw, true);
        safeSaveAsIni(config_, "conf/wetype.conf");
        applyEngineConfig(previousScheme, previousFuzzy);
        applyPreeditLocation(previousPreedit);
    }
    void reloadConfig() override {
        const auto previousScheme = *config_.pinyinScheme;
        const auto previousFuzzy = config_.fuzzyFlags();
        const bool previousPreedit = *config_.showPinyinInInput;
        readAsIni(config_, "conf/wetype.conf");
        applyEngineConfig(previousScheme, previousFuzzy);
        applyPreeditLocation(previousPreedit);
    }
    std::string subMode(const InputMethodEntry &,InputContext &ic) override {
        return ic.propertyFor(&modeFactory_)->english?"英文":"中文";
    }
    std::string subModeLabelImpl(const InputMethodEntry &,InputContext &ic) override {
        return ic.propertyFor(&modeFactory_)->english?"英":"中";
    }

    void reset(const InputMethodEntry &entry, InputContextEvent &event) override {
        auto *ic = event.inputContext();
        if (!ic) return;
        auto *mode=ic->propertyFor(&modeFactory_);
        mode->shiftDown=FcitxKey_None;mode->shiftSolo=false;
        bool had = !buf_.empty() || !cands_.empty() || !digitHits_.empty();
        eng_.send("SAVE", nullptr);
        clearAll();
        if (had) updateUI(*ic);
    }

    // 失焦/切换 IM: 彻底清理, 候选框随之消失
    void deactivate(const InputMethodEntry &entry, InputContextEvent &event) override {
        auto *ic = event.inputContext();
        if (!ic) return;
        // Fcitx calls deactivate before changing the active input method. If
        // Shift (or another IM toggle) is pressed mid-composition, preserve
        // the unfinished pinyin as literal text instead of dropping it.
        if (event.type() == EventType::InputContextSwitchInputMethod && !buf_.empty()) {
            WLOG("deactivate commits raw pinyin len=%zu\n", buf_.size());
            const auto raw = pendingRaw();
            rememberLanguage(ic, raw);
            ic->commitString(raw);
        } else if (event.type() == EventType::InputContextSwitchInputMethod) {
            ic->propertyFor(&punctuationFactory_)->language = wetype::TextLanguage::Unknown;
        }
        bool had = !buf_.empty() || !cands_.empty();
        clearAll();
        if (had) updateUI(*ic);
    }

private:
    void applyPreeditLocation(bool previous) {
        if (previous != *config_.showPinyinInInput) {
            if (auto *ic = icRef_.get()) updateUI(*ic);
        }
    }

    void applyEngineConfig(PinyinScheme previousScheme, uint32_t previousFuzzy) {
        const bool schemeChanged = (previousScheme != *config_.pinyinScheme);
        const bool fuzzyChanged = (previousFuzzy != config_.fuzzyFlags());
        if (!schemeChanged && !fuzzyChanged) return;
        // 保存未完成的输入，再启动带新方案的会话；Q 会刷新学习数据。
        auto *ic = icRef_.get();
        if (ic && (!buf_.empty() || pending_)) {
            const auto raw = pendingRaw();
            clearAll();
            commitText(ic, raw);
        } else {
            clearAll(ic);
        }
        restartSource_.reset();
        eng_.stop();
        eng_.setDoublePinSolution(*config_.pinyinScheme == PinyinScheme::Flypy ? 3 : 0);
        eng_.setFuzzyFlags(config_.fuzzyFlags());
        ensureEngine();
    }

    void processKey(KeyEvent &event);
    void toggleMode(InputContext *ic) {
        if (icRef_.get()!=ic) { clearAll();icRef_=ic->watch(); }
        if (pending_) fallbackPending(ic);
        else if (!buf_.empty()) commitText(ic,buf_);
        else clearAll(ic);
        auto *state=ic->propertyFor(&modeFactory_);state->english=!state->english;
        ic->updateUserInterface(UserInterfaceComponent::StatusArea);
    }

    // 每页候选数跟随 fcitx5 全局设置"候选词数量"(1-10), 取不到时回退 5。
    // 翻页步进、面板显示与数字选词都以它为准, 保证每个可见候选都能选中。
    int pageSize() const {
        const int n = instance_->globalConfig().defaultPageSize();
        return (n >= 1 && n <= 10) ? n : FALLBACK_PAGE_SIZE;
    }

    // Global page size can change while composing. Keep both indices valid
    // and align the page before rendering or handling another selection key.
    bool normalizePage() {
        const int oldStart = windowStart_;
        const int oldSelected = selected_;
        const int count = static_cast<int>(cands_.size());
        const int page = pageSize();
        if (count == 0) {
            windowStart_ = selected_ = 0;
        } else {
            windowStart_ = std::clamp(windowStart_, 0, count - 1);
            windowStart_ = (windowStart_ / page) * page;
            selected_ = std::clamp(selected_, windowStart_,
                                  std::min(windowStart_ + page, count) - 1);
        }
        return oldStart != windowStart_ || oldSelected != selected_ ||
               (!cands_.empty() && renderedPageSize_ != page);
    }

    void updateUI(InputContext &ic) {
        normalizePage();
        renderedPageSize_ = pageSize();
        auto &panel = ic.inputPanel();
        const bool hadClientPreedit = !panel.clientPreedit().empty();
        panel.reset();
        // Frontends may commit preedit on focus-out; include all consumed text.
        Text preedit = pending_ ? Text(pendingRaw())
                                : pinyinPreedit(buf_, *config_.pinyinScheme == PinyinScheme::Flypy);
        const bool inInput = *config_.showPinyinInInput &&
                             ic.capabilityFlags().test(CapabilityFlag::Preedit) &&
                             ic.isPreeditEnabled();
        if (inInput) {
            // 与 Rime 相同：预编辑光标放在开头，使候选框锚定本轮输入起点。
            preedit.setCursor(0);
            panel.setClientPreedit(preedit);
        } else {
            panel.setPreedit(preedit);
        }
        if (cands_.empty() && !digitHits_.empty()) {
            // 数字常用语：已输入的数字照常上屏，回车只补全剩余部分。
            panel.setAuxUp(Text("常用语 · 回车补全，Esc 关闭"));
            auto cl = std::make_unique<CommonCandidateList>();
            cl->setLayoutHint(CandidateLayoutHint::Vertical);
            for (size_t i = 0; i < digitHits_.size(); ++i) {
                cl->append<GridColumnCandidate>(Text(digitHits_[i].second),
                    [this, i, revision=revision_](InputContext *context) {
                        if (revision == revision_) commitDigitPhrase(context, i);
                    });
            }
            cl->setPageSize(static_cast<int>(digitHits_.size()));
            cl->setGlobalCursorIndex(digitSelected_);
            panel.setCandidateList(std::move(cl));
        }
        if (!cands_.empty()) {
            const int start = windowStart_;
            const int end = std::min<int>(start + pageSize(), cands_.size());
            const int pageCount = end - start;
            if (selected_ < start || selected_ >= end) selected_ = start;
            auto cl = std::make_unique<CommonCandidateList>();
            cl->setLayoutHint(CandidateLayoutHint::Horizontal);
            cl->setPageSize(pageCount);
            for (int index = start; index < end; ++index) {
                Text candidate;
                // 序号即选词键: 第 10 个显示为 0, 与 fcitx5 的选词习惯一致
                const int ordinal = index - start;
                candidate.append(std::string(1, ordinal == 9 ? '0'
                                        : static_cast<char>('1' + ordinal)) + " ");
                candidate.append(cands_[index]);
                if (const auto *gloss = glossary_.lookup(cands_[index])) {
                    candidate.append(" " + *gloss, TextFormatFlag::Italic);
                }
                cl->append<GridColumnCandidate>(std::move(candidate),
                    [this, index, generation=candidateGeneration_, revision=revision_](InputContext *context) {
                        if(generation==candidateGeneration_ && revision==revision_)commitCandidate(context, index);
                    });
            }
            // The API validates this index immediately against the list
            // size, so set it only after all candidates exist.
            cl->setGlobalCursorIndex(selected_ - start);
            panel.setCandidateList(std::move(cl));
        }
        if (inInput || hadClientPreedit) ic.updatePreedit();
        ic.updateUserInterface(UserInterfaceComponent::InputPanel);
    }

    void rememberLanguage(InputContext *ic, std::string_view text) {
        ic->propertyFor(&punctuationFactory_)->language = wetype::endingLanguage(text);
        ic->propertyFor(&punctuationFactory_)->lastDigit = !text.empty() && text.back()>='0' && text.back()<='9';
    }

    std::string punctuationText(InputContext *ic, KeySym symbol) {
        // 只改变标点，不切换输入语言；组词时仍按原路径上屏当前候选。
        if (*config_.alwaysEnglishPunctuation &&
            ((symbol >= FcitxKey_exclam && symbol <= FcitxKey_slash) ||
             (symbol >= FcitxKey_colon && symbol <= FcitxKey_at) ||
             (symbol >= FcitxKey_bracketleft && symbol <= FcitxKey_grave) ||
             (symbol >= FcitxKey_braceleft && symbol <= FcitxKey_asciitilde))) {
            return std::string(1, static_cast<char>(symbol));
        }
        if (punctuationLanguage(ic)!=wetype::TextLanguage::Cjk) return {};
        auto *state=ic->propertyFor(&punctuationFactory_);
        switch (symbol) {
        case FcitxKey_comma: return "，";
        case FcitxKey_period: return buf_.empty() && state->lastDigit ? std::string{} : "。";
        case FcitxKey_semicolon: return "；";
        case FcitxKey_colon: return "：";
        case FcitxKey_exclam: return "！";
        case FcitxKey_question: return "？";
        case FcitxKey_parenleft: return "（";
        case FcitxKey_parenright: return "）";
        case FcitxKey_bracketleft: return "【";
        case FcitxKey_bracketright: return "】";
        case FcitxKey_braceleft: return "｛";
        case FcitxKey_braceright: return "｝";
        case FcitxKey_less: return "《";
        case FcitxKey_greater: return "》";
        case FcitxKey_backslash: return "、";
        case FcitxKey_asciitilde: return "～";
        case FcitxKey_asciicircum: return "……";
        case FcitxKey_underscore: return "——";
        case FcitxKey_dollar: return "￥";
        case FcitxKey_grave: return "·";
        case FcitxKey_quotedbl: state->doubleQuote=!state->doubleQuote; return state->doubleQuote?"“":"”";
        case FcitxKey_apostrophe: state->singleQuote=!state->singleQuote; return state->singleQuote?"‘":"’";
        default: return {};
        }
    }

    wetype::TextLanguage punctuationLanguage(InputContext *ic, std::string_view text = {}) {
        // Structured input fields must retain ASCII separators.
        if (ic->capabilityFlags().testAny(CapabilityFlags{
                CapabilityFlag::Password, CapabilityFlag::Email, CapabilityFlag::Url,
                CapabilityFlag::Digit, CapabilityFlag::Number, CapabilityFlag::Dialable})) {
            return wetype::TextLanguage::Latin;
        }
        // 中文模式默认中文标点；仅数字紧邻的小数点保留 ASCII。
        // 中英切换由 Fcitx 负责，英文模式不会进入此引擎。
        return wetype::TextLanguage::Cjk;
    }

    void commitText(InputContext *ic, const std::string &text) {
        WLOG("commit len=%zu revision=%llu\n", text.size(),
             static_cast<unsigned long long>(revision_));
        rememberLanguage(ic, text);
        ic->commitString(text);
        ++revision_;
        buf_.clear();
        cands_.clear();
        covers_.clear();
        candidatesCurrent_ = false;
        windowStart_ = 0;
        selected_ = 0;
        recoveryTried_ = false;
        eng_.send("C", nullptr);          // 重建会话
        updateUI(*ic);
    }

    void commitCandidate(InputContext *ic, int index) {
        if (!candidatesCurrent_ || index < 0 || index >= static_cast<int>(cands_.size())) return;
        if ((index < static_cast<int>(engineIndices_.size()) && engineIndices_[index] < 0)) {
            commitText(ic, cands_[index]); return;
        }
        const int cover = index < static_cast<int>(covers_.size()) ? covers_[index] : 0;
        WLOG("commit candidate index=%d count=%zu cover=%d buffer_len=%zu revision=%llu\n", index,
             cands_.size(), cover, buf_.size(), static_cast<unsigned long long>(revision_));
        if (cover <= 0 || static_cast<size_t>(cover) >= buf_.size()) {
            eng_.send(selectionCommand(index), nullptr);
            commitText(ic, cands_[index]);
            return;
        }
        // The candidate covers only a prefix (e.g. 你好 of nihaoshijie): commit
        // it and keep composing the rest. The engine answers S with the
        // candidates for the remaining pinyin.
        rememberLanguage(ic, cands_[index]);
        ic->commitString(cands_[index]);
        ++revision_;
        buf_.erase(0, cover);
        cands_.clear();
        covers_.clear();
        candidatesCurrent_ = false;
        windowStart_ = 0;
        selected_ = 0;
        eng_.send(selectionCommand(index), candidateHandler());
        updateUI(*ic);
    }

    void scheduleRestart() {
        if (restartSource_ || eng_.gaveUp()) return;
        restartSource_ = instance_->eventLoop().addTimeEvent(
            CLOCK_MONOTONIC, now(CLOCK_MONOTONIC) + ENGINE_RESTART_DELAY_USEC, 0,
            [this](EventSourceTime *, uint64_t) {
                // 回调还在执行，保持事件源存活到返回，避免释放捕获 this 的闭包。
                auto keepAlive = std::move(restartSource_);
                if (eng_.alive()) return true;
                WLOG("restarting engine after exit buffer_len=%zu\n", buf_.size());
                ensureEngine();
                // The new session is empty: replay the unfinished pinyin.
                requestCandidates(buf_);
                return true;
            });
    }

    void ensureEngine() {
        if (syncPaused_) return;
        if (eng_.alive() || eng_.gaveUp()) return;
        spansEnabled_ = false;
        tokensEnabled_ = false;
        if (!eng_.start()) return;
        // Ask for per-candidate input spans; older harnesses answer ERR and
        // keep whole-buffer selection.
        eng_.send("OPT spans", [this](const std::string &resp) {
            spansEnabled_ = resp == "OK";
            WLOG("candidate spans %s\n", spansEnabled_ ? "enabled" : "unavailable");
        });
        eng_.send("OPT tokens",[this](const std::string &resp){tokensEnabled_=resp=="OK";});
    }

    // Handles a CAND/EMPTY reply for the buffer as it is now. A reply for an
    // earlier prefix of the pinyin still being typed is shown as a
    // non-selectable preview so fast typing keeps refreshing the panel; other
    // stale replies are ignored.
    // 输入带大写字母时按英文处理：引擎返回的英文候选一律小写（词库里的 BevFusion 会给出
    // bevfusion），把已输入部分的大小写套上去并排到最前；没有英文候选时原样输入排第一。
    void applyTypedCase(const std::string &typed) {
        if (std::none_of(typed.begin(), typed.end(), [](char c) { return c >= 'A' && c <= 'Z'; })) return;
        const auto lower = [](char c) { return c >= 'A' && c <= 'Z' ? static_cast<char>(c + 32) : c; };
        std::vector<size_t> english;
        for (size_t i = 0; i < cands_.size(); ++i) {
            auto &text = cands_[i];
            if (covers_[i] > 0 && static_cast<size_t>(covers_[i]) < typed.size()) continue;
            if (text.size() < typed.size() || !std::all_of(text.begin(), text.end(), [](char c) {
                    return (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z'); })) continue;
            if (!std::equal(typed.begin(), typed.end(), text.begin(),
                            [&](char a, char b) { return lower(a) == lower(b); })) continue;
            std::copy(typed.begin(), typed.end(), text.begin());
            english.push_back(i);
        }
        std::vector<std::string> texts; std::vector<int> covers, indices;
        if (english.empty()) { texts.push_back(typed); covers.push_back(static_cast<int>(typed.size())); indices.push_back(-1); }
        for (int pass = 0; pass < 2; ++pass)
            for (size_t i = 0; i < cands_.size(); ++i) {
                if ((std::find(english.begin(), english.end(), i) != english.end()) != (pass == 0)) continue;
                texts.push_back(cands_[i]); covers.push_back(covers_[i]); indices.push_back(engineIndices_[i]);
            }
        cands_ = std::move(texts); covers_ = std::move(covers); engineIndices_ = std::move(indices);
        for (size_t i = cands_.size(); i-- > 1;) {
            if (std::find(cands_.begin(), cands_.begin() + i, cands_[i]) == cands_.begin() + i) continue;
            cands_.erase(cands_.begin() + i); covers_.erase(covers_.begin() + i);
            engineIndices_.erase(engineIndices_.begin() + i);
        }
    }

    EngineProc::Handler candidateHandler() {
        auto ref = icRef_;
        const auto expectedEpoch = epoch_;
        const auto expectedBuf = buf_;
        const auto expectedRevision = revision_;
        return [this, ref, expectedEpoch, expectedBuf, expectedRevision](const std::string &resp) {
            fcitx::InputContext *ic = ref.get();
            WLOG("response len=%zu prefix=%.4s expected_revision=%llu current_revision=%llu valid_ic=%d\n",
                 resp.size(), resp.c_str(), static_cast<unsigned long long>(expectedRevision),
                 static_cast<unsigned long long>(revision_), ic ? 1 : 0);
            if (!ic || ic != icRef_.get() || expectedEpoch != epoch_ || resp == "SKIP") return;   // SKIP: merged into a later request
            const bool current = expectedRevision == revision_ && expectedBuf == buf_;
            const bool preview = !current && !expectedBuf.empty() &&
                                 buf_.size() > expectedBuf.size() &&
                                 buf_.compare(0, expectedBuf.size(), expectedBuf) == 0 &&
                                 resp.rfind("CAND\t", 0) == 0;
            if (!current && !preview) return;
            if (resp.empty()) {
                // Keep the last visible page while recovering; a transient
                // missing response must not collapse the candidate panel.
                updateUI(*ic);
                if (!buf_.empty() && !recoveryTried_) {
                    recoveryTried_ = true;
                    WLOG("engine response lost; restarting and replaying buffer_len=%zu revision=%llu\n",
                         buf_.size(), static_cast<unsigned long long>(revision_));
                    ensureEngine();
                    requestCandidates(buf_);
                }
                return;
            }
            if (resp != "EMPTY" && resp.rfind("CAND\t", 0) != 0) {
                // A partial selection the engine did not keep composing
                // (OK/ERR): rebuild the session from the remaining pinyin.
                WLOG("unexpected candidate reply prefix=%.4s; replaying buffer_len=%zu\n",
                     resp.c_str(), buf_.size());
                if (recoveryTried_) {
                    if (pending_) fallbackPending(ic);
                    return;
                }
                recoveryTried_ = true;
                eng_.send("C", nullptr);
                requestCandidates(buf_);
                return;
            }
            cands_.clear();
            covers_.clear();
            engineIndices_.clear();
            if (resp.rfind("CAND\t", 0) == 0) {
                std::stringstream ss(resp.substr(5));
                std::string item;
                while (std::getline(ss, item, '\t')) {
                    if(!item.empty() && item[0]=='@') {candidateGeneration_=std::strtoull(item.c_str()+1,nullptr,10);continue;}
                    int cover = 0;
                    if (spansEnabled_) {
                        const auto colon = item.find(':');
                        if (colon == std::string::npos) continue;
                        cover = std::atoi(item.c_str());
                        item.erase(0, colon + 1);
                    }
                    if (item.empty()) continue;
                    cands_.push_back(item);
                    covers_.push_back(cover);
                    engineIndices_.push_back(static_cast<int>(engineIndices_.size()));
                }
            }
            applyTypedCase(expectedBuf);
            if (current) {
                auto phrases = commonPhrases_.automaticLookup(buf_);
                // 常用语不交给普通词库学习；保留每个原生候选的真实序号。
                for (auto it=phrases.rbegin(); it!=phrases.rend(); ++it) {
                    for (size_t i=cands_.size(); i-- > 0;) if (cands_[i] == *it) {
                        cands_.erase(cands_.begin()+i); covers_.erase(covers_.begin()+i);
                        engineIndices_.erase(engineIndices_.begin()+i);
                    }
                    cands_.insert(cands_.begin(),*it); covers_.insert(covers_.begin(),static_cast<int>(buf_.size()));
                    engineIndices_.insert(engineIndices_.begin(),-1);
                }
            }
            candidatesCurrent_ = current;
            WLOG("parsed candidates=%zu buffer_len=%zu preview=%d\n", cands_.size(), buf_.size(),
                 preview ? 1 : 0);
            windowStart_ = 0;
            selected_ = 0;
            updateUI(*ic);
            if (current && pending_) {
                if (cands_.empty()) fallbackPending(ic);
                else {
                    disarmPending();
                    commitCandidate(ic, selected_);
                    drainQueued(ic);
                }
            }
        };
    }

    // Sends keys right away; the engine appends them to the current session.
    void requestCandidates(const std::string &keys) {
        if (buf_.empty() || keys.empty()) return;
        ensureEngine();
        preparePhraseCodes();
        WLOG("send keys chars=%zu buffer_len=%zu revision=%llu\n", keys.size(),
             buf_.size(), static_cast<unsigned long long>(revision_));
        // 引擎按小写拼音检索（大写会被当作新音节开头，补全失效），大小写由 applyTypedCase 还原。
        std::string lowered = keys;
        for (auto &c : lowered) if (c >= 'A' && c <= 'Z') c = static_cast<char>(c + 32);
        eng_.send("B " + lowered, candidateHandler());
    }

    struct QueuedKey {
        Key key, raw;
        bool release;
        int time;
    };
    bool pending_ = false;
    std::deque<QueuedKey> queued_;
    std::unique_ptr<EventSourceTime> pendingTimer_;
    bool draining_ = false;
    uint64_t epoch_ = 0;

    static std::string printableText(Key key) {
        const auto codepoint = Key::keySymToUnicode(key.sym());
        if (codepoint < 0x20 || (codepoint >= 0x7f && codepoint <= 0x9f)) return {};
        return Key::keySymToUTF8(key.sym());
    }

    static bool printable(Key key) { return !printableText(key).empty(); }

    std::string pendingRaw() const {
        std::string raw = buf_;
        if (pending_) raw += ' ';
        for (const auto &key : queued_) {
            if (!key.release) raw += printableText(key.key);
        }
        return raw;
    }

    void disarmPending() {
        pending_ = false;
        pendingTimer_.reset();
    }

    void cancelPending() {
        disarmPending();
        queued_.clear();
    }

    void fallbackPending(InputContext *ic) {
        const auto raw = pendingRaw();
        auto keys = std::move(queued_);
        cancelPending();
        commitText(ic, raw);
        for (const auto &key : keys) {
            if (key.release) ic->forwardKey(key.raw, true, key.time);
        }
    }

    void armPending(InputContext *ic) {
        pending_ = true;
        auto ref = ic->watch();
        const auto epoch = epoch_;
        // Finite wait covers SKIP, hung engines, and exhausted restarts.
        pendingTimer_ = instance_->eventLoop().addTimeEvent(
            CLOCK_MONOTONIC, now(CLOCK_MONOTONIC) + 5000000, 0,
            [this, ref, epoch](EventSourceTime *, uint64_t) {
                if (auto *owner = ref.get(); owner && owner == icRef_.get() &&
                    epoch == epoch_ && pending_) fallbackPending(owner);
                return true;
            });
        updateUI(*ic);
        if ((candidatesCurrent_ && cands_.empty()) || eng_.gaveUp()) fallbackPending(ic);
    }

    void drainQueued(InputContext *ic) {
        if (draining_) return;
        draining_ = true;
        while (!pending_ && !queued_.empty() && icRef_.get() == ic) {
            const auto key = queued_.front();
            queued_.pop_front();
            KeyEvent event(ic, key.raw, key.release, key.time);
            event.setKey(key.key);
            processKey(event);
            if (!event.filtered()) ic->forwardKey(key.raw, key.release, key.time);
        }
        draining_ = false;
    }

    Instance *instance_;
    SimpleInputContextPropertyFactory<PunctuationState> punctuationFactory_;
    SimpleInputContextPropertyFactory<ModeState> modeFactory_;
    std::unique_ptr<HandlerTableEntry<EventHandler>> modeWatcher_;
    std::unique_ptr<HandlerTableEntry<EventHandler>> surroundingWatcher_;
    wetype::Glossary glossary_;
    wetype::CommonPhrases commonPhrases_;
    std::string syncDir_, syncToken_;
    int syncLock_ = -1;
    bool syncPaused_ = false, syncReplay_ = false, syncSaved_ = false;
    uint64_t lastKeyTime_ = 0, syncIdle_ = 20000000;
    std::unique_ptr<EventSourceTime> syncTimer_;
    struct SyncKey {
        TrackableObjectReference<InputContext> context;
        Key raw, key; bool release; int time;
    };
    std::deque<SyncKey> syncKeys_;
    std::string syncRequest() {
        std::ifstream request(syncDir_ + "/request"); std::string token; std::getline(request, token);
        if (token.size() != 32 || token.find_first_not_of("0123456789abcdef") != std::string::npos) return {};
        return token;
    }
    void syncAcknowledge(const std::string &token) {
        syncToken_ = token;
        std::ofstream ack(syncDir_ + "/ack.pending"); ack << token << (syncSaved_ ? " OK\n" : " FAILED\n"); ack.close();
        chmod((syncDir_ + "/ack.pending").c_str(), 0600);
        rename((syncDir_ + "/ack.pending").c_str(), (syncDir_ + "/ack").c_str());
    }
    void syncTick() {
        if (syncLock_ < 0) return;
        bool worker = flock(syncLock_, LOCK_EX | LOCK_NB) != 0;
        if (!worker) flock(syncLock_, LOCK_UN);
        if (syncPaused_) {
            if (!worker) {
                syncPaused_ = false; ensureEngine();
                unlink((syncDir_ + "/ack").c_str());
            } else {
                const auto token = syncRequest();
                if (!token.empty() && token != syncToken_) syncAcknowledge(token);
            }
        } else if (worker && eng_.idle() && !pending_ && buf_.empty() &&
                   now(CLOCK_MONOTONIC) - lastKeyTime_ >= syncIdle_) {
            const auto token = syncRequest(); if (token.empty()) return;
            syncPaused_ = true; restartSource_.reset();
            syncSaved_ = eng_.stop(); syncAcknowledge(token);
            WLOG("sync checkpoint graceful=%d\n", syncSaved_ ? 1 : 0);
        }
        if (!syncPaused_ && (eng_.ready() || eng_.gaveUp()) && !syncKeys_.empty()) {
            syncReplay_ = true;
            while (!syncKeys_.empty()) {
                auto key = std::move(syncKeys_.front()); syncKeys_.pop_front();
                if (auto *ic = key.context.get()) {
                    KeyEvent event(ic, key.raw, key.release, key.time); event.setKey(key.key);
                    processKey(event);
                    if (!event.filtered()) {
                        ic->forwardKey(key.raw, key.release, key.time);
                        // GTK handles forwarded editing keys asynchronously and may
                        // reset its input context. Let that finish before replaying text.
                        if (!key.release) break;
                    }
                }
            }
            syncReplay_ = false;
        }
    }

    void preparePhraseCodes() {
        commonPhrases_.reload();
        const auto version=commonPhrases_.revision();
        if (version == phraseCodesVersion_) return;
        phraseCodesVersion_=version;
        static constexpr char hex[]="0123456789abcdef";
        const auto &entries=commonPhrases_.entries();
        for (size_t i=0; i<entries.size(); ++i) {
            if (!entries[i].first.empty()) continue;
            // 只需开头文字即可取得前三字推荐码；限制单次 pipe 写入大小。
            const auto &text=entries[i].second;
            size_t count=std::min<size_t>(text.size(),768);
            while (count<text.size() && count && (static_cast<unsigned char>(text[count]) & 0xc0)==0x80) --count;
            std::string command="HK ";
            for (size_t j=0;j<count;++j) { const auto c=static_cast<unsigned char>(text[j]); command+=hex[c>>4];command+=hex[c&15]; }
            eng_.send(command,[this,i,version](const std::string &response) {
                if (response.rfind("HK ",0)!=0) { phraseCodesVersion_=0; return; }
                std::string code;
                for (size_t j=3;j+1<response.size();j+=2) {
                    char *end=nullptr;const auto pair=response.substr(j,2);
                    const auto c=std::strtol(pair.c_str(),&end,16);
                    if (*end || c<1 || c>127) return;
                    code+=static_cast<char>(c);
                }
                commonPhrases_.setRecommendation(i,version,std::move(code));
            });
        }
    }
    EngineProc eng_;
    std::string buf_;
    WeTypeConfig config_;
    std::unique_ptr<EventSourceTime> restartSource_;
    std::vector<std::string> cands_;
    std::vector<int> covers_;     // pinyin letters each candidate consumes (0 = unknown)
    std::vector<int> engineIndices_; // -1 为宿主只读常用语，其余为原生候选序号
    uint64_t phraseCodesVersion_=0;
    bool spansEnabled_ = false;
    bool tokensEnabled_ = false;
    uint64_t candidateGeneration_=0,cloudAppliedRevision_=0;
    std::unique_ptr<EventSourceTime> cloudTimer_;
    std::string selectionCommand(int index) const {
        const int native=index<static_cast<int>(engineIndices_.size())?engineIndices_[index]:index;
        return "S "+std::to_string(native)+(tokensEnabled_?" "+std::to_string(candidateGeneration_):"");
    }
    void cloudTick() {
        auto *ic=icRef_.get();
        if(!ic || !eng_.cloudConfigured() || !tokensEnabled_ || !eng_.idle() || syncPaused_ || pending_ ||
            buf_.size()<4 || !candidatesCurrent_ || cloudAppliedRevision_==revision_ ||
            windowStart_ || selected_ || now(CLOCK_MONOTONIC)-lastKeyTime_>2500000)return;
        auto ref=icRef_;const auto revision=revision_,epoch=epoch_;auto handler=candidateHandler();
        eng_.send("R",[this,ref,revision,epoch,handler](const std::string &resp){
            if(resp=="UNCHANGED" || resp.empty() || ref.get()!=icRef_.get() || revision!=revision_ || epoch!=epoch_ || buf_.empty() || windowStart_ || selected_)return;
            if(resp.rfind("CAND\t",0)==0){cloudAppliedRevision_=revision;handler(resp);}
        });
    }
    // Retained candidates remain visible during composition updates, but are
    // not selectable until a result for the current buffer arrives.
    bool candidatesCurrent_ = false;
    int windowStart_ = 0;
    int selected_ = 0;
    int renderedPageSize_ = 0;
    bool recoveryTried_ = false;
    uint64_t revision_ = 0;
    TrackableObjectReference<InputContext> icRef_;

    // 数字常用语：空闲时连续输入的数字（已上屏）及命中的 (需删除的已输入位数, 常用语全文)。
    std::string digitRun_;
    std::vector<std::pair<size_t, std::string>> digitHits_;
    int digitSelected_ = 0;

    void resetDigitRun(InputContext *ic) {
        const bool shown = !digitHits_.empty();
        digitRun_.clear(); digitHits_.clear(); digitSelected_ = 0;
        if (shown && ic) updateUI(*ic);
    }
    void updateDigitPhrases(InputContext *ic) {
        const bool surrounding = ic->capabilityFlags().test(CapabilityFlag::SurroundingText);
        digitHits_.clear(); digitSelected_ = 0;
        for (auto &hit : commonPhrases_.digitLookup(digitRun_)) {
            // 内容以已输入数字开头时只补后半段；否则需要应用支持删除光标前文字。
            if (hit.first == 0 && !surrounding) continue;
            digitHits_.push_back(std::move(hit));
        }
        updateUI(*ic);
    }
    void commitDigitPhrase(InputContext *ic, size_t index) {
        if (index >= digitHits_.size()) return;
        const auto [keep, text] = digitHits_[index];
        const auto typed = digitRun_.size();
        resetDigitRun(ic);
        if (keep == typed) ic->commitString(text.substr(typed));
        else { ic->deleteSurroundingText(-static_cast<int>(typed), typed); ic->commitString(text); }
        rememberLanguage(ic, text);
        ++revision_;
    }
    // 返回 true 表示按键已被数字常用语消费。
    bool digitPhraseKey(KeyEvent &event, InputContext *ic) {
        const auto sym = event.key().sym();
        if (event.isRelease()) return false;
        const bool plain = !event.key().states().testAny(KeyStates{KeyState::Ctrl, KeyState::Alt,
            KeyState::Super, KeyState::Super2, KeyState::Meta, KeyState::Hyper, KeyState::Hyper2, KeyState::Mod5});
        if (!buf_.empty() || pending_) { resetDigitRun(ic); return false; }
        if (!digitHits_.empty() && plain) {
            if (sym == FcitxKey_Return || sym == FcitxKey_KP_Enter) {
                commitDigitPhrase(ic, digitSelected_); event.filterAndAccept(); return true;
            }
            if (sym == FcitxKey_Escape) { resetDigitRun(ic); event.filterAndAccept(); return true; }
            if ((sym == FcitxKey_Up || sym == FcitxKey_Down) && digitHits_.size() > 1) {
                const int n = static_cast<int>(digitHits_.size());
                digitSelected_ = (digitSelected_ + n + (sym == FcitxKey_Down ? 1 : -1)) % n;
                updateUI(*ic); event.filterAndAccept(); return true;
            }
        }
        int digit = -1;
        if (sym >= FcitxKey_0 && sym <= FcitxKey_9) digit = sym - FcitxKey_0;
        else if (sym >= FcitxKey_KP_0 && sym <= FcitxKey_KP_9) digit = sym - FcitxKey_KP_0;
        if (plain && digit >= 0) {
            if (digitRun_.size() < 64) digitRun_ += static_cast<char>('0' + digit);
            updateDigitPhrases(ic);
            return false;   // 数字本身照常交给应用
        }
        if (Key(sym).isModifier()) return false;
        resetDigitRun(ic);
        return false;
    }

    void clearAll(InputContext *ic = nullptr) {
        digitRun_.clear(); digitHits_.clear(); digitSelected_ = 0;
        cancelPending();
        ++epoch_;
        ++revision_;
        buf_.clear();
        cands_.clear();
        covers_.clear();
        candidatesCurrent_ = false;
        windowStart_ = 0;
        selected_ = 0;
        recoveryTried_ = false;
        eng_.send("C", nullptr);
        if (ic) updateUI(*ic);
    }

};

void WeTypeEngine::processKey(KeyEvent &event) {
    auto ic = event.inputContext();
    if (ic->propertyFor(&modeFactory_)->english) return;
    if(ic->capabilityFlags().test(CapabilityFlag::Password)) {
        if(!buf_.empty())clearAll(ic);
        return;
    }
    lastKeyTime_ = now(CLOCK_MONOTONIC);
    if (!syncReplay_ && (syncPaused_ || !syncKeys_.empty())) {
        syncKeys_.push_back({ic->watch(), event.rawKey(), event.key(), event.isRelease(), event.time()});
        event.filterAndAccept(); return;
    }
    WLOG("keyEvent sym=%d ready=%d\n", (int)event.key().sym(), eng_.ready() ? 1 : 0);
    const auto sym = event.key().sym();
    bool handled = false;
    if (icRef_.get() != ic) clearAll();
    icRef_ = ic->watch();
    if (digitPhraseKey(event, ic)) return;

    if (pending_) {
        const bool shortcut = event.key().states().testAny(KeyStates{
            KeyState::Ctrl, KeyState::Alt, KeyState::Super, KeyState::Super2,
            KeyState::Meta, KeyState::Hyper, KeyState::Hyper2, KeyState::Mod5});
        if (!event.isRelease() && !shortcut && sym == FcitxKey_Escape) {
            clearAll(ic);
            event.filterAndAccept();
            return;
        }
        if (!event.isRelease() && !shortcut && sym == FcitxKey_BackSpace) {
            // Delete the latest deferred press and any trailing release first.
            auto last = std::find_if(queued_.rbegin(), queued_.rend(),
                [](const QueuedKey &key) { return !key.release; });
            if (last != queued_.rend()) {
                auto press = std::prev(last.base());
                const auto raw = press->raw;
                auto next = queued_.erase(press);
                auto release = std::find_if(next, queued_.end(), [&raw](const QueuedKey &key) {
                    return key.release && key.raw.sym() == raw.sym();
                });
                if (release != queued_.end()) queued_.erase(release);
                updateUI(*ic);
                event.filterAndAccept();
                return;
            }
            // The pending Space remains an intent after editing the frozen
            // pinyin. Old replies are invalidated by the new revision.
            buf_.pop_back();
            ++revision_;
            candidatesCurrent_ = false;
            cands_.clear();
            covers_.clear();
            eng_.send("C", nullptr);
            if (buf_.empty()) fallbackPending(ic);
            else {
                requestCandidates(buf_);
                updateUI(*ic);
            }
            event.filterAndAccept();
            return;
        } else if (!event.isRelease() && (!printable(event.key()) || shortcut)) {
            // Navigation and shortcuts must not overtake consumed text.
            fallbackPending(ic);
            if (!shortcut && sym == FcitxKey_Return) event.filterAndAccept();
            return;
        } else {
            queued_.push_back({event.key(), event.rawKey(), event.isRelease(), event.time()});
            event.filterAndAccept();
            if (queued_.size() >= 256) fallbackPending(ic);
            else updateUI(*ic);
            return;
        }
    }

    if (event.key().states().testAny(KeyStates{KeyState::Ctrl, KeyState::Alt,
                                               KeyState::Super, KeyState::Super2,
                                               KeyState::Meta, KeyState::Hyper,
                                               KeyState::Hyper2, KeyState::Mod5})) {
        return;
    }

    if (!event.isRelease()) {
        if (normalizePage()) updateUI(*ic);
        // Digits passed through without surrounding-text support still make
        // decimal punctuation ASCII rather than inheriting a Chinese commit.
        if (buf_.empty() && sym >= FcitxKey_0 && sym <= FcitxKey_9) {
            ic->propertyFor(&punctuationFactory_)->language = wetype::TextLanguage::Latin;
            ic->propertyFor(&punctuationFactory_)->lastDigit = true;
        }
        // 字母保留大小写送给引擎（如 BevFusion 可匹配词库里的英文词）。
        // Ctrl/Alt/Super/Meta 组合键已在上面放行。
        if ((sym >= FcitxKey_a && sym <= FcitxKey_z) ||
            (sym >= FcitxKey_A && sym <= FcitxKey_Z)) {
            if (buf_.empty()) recoveryTried_ = false;
            const bool replayBuffer = !eng_.alive() && !buf_.empty();
            char c = (sym >= FcitxKey_a && sym <= FcitxKey_z)
                         ? static_cast<char>('a' + (sym - FcitxKey_a))
                         : static_cast<char>('A' + (sym - FcitxKey_A));
            ensureEngine();
            ++revision_;
            buf_ += c;
            candidatesCurrent_ = false;
            WLOG("typed alpha buffer_len=%zu revision=%llu\n",
                 buf_.size(), static_cast<unsigned long long>(revision_));
            handled = true;
            // Refresh the preedit immediately while keeping the last
            // candidate page visible until the new engine response arrives.
            updateUI(*ic);
            requestCandidates(replayBuffer ? buf_ : std::string(1, c));
        }
        // 方向键左右: 在当前页候选内移动
        else if (!buf_.empty() && candidatesCurrent_ && !cands_.empty() &&
                 (sym == FcitxKey_Left || sym == FcitxKey_Right)) {
            const int page = pageSize();
            if (sym == FcitxKey_Left && selected_ > windowStart_) {
                --selected_;
            } else if (sym == FcitxKey_Right &&
                       selected_ + 1 < std::min<int>(windowStart_ + page,
                                                     cands_.size())) {
                ++selected_;
            }
            // Composition navigation remains consumed at page boundaries.
            updateUI(*ic);
            handled = true;
        }
        // 翻上一页: 按配置的按键列表 (默认 -、↑、PgUp 等)。组词状态下这些键只用于翻页,
        // 即使已到边界也要吞掉, 否则按键会漏进目标文档 (issue #5)。
        else if (!buf_.empty() && event.key().checkKeyList(*config_.prevPage)) {
            if (windowStart_ > 0) {
                windowStart_ = std::max(0, windowStart_ - pageSize());
                selected_ = windowStart_;
                updateUI(*ic);
            }
            handled = true;
        }
        // 翻下一页: 按配置的按键列表 (默认 =、+、KP_Add、↓、PgDn 等)。
        else if (!buf_.empty() && event.key().checkKeyList(*config_.nextPage)) {
            if (candidatesCurrent_ &&
                windowStart_ + pageSize() < (int)cands_.size()) {
                windowStart_ += pageSize();
                selected_ = windowStart_;
                updateUI(*ic);
            }
            handled = true;
        }
        else if (const auto symbol = punctuationText(ic,sym); !symbol.empty()) {
            if (!buf_.empty()) {
                const std::string text = !candidatesCurrent_ || cands_.empty() ? buf_ : cands_[selected_];
                if (candidatesCurrent_ && !cands_.empty() &&
                    (selected_>=static_cast<int>(engineIndices_.size()) || engineIndices_[selected_]>=0))
                    eng_.send(selectionCommand(selected_), nullptr);
                commitText(ic, text + symbol);
                handled = true;
            } else {
                ic->commitString(symbol);
                handled = true;
            }
        }
        // 数字选词(全局序号 = 页首 + 数字, 0 选第 10 个)
        else if ((sym >= FcitxKey_1 && sym <= FcitxKey_9) ||
                 (sym == FcitxKey_0 && pageSize() >= 10)) {
            const int ordinal = (sym == FcitxKey_0) ? 9
                              : static_cast<int>(sym - FcitxKey_1);
            const int idx = windowStart_ + ordinal;
            if (ordinal < pageSize() && candidatesCurrent_ && !cands_.empty() &&
                idx < (int)cands_.size()) {
                commitCandidate(ic, idx);
                handled = true;
            }
        }
        // 空格: 上屏首选(或原文)
        else if (sym == FcitxKey_space) {
            if (!buf_.empty()) {
                if (candidatesCurrent_ && !cands_.empty()) commitCandidate(ic, selected_);   // 含部分选词与词库学习
                else armPending(ic);
                handled = true;
            }
        }
        // Enter commits the in-progress pinyin literally as Latin text.
        else if (sym == FcitxKey_Return) {
            if (!buf_.empty()) {
                WLOG("enter commits raw pinyin len=%zu\n", buf_.size());
                commitText(ic, buf_);
                handled = true;
            }
        }
        // 退格: C 重建 + 前缀重放
        else if (sym == FcitxKey_BackSpace) {
            if (!buf_.empty()) {
                buf_.pop_back();
                ++revision_;
                candidatesCurrent_ = false;
                handled = true;
                eng_.send("C", nullptr);
                if (buf_.empty()) {
                    cands_.clear();
                    covers_.clear();
                    candidatesCurrent_ = false;
                    windowStart_ = 0;
                    selected_ = 0;
                    updateUI(*ic);
                } else {
                    requestCandidates(buf_);
                    updateUI(*ic);
                }
            }
        }
        // Esc: 清空
        else if (sym == FcitxKey_Escape) {
            if (!buf_.empty()) {
                clearAll(ic);
                handled = true;
            }
        }
    }

    if (handled) event.filterAndAccept();
}

class WeTypeEngineFactory : public AddonFactory {
public:
    AddonInstance *create(AddonManager *manager) override {
        return new WeTypeEngine(manager->instance());
    }
};

}  // namespace fcitx

FCITX_ADDON_FACTORY(fcitx::WeTypeEngineFactory);
