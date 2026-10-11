"""给 HyperLPR3 喂"车辆区域"（而非紧裁车牌）：这是它设计使用的输入形态。

思路依据（实测）：
  - HyperLPR3 的整帧识别很准（大车牌 conf 1.000），但检不到小目标
  - 把紧裁车牌（74x25）喂给它 → 它的检测头失效（0 结果）
  - 那么正确用法应是：**给它一块"以目标车辆为中心、含一定背景"的区域**，
    并按倍数放大，使车牌在该区域内占据足够像素

对照实验：不同 padding 倍数 × 放大倍数 × 检测档位，找出能否检出目标车牌。

用法：
    python scan_vehicle_region.py --video <mp4> --veh-jsonl <jsonl> --out <json>
"""
import argparse
import json
import sys
import time
from collections import Counter

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
    ap.add_argument("--veh-jsonl", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--pads", nargs="+", type=float, default=[0.6, 1.2, 2.0],
                    help="车辆框外扩比例（按框尺寸的比例）")
    ap.add_argument("--upscales", nargs="+", type=float, default=[1.0, 3.0])
    ap.add_argument("--levels", nargs="+", type=int, default=[0, 1])
    ap.add_argument("--window", type=float, default=2.0)
    ap.add_argument("--fps-scan", type=float, default=4.0)
    a = ap.parse_args()

    from hyperlpr3 import LicensePlateCatcher
    cats = {lv: LicensePlateCatcher(detect_level=lv) for lv in a.levels}
    veh = [json.loads(l) for l in open(a.veh_jsonl, encoding="utf-8")]

    cap = cv2.VideoCapture(a.video)
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    W = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    H = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    print("视频 %dx%d @%.1fps  pads=%s upscales=%s levels=%s"
          % (W, H, fps, a.pads, a.upscales, a.levels))

    step = max(1, int(round(fps / a.fps_scan)))
    report = []
    t_all = time.time()

    for t0, truth in TRUTH:
        ntf = norm(truth)
        print("\n" + "=" * 76)
        print("真值 t=%.1fs  %s" % (t0, truth))
        cands = []
        # 记录该时段代表性目标车辆框（供投票阶段做归属筛选）
        veh_box_used = None
        veh_box_used_conf = -1.0
        for fi in range(int((t0 - a.window) * fps), int((t0 + a.window) * fps), step):
            if fi < 0:
                continue
            cap.set(cv2.CAP_PROP_POS_FRAMES, fi)
            ok, f = cap.read()
            if not ok:
                continue
            t = fi / fps
            # 该帧目标车辆（在应急车道内、置信度最高）
            pool = [v for v in veh if abs(v["t"] - t) <= 0.2 and v["in_emergency_lane"]]
            if not pool:
                pool = [v for v in veh if abs(v["t"] - t) <= 0.2]
            if not pool:
                continue
            v = max(pool, key=lambda x: x["conf"])
            x1, y1, x2, y2 = v["box"]
            bw, bh = x2 - x1, y2 - y1
            if veh_box_used is None or v["conf"] > veh_box_used_conf:
                veh_box_used = [x1, y1, x2, y2]
                veh_box_used_conf = v["conf"]
            for pad in a.pads:
                ax1 = max(0, int(x1 - bw * pad)); ay1 = max(0, int(y1 - bh * pad))
                ax2 = min(W, int(x2 + bw * pad)); ay2 = min(H, int(y2 + bh * pad))
                crop = f[ay1:ay2, ax1:ax2]
                if crop.size == 0 or min(crop.shape[:2]) < 32:
                    continue
                for up in a.upscales:
                    img = crop if up == 1.0 else cv2.resize(
                        crop, None, fx=up, fy=up, interpolation=cv2.INTER_CUBIC)
                    if max(img.shape[:2]) > 4000:
                        continue
                    for lv, cat in cats.items():
                        try:
                            for r in cat(img):
                                txt, conf = str(r[0]), float(r[1])
                                pb = r[3] if len(r) > 3 else None
                                # 车牌框换算回原图
                                if pb:
                                    ox1 = ax1 + pb[0] / up; oy1 = ay1 + pb[1] / up
                                    ox2 = ax1 + pb[2] / up; oy2 = ay1 + pb[3] / up
                                    pw, ph = (ox2 - ox1), (oy2 - oy1)
                                else:
                                    ox1 = oy1 = ox2 = oy2 = pw = ph = 0
                                cands.append({"t": round(t, 2), "text": txt,
                                              "conf": round(conf, 4), "pad": pad, "up": up,
                                              "level": lv,
                                              "plate_box": [round(ox1), round(oy1), round(ox2), round(oy2)],
                                              "pw": round(pw), "ph": round(ph),
                                              # 记录**该候选自己那一帧**的车辆框：
                                              # 车辆在 2 秒窗口内会移动，用单一"代表框"
                                              # 过滤跨帧候选会把正确结果误滤掉（实测过）
                                              "veh_box": [round(x1), round(y1), round(x2), round(y2)],
                                              "hit": norm(txt) == ntf})
                        except Exception:
                            pass

        cnt = Counter(norm(c["text"]) for c in cands)
        hits = [c for c in cands if c["hit"]]
        print("  候选 %d 条（%d 种文本）" % (len(cands), len(cnt)))
        for txt, c in cnt.most_common(8):
            ex = [x for x in cands if norm(x["text"]) == txt]
            mx = max(ex, key=lambda x: x["conf"])
            print("     %-12s x%-3d 最高conf=%.3f 车牌%dx%d (pad=%.1f up=%.1f lv=%d) 位置%s %s"
                  % (txt, c, mx["conf"], mx["pw"], mx["ph"], mx["pad"], mx["up"],
                     mx["level"], mx["plate_box"], "← 真值!" if norm(txt) == ntf else ""))
        print("  命中真值: %s" % ("✅ %d 次" % len(hits) if hits else "❌ 未出现"))
        if hits:
            h = max(hits, key=lambda x: x["conf"])
            print("     最佳: conf=%.3f 车牌%dx%d pad=%.1f up=%.1f lv=%d"
                  % (h["conf"], h["pw"], h["ph"], h["pad"], h["up"], h["level"]))
        report.append({"t": t0, "truth": truth, "hit": bool(hits),
                       "n": len(cands), "texts": dict(cnt),
                       "veh_box": list(veh_box_used) if veh_box_used else None,
                       "cands": cands})

    cap.release()
    n = sum(1 for r in report if r["hit"])
    print("\n" + "=" * 76)
    print("车辆区域模式命中 %d/%d = %.0f%%   耗时 %.1fs"
          % (n, len(TRUTH), 100.0 * n / len(TRUTH), time.time() - t_all))
    for r in report:
        print("  t=%5.1fs %-10s %s" % (r["t"], r["truth"], "✅" if r["hit"] else "❌"))
    with open(a.out, "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2)
    print("-> %s" % a.out)


if __name__ == "__main__":
    main()
