"""在帧上叠加坐标网格并可选裁剪放大，便于人工精确读出车道线坐标。"""
import argparse
import os
import sys

import cv2
import numpy as np

sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--frame", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--step", type=int, default=100)
    ap.add_argument("--crop", nargs=4, type=int, default=None,
                    metavar=("X0", "Y0", "X1", "Y1"))
    ap.add_argument("--zoom", type=float, default=1.0)
    a = ap.parse_args()

    img = cv2.imread(a.frame)
    if img is None:
        raise SystemExit("读不到 %s" % a.frame)
    H, W = img.shape[:2]
    ox, oy = 0, 0
    if a.crop:
        x0, y0, x1, y1 = a.crop
        img = img[y0:y1, x0:x1].copy()
        ox, oy = x0, y0
        print("裁剪 [%d,%d,%d,%d] -> %dx%d" % (x0, y0, x1, y1, img.shape[1], img.shape[0]))

    # 网格（注意：网格坐标标注的是**原图**坐标）
    for x in range(0, W + 1, a.step):
        if x < ox or x > ox + img.shape[1]:
            continue
        cx = x - ox
        cv2.line(img, (cx, 0), (cx, img.shape[0]), (0, 255, 255), 1)
        cv2.putText(img, str(x), (cx + 3, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 255), 2)
    for y in range(0, H + 1, a.step):
        if y < oy or y > oy + img.shape[0]:
            continue
        cy = y - oy
        cv2.line(img, (0, cy), (img.shape[1], cy), (0, 255, 255), 1)
        cv2.putText(img, str(y), (4, cy + 20), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 255), 2)

    if a.zoom != 1.0:
        img = cv2.resize(img, None, fx=a.zoom, fy=a.zoom,
                         interpolation=cv2.INTER_CUBIC)
    os.makedirs(os.path.dirname(os.path.abspath(a.out)) or ".", exist_ok=True)
    cv2.imwrite(a.out, img, [cv2.IMWRITE_JPEG_QUALITY, 92])
    print("-> %s  (%dx%d)" % (a.out, img.shape[1], img.shape[0]))


if __name__ == "__main__":
    main()
