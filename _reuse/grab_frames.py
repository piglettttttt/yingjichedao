"""按固定间隔抽帧出图，用于人工确认应急车道占用的时间分布与 ROI 覆盖情况。"""
import argparse
import os
import sys

import cv2
import numpy as np

sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--every", type=float, default=5.0, help="每多少秒出一张")
    ap.add_argument("--roi", default=None, help="可选 roi.json，叠加红线便于确认")
    ap.add_argument("--scale", type=float, default=1.0, help="输出缩放")
    a = ap.parse_args()

    os.makedirs(a.out, exist_ok=True)
    cap = cv2.VideoCapture(a.video)
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    dur = total / fps
    print("视频 %.1fs / %d 帧 @ %.1ffps" % (dur, total, fps))

    line = None
    if a.roi:
        import json
        with open(a.roi, encoding="utf-8") as fh:
            r = json.load(fh)
        line = r["line"]
        print("叠加红线: top=%s bottom=%s" % (line["top"], line["bottom"]))

    t = 0.0
    n = 0
    while t <= dur:
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(t * fps))
        ok, frame = cap.read()
        if not ok:
            break
        # 读取帧内烧录的时间戳区域之外，画面左上已有时间戳，保留原样
        vis = frame.copy()
        if line:
            p1 = (int(line["top"][0]), int(line["top"][1]))
            p2 = (int(line["bottom"][0]), int(line["bottom"][1]))
            cv2.line(vis, p1, p2, (0, 0, 255), 3)
        if a.scale != 1.0:
            vis = cv2.resize(vis, None, fx=a.scale, fy=a.scale)
        p = os.path.join(a.out, "t%05.1f.jpg" % t)
        cv2.imwrite(p, vis, [cv2.IMWRITE_JPEG_QUALITY, 88])
        n += 1
        t += a.every
    cap.release()
    print("输出 %d 张 -> %s" % (n, a.out))


if __name__ == "__main__":
    main()
