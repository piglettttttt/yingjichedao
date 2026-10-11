"""车辆检测冒烟测试：确认 YOLO 能稳定检出道路车辆，并与用户人眼记录对位。

这是对"车牌检测器漏车"这一根因的修复验证——
先证明通用车辆检测器在同样帧上能检出那些被漏掉的车。
"""
import argparse
import os
import sys

import cv2
import numpy as np
from ultralytics import YOLO

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# COCO 里的道路车辆类
VEHICLE_CLASSES = {2: "car", 3: "motorcycle", 5: "bus", 7: "truck"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", required=True)
    ap.add_argument("--weights", default="yolo11n.pt")
    ap.add_argument("--times", nargs="+", type=float, required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--conf", type=float, default=0.25)
    ap.add_argument("--imgsz", type=int, default=640)
    a = ap.parse_args()

    os.makedirs(a.out, exist_ok=True)
    model = YOLO(a.weights)
    cap = cv2.VideoCapture(a.video)
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    W = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    H = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    print("视频 %dx%d @%.1ffps  模型=%s" % (W, H, fps, a.weights))

    # 读取 ROI（若存在）
    roi_poly = None
    rp = os.path.join(os.path.dirname(os.path.abspath(__file__)), "run", "roi_v2.json")
    if os.path.exists(rp):
        import json
        roi_poly = [tuple(p) for p in json.load(open(rp, encoding="utf-8"))["polygon"]]
        print("已载入 ROI 多边形（roi_v2.json）")

    for t in a.times:
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(round(t * fps)))
        ok, fr = cap.read()
        if not ok:
            print("读帧失败 t=%.2f" % t)
            continue
        res = model.predict(fr, conf=a.conf, imgsz=a.imgsz, verbose=False)[0]
        vis = fr.copy()
        if roi_poly:
            pa = np.array(roi_poly, dtype=np.int32)
            ov = vis.copy(); cv2.fillPoly(ov, [pa], (0, 140, 255))
            vis = cv2.addWeighted(ov, 0.22, vis, 0.78, 0)
            cv2.polylines(vis, [pa], True, (0, 0, 255), 3)
        n_veh = 0
        print("--- t=%.2f ---" % t)
        for bx in res.boxes:
            cid = int(bx.cls.item())
            if cid not in VEHICLE_CLASSES:
                continue
            x1, y1, x2, y2 = [int(v) for v in bx.xyxy[0].tolist()]
            cf = float(bx.conf.item())
            n_veh += 1
            # 用车辆框底部中心点判断是否在应急车道
            cx, cy = (x1 + x2) / 2.0, float(y2)
            inroi = ""
            if roi_poly:
                # 射线法
                inside = False
                j = len(roi_poly) - 1
                for i in range(len(roi_poly)):
                    xi, yi = roi_poly[i]; xj, yj = roi_poly[j]
                    if ((yi > cy) != (yj > cy)) and (cx < (xj - xi) * (cy - yi) / (yj - yi + 1e-12) + xi):
                        inside = not inside
                    j = i
                inroi = " [应急车道]" if inside else ""
            cv2.rectangle(vis, (x1, y1), (x2, y2), (0, 255, 0), 2)
            cv2.circle(vis, (int(cx), int(cy)), 6, (255, 0, 255), -1)
            cv2.putText(vis, "%s %.2f%s" % (VEHICLE_CLASSES[cid], cf, inroi),
                        (x1, max(24, y1 - 6)), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)
            print("   %-10s conf=%.2f box=[%d,%d,%d,%d] 底中心=(%.0f,%.0f)%s"
                  % (VEHICLE_CLASSES[cid], cf, x1, y1, x2, y2, cx, cy, inroi))
        cv2.putText(vis, "t=%.1fs vehicles=%d" % (t, n_veh), (40, 90),
                    cv2.FONT_HERSHEY_SIMPLEX, 1.6, (0, 0, 255), 4)
        p = os.path.join(a.out, "veh_t%06.2f.jpg" % t)
        cv2.imwrite(p, vis, [cv2.IMWRITE_JPEG_QUALITY, 90])
        print("   检出车辆 %d 辆 -> %s" % (n_veh, p))
    cap.release()


if __name__ == "__main__":
    main()
