"""用人工读点拟合车道线并生成 ROI，输出叠加图供核对。

输入若干"原图坐标"下的白线点，做最小二乘拟合，再延展到画面上下边界，
生成"线右侧为应急车道"的多边形。
"""
import argparse
import json
import os
import sys

import cv2
import numpy as np

sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--points", nargs="+", required=True,
                    help="白线点，格式 x,y（原图坐标），至少 2 个")
    ap.add_argument("--frames", nargs="+", type=float, default=[12.0, 2.5, 15.0, 23.0],
                    help="用于出核对图的时间点")
    a = ap.parse_args()

    P = np.array([[float(v) for v in p.split(",")] for p in a.points], dtype=np.float64)
    print("输入点:")
    for x, y in P:
        print("   (%.0f, %.0f)" % (x, y))
    c = P.mean(axis=0)
    _, _, vt = np.linalg.svd(P - c, full_matrices=False)
    d = vt[0]
    if d[1] < 0:
        d = -d
    print("拟合方向 (dx,dy)=(%.4f,%.4f)  斜率 dx/dy=%.4f" % (d[0], d[1], d[0] / d[1]))

    cap = cv2.VideoCapture(a.video)
    W = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    H = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    print("视频 %dx%d" % (W, H))

    def x_at_y(y):
        return c[0] + (y - c[1]) * (d[0] / d[1])

    y_top, y_bot = float(min(P[:, 1])) - 60.0, float(H - 1)
    y_top = max(0.0, y_top)
    p_top = (x_at_y(y_top), y_top)
    p_bot = (x_at_y(y_bot), y_bot)
    print("车道线延展: 上=(%.0f,%.0f) 下=(%.0f,%.0f)" % (p_top[0], p_top[1], p_bot[0], p_bot[1]))
    for y in (500, 600, 700, 800, 900, 1000):
        print("   y=%4d -> x=%7.1f" % (y, x_at_y(y)))

    poly = [[p_top[0], p_top[1]], [p_bot[0], p_bot[1]], [W - 1, H - 1], [W - 1, p_top[1]]]
    os.makedirs(a.out, exist_ok=True)

    tiles = []
    for t in a.frames:
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(round(t * fps)))
        ok, fr = cap.read()
        if not ok:
            continue
        vis = fr.copy()
        pa = np.array(poly, dtype=np.int32)
        ov = vis.copy(); cv2.fillPoly(ov, [pa], (0, 140, 255))
        vis = cv2.addWeighted(ov, 0.28, vis, 0.72, 0)
        cv2.polylines(vis, [pa], True, (0, 0, 255), 3)
        for x, y in P:
            cv2.circle(vis, (int(x), int(y)), 7, (0, 255, 0), -1)
        cv2.putText(vis, "t=%.1fs" % t, (40, 90), cv2.FONT_HERSHEY_SIMPLEX, 1.8, (0, 0, 255), 4)
        tiles.append(cv2.resize(vis, (640, 360)))
    cap.release()

    rows = []
    for i in range(0, len(tiles), 2):
        r = tiles[i:i + 2]
        if len(r) == 1:
            r.append(np.zeros_like(r[0]))
        rows.append(np.hstack(r))
    grid = np.vstack(rows)
    gp = os.path.join(a.out, "roi_v2_grid.jpg")
    cv2.imwrite(gp, grid, [cv2.IMWRITE_JPEG_QUALITY, 90])
    print("\n核对图 ->", gp)

    roi = json.load(open(os.path.join(a.out, "roi_v2.json"), encoding="utf-8")) \
        if os.path.exists(os.path.join(a.out, "roi_v2.json")) else {}
    roi.update({"line_top": list(p_top), "line_bottom": list(p_bot),
                "polygon": poly, "slope_dxdy": d[0] / d[1],
                "x_at_y": {str(y): x_at_y(y) for y in (500, 600, 700, 800, 900, 1000)}})
    with open(os.path.join(a.out, "roi_v2.json"), "w", encoding="utf-8") as fh:
        json.dump(roi, fh, ensure_ascii=False, indent=2)
    print("ROI ->", os.path.join(a.out, "roi_v2.json"))
    print("多边形:", [[round(x), round(y)] for x, y in poly])


if __name__ == "__main__":
    main()
