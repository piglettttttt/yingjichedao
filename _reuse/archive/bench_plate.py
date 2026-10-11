"""车牌识别能力基准测试：对同一批车辆，比较不同识别方案。

用户记录（验收真值）：
    00:02 粤A130V9 / 00:11 粤A8R902 / 00:14 粤SVZ600 / 00:23 粤A7M7M4 / 00:25 粤AAG4884

做法：以**车辆检测框**为入口（按既定架构，车辆优先），对每辆车取其
车尾候选区域（整车 / 下半 / 下 1/3 三种竖直区域），跑识别器，统计
"能否得到与真值一致的号码"。

用法：
    python bench_plate.py --video <mp4> --jsonl <车辆检测jsonl> --out <输出目录>
"""
import argparse
import json
import os
import sys
import time

import cv2
import numpy as np

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

TRUTH = [(2.0, "粤A130V9"), (11.0, "粤A8R902"), (14.0, "粤SVZ600"),
         (23.0, "粤A7M7M4"), (25.0, "粤AAG4884")]


def vertical_regions(frame, bbox, pad_ratio=(0.10, 0.15)):
    """按参考项目做法给三种竖直候选区域：整车 / 下半 / 下 1/3。

    车牌在车尾下部，多给几个竖直切法能显著提高"框住车牌"的概率。
    """
    x1, y1, x2, y2 = [int(v) for v in bbox]
    h, w = frame.shape[:2]
    px = int((x2 - x1) * pad_ratio[0])
    py = int((y2 - y1) * pad_ratio[1])
    cx1, cy1 = max(0, x1 - px), max(0, y1 - py)
    cx2, cy2 = min(w, x2 + px), min(h, y2 + py)
    out = []
    out.append((frame[cy1:cy2, cx1:cx2], "full"))
    my = (y1 + y2) // 2
    out.append((frame[max(0, my - py):cy2, cx1:cx2], "lower_half"))
    ty = y1 + int((y2 - y1) * 2 / 3)
    out.append((frame[max(0, ty - py):cy2, cx1:cx2], "lower_third"))
    return [(c, n) for c, n in out if c.size > 0]


def enh_clahe(img):
    """CLAHE 增强（LAB 空间只处理 L 通道），属图像增强而非生成式超分。"""
    lab = cv2.cvtColor(img, cv2.COLOR_BGR2LAB)
    l, a, b = cv2.split(lab)
    l = cv2.createCLAHE(3.0, (8, 8)).apply(l)
    return cv2.cvtColor(cv2.merge([l, a, b]), cv2.COLOR_LAB2BGR)


def norm_plate(s):
    """归一化车牌：去掉分隔符、统一大小写，便于比对。"""
    if not s:
        return ""
    return "".join(ch for ch in str(s).upper() if ch.isalnum() or "\u4e00" <= ch <= "\u9fff")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", required=True)
    ap.add_argument("--jsonl", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--window", type=float, default=2.5)
    ap.add_argument("--use-clahe", action="store_true", default=True)
    ap.add_argument("--upscale2x", action="store_true", default=True)
    a = ap.parse_args()

    from hyperlpr3 import LicensePlateCatcher
    os.makedirs(a.out, exist_ok=True)

    # HyperLPR3 返回 [[车牌文本, 置信度, 类型, 车牌框(x1,y1,x2,y2)], ...]
    catchers = {
        "320x": LicensePlateCatcher(detect_level=0),
        "640x": LicensePlateCatcher(detect_level=1),
    }
    print("已加载 HyperLPR3: %s" % list(catchers.keys()))

    veh = [json.loads(l) for l in open(a.jsonl, encoding="utf-8")]
    print("车辆检出 %d 条\n" % len(veh))

    cap = cv2.VideoCapture(a.video)
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0

    def read_frame(t):
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(round(t * fps)))
        ok, f = cap.read()
        return f if ok else None

    results = []
    t_start = time.time()
    for t, plate_truth in TRUTH:
        near = [v for v in veh if abs(v["t"] - t) <= a.window and v["in_emergency_lane"]]
        if not near:
            near = [v for v in veh if abs(v["t"] - t) <= a.window]
        if not near:
            print("t=%.1f %s : 窗口内无车辆，跳过" % (t, plate_truth))
            results.append({"t": t, "truth": plate_truth, "best": None, "n_try": 0})
            continue
        # 取该窗口内最靠近 t 的、且在应急车道内的车
        target = min(near, key=lambda v: abs(v["t"] - t))
        frame = read_frame(target["t"])
        if frame is None:
            continue

        cands = []
        n_try = 0
        for crop, region in vertical_regions(frame, target["box"]):
            variants = [("raw", crop)]
            if a.use_clahe:
                variants.append(("clahe", enh_clahe(crop)))
            if a.upscale2x and min(crop.shape[:2]) < 400:
                variants.append(("2x", cv2.resize(crop, None, fx=2, fy=2,
                                                  interpolation=cv2.INTER_CUBIC)))
            for vname, img in variants:
                for det_name, catcher in catchers.items():
                    n_try += 1
                    try:
                        for r in catcher(img):
                            # r = [文本, 置信度, 类型, 车牌框]
                            txt = r[0] if len(r) > 0 else None
                            conf = float(r[1]) if len(r) > 1 else 0.0
                            pbox = r[3] if len(r) > 3 else None
                            if txt:
                                cands.append({"text": str(txt), "conf": conf,
                                              "region": region, "variant": vname,
                                              "det": det_name, "plate_box": pbox})
                    except Exception:
                        pass

        norm_truth = norm_plate(plate_truth)
        exact = [c for c in cands if norm_plate(c["text"]) == norm_truth]
        best = max(cands, key=lambda c: c["conf"]) if cands else None
        hit = bool(exact)
        print("t=%6.1f 真值 %-10s 车辆框=%s" % (t, plate_truth, target["box"]))
        print("        尝试 %d 次，得到 %d 个候选，命中真值: %s"
              % (n_try, len(cands), "✅" if hit else "❌"))
        if cands:
            from collections import Counter
            cnt = Counter(norm_plate(c["text"]) for c in cands)
            print("        候选分布: %s" % cnt.most_common(6))
            bc = max(cands, key=lambda c: c["conf"])
            print("        最高置信: %s conf=%.3f (%s/%s/%s)"
                  % (bc["text"], bc["conf"], bc["region"], bc["variant"], bc["det"]))
        results.append({"t": t, "truth": plate_truth, "best": best,
                        "n_try": n_try, "hit": hit,
                        "candidates": cands[:50]})

    el = time.time() - t_start
    n_hit = sum(1 for r in results if r.get("hit"))
    print("\n=== 汇总 ===")
    print("识别命中 %d/%d = %.0f%%   总耗时 %.1fs" % (n_hit, len(TRUTH), 100.0 * n_hit / len(TRUTH), el))
    with open(os.path.join(a.out, "plate_bench.json"), "w", encoding="utf-8") as fh:
        json.dump({"results": results, "n_hit": n_hit, "total": len(TRUTH)},
                  fh, ensure_ascii=False, indent=2)
    print("-> %s" % os.path.join(a.out, "plate_bench.json"))


if __name__ == "__main__":
    main()
