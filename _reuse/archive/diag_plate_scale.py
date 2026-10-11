"""决定性诊断：对每辆目标车辆，做递增范围的裁剪，看 HyperLPR3 在多大/多清晰时能识别。

目的：区分"车牌根本没进画面/太小"与"识别能力不足"，
并给出 HyperLPR3 生效所需的最小车牌像素尺寸——这直接决定
是否需要用户提供更高清视频源。

做法：以车辆框（含 ROI 内、置信度最高者）为中心，做多个放大倍数的区域裁剪，
逐个交给 HyperLPR3（320x/640x 两档），记录识别结果与车牌框尺寸。
"""
import argparse
import json
import sys

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
    ap.add_argument("--jsonl", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--window", type=float, default=2.0)
    a = ap.parse_args()

    from hyperlpr3 import LicensePlateCatcher
    cats = {"320x": LicensePlateCatcher(detect_level=0),
            "640x": LicensePlateCatcher(detect_level=1)}

    veh = [json.loads(l) for l in open(a.jsonl, encoding="utf-8")]
    cap = cv2.VideoCapture(a.video)
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    W = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    H = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

    report = []
    for t0, truth in TRUTH:
        ntf = norm(truth)
        print("\n" + "=" * 74)
        print("真值 t=%.1fs  %s" % (t0, truth))

        # 取窗口内"在应急车道且置信度最高"的车辆框
        cand = [v for v in veh if abs(v["t"] - t0) <= a.window and v["in_emergency_lane"]]
        if not cand:
            cand = [v for v in veh if abs(v["t"] - t0) <= a.window]
        if not cand:
            print("  窗口内无车辆")
            continue
        v = max(cand, key=lambda x: x["conf"])
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(round(v["t"] * fps)))
        ok, frame = cap.read()
        if not ok:
            continue
        x1, y1, x2, y2 = [int(q) for q in v["box"]]
        print("  选定车辆: t=%.2f %s conf=%.2f box=[%d,%d,%d,%d] (%dx%d)"
              % (v["t"], v["cls"], v["conf"], x1, y1, x2, y2, x2 - x1, y2 - y1))

        results = []
        # 多档放大倍数：从"紧贴车辆"到"大范围含背景"
        for scale in (1.0, 1.5, 2.0, 3.0):
            bw, bh = x2 - x1, y2 - y1
            cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
            hw, hh = bw * scale / 2, bh * scale / 2
            ax1, ay1 = max(0, int(cx - hw)), max(0, int(cy - hh))
            ax2, ay2 = min(W, int(cx + hw)), min(H, int(cy + hh))
            crop = frame[ay1:ay2, ax1:ax2]
            if crop.size == 0:
                continue
            # 再叠一个 2x 上采样版本（整块区域放大，让车牌占更多像素）
            variants = [("区域1x", crop),
                        ("区域2x", cv2.resize(crop, None, fx=2, fy=2,
                                              interpolation=cv2.INTER_CUBIC))]
            for vname, img in variants:
                for dname, cat in cats.items():
                    try:
                        for r in cat(img):
                            txt, conf = str(r[0]), float(r[1])
                            pb = r[3] if len(r) > 3 else None
                            pw = (pb[2] - pb[0]) if pb else 0
                            ph = (pb[3] - pb[1]) if pb else 0
                            results.append({"scale": scale, "vname": vname, "det": dname,
                                            "text": txt, "conf": round(conf, 4),
                                            "pw": pw, "ph": ph,
                                            "hit": norm(txt) == ntf})
                    except Exception:
                        pass

        hits = [r for r in results if r["hit"]]
        if results:
            print("  HyperLPR3 识别结果 %d 条：" % len(results))
            for r in results[:12]:
                print("     scale=%.1f %-7s %-5s -> %-12s conf=%.3f 车牌%dx%d %s"
                      % (r["scale"], r["vname"], r["det"], r["text"], r["conf"],
                         r["pw"], r["ph"], "✅" if r["hit"] else ""))
        else:
            print("  HyperLPR3 未识别出任何车牌")
        print("  命中真值: %s" % ("✅ %d 次" % len(hits) if hits else "❌"))
        report.append({"t": t0, "truth": truth, "veh": v, "n_res": len(results),
                       "hit": bool(hits), "results": results})
    cap.release()

    n_hit = sum(1 for r in report if r["hit"])
    print("\n" + "=" * 74)
    print("诊断命中 %d/%d" % (n_hit, len(report)))
    with open(a.out, "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2)
    print("-> %s" % a.out)


if __name__ == "__main__":
    main()
