"""从用户在截图上划定的红色 ROI 生成配置。

支持两种标注（与 parse_roi.py 一致）：
    line    画一条线（线右侧为目标区域）
    polygon 画闭合区域

关键处理：
 1. **顶点顺序**：凸包重排。手写/转录顶点极易乱序 → 多边形自相交 → 射线法失效
    （实测踩过：同样车框算出 0.00 重叠，跑出"0 条"）。
 2. **视频画面区定位**：播放器截图含标题栏/进度条，必须取"最长连续非黑行段"，
    否则坐标整体偏移（实测 std 阈值法得到 2916x1862、比例 1.566，与视频 1.778 不符）。
 3. **干扰剔除**：截图里常有红圈等其它红色元素；polygon 模式取最大连通域，
    line 模式用 RANSAC 锁定长直线。
 4. 输出**归一化坐标**，使 ROI 与分辨率无关。

用法：
    python make_roi.py --shot <标注截图> --video <mp4> --out <roi.json> \
        [--mode polygon|line] [--color red]
"""
import argparse
import json
import os
import sys

import cv2
import numpy as np

sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def find_video_area(img, expected_aspect=None):
    """定位播放器截图里的视频画面区：取最长连续非黑行段。"""
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY).astype(np.float32)
    H, W = gray.shape
    bright = gray.mean(axis=1) > 12
    best, start = (0, 0), None
    for y in range(H):
        if bright[y] and start is None:
            start = y
        elif not bright[y] and start is not None:
            if y - start > best[1] - best[0]:
                best = (start, y - 1)
            start = None
    if start is not None and H - start > best[1] - best[0]:
        best = (start, H - 1)
    y0, y1 = best
    col_mean = gray[y0:y1 + 1].mean(axis=0)
    cols = np.nonzero(col_mean > 8)[0]
    x0, x1 = (int(cols.min()), int(cols.max())) if len(cols) else (0, W - 1)
    if expected_aspect:
        h = y1 - y0 + 1
        w_exp = int(round(h * expected_aspect))
        if w_exp <= W:
            cx = (x0 + x1) // 2
            x0 = max(0, cx - w_exp // 2)
            x1 = min(W - 1, x0 + w_exp - 1)
    return x0, y0, x1, y1


def extract_marked(img, color="red", exclude_bottom_ratio=0.055, keep_main=True):
    """提取标注色像素；keep_main 只保留最大连通域（滤掉画面里同色的其它元素）。"""
    H, W = img.shape[:2]
    b = img[:, :, 0].astype(np.int16)
    g = img[:, :, 1].astype(np.int16)
    r = img[:, :, 2].astype(np.int16)
    if color == "red":
        mask = (r > 130) & (g < 120) & (b < 120) & ((r - g) > 55) & ((r - b) > 55)
    elif color == "green":
        mask = (g > 130) & (r < 120) & (b < 120) & ((g - r) > 55) & ((g - b) > 55)
    elif color == "blue":
        mask = (b > 130) & (r < 120) & (g < 140) & ((b - r) > 55) & ((b - g) > 40)
    elif color == "magenta":
        mask = (r > 130) & (b > 130) & (g < 110) & ((r - g) > 55) & ((b - g) > 55)
    elif color == "yellow":
        mask = (r > 150) & (g > 150) & (b < 120) & ((r - b) > 60) & ((g - b) > 60)
    else:
        raise SystemExit("不支持的颜色: %s" % color)
    mask = mask.astype(np.uint8)
    mask[int(H * (1 - exclude_bottom_ratio)):, :] = 0
    if keep_main:
        n, labels, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
        if n > 1:
            area = stats[1:, cv2.CC_STAT_AREA].astype(np.float64)
            box = (stats[1:, cv2.CC_STAT_WIDTH] * stats[1:, cv2.CC_STAT_HEIGHT]).astype(np.float64)
            best = int(np.argmax(area + 0.02 * box)) + 1
            print("  连通域 %d 个，保留最大者（面积 %d, 外接框 %dx%d）"
                  % (n - 1, stats[best, cv2.CC_STAT_AREA],
                     stats[best, cv2.CC_STAT_WIDTH], stats[best, cv2.CC_STAT_HEIGHT]))
            mask = (labels == best).astype(np.uint8)
    ys, xs = np.nonzero(mask)
    return np.stack([xs, ys], axis=1) if len(xs) else np.zeros((0, 2), dtype=int)


def ransac_line(pts, iters=3000, thresh=6.0, min_inliers=40, seed=0):
    """RANSAC 拟合直线，剔除红圈等离群标注。"""
    rng = np.random.default_rng(seed)
    n = len(pts)
    if n < 2:
        return None, np.zeros(n, bool)
    P = pts.astype(np.float64)
    best_mask, best_cnt = np.zeros(n, bool), 0
    for _ in range(iters):
        i, j = rng.choice(n, 2, replace=False)
        p1, p2 = P[i], P[j]
        d = p2 - p1
        L = np.hypot(*d)
        if L < 30:
            continue
        nrm = np.array([-d[1], d[0]]) / L
        mask = np.abs((P - p1) @ nrm) < thresh
        cnt = int(mask.sum())
        if cnt > best_cnt:
            sub = P[mask]
            if float(np.ptp(sub[:, 0]) + np.ptp(sub[:, 1])) > 120:
                best_cnt, best_mask = cnt, mask
    if best_cnt < min_inliers:
        return None, best_mask
    Q = P[best_mask]
    c = Q.mean(axis=0)
    _, _, vt = np.linalg.svd(Q - c, full_matrices=False)
    return (vt[0], c, best_mask), best_mask


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--shot", required=True, help="用户标注的截图")
    ap.add_argument("--video", required=True, help="对应视频")
    ap.add_argument("--out", required=True, help="ROI json 输出路径")
    ap.add_argument("--mode", choices=["polygon", "line"], default="polygon")
    ap.add_argument("--color", default="red")
    ap.add_argument("--side", choices=["right", "left"], default="right")
    ap.add_argument("--t", type=float, default=0.0, help="校验图取视频哪个时间点")
    ap.add_argument("--verify", default=None, help="校验图输出路径（可选）")
    a = ap.parse_args()

    img = cv2.imread(a.shot)
    if img is None:
        raise SystemExit("读不到截图: %s" % a.shot)
    IH, IW = img.shape[:2]
    cap = cv2.VideoCapture(a.video)
    fw = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    fh = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    print("截图 %dx%d ; 视频 %dx%d @%.1ffps" % (IW, IH, fw, fh, fps))

    pts = extract_marked(img, a.color)
    print("提取到 %s 标注像素: %d" % (a.color, len(pts)))
    if len(pts) < 30:
        raise SystemExit("标注像素太少")

    x0, y0, x1, y1 = find_video_area(img, expected_aspect=fw / fh)
    vw, vh = x1 - x0 + 1, y1 - y0 + 1
    print("视频画面区 x[%d,%d] y[%d,%d] -> %dx%d 比例=%.4f（视频 %.4f）"
          % (x0, x1, y0, y1, vw, vh, vw / vh, fw / fh))
    sx, sy = fw / vw, fh / vh
    vpts = np.stack([(pts[:, 0] - x0) * sx, (pts[:, 1] - y0) * sy], axis=1)

    if a.mode == "polygon":
        hull = cv2.convexHull(vpts.astype(np.float32)).reshape(-1, 2)
        print("凸包重排后顶点 %d 个" % len(hull))
        poly = [[float(p[0]), float(p[1])] for p in hull]
    else:
        fit, inliers = ransac_line(vpts)
        if fit is None:
            raise SystemExit("RANSAC 未找到直线")
        d, c, _ = fit
        print("RANSAC 内点 %d/%d" % (int(inliers.sum()), len(vpts)))
        if d[1] < 0:
            d = -d

        def x_at_y(y):
            return c[0] + (y - c[1]) * (d[0] / d[1])
        ytop = float(np.percentile(vpts[inliers][:, 1], 1))
        ybot = float(fh - 1)
        pt, pb = (x_at_y(ytop), ytop), (x_at_y(ybot), ybot)
        print("线端点 上=(%.0f,%.0f) 下=(%.0f,%.0f)" % (pt[0], pt[1], pb[0], pb[1]))
        if a.side == "right":
            poly = [[pt[0], pt[1]], [pb[0], pb[1]], [fw - 1, fh - 1], [fw - 1, pt[1]]]
        else:
            poly = [[pt[0], pt[1]], [pb[0], pb[1]], [0, fh - 1], [0, pt[1]]]
        # 同样跑凸包保证顺序正确
        poly = [[float(p[0]), float(p[1])] for p in
                cv2.convexHull(np.array(poly, dtype=np.float32)).reshape(-1, 2)]

    # 自检：多边形不可自相交、面积合理
    pa = np.array(poly, dtype=np.float32)
    area = cv2.contourArea(pa)
    print("多边形顶点 %d 个，面积 %.0f px²（画面 %.0f px²，占 %.1f%%）"
          % (len(poly), area, fw * fh, 100 * area / (fw * fh)))
    if area <= 0:
        raise SystemExit("多边形面积为 0，标注有问题")

    norm = [[round(float(x) / fw, 6), round(float(y) / fh, 6)] for x, y in poly]
    out = {"source": os.path.abspath(a.shot), "mode": a.mode,
           "video_size": [fw, fh], "shot_size": [IW, IH],
           "video_area_px": [x0, y0, x1, y1],
           "polygon": [[round(float(x)), round(float(y))] for x, y in poly],
           "polygon_norm": norm,
           "note": "顶点已用凸包重排，避免自相交；坐标为归一化比例，与分辨率无关"}
    os.makedirs(os.path.dirname(os.path.abspath(a.out)) or ".", exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as fh_:
        json.dump(out, fh_, ensure_ascii=False, indent=2)
    print("ROI -> %s" % a.out)
    print("归一化多边形: %s" % norm)

    if a.verify:
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(round(a.t * fps)))
        ok, fr = cap.read()
        if ok:
            vis = fr.copy()
            pa2 = np.array(poly, dtype=np.int32)
            ov = vis.copy(); cv2.fillPoly(ov, [pa2], (0, 140, 255))
            vis = cv2.addWeighted(ov, 0.25, vis, 0.75, 0)
            cv2.polylines(vis, [pa2], True, (0, 0, 255), 3)
            okw, buf = cv2.imencode(".jpg", vis, [int(cv2.IMWRITE_JPEG_QUALITY), 92])
            if okw:
                with open(a.verify, "wb") as fh2:
                    fh2.write(buf.tobytes())
                print("校验图 -> %s" % a.verify)
    cap.release()


if __name__ == "__main__":
    main()
