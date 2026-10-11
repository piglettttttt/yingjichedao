"""解析用户标注的 ROI 截图 —— 每个视频检测前的第一步。

设计立场（重要）：
    "哪块是应急车道"对人一眼就成立，对机器却是不可靠推理。
    本项目实测两种自动提线方案都失败（一次偏右 210px，一次拟合出斜率 -6.19 的荒谬线）。
    因此 ROI **不自作聪明地自动识别**，而是由用户用截图划线指定，本工具只做解析与校验。

支持两种标注方式：
    line    用户画一条线标出应急车道边界（线右侧为应急车道）—— 推荐
    polygon 用户画出闭合区域或多点标注（取凸包）

稳健性要点：
    标注图里常有其它红色元素（圈车的红圈、播放器进度条）。因此用 **RANSAC 拟合直线**
    自动剔除离群点，而不是直接用全部彩色像素做最小二乘。

用法：
    python parse_roi.py --shot <标注截图> --video <mp4> --out <输出目录> \
        [--mode line] [--t 3.0] [--color red]
"""
import argparse
import json
import os
import sys

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import runmeta  # noqa: E402

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# 支持的标注色（BGR 阈值方向：主通道明显高于另两个）
COLOR_RULES = {
    "red":    (2, 0, 1),   # B>G, R 最大
    "blue":   (0, 2, 1),
    "green":  (0, 1, 2),
    "yellow": (0, 1, 2),   # 黄 = G+R 高，B 低；单独处理
    "magenta": (0, 2, 1),
}


def find_video_area(img, expected_aspect=None):
    """在播放器截图里定位视频画面区：找**最长的连续非黑行段**。

    踩坑：早期版本用"std>12 的行范围"，会把下方播放器控制条也算进去，
    得到 2916x1862（比例 1.566），而视频是 1.778，导致坐标映射整体偏移。
    顶部标题栏、画面、控制条之间都有近全黑的窄带，所以取最长连续段才稳。
    """
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY).astype(np.float32)
    H, W = gray.shape
    row_mean = gray.mean(axis=1)
    bright = row_mean > 12

    # 找最长连续 True 段
    best = (0, 0)
    start = None
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

    # 在行段内用列均值定位左右边界（左右通常有窄黑边）
    if y1 > y0:
        col_mean = gray[y0:y1 + 1].mean(axis=0)
        cols = np.nonzero(col_mean > 8)[0]
        x0, x1 = (int(cols.min()), int(cols.max())) if len(cols) else (0, W - 1)
    else:
        x0, x1 = 0, W - 1

    # 用已知视频宽高比校正：以行段高度为准推算宽度，并居中
    if expected_aspect:
        h = y1 - y0 + 1
        w_exp = int(round(h * expected_aspect))
        if w_exp <= W:
            cx = (x0 + x1) // 2
            x0 = max(0, cx - w_exp // 2)
            x1 = min(W - 1, x0 + w_exp - 1)
    return x0, y0, x1, y1


def extract_marked(img, color="red", exclude_bottom_ratio=0.055, keep_main=True):
    """提取指定颜色的标注像素坐标（先裁掉可能的进度条区域）。

    keep_main：只保留**最大连通域**。标注截图里除用户画的 ROI 外，
    画面本身也可能有同色像素（红色车身、尾灯、其它 UI），
    不过滤会让凸包被远端的杂散点拉成覆盖整幅图（实测凸包顶点里出现
    y=-55、x=23 这类外点）。只取最大连通域即可稳定锁定用户的手绘区域。
    """
    H, W = img.shape[:2]
    b = img[:, :, 0].astype(np.int16)
    g = img[:, :, 1].astype(np.int16)
    r = img[:, :, 2].astype(np.int16)

    if color == "red":
        mask = (r > 130) & (g < 120) & (b < 120) & ((r - g) > 55) & ((r - b) > 55)
    elif color == "blue":
        mask = (b > 130) & (r < 120) & (g < 140) & ((b - r) > 55) & ((b - g) > 40)
    elif color == "green":
        mask = (g > 130) & (r < 120) & (b < 120) & ((g - r) > 55) & ((g - b) > 55)
    elif color == "magenta":
        mask = (r > 130) & (b > 130) & (g < 110) & ((r - g) > 55) & ((b - g) > 55)
    elif color == "yellow":
        mask = (r > 150) & (g > 150) & (b < 120) & ((r - b) > 60) & ((g - b) > 60)
    else:
        raise SystemExit("不支持的颜色: %s" % color)

    mask = mask.astype(np.uint8)
    mask[int(H * (1 - exclude_bottom_ratio)):, :] = 0     # 排除底部播放器控制条

    if keep_main:
        n, labels, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
        if n > 1:
            # 用"面积"选主连通域；对细笔画与实心块都适用
            areas = stats[1:, cv2.CC_STAT_AREA]
            # 括号化：也考虑外接框很大的连通域（用户可能画得断续）
            boxes = stats[1:, cv2.CC_STAT_WIDTH] * stats[1:, cv2.CC_STAT_HEIGHT]
            score = areas.astype(np.float64) + 0.02 * boxes
            best = int(np.argmax(score)) + 1
            keep = (labels == best).astype(np.uint8)
            print("  连通域 %d 个，保留最大者 #%d（面积 %d，外接框 %dx%d）"
                  % (n - 1, best, stats[best, cv2.CC_STAT_AREA],
                     stats[best, cv2.CC_STAT_WIDTH], stats[best, cv2.CC_STAT_HEIGHT]))
            mask = keep

    ys, xs = np.nonzero(mask)
    return np.stack([xs, ys], axis=1) if len(xs) else np.zeros((0, 2), dtype=int), mask


def ransac_line(pts, iters=3000, thresh=6.0, min_inliers=40, seed=0):
    """RANSAC 拟合直线，返回 (方向, 内点掩码)。

    标注图里除目标线外常有红圈等干扰，最小二乘会被拖偏；
    RANSAC 随机采两点定线、统计内点，能自动锁定那条长直线。
    """
    rng = np.random.default_rng(seed)
    n = len(pts)
    if n < 2:
        return None, np.zeros(n, dtype=bool)
    best_mask = np.zeros(n, dtype=bool)
    best_cnt = 0
    P = pts.astype(np.float64)
    for _ in range(iters):
        i, j = rng.choice(n, 2, replace=False)
        p1, p2 = P[i], P[j]
        d = p2 - p1
        L = np.hypot(*d)
        if L < 30:                      # 两点太近，方向不稳
            continue
        nrm = np.array([-d[1], d[0]]) / L
        dist = np.abs((P - p1) @ nrm)
        mask = dist < thresh
        cnt = int(mask.sum())
        if cnt > best_cnt:
            # 要求内点跨度足够大，避免锁到局部小结构
            sub = P[mask]
            span = float(np.ptp(sub[:, 0]) + np.ptp(sub[:, 1]))
            if span > 120:
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
    ap.add_argument("--video", required=True, help="对应视频（用于对齐首帧与出校验图）")
    ap.add_argument("--out", required=True)
    ap.add_argument("--mode", choices=["line", "polygon"], default="line")
    ap.add_argument("--color", default="red", choices=list(COLOR_RULES.keys()))
    ap.add_argument("--t", type=float, default=0.0,
                    help="截图取自视频的哪个时间点（秒）；用于校验映射是否正确")
    ap.add_argument("--side", choices=["right", "left"], default="right",
                    help="线哪一侧是目标区域（默认右侧）")
    a = ap.parse_args()

    img = cv2.imread(a.shot)
    if img is None:
        raise SystemExit("读不到截图: %s" % a.shot)
    IH, IW = img.shape[:2]
    print("标注截图 %dx%d" % (IW, IH))

    cap = cv2.VideoCapture(a.video)
    fw = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    fh = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    print("视频 %dx%d @%.1fps" % (fw, fh, fps))

    pts, mask = extract_marked(img, a.color)
    print("提取到 %s 标注像素: %d" % (a.color, len(pts)))
    if len(pts) < 30:
        raise SystemExit("标注像素太少，检查颜色或重标")

    x0, y0, x1, y1 = find_video_area(img, expected_aspect=fw / fh)
    vw, vh = x1 - x0 + 1, y1 - y0 + 1
    print("视频画面区域: x[%d,%d] y[%d,%d] -> %dx%d  比例=%.4f" % (x0, x1, y0, y1, vw, vh, vw / vh))

    sx, sy = fw / vw, fh / vh
    print("映射比例 sx=%.4f sy=%.4f" % (sx, sy))
    if abs(vw / vh - fw / fh) > 0.02:
        print("  ⚠ 宽高比仍不一致（%.4f vs %.4f）：建议用无黑边截图，或后续加 --crop 手填"
              % (vw / vh, fw / fh))

    # 映射到视频坐标
    vpts = np.stack([(pts[:, 0] - x0) * sx, (pts[:, 1] - y0) * sy], axis=1)

    os.makedirs(a.out, exist_ok=True)
    result = {"shot": os.path.abspath(a.shot), "video_size": [fw, fh],
              "shot_size": [IW, IH], "video_area_px": [x0, y0, x1, y1]}

    if a.mode == "line":
        fit, inliers = ransac_line(vpts)
        if fit is None:
            raise SystemExit("RANSAC 未找到直线（内点不足），请确认画的是线而不是闭合圈")
        d, c, _ = fit
        print("RANSAC 内点: %d/%d" % (int(inliers.sum()), len(vpts)))
        print("方向 (dx,dy)=(%.4f,%.4f)  斜率 dx/dy=%.4f" % (d[0], d[1], d[0] / d[1]))
        if d[1] < 0:
            d = -d
        def x_at_y(y):
            return c[0] + (y - c[1]) * (d[0] / d[1])
        ytop = float(np.percentile(vpts[inliers][:, 1], 1))
        ybot = float(fh - 1)
        ptop, pbot = (x_at_y(ytop), ytop), (x_at_y(ybot), ybot)
        print("线端点(视频坐标): 上=(%.0f,%.0f) 下=(%.0f,%.0f)" % (ptop[0], ptop[1], pbot[0], pbot[1]))

        if a.side == "right":
            poly = [[ptop[0], ptop[1]], [pbot[0], pbot[1]], [fw - 1, fh - 1], [fw - 1, ptop[1]]]
        else:
            poly = [[ptop[0], ptop[1]], [pbot[0], pbot[1]], [0, fh - 1], [0, ptop[1]]]
        result.update({"mode": "line", "line_top": list(ptop), "line_bottom": list(pbot),
                       "slope_dxdy": d[0] / d[1], "ransac_inliers": int(inliers.sum()),
                       "polygon": poly})
    else:
        hull = cv2.convexHull(vpts.astype(np.float32)).reshape(-1, 2)
        print("凸包顶点数: %d" % len(hull))
        result.update({"mode": "polygon", "polygon": hull.tolist()})

    # 归一化坐标（比例），使 ROI 与分辨率无关，便于换视频/换分辨率复用
    poly = result["polygon"]
    result["polygon_norm"] = [[round(x / fw, 6), round(y / fh, 6)] for x, y in poly]
    result["lane_top_norm"] = round(float(np.percentile([p[1] for p in poly], 0) / fh), 6)

    # 校验图：把解析出的 ROI 叠到对应视频帧上
    cap.set(cv2.CAP_PROP_POS_FRAMES, int(round(a.t * fps)))
    ok, frame = cap.read()
    if ok:
        vis = frame.copy()
        pa = np.array(poly, dtype=np.int32)
        ov = vis.copy(); cv2.fillPoly(ov, [pa], (0, 140, 255))
        vis = cv2.addWeighted(ov, 0.25, vis, 0.75, 0)
        cv2.polylines(vis, [pa], True, (0, 0, 255), 3)
        p = os.path.join(a.out, "roi_verify.jpg")
        cv2.imwrite(p, vis, [cv2.IMWRITE_JPEG_QUALITY, 92])
        print("校验图 ->", p)
    cap.release()

    rp = os.path.join(a.out, "roi.json")
    # 注意：文件句柄不能叫 fh —— 本函数里 fh 是**视频高度**，
    # 之前写成 `with open(...) as fh` 把 fh 覆盖成文件对象，
    # 于是后面 (fw, fh) 变成 (宽度, 文件对象)，ROI 校验直接失效。
    with open(rp, "w", encoding="utf-8") as fp:
        json.dump(result, fp, ensure_ascii=False, indent=2)
    print("ROI -> %s" % rp)
    print("多边形(像素):", [[round(x), round(y)] for x, y in poly])
    print("多边形(归一化):", result["polygon_norm"])
    # 越界要在这里就提示：此时人刚画完，改还来得及
    runmeta.warn_roi_early(rp, (fw, fh), tag="ROI 校验")
    print("请打开 %s 确认叠加的 ROI 与标注一致后再开始检测" % os.path.join(a.out, "roi_verify.jpg"))

    # ROI 是逐视频人工重划的，必须记下这次标定的来源截图与映射比例
    meta = runmeta.collect(
        __file__, a, video=a.video, roi=rp,
        inputs={"shot": a.shot},
        extra={"video_fps_declared": round(fps, 4),
               "video_area_px": [int(v) for v in (x0, y0, x1, y1)],
               "map_scale": {"sx": round(sx, 6), "sy": round(sy, 6),
                             "video_area_wh": [int(vw), int(vh)]},
               "n_marked_px": int(len(pts)),
               "outputs": {"roi_json": os.path.abspath(rp)}})
    runmeta.write(os.path.join(a.out, "run_meta.json"), meta)


if __name__ == "__main__":
    main()
