"""标定应急车道 ROI：把用户标注图里的红线映射到视频坐标并可视化验证。

背景：用户在 downloads/image_43.png 上用红线画出了应急车道分界线，
线右侧为应急车道。该图是 PotPlayer 播放截图，含标题栏/进度条与黑边，
必须先把图像像素映射回视频帧像素，否则 ROI 会整体偏移。

红线在图中是纯红 (约 255,0,0)，可以按颜色阈值提取后做直线拟合。

用法：
    python calibrate_roi.py --image <标注图> --video <mp4> --out <输出目录>
"""
import argparse
import json
import os
import sys

import cv2
import numpy as np

sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def extract_red_line(path):
    """提取标注图中的红线，并与红圈分离。

    标注图里有多处红色：车道分界线（细长斜线）、圈出目标车的红圆圈、
    播放器进度条等。必须按连通域的**形状特征**挑出那条线：
    细长（面积/外接框面积低）、跨度大、接近对角走向。
    """
    img = cv2.imread(path)
    if img is None:
        raise SystemExit("无法读取图片: %s" % path)
    H, W = img.shape[:2]
    b = img[:, :, 0].astype(np.int16)
    g = img[:, :, 1].astype(np.int16)
    r = img[:, :, 2].astype(np.int16)
    mask = ((r > 140) & (g < 120) & (b < 120) &
            ((r - g) > 60) & ((r - b) > 60)).astype(np.uint8)
    # 去掉最底部播放器区域（进度条）
    mask[int(H * 0.945):, :] = 0

    n, labels, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    best, best_score = None, -1.0
    print("  连通域数量: %d" % (n - 1))
    for i in range(1, n):
        x, y, w, h, area = stats[i]
        if area < 200:
            continue
        fill = area / float(max(1, w * h))          # 细长线填充率低
        span = max(w, h)                            # 跨度
        aspect = max(w / max(1, h), h / max(1, w))
        # 线的特征：跨度大、填充率低、长宽比大
        score = span * (1.0 - fill) * min(aspect, 12.0)
        print("    #%d bbox=(%d,%d,%d,%d) area=%d fill=%.2f aspect=%.1f score=%.0f"
              % (i, x, y, w, h, area, fill, aspect, score))
        if score > best_score:
            best, best_score = i, score
    if best is None:
        raise SystemExit("未找到候选红线连通域")
    ys, xs = np.nonzero(labels == best)
    pts = np.stack([xs, ys], axis=1)
    print("  选中连通域 #%d，像素 %d" % (best, len(pts)))
    return pts, (W, H), mask


def find_video_area(img):
    """在截图里定位视频画面区域（排除标题栏、进度条与黑边）。

    做法：逐行统计"非黑且饱和度/方差较高"的像素比例，取比例高的连续行段；
    再用列方向同理收紧。对 PotPlayer 这类播放器截图足够稳。
    """
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    H, W = gray.shape
    # 行：视频画面亮度分布丰富，黑边接近全黑
    row_std = gray.std(axis=1)
    row_mean = gray.mean(axis=1)
    rows = np.nonzero((row_std > 12) & (row_mean > 20))[0]
    if len(rows) == 0:
        return 0, 0, W, H
    y0, y1 = int(rows.min()), int(rows.max())
    sub = gray[y0:y1 + 1]
    col_std = sub.std(axis=0)
    cols = np.nonzero(col_std > 12)[0]
    x0, x1 = (int(cols.min()), int(cols.max())) if len(cols) else (0, W - 1)
    return x0, y0, x1, y1


def fit_line(pts):
    """总体最小二乘拟合直线（SVD），返回 (方向单位向量, 质心)。

    不能用 cv2.fitLine：它按 y 参数化，遇到近垂直直线会给出错误的"水平"方向。
    SVD 对任意角度都稳。
    """
    p = pts.astype(np.float64)
    c = p.mean(axis=0)
    u, s, vt = np.linalg.svd(p - c, full_matrices=False)
    d = vt[0]
    return float(d[0]), float(d[1]), float(c[0]), float(c[1])


def line_x_at_y(vx, vy, x0, y0, y):
    """给定 y，求直线上的 x（用于构造 ROI 多边形）。"""
    if abs(vy) < 1e-9:
        return x0
    t = (y - y0) / vy
    return x0 + t * vx


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--image", required=True)
    ap.add_argument("--video", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--t", type=float, default=57.0, help="用于出对比图的视频时间点（秒）")
    a = ap.parse_args()

    img = cv2.imread(a.image)
    pts, (IW, IH), mask = extract_red_line(a.image)
    print("标注图尺寸: %dx%d" % (IW, IH))
    print("提取到红色像素: %d" % len(pts))
    if len(pts) < 50:
        raise SystemExit("红线像素太少，阈值可能需要调整")

    x0, y0, x1, y1 = find_video_area(img)
    vw, vh = x1 - x0 + 1, y1 - y0 + 1
    print("检测到视频画面区域: x[%d,%d] y[%d,%d]  -> %dx%d" % (x0, x1, y0, y1, vw, vh))

    cap = cv2.VideoCapture(a.video)
    fw = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    fh = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    print("视频分辨率: %dx%d @ %.1ffps" % (fw, fh, fps))

    sx, sy = fw / vw, fh / vh
    print("映射比例: sx=%.4f sy=%.4f  (宽高比 图%.4f vs 视频%.4f)"
          % (sx, sy, vw / vh, fw / fh))

    # 红线像素 -> 视频坐标
    vpts = np.stack([(pts[:, 0] - x0) * sx, (pts[:, 1] - y0) * sy], axis=1)
    vx, vy, px, py = fit_line(vpts)
    print("红线拟合(视频坐标): 方向=(%.4f,%.4f) 过点=(%.1f,%.1f)" % (vx, vy, px, py))
    print("  斜率 dx/dy = %.4f" % (vx / vy if abs(vy) > 1e-9 else float('inf')))

    # 端点（用红线像素的 y 范围裁剪）
    ytop = float(np.percentile(vpts[:, 1], 2))
    ybot = float(np.percentile(vpts[:, 1], 98))
    ptop = (line_x_at_y(vx, vy, px, py, ytop), ytop)
    pbot = (line_x_at_y(vx, vy, px, py, ybot), ybot)
    print("红线端点(视频坐标): 上=(%.0f,%.0f) 下=(%.0f,%.0f)" % (ptop[0], ptop[1], pbot[0], pbot[1]))

    # 构造应急车道 ROI 多边形：红线 -> 右下角 -> 右上角
    poly = [[ptop[0], ptop[1]], [pbot[0], pbot[1]], [fw - 1, fh - 1], [fw - 1, ptop[1]]]

    os.makedirs(a.out, exist_ok=True)
    # 输出对比图：在对应视频帧上画 ROI
    cap.set(cv2.CAP_PROP_POS_FRAMES, int(a.t * fps))
    ok, frame = cap.read()
    if ok:
        vis = frame.copy()
        cv2.polylines(vis, [np.array(poly, dtype=np.int32)], True, (0, 255, 255), 3)
        cv2.line(vis, (int(ptop[0]), int(ptop[1])), (int(pbot[0]), int(pbot[1])),
                 (0, 0, 255), 3)
        ov = vis.copy()
        cv2.fillPoly(ov, [np.array(poly, dtype=np.int32)], (0, 140, 255))
        vis = cv2.addWeighted(ov, 0.22, vis, 0.78, 0)
        p = os.path.join(a.out, "roi_check_t%05.1f.jpg" % a.t)
        cv2.imwrite(p, vis)
        print("对比图 ->", p)
    cap.release()

    roi = {
        "image": os.path.abspath(a.image),
        "image_size": [IW, IH],
        "video_area_px": [x0, y0, x1, y1],
        "video_size": [fw, fh],
        "scale": [sx, sy],
        "line": {"point": [px, py], "dir": [vx, vy],
                 "top": list(ptop), "bottom": list(pbot)},
        "emergency_lane_polygon": poly,
        "note": "多边形 = 红线右侧区域。判定用车辆框底部中心点做 point-in-polygon。",
    }
    rp = os.path.join(a.out, "roi.json")
    with open(rp, "w", encoding="utf-8") as fh:
        json.dump(roi, fh, ensure_ascii=False, indent=2)
    print("ROI ->", rp)
    print("多边形:", [[round(x), round(y)] for x, y in poly])


if __name__ == "__main__":
    main()
