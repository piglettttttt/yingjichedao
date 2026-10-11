"""整帧模式基准：HyperLPR3 直接在全帧上找车牌（它自带检测+识别）。

相对"车辆 crop 再识别"的优势：
  - 不依赖车辆框的精度（我此前选车时选到过 623px 宽的错框，导致 crop 里没有车牌）
  - HyperLPR3 本身的检测头就是为车牌训练的，比通用方案更对口
代价：远处车牌在整帧里像素更少，可能检不出。

输出每个时刻检出的全部车牌 + 车牌框尺寸，用于判断"目标车牌是否被检出/多小"。
"""
import argparse
import json
import sys
import time

import cv2

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

TRUTH = [(2.0, "粤A130V9"), (11.0, "粤A8R902"), (14.0, "粤SVZ600"),
         (23.0, "粤A7M7M4"), (25.0, "粤AAG4884")]


def norm(s):
    if not s:
        return ""
    return "".join(ch for ch in str(s).upper() if ch.isalnum() or "\u4e00" <= ch <= "\u9fff")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--window", type=float, default=2.0, help="每个时刻扫描的时间窗(秒)")
    ap.add_argument("--fps-scan", type=float, default=6.0, help="窗口内扫描帧率")
    a = ap.parse_args()

    from hyperlpr3 import LicensePlateCatcher
    cats = {"320x": LicensePlateCatcher(detect_level=0),
            "640x": LicensePlateCatcher(detect_level=1)}

    cap = cv2.VideoCapture(a.video)
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    W = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    H = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    print("视频 %dx%d @%.1fps" % (W, H, fps))

    step = max(1, int(round(fps / a.fps_scan)))
    out = []
    t_all0 = time.time()
    for t0, truth in TRUTH:
        ntf = norm(truth)
        print("\n" + "=" * 72)
        print("真值 t=%.1fs  %s   扫描 %.1fs 窗口，步长 %d 帧" % (t0, truth, a.window, step))
        found = []
        frames = 0
        for fi in range(int((t0 - a.window) * fps), int((t0 + a.window) * fps), step):
            if fi < 0:
                continue
            cap.set(cv2.CAP_PROP_POS_FRAMES, fi)
            ok, f = cap.read()
            if not ok:
                continue
            frames += 1
            t = fi / fps
            for name, cat in cats.items():
                try:
                    for r in cat(f):
                        txt, conf = str(r[0]), float(r[1])
                        box = r[3] if len(r) > 3 else None
                        w = (box[2] - box[0]) if box else 0
                        h = (box[3] - box[1]) if box else 0
                        found.append({"t": round(t, 2), "text": txt, "conf": round(conf, 4),
                                      "box": box, "pw": w, "ph": h, "det": name})
                except Exception:
                    pass
        hits = [x for x in found if norm(x["text"]) == ntf]
        from collections import Counter
        cnt = Counter(x["text"] for x in found)
        print("  扫描 %d 帧，检出车牌 %d 个（不同文本 %d 种）" % (frames, len(found), len(cnt)))
        for txt, c in cnt.most_common(8):
            ex = [x for x in found if x["text"] == txt]
            mx = max(ex, key=lambda x: x["conf"])
            mark = "  ← 命中真值" if norm(txt) == ntf else ""
            print("     %-12s x%-3d 最高conf=%.3f 车牌框%dx%d 尺寸%s"
                  % (txt, c, mx["conf"], mx["pw"], mx["ph"], mx["box"]))
        print("  命中真值: %s" % ("✅ %d 次" % len(hits) if hits else "❌ 未出现"))
        out.append({"t": t0, "truth": truth, "hit": bool(hits),
                    "n_found": len(found), "texts": dict(cnt)})
    cap.release()

    n_hit = sum(1 for o in out if o["hit"])
    print("\n" + "=" * 72)
    print("整帧模式命中 %d/%d = %.0f%%   总耗时 %.1fs"
          % (n_hit, len(TRUTH), 100.0 * n_hit / len(TRUTH), time.time() - t_all0))
    with open(a.out, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=2)
    print("-> %s" % a.out)


if __name__ == "__main__":
    main()
