"""在指定时间点出证据图：叠加 ROI 多边形 + 该帧检出的车牌框 + 被判定的车辆占位区间。

用于人工核对"这辆车到底是不是在应急车道里"，是 ROI 标定正确性的验收手段。
"""
import argparse
import json
import os
import sys

import cv2
import numpy as np
import yaml

sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", required=True)
    ap.add_argument("--roi", required=True)
    ap.add_argument("--jsonl", required=True)
    ap.add_argument("--times", nargs="+", type=float, required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--tol", type=float, default=0.2, help="时间匹配容差(秒)")
    a = ap.parse_args()

    cfg = yaml.safe_load(open(a.roi, encoding="utf-8"))
    el = cfg["emergency_lane"]
    poly = np.array(el["polygon"], dtype=np.int32)
    up_ratio = float((el.get("keypoint") or {}).get("up_ratio", 0.35))
    wmult = float(el.get("vehicle_width_from_plate", 3.4))

    rows = [json.loads(l) for l in open(a.jsonl, encoding="utf-8")]
    os.makedirs(a.out, exist_ok=True)
    cap = cv2.VideoCapture(a.video)
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0

    for t in a.times:
        idx = int(round(t * fps))
        cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
        ok, fr = cap.read()
        if not ok:
            print("跳过 t=%.2f（读帧失败）" % t)
            continue
        vis = fr.copy()
        ov = vis.copy()
        cv2.fillPoly(ov, [poly], (0, 140, 255))
        vis = cv2.addWeighted(ov, 0.22, vis, 0.78, 0)
        cv2.polylines(vis, [poly], True, (0, 0, 255), 3)

        near = [r for r in rows if abs(r["t"] - t) <= a.tol]
        for r in near:
            x1, y1, x2, y2 = [int(v) for v in r["box"]]
            cv2.rectangle(vis, (x1, y1), (x2, y2), (0, 255, 0), 3)
            # 车辆估计占位
            cx = (x1 + x2) / 2.0
            half = 0.5 * (x2 - x1) * wmult
            yg = int(y2 - up_ratio * (y2 - y1))
            cv2.line(vis, (int(cx - half), yg), (int(cx + half), yg), (255, 0, 255), 4)
            cv2.circle(vis, (int(cx), yg), 7, (255, 0, 255), -1)
            cv2.putText(vis, r["text"] or "?", (x1, max(30, y1 - 12)),
                        cv2.FONT_HERSHEY_SIMPLEX, 1.1, (0, 255, 0), 3)
        cv2.putText(vis, "t=%.2fs  dets=%d" % (t, len(near)), (40, 90),
                    cv2.FONT_HERSHEY_SIMPLEX, 1.8, (0, 0, 255), 4)
        p = os.path.join(a.out, "evidence_t%06.2f.jpg" % t)
        cv2.imwrite(p, vis, [cv2.IMWRITE_JPEG_QUALITY, 90])
        print("-> %s  (该帧候选 %d 条: %s)" % (p, len(near), [r["text"] for r in near]))
    cap.release()


if __name__ == "__main__":
    main()
