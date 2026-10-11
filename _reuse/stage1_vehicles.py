"""车辆优先的检测流程（按用户指定的正确架构）。

架构（务必遵守）：
    1. 车辆检测   —— 不依赖车牌可见性，保证"有没有车"能判对
    2. ROI 归属   —— 判定车辆是否在应急车道
    3. 车牌识别   —— 附属于车辆；识别失败也保留车辆记录，留待人工修正

为什么不能用"车牌优先"：实测车牌检测器（plate_detect.onnx）在行车记录仪侧向车道上
完全漏检（5 辆车只找到 2 辆），导致应急车道上的车"不存在"。车辆检测器（YOLO）
在同样帧上直接检出，所以必须以车辆为入口。

用法：
    python stage1_vehicles.py --video <mp4> --roi <roi.json> --out <jsonl> \
        [--start 0] [--end 30] [--fps 15] [--imgsz 640] [--conf 0.25]
"""
import argparse
import json
import os
import sys
import time

import cv2
import numpy as np
from ultralytics import YOLO

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import runmeta  # noqa: E402

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# COCO 中的道路车辆类
VEHICLE_CLASSES = {2: "car", 3: "motorcycle", 5: "bus", 7: "truck"}


def load_roi(path, fw, fh):
    """读 ROI；优先用归一化坐标（与分辨率无关）。"""
    with open(path, encoding="utf-8") as fh_:
        r = json.load(fh_)
    if "polygon_norm" in r:
        poly = [(float(x) * fw, float(y) * fh) for x, y in r["polygon_norm"]]
    else:
        poly = [tuple(p) for p in r["polygon"]]
    return poly


def point_in_polygon(pt, poly):
    """射线法。"""
    x, y = pt
    inside = False
    n = len(poly)
    j = n - 1
    for i in range(n):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if ((yi > y) != (yj > y)) and (x < (xj - xi) * (y - yi) / (yj - yi + 1e-12) + xi):
            inside = not inside
        j = i
    return inside


def bottom_span_in_roi(bbox, poly, samples=21):
    """车辆**底边**（车轮着地线）落在 ROI 内的水平比例。

    用底边而不是检测框整块面积：车牌/车身贴在车尾上部，
    用整块面积会把"车已压线但车身仍在车道内"的情况算成占用；
    用底边更接近"车实际压在哪个区域"。
    """
    x1, y1, x2, y2 = bbox
    y = y2 - 1.0                      # 底边略上移 1px，避免正好落在边界上
    if x2 <= x1:
        return 0.0
    hit = 0
    for i in range(samples):
        x = x1 + (x2 - x1) * (i + 0.5) / samples
        if point_in_polygon((x, y), poly):
            hit += 1
    return hit / float(samples)


def check_load_roi_and_bottom_span():
    """load_roi 的归一化换算 + bottom_span_in_roi 的边界行为。"""
    import json
    import tempfile
    poly_sq = [(0.0, 0.0), (100.0, 0.0), (100.0, 100.0), (0.0, 100.0)]
    # 底边完全在矩形内 → 1.0
    assert bottom_span_in_roi((10, 10, 90, 90), poly_sq) == 1.0, "完全在内应为 1.0"
    # 底边一半在内 → 约 0.5
    assert abs(bottom_span_in_roi((50, 10, 150, 90), poly_sq) - 0.5) < 0.06, "一半在内应约 0.5"
    # 底边完全在外 → 0.0
    assert bottom_span_in_roi((200, 10, 300, 90), poly_sq) == 0.0, "完全在外应为 0.0"
    # 退化的零宽框不能除零
    assert bottom_span_in_roi((10, 10, 10, 90), poly_sq) == 0.0, "零宽框应返回 0"

    with tempfile.TemporaryDirectory() as td:
        p = os.path.join(td, "r.json")
        with open(p, "w", encoding="utf-8") as fh:
            json.dump({"polygon_norm": [[0.0, 0.0], [1.0, 0.0], [1.0, 1.0]]}, fh)
        poly = load_roi(p, 1920, 1080)
        assert poly[1] == (1920.0, 0.0), "归一化必须按当前视频分辨率换算：%s" % (poly,)
        q = os.path.join(td, "r2.json")
        with open(q, "w", encoding="utf-8") as fh:
            json.dump({"polygon": [[3, 4], [5, 6]]}, fh)
        assert load_roi(q, 1920, 1080) == [(3, 4), (5, 6)], "无 polygon_norm 时用像素多边形"
    print("  [PASS] load_roi / bottom_span_in_roi")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", required=True)
    ap.add_argument("--roi", required=True, help="parse_roi.py 产出的 roi.json")
    ap.add_argument("--weights", default="yolo11n.pt")
    ap.add_argument("--out", required=True)
    ap.add_argument("--start", type=float, default=0.0)
    ap.add_argument("--end", type=float, default=30.0)
    ap.add_argument("--fps", type=float, default=15.0)
    ap.add_argument("--imgsz", type=int, default=640)
    ap.add_argument("--conf", type=float, default=0.25)
    ap.add_argument("--overlap-thresh", type=float, default=0.35,
                    help="车辆底边落在应急车道内的比例阈值")
    ap.add_argument("--lane-top", type=float, default=None,
                    help="忽略画面上方该比例以内（远处目标小、误检多），默认取 ROI 顶边")
    a = ap.parse_args()

    cap = cv2.VideoCapture(a.video)
    if not cap.isOpened():
        raise SystemExit("打不开视频: %s" % a.video)
    fw = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    fh = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    vfps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))

    poly = load_roi(a.roi, fw, fh)
    runmeta.warn_roi_early(a.roi, (fw, fh))   # 越界/错位要在跑之前就喊出来
    if a.lane_top is None:
        lane_top = min(p[1] for p in poly)
    else:
        lane_top = fh * a.lane_top
    print("视频 %dx%d @%.1ffps  共 %d 帧" % (fw, fh, vfps, total))
    print("ROI 多边形: %s" % [[round(x), round(y)] for x, y in poly])
    print("lane_top = %.0f（该 y 以上的目标忽略）" % lane_top)

    f0 = int(a.start * vfps)
    f1 = min(int(a.end * vfps), total)
    step = max(1, int(round(vfps / a.fps)))
    print("分析帧 %d..%d，每 %d 帧取 1（≈%.1f fps）" % (f0, f1, step, vfps / step))

    model = YOLO(a.weights)
    cap.set(cv2.CAP_PROP_POS_FRAMES, f0)
    os.makedirs(os.path.dirname(os.path.abspath(a.out)) or ".", exist_ok=True)

    n_frames = n_veh = n_in_roi = n_skipped_top = 0
    t0 = time.time()
    with open(a.out, "w", encoding="utf-8") as fh_out:
        for fi in range(f0, f1):
            ok, frame = cap.read()
            if not ok:
                break
            if (fi - f0) % step:
                continue
            n_frames += 1
            ts = fi / vfps
            res = model.predict(frame, conf=a.conf, imgsz=a.imgsz,
                                classes=list(VEHICLE_CLASSES.keys()), verbose=False)[0]
            if res.boxes is None or len(res.boxes) == 0:
                continue
            for bx in res.boxes:
                cid = int(bx.cls.item())
                x1, y1, x2, y2 = [float(v) for v in bx.xyxy[0].tolist()]
                cf = float(bx.conf.item())
                # lane_top：忽略画面远处的目标
                if y2 < lane_top:
                    n_skipped_top += 1
                    continue
                inroi_ratio = bottom_span_in_roi((x1, y1, x2, y2), poly)
                n_veh += 1
                if inroi_ratio >= a.overlap_thresh:
                    n_in_roi += 1
                fh_out.write(json.dumps({
                    "frame": fi, "t": round(ts, 3),
                    "cls": VEHICLE_CLASSES[cid], "cls_id": cid,
                    "conf": round(cf, 4),
                    "box": [round(x1, 1), round(y1, 1), round(x2, 1), round(y2, 1)],
                    "w": round(x2 - x1, 1), "h": round(y2 - y1, 1),
                    "bottom_center": [round((x1 + x2) / 2, 1), round(y2, 1)],
                    "roi_overlap": round(inroi_ratio, 3),
                    "in_emergency_lane": inroi_ratio >= a.overlap_thresh,
                }, ensure_ascii=False) + "\n")
            if n_frames % 50 == 0 and n_frames:
                el = time.time() - t0
                print("  %d 帧 / 检出 %d（应急车道内 %d）%.1fs (%.1f 帧/秒)"
                      % (n_frames, n_veh, n_in_roi, el, n_frames / el), flush=True)
    cap.release()
    el = time.time() - t0
    print("完成：处理 %d 帧，检出车辆 %d 次（应急车道内 %d 次），"
          "因 lane_top 忽略 %d 次，用时 %.1fs"
          % (n_frames, n_veh, n_in_roi, n_skipped_top, el))
    print("-> %s" % a.out)

    # 运行元数据：ROI 逐视频重划 + 参数随视频调，必须记下这一次用的是哪份 ROI、哪组参数
    n_meta = runmeta.collect(
        __file__, a, video=a.video, roi=a.roi, inputs={"weights": a.weights},
        extra={"video_size": [fw, fh], "video_fps_declared": round(vfps, 4),
               "n_frames_total": total, "lane_top_px": round(lane_top, 1),
               "stats": {"n_frames_analyzed": n_frames, "n_veh": n_veh,
                         "n_in_roi": n_in_roi, "n_skipped_top": n_skipped_top,
                         "elapsed_s": round(el, 1)}})
    n_meta["outputs"] = {"veh_jsonl": {"path": os.path.abspath(a.out),
                                       "size": os.path.getsize(a.out),
                                       "sha256": runmeta.sha256_file(a.out)}}
    runmeta.write(a.out + ".runmeta.json", n_meta)
    return 0


if __name__ == "__main__":
    sys.exit(main())
