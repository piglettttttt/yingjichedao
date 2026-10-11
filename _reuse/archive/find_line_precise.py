"""精确查找"最右车道分隔白线"：只找高亮+低饱和的细长结构，并做直线拟合。

比 detect_lane_line.py 更克制：
 - 用亮度分位数而非最大值定白线（避免护栏反光/车灯抢峰）
 - 要求局部对比度（白线两侧是暗沥青）
 - 用 RANSAC 式迭代剔除离群点后再拟合
输出拟合线 + 可视化 + 在若干 y 处的 x，便于与用户记录对位。
"""
import argparse
import json
import os
import sys

import cv2
import numpy as np

sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def candidate_points(img, x_from=0.45, y_from=0.30, y_to=0.98):
    """返回候选白线点 (x, y, score)。逐行找"窄而亮"的峰。"""
    H, W = img.shape[:2]
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY).astype(np.float32)
    sat = hsv[:, :, 1].astype(np.float32)

    y0, y1 = int(H * y_from), int(H * y_to)
    x0 = int(W * x_from)
    pts = []
    for y in range(y0, y1, 4):
        band = gray[max(0, y - 2):y + 3, :].mean(axis=0)
        sband = sat[max(0, y - 2):y + 3, :].mean(axis=0)
        seg = band[x0:]
        sseg = sband[x0:]
        if len(seg) < 30:
            continue
        # 车道线：亮且不彩（护栏反光通常高光但可能低饱和，用局部对比度进一步筛）
        base = np.percentile(seg, 60)
        hi = seg > max(base + 25, 150)
        hi &= sseg < 70
        if not hi.any():
            continue
        idx = np.nonzero(hi)[0]
        # 找连续段，取宽度小（<=25px，车道线细）且最亮的段
        groups = np.split(idx, np.nonzero(np.diff(idx) > 3)[0] + 1)
        best = None
        for g in groups:
            if len(g) < 2 or len(g) > 30:
                continue
            strength = seg[g].mean()
            # 两侧对比度
            l = max(0, g[0] - 12); r = min(len(seg), g[-1] + 13)
            contrast = strength - (seg[l:g[0]].mean() if g[0] > l else base)
            sc = strength + contrast
            if best is None or sc > best[0]:
                best = (sc, float(g.mean() + x0), float(y))
        if best:
            pts.append(best)
    return pts


def fit_robust(pts, iters=6):
    """迭代最小二乘：先全量拟合，再剔除残差大的点。"""
    P = np.array([[x, y] for _, x, y in pts], dtype=np.float64)
    idx = np.arange(len(P))
    for _ in range(iters):
        Q = P[idx]
        c = Q.mean(axis=0)
        _, _, vt = np.linalg.svd(Q - c, full_matrices=False)
        d = vt[0]
        n = np.array([-d[1], d[0]])                    # 法向
        resid = np.abs((P - c) @ n)
        thr = max(4.0, np.percentile(resid[idx], 70))
        keep = np.nonzero(resid <= thr)[0]
        if len(keep) < 5 or len(keep) == len(idx):
            idx = keep if len(keep) >= 5 else idx
            break
        idx = keep
    Q = P[idx]
    c = Q.mean(axis=0)
    _, _, vt = np.linalg.svd(Q - c, full_matrices=False)
    return vt[0], c, idx


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--frame", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--x-from", type=float, default=0.45)
    a = ap.parse_args()

    img = cv2.imread(a.frame)
    H, W = img.shape[:2]
    pts = candidate_points(img, a.x_from)
    print("候选白线点 %d 个" % len(pts))
    for _, x, y in pts:
        print("   y=%4.0f x=%7.1f" % (y, x))
    if len(pts) < 5:
        raise SystemExit("点太少")

    d, c, keep = fit_robust(pts)
    print("\n内点 %d/%d" % (len(keep), len(pts)))
    print("方向 (dx,dy)=(%.4f,%.4f) 斜率 dx/dy=%.4f" % (d[0], d[1], d[0] / d[1]))
    print("质心 (%.1f, %.1f)" % (c[0], c[1]))

    def x_at_y(y):
        return c[0] + (y - c[1]) * (d[0] / d[1])

    print("\n白线位置:")
    for y in (350, 450, 550, 650, 750, 850, 950, 1050):
        print("   y=%4d -> x=%7.1f" % (y, x_at_y(y)))

    vis = img.copy()
    y1, y2 = 320.0, float(H - 1)
    cv2.line(vis, (int(x_at_y(y1)), int(y1)), (int(x_at_y(y2)), int(y2)), (0, 0, 255), 3)
    for i, (_, x, y) in enumerate(pts):
        col = (0, 255, 0) if i in set(keep.tolist()) else (0, 165, 255)
        cv2.circle(vis, (int(x), int(y)), 5, col, -1)
    poly = np.array([[x_at_y(y1), y1], [x_at_y(y2), y2], [W - 1, y2], [W - 1, y1]],
                    dtype=np.int32)
    ov = vis.copy(); cv2.fillPoly(ov, [poly], (0, 140, 255))
    vis = cv2.addWeighted(ov, 0.22, vis, 0.78, 0)
    p = os.path.join(a.out, "line_precise.jpg")
    cv2.imwrite(p, vis, [cv2.IMWRITE_JPEG_QUALITY, 92])
    print("\n->", p)
    with open(os.path.join(a.out, "line_precise.json"), "w", encoding="utf-8") as fh:
        json.dump({"top": [x_at_y(y1), y1], "bottom": [x_at_y(y2), y2],
                   "polygon": poly.tolist(),
                   "x_at_y": {str(y): x_at_y(y) for y in (350, 450, 550, 650, 750, 850, 950, 1050)}},
                  fh, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
