"""诊断：为什么某些清晰车牌没被检出。

在指定帧上以**极低置信度**跑检测器，打印原始输出分布，
并导出可视化图，看检测器是否根本没激活、还是被后处理/阈值丢掉。
"""
import argparse
import os
import sys

import cv2
import numpy as np
import onnxruntime as ort

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from stage1_candidates import letterbox, post_process, nms, _xywh2xyxy  # noqa: E402


def raw_stats(out, r, left, top):
    """统计原始检测输出（未阈值化）的置信度分布与最高几个候选。"""
    det = out[0].copy()                       # [25200, 15]
    conf = det[:, 4]
    order = np.argsort(conf)[::-1][:8]
    print("  原始置信度: max=%.4f  p99=%.4f  p95=%.4f  中位=%.4f  非零个数=%d"
          % (conf.max(), np.percentile(conf, 99), np.percentile(conf, 95),
             np.median(conf), int((conf > 0.01).sum())))
    print("  置信度最高的 8 个锚点:")
    for i in order:
        row = det[i]
        cls = row[13:15]
        ci = int(np.argmax(cls))
        cx, cy, w, h = row[0], row[1], row[2], row[3]
        # 还原到原图坐标
        x1 = (cx - w / 2 - left) / r
        y1 = (cy - h / 2 - top) / r
        print("     conf=%.4f 面积=%.0f 还原框=[%.0f,%.0f,%.0fx%.0f] 类别=%d 类别分=%.3f"
              % (row[4], w * h, x1, y1, w / r, h / r, ci, cls[ci]))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", required=True)
    ap.add_argument("--onnx", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "onnx"))
    ap.add_argument("--times", nargs="+", type=float, required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--conf", type=float, default=0.05)
    a = ap.parse_args()

    det = ort.InferenceSession(os.path.join(a.onnx, "plate_detect.onnx"),
                              providers=["CPUExecutionProvider"])
    dn = det.get_inputs()[0].name
    cap = cv2.VideoCapture(a.video)
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    os.makedirs(a.out, exist_ok=True)

    for t in a.times:
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(round(t * fps)))
        ok, frame = cap.read()
        if not ok:
            print("读帧失败 t=%.2f" % t)
            continue
        print("=" * 70)
        print("t=%.2f  frame=%d" % (t, int(round(t * fps))))
        blob, r, left, top = letterbox(frame)
        out = det.run(None, {dn: blob})[0]
        raw_stats(out, r, left, top)
        res = post_process(out, r, left, top, a.conf, 0.5)
        print("  后处理(conf>%.2f) 得到 %d 个框" % (a.conf, len(res)))
        vis = frame.copy()
        for row in res:
            x1, y1, x2, y2 = [int(v) for v in row[:4]]
            cv2.rectangle(vis, (x1, y1), (x2, y2), (0, 255, 0), 2)
            cv2.putText(vis, "%.2f" % row[4], (x1, max(20, y1 - 5)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)
        cv2.putText(vis, "t=%.1fs conf>%.2f n=%d" % (t, a.conf, len(res)), (40, 90),
                    cv2.FONT_HERSHEY_SIMPLEX, 1.6, (0, 0, 255), 4)
        p = os.path.join(a.out, "diag_t%06.2f.jpg" % t)
        cv2.imwrite(p, vis, [cv2.IMWRITE_JPEG_QUALITY, 90])
        print("  ->", p)
    cap.release()


if __name__ == "__main__":
    main()
