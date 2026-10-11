"""在参考帧中自动检测"最右车道与应急车道之间的白色实线"。

思路：该白线在画面右下区域表现为一条细长的高亮带。对若干水平条带（y 带）
统计亮度峰值列，再对这些点做直线拟合；用 Hough 变换交叉验证。

输出：白线在视频坐标下的直线参数 + 可视化图，供人工确认。
"""
import argparse
import os
import sys

import cv2
import numpy as np

sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def detect_lane_line(img, x_from_ratio=0.5, y_bands=14, y_top_ratio=0.35, y_bot_ratio=0.98):
    """逐条带找白线列位置。

    白线特征：局部亮度显著高于两侧、且饱和度低（灰白）。
    只搜索画面右半部（x > x_from_ratio*W）避免混入其它车道线。
    """
    H, W = img.shape[:2]
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY).astype(np.float32)
    sat = hsv[:, :, 1].astype(np.float32)

    ys = np.linspace(int(H * y_top_ratio), int(H * y_bot_ratio), y_bands).astype(int)
    pts = []
    for y in ys:
        band = gray[max(0, y - 3):y + 4, :]
        sband = sat[max(0, y - 3):y + 4, :]
        prof = band.mean(axis=0)
        sprof = sband.mean(axis=0)
        x0 = int(W * x_from_ratio)
        seg = prof[x0:]
        sseg = sprof[x0:]
        if len(seg) < 20:
            continue
        # 用"高出局部均值"的强度做加权质心，且要求低饱和（白线）
        base = np.median(seg)
        lift = np.clip(seg - base, 0, None)
        lift[sseg > 90] = 0                     # 排除彩色区域（车灯、护栏反光）
        if lift.max() < 8:
            continue
        # 只在最显著的峰附近求质心，避免整行拖偏
        k = int(np.argmax(lift))
        lo, hi = max(0, k - 60), min(len(seg), k + 61)
        w = lift[lo:hi]
        if w.sum() <= 0:
            continue
        c = (np.arange(lo, hi) * w).sum() / w.sum() + x0
        pts.append((float(c), float(y), float(lift.max())))
    return pts


def fit(pts):
    p = np.array([[x, y] for x, y, _ in pts], dtype=np.float64)
    c = p.mean(axis=0)
    _, _, vt = np.linalg.svd(p - c, full_matrices=False)
    return vt[0], c


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--frame", required=True, help="参考帧图片")
    ap.add_argument("--out", required=True)
    ap.add_argument("--x-from", type=float, default=0.5, help="只搜索该 x 比例右侧")
    ap.add_argument("--y-top", type=float, default=0.35)
    ap.add_argument("--y-bot", type=float, default=0.98)
    a = ap.parse_args()

    img = cv2.imread(a.frame)
    if img is None:
        raise SystemExit("读不到 %s" % a.frame)
    H, W = img.shape[:2]
    print("参考帧 %dx%d" % (W, H))

    pts = detect_lane_line(img, a.x_from, y_top_ratio=a.y_top, y_bot_ratio=a.y_bot)
    print("找到 %d 个条带白线点：" % len(pts))
    for x, y, s in pts:
        print("   y=%4.0f  x=%7.1f  强度=%.1f" % (y, x, s))
    if len(pts) < 3:
        raise SystemExit("白线点太少，需调整搜索范围")

    d, c = fit(pts)
    print("\n拟合方向 (dx,dy) = (%.4f, %.4f)  斜率 dx/dy = %.4f"
          % (d[0], d[1], d[0] / d[1] if abs(d[1]) > 1e-9 else float("inf")))
    print("质心 = (%.1f, %.1f)" % (c[0], c[1]))

    def x_at_y(y):
        return c[0] + (y - c[1]) * (d[0] / d[1])

    y1, y2 = 330.0, float(H - 1)
    print("白线在 y=%.0f 时 x=%.0f ; y=%.0f 时 x=%.0f" % (y1, x_at_y(y1), y2, x_at_y(y2)))

    # 可视化
    vis = img.copy()
    cv2.line(vis, (int(x_at_y(y1)), int(y1)), (int(x_at_y(y2)), int(y2)), (0, 0, 255), 3)
    for x, y, _ in pts:
        cv2.circle(vis, (int(x), int(y)), 6, (0, 255, 0), -1)
    # 应急车道区域填充（白线右侧到画面右边）
    poly = np.array([[x_at_y(y1), y1], [x_at_y(y2), y2], [W - 1, y2], [W - 1, y1]],
                    dtype=np.int32)
    ov = vis.copy()
    cv2.fillPoly(ov, [poly], (0, 140, 255))
    vis = cv2.addWeighted(ov, 0.25, vis, 0.75, 0)
    p = os.path.join(a.out, "lane_line_detect.jpg")
    cv2.imwrite(p, vis)
    print("\n可视化 ->", p)
    print("应急车道多边形(视频坐标):", poly.tolist())

    import json
    with open(os.path.join(a.out, "lane_line.json"), "w", encoding="utf-8") as fh:
        json.dump({"line_top": [x_at_y(y1), y1], "line_bottom": [x_at_y(y2), y2],
                   "points": pts, "polygon": poly.tolist()}, fh,
                  ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
