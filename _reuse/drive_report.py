"""驱动 PlateReview.exe 跑通完整链路并验证报告产出。

 1. 在入口页输入初成果目录（真实 EDIT，直接 SetWindowTextW + 发 EN_CHANGE）
 2. 点「是」确认该案例
 3. 点「完成报告」
 4. 检查 人工审核结果/ 下的 CSV 与 HTML 报告

注意：会点「完成报告」，其成功路径只弹一个 MessageBox。
MessageBox 会阻塞 UI 线程，因此这里用一个后台线程定期找对话框并关掉它
（否则脚本会一直等下去——这个坑之前踩过）。
"""
import ctypes
import ctypes.wintypes as wt
import os
import subprocess
import sys
import threading
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

u32 = ctypes.WinDLL("user32", use_last_error=True)
k32 = ctypes.WinDLL("kernel32", use_last_error=True)
u32.SetProcessDPIAware()

EXE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dist", "PlateReview.exe")
TDIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "downloads", "xiangtang")
WM_CHAR = 0x0102
WM_COMMAND = 0x0111
BM_CLICK = 0x00F5
WM_SETTEXT = 0x000C

stop = threading.Event()


def find(pid, cls):
    hits = []

    def cb(h, l):
        p = wt.DWORD()
        u32.GetWindowThreadProcessId(h, ctypes.byref(p))
        if p.value != pid:
            return True
        c = ctypes.create_unicode_buffer(128)
        u32.GetClassNameW(h, c, 128)
        if c.value == cls:
            hits.append(h)
        return True
    u32.EnumWindows(ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)(cb), 0)
    return max(hits) if hits else None


def kids(h, cls=None):
    out = []

    def cb(c, l):
        cl = ctypes.create_unicode_buffer(64)
        u32.GetClassNameW(c, cl, 64)
        if cls is None or cl.value == cls:
            out.append(c)
        return True
    u32.EnumChildWindows(h, ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)(cb), 0)
    return out


def text_of(h):
    n = u32.GetWindowTextLengthW(h)
    b = ctypes.create_unicode_buffer(n + 2)
    u32.GetWindowTextW(h, b, n + 2)
    return b.value


def click_at(h, x, y):
    """把点击送到窗口客户区坐标 (x,y)。"""
    lp = ((y & 0xFFFF) << 16) | (x & 0xFFFF)
    u32.SendMessageW(h, 0x0201, 1, lp)      # WM_LBUTTONDOWN
    u32.SendMessageW(h, 0x0202, 0, lp)      # WM_LBUTTONUP


def dismiss_dialogs(pid, label):
    """后台线程：发现模态对话框就按回车关掉，避免脚本永久阻塞。"""
    while not stop.is_set():
        found = []

        def cb(h, l):
            p = wt.DWORD()
            u32.GetWindowThreadProcessId(h, ctypes.byref(p))
            if p.value != pid:
                return True
            c = ctypes.create_unicode_buffer(64)
            u32.GetClassNameW(h, c, 64)
            if c.value == "#32770" and u32.IsWindowVisible(h):
                found.append(h)
            return True
        u32.EnumWindows(ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)(cb), 0)
        for d in found:
            txt = []
            for c in kids(d, "Static"):
                t = text_of(c)
                if t:
                    txt.append(t)
            print("   [%s] 对话框: %s" % (label, " | ".join(txt)[:160]))
            u32.PostMessageW(d, WM_COMMAND, 1, 0)      # IDOK
            time.sleep(0.3)
            u32.PostMessageW(d, 0x0010, 0, 0)          # WM_CLOSE
        time.sleep(0.25)


def main():
    p = subprocess.Popen([EXE])
    time.sleep(4)
    h = find(p.pid, "PlateReviewDeStijl")
    if not h:
        print("未找到主窗口")
        p.kill()
        return 1

    t = threading.Thread(target=dismiss_dialogs, args=(p.pid, "弹窗"), daemon=True)
    t.start()

    ts = k32.GetCurrentThreadId()
    tt = u32.GetWindowThreadProcessId(h, None)
    att = ts != tt
    if att:
        u32.AttachThreadInput(ts, tt, True)
    try:
        u32.SetForegroundWindow(h)
        u32.SetFocus(h)
        time.sleep(0.4)

        # ---- 1. 入口页输入目录（真实 EDIT，用真实键盘输入） ----
        # 跨进程 SetWindowTextW/WM_SETTEXT 对 EDIT 不生效（读回仍为空），
        # 必须用真实键盘消息；而键盘消息要求目标窗口持有焦点，所以这里
        # 先 AttachThreadInput 再 SetFocus 到输入框。
        eds = kids(h, "Edit")
        print("入口页输入框数: %d" % len(eds))
        vis = [e for e in eds if u32.IsWindowVisible(e)]
        print("  可见输入框: %d" % len(vis))
        if not vis:
            print("!! 没有可见输入框")
            return 1
        fe = vis[0]
        u32.SetFocus(fe)
        time.sleep(0.3)
        for ch in TDIR:
            u32.PostMessageW(fe, WM_CHAR, ord(ch), 0)
        time.sleep(0.8)
        got = text_of(fe)
        print("  内容: %r" % got)
        if got != TDIR:
            print("!! 输入未进入（读到 %d 字符，期望 %d）" % (len(got), len(TDIR)))
            return 1

        # 点「进入」：按钮几何 m=48, y = field.y + field.h + 56
        # 客户区尺寸反推：入口字段 y = H*0.42 + 30
        r = wt.RECT()
        u32.GetClientRect(h, ctypes.byref(r))
        CW, CH = r.right, r.bottom
        # DIP→物理按 2 倍（本机 200%）
        fx = 48 * 2
        fy = int(CH * 0.42) + 30 * 2
        gx = fx + 180 * 2 // 2
        gy = fy + 44 * 2 + 56 * 2 + 56 * 2 // 2
        print("  点「进入」于 (%d,%d)" % (gx, gy))
        click_at(h, gx, gy)
        time.sleep(2.5)

        # ---- 2. 进入审核页 ----
        eds = kids(h, "Edit")
        vis = [e for e in eds if u32.IsWindowVisible(e)]
        print("审核页可见输入框数: %d (期望 2: 检测车牌 + 确认车牌)" % len(vis))
        for i, e in enumerate(vis):
            print("   [%d] %r" % (i, text_of(e)))
        if len(vis) < 2:
            print("!! 未进入审核页")
            return 1

        # ---- 3. 点「是」（第一格） ----
        # 按钮几何：m=48, gap=16, 六等分, y = H - m - 56
        m = 48 * 2
        gap = 16 * 2
        bw = (CW - 2 * m - 5 * gap) // 6
        by = CH - m - 56 * 2
        yesx, yesy = m + bw // 2, by + 56
        print("  点「是」于 (%d,%d)" % (yesx, yesy))
        click_at(h, yesx, yesy)
        time.sleep(2.0)

        # ---- 4. 点「完成报告」（第六格） ----
        rx = m + 5 * (bw + gap) + bw // 2
        print("  点「完成报告」于 (%d,%d)" % (rx, yesy))
        click_at(h, rx, yesy)
        time.sleep(4.0)
    finally:
        if att:
            u32.AttachThreadInput(ts, tt, False)

    time.sleep(1.5)
    stop.set()
    try:
        p.kill()
    except Exception:
        pass

    # ---- 5. 校验产出 ----
    outdir = os.path.join(TDIR, "人工审核结果")
    print("\n=== 产出校验 ===")
    if not os.path.isdir(outdir):
        print("!! 未生成 人工审核结果 目录")
        return 1
    for f in sorted(os.listdir(outdir)):
        full = os.path.join(outdir, f)
        if os.path.isdir(full):
            print("   DIR  %s" % f)
        else:
            print("   FILE %-28s %d KB" % (f, os.path.getsize(full) // 1024))

    html = os.path.join(outdir, "违章检测报告.html")
    if os.path.isfile(html):
        data = open(html, "rb").read()
        print("\n报告大小: %d KB" % (len(data) // 1024))
        print("含 base64 内嵌图: %s" % (b"data:image/jpeg;base64," in data))
        print("含确认车牌 %s: %s" % ("粤AX285E", "粤AX285E".encode("utf-8") in data))
        print("含 ROI 回退提示: %s"
              % ("未找到未标注原图".encode("utf-8") in data))
    else:
        print("!! 未生成 HTML 报告")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
