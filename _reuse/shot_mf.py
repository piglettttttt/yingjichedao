"""截图极简扁平风界面：入口页 → 主界面 → hover 反色。

DPI 感知；BMP 用 biHeight=+h（自下而上）保证朝向正确。
"""
import ctypes
import ctypes.wintypes as wt
import os
import struct
import subprocess
import sys
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

u32 = ctypes.WinDLL("user32", use_last_error=True)
gdi = ctypes.WinDLL("gdi32", use_last_error=True)
k32 = ctypes.WinDLL("kernel32", use_last_error=True)
u32.SetProcessDPIAware()

EXE = r"D:\Users\Lhh\Documents\dsh\yingjichedao\_reuse\dist\PlateReview.exe"
TDIR = r"D:\Users\Lhh\Documents\dsh\yingjichedao\downloads\xiangtang"
OUT = r"D:\Users\Lhh\Documents\dsh\yingjichedao\_reuse\run"

WM_CHAR = 0x0102
WM_MOUSEMOVE = 0x0200
WM_MOUSEWHEEL = 0x020A


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
    return hits[0] if hits else None


def shot(rect, path):
    x0, y0, x1, y1 = rect
    w, h = x1 - x0, y1 - y0
    hdc = u32.GetDC(0)
    mdc = gdi.CreateCompatibleDC(hdc)
    bmp = gdi.CreateCompatibleBitmap(hdc, w, h)
    old = gdi.SelectObject(mdc, bmp)
    gdi.BitBlt(mdc, 0, 0, w, h, hdc, x0, y0, 0x00CC0020)

    class BIH(ctypes.Structure):
        _fields_ = [("biSize", wt.DWORD), ("biWidth", wt.LONG), ("biHeight", wt.LONG),
                    ("biPlanes", wt.WORD), ("biBitCount", wt.WORD),
                    ("biCompression", wt.DWORD), ("biSizeImage", wt.DWORD),
                    ("biXPelsPerMeter", wt.LONG), ("biYPelsPerMeter", wt.LONG),
                    ("biClrUsed", wt.DWORD), ("biClrImportant", wt.DWORD)]
    bi = BIH()
    bi.biSize = ctypes.sizeof(bi)
    bi.biWidth = w
    bi.biHeight = h          # positive => bottom-up, matches BMP storage
    bi.biPlanes = 1
    bi.biBitCount = 32
    buf = ctypes.create_string_buffer(w * h * 4)
    gdi.GetDIBits(mdc, bmp, 0, h, buf, ctypes.byref(bi), 0)
    hdr = struct.pack("<2sIHHI", b"BM", 14 + 40 + len(buf), 0, 0, 14 + 40)
    ihdr = struct.pack("<IiiHHIIiiII", 40, w, h, 1, 32, 0, len(buf), 0, 0, 0, 0)
    with open(path, "wb") as fh:
        fh.write(hdr + ihdr + buf.raw)
    gdi.SelectObject(mdc, old)
    gdi.DeleteObject(bmp)
    gdi.DeleteDC(mdc)
    u32.ReleaseDC(0, hdc)


def win_rect(h):
    r = wt.RECT()
    u32.GetWindowRect(h, ctypes.byref(r))
    return r


def pin_and_shot(h, path, tag):
    """Move the window to the top-left, then capture exactly that rectangle.

    Re-pinning before every capture matters: if the window drifts (or another
    window lands on top) the rectangle capture silently grabs the wrong pixels,
    which is how a game client once ended up in the screenshot.
    """
    r = win_rect(h)
    W, H = r.right - r.left, r.bottom - r.top
    u32.SetWindowPos(h, ctypes.c_void_p(-1), 0, 0, W, H,
                     0x0040 | 0x0004)     # SWP_SHOWWINDOW | SWP_NOACTIVATE
    time.sleep(1.0)
    r = win_rect(h)
    shot((r.left, r.top, r.right, r.bottom), path)
    print("%s 截图  rect=%s" % (tag, (r.left, r.top, r.right, r.bottom)))
    return H


def main():
    p = subprocess.Popen([EXE])
    time.sleep(4)
    h = find(p.pid, "PlateReviewDeStijl")
    if not h:
        print("未找到窗口")
        p.kill()
        return 1

    tid_self = k32.GetCurrentThreadId()
    tid_tgt = u32.GetWindowThreadProcessId(h, None)
    att = tid_self != tid_tgt
    if att:
        u32.AttachThreadInput(tid_self, tid_tgt, True)
    try:
        pin_and_shot(h, os.path.join(OUT, "mf_entry.bmp"), "入口页")

        u32.SetForegroundWindow(h)
        u32.SetFocus(h)
        for ch in TDIR:
            u32.PostMessageW(h, WM_CHAR, ord(ch), 0)
        time.sleep(0.8)
        u32.PostMessageW(h, WM_CHAR, 13, 0)      # Enter
        time.sleep(3.0)

        H = pin_and_shot(h, os.path.join(OUT, "mf_review.bmp"), "主界面")

        # hover 到「是」按钮（第一格）
        r = win_rect(h)
        hx = r.left + u32.GetSystemMetrics(0) // 80
        hy = r.top + (r.bottom - r.top) - int((96 + 56 / 2) * 2)
        lp = ((hy & 0xFFFF) << 16) | (hx & 0xFFFF)
        for _ in range(3):
            u32.PostMessageW(h, WM_MOUSEMOVE, 0, lp)
            time.sleep(0.3)
        time.sleep(0.8)
        pin_and_shot(h, os.path.join(OUT, "mf_hover.bmp"), "hover")
    finally:
        if att:
            u32.AttachThreadInput(tid_self, tid_tgt, False)
    p.kill()
    return 0


if __name__ == "__main__":
    sys.exit(main())
