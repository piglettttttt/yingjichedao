"""分工组合：PlateNet 的检测头定位车牌 → HyperLPR3 识别字符。

动机（实测依据）：
  - HyperLPR3 识别很准：正前方车 粤A532GU conf=1.000、粤E7WU16 conf=0.983
  - 但 HyperLPR3 的**检测头**找不到应急车道车辆的车牌（整帧 5 辆仅命中 1 辆）
  - 而 PlateNet 的检测头能找到那些车牌（前 30s 有大量候选），只是识别不准
  → 用前者做定位、后者做识别，取长补短。

做法：
  1. 用 plate_detect.onnx 在原帧上检测车牌，按 4 个关键点做透视校正得到正矩形 crop
  2. crop 做 2x 放大后交给 HyperLPR3 识别
  3. 与真值比对
"""
import argparse
import json
import os
import sys
import time

import cv2
import numpy as np
import onnxruntime as ort

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from stage1_candidates import (letterbox, post_process, four_point_transform,  # noqa: E402
                              split_double_plate)

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
    ap.add_argument("--onnx-dir", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "onnx"))
    ap.add_argument("--out", required=True)
    ap.add_argument("--window", type=float, default=2.0)
    ap.add_argument("--fps-scan", type=float, default=6.0)
    ap.add_argument("--conf", type=float, default=0.25)
    ap.add_argument("--min-side", type=float, default=12.0, help="车牌框最短边下限")
    a = ap.parse_args()

    det = ort.InferenceSession(os.path.join(a.onnx_dir, "plate_detect.onnx"),
                               providers=["CPUExecutionProvider"])
    dn = det.get_inputs()[0].name
    from hyperlpr3 import LicensePlateCatcher
    rec = LicensePlateCatcher(detect_level=0)

    cap = cv2.VideoCapture(a.video)
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    W = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    H = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    print("视频 %dx%d @%.1fps" % (W, H, fps))

    step = max(1, int(round(fps / a.fps_scan)))
    summary = []
    t_all = time.time()

    for t0, truth in TRUTH:
        ntf = norm(truth)
        print("\n" + "=" * 72)
        print("真值 t=%.1fs  %s" % (t0, truth))
        found = []
        n_plate = 0
        for fi in range(int((t0 - a.window) * fps), int((t0 + a.window) * fps), step):
            if fi < 0:
                continue
            cap.set(cv2.CAP_PROP_POS_FRAMES, fi)
            ok, frame = cap.read()
            if not ok:
                continue
            t = fi / fps
            blob, r, left, top = letterbox(frame)
            out = det.run(None, {dn: blob})[0]
            res = post_process(out, r, left, top, a.conf, 0.5)
            for row in res:
                x1, y1, x2, y2 = [float(v) for v in row[:4]]
                if min(x2 - x1, y2 - y1) < a.min_side:
                    continue
                lms = row[5:13].reshape(4, 2).astype(np.float32)
                crop = four_point_transform(frame, lms)
                if crop is None or crop.size == 0 or min(crop.shape[:2]) < 4:
                    continue
                if int(row[13]) == 1:
                    crop = split_double_plate(crop)
                n_plate += 1
                # 2x 放大 + CLAHE 两种变体交给 HyperLPR3
                variants = [("raw", crop)]
                if min(crop.shape[:2]) < 200:
                    variants.append(("2x", cv2.resize(crop, None, fx=2, fy=2,
                                                      interpolation=cv2.INTER_CUBIC)))
                lab = cv2.cvtColor(crop, cv2.COLOR_BGR2LAB)
                l_, aa, bb = cv2.split(lab)
                l_ = cv2.createCLAHE(3.0, (8, 8)).apply(l_)
                variants.append(("clahe", cv2.cvtColor(cv2.merge([l_, aa, bb]), cv2.COLOR_LAB2BGR)))
                for vname, img in variants:
                    try:
                        for rr in rec(img):
                            txt, conf = str(rr[0]), float(rr[1])
                            found.append({"t": round(t, 2), "text": txt,
                                          "conf": round(conf, 4), "variant": vname,
                                          "plate_box": [round(x1), round(y1), round(x2), round(y2)],
                                          "plate_w": round(x2 - x1), "plate_h": round(y2 - y1)})
                    except Exception:
                        pass

        hits = [x for x in found if norm(x["text"]) == ntf]
        from collections import Counter
        cnt = Counter(x["text"] for x in found)
        print("  PlateNet 检出车牌 %d 个候选 → HyperLPR3 识别出 %d 个结果（%d 种文本）"
              % (n_plate, len(found), len(cnt)))
        for txt, c in cnt.most_common(6):
            ex = [x for x in found if x["text"] == txt]
            mx = max(ex, key=lambda x: x["conf"])
            mark = "  ← 命中真值" if norm(txt) == ntf else ""
            print("     %-12s x%-3d 最高conf=%.3f 车牌%dx%d%s"
                  % (txt, c, mx["conf"], mx["plate_w"], mx["plate_h"], mark))
        print("  命中真值: %s" % ("✅ %d 次" % len(hits) if hits else "❌ 未出现"))
        summary.append({"t": t0, "truth": truth, "hit": bool(hits),
                        "n_plate": n_plate, "n_rec": len(found), "texts": dict(cnt)})

    cap.release()
    n_hit = sum(1 for s in summary if s["hit"])
    print("\n" + "=" * 72)
    print("组合方案命中 %d/%d = %.0f%%   总耗时 %.1fs"
          % (n_hit, len(TRUTH), 100.0 * n_hit / len(TRUTH), time.time() - t_all))
    with open(a.out, "w", encoding="utf-8") as fh:
        json.dump(summary, fh, ensure_ascii=False, indent=2)
    print("-> %s" % a.out)


if __name__ == "__main__":
    main()
