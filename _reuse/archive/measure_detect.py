"""用真实行车记录仪视频实测 ONNX 车牌检测+识别闭环。

关键问题：这个检测器是在 CCPD（停车场近景车牌）上训练的，权重只有 1.12MB、
backbone 是 ShuffleNetV2、宽度 0.5。行车记录仪里的前车车牌通常只有几十像素，
**能不能检出**是这个方案最大的未知数，必须实测而不是假设。

用法：
    python measure_detect.py --onnx ./onnx --video <mp4> [--frames 40] [--conf 0.25]
"""
import argparse
import os
import sys

import cv2
import numpy as np
import onnxruntime as ort

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# 与 onnx_infer.py 完全一致的字典与常量
PLATE_NAME = ("#京沪津渝冀晋蒙辽吉黑苏浙皖闽赣鲁豫鄂湘粤桂琼川贵云藏陕甘青宁新"
              "学警港澳挂使领民航危0123456789ABCDEFGHJKLMNPQRSTUVWXYZ险品")
COLOR_LIST = ["黑色", "蓝色", "绿色", "白色", "黄色"]
MEAN, STD = 0.588, 0.193


def decode_plate(preds):
    """CTC 式去重解码（与 onnx_infer.py 一致）。"""
    pre, out = 0, []
    for p in preds:
        if p != 0 and p != pre:
            out.append(p)
        pre = p
    return "".join(PLATE_NAME[int(i)] for i in out)


def order_points(pts):
    rect = np.zeros((4, 2), dtype="float32")
    s = pts.sum(axis=1)
    rect[0] = pts[np.argmin(s)]
    rect[2] = pts[np.argmax(s)]
    diff = np.diff(pts, axis=1)
    rect[1] = pts[np.argmin(diff)]
    rect[3] = pts[np.argmax(diff)]
    return rect


def four_point_transform(image, pts):
    """车牌透视校正：把 4 个关键点拉成标准矩形。"""
    rect = order_points(pts)
    tl, tr, br, bl = rect
    wa = np.sqrt(((br[0] - bl[0]) ** 2) + ((br[1] - bl[1]) ** 2))
    wb = np.sqrt(((tr[0] - tl[0]) ** 2) + ((tr[1] - tl[1]) ** 2))
    maxw = max(int(wa), int(wb))
    ha = np.sqrt(((tr[0] - br[0]) ** 2) + ((tr[1] - br[1]) ** 2))
    hb = np.sqrt(((tl[0] - bl[0]) ** 2) + ((tl[1] - bl[1]) ** 2))
    maxh = max(int(ha), int(hb))
    if maxw < 2 or maxh < 2:
        return None
    dst = np.array([[0, 0], [maxw - 1, 0], [maxw - 1, maxh - 1], [0, maxh - 1]],
                   dtype="float32")
    M = cv2.getPerspectiveTransform(rect, dst)
    return cv2.warpPerspective(image, M, (maxw, maxh))


def letterbox(img, size=(640, 640)):
    h, w, _ = img.shape
    r = min(size[0] / h, size[1] / w)
    nh, nw = int(h * r), int(w * r)
    top, left = int((size[0] - nh) / 2), int((size[1] - nw) / 2)
    bottom, right = size[0] - nh - top, size[1] - nw - left
    resized = cv2.resize(img, (nw, nh))
    padded = cv2.copyMakeBorder(resized, top, bottom, left, right,
                                cv2.BORDER_CONSTANT, value=(114, 114, 114))
    blob = padded[:, :, ::-1].transpose(2, 0, 1).copy().astype(np.float32) / 255.0
    return blob.reshape(1, *blob.shape), r, left, top


def xywh2xyxy(b):
    out = b.copy()
    out[:, 0] = b[:, 0] - b[:, 2] / 2
    out[:, 1] = b[:, 1] - b[:, 3] / 2
    out[:, 2] = b[:, 0] + b[:, 2] / 2
    out[:, 3] = b[:, 1] + b[:, 3] / 2
    return out


def my_nms(boxes, iou_thresh):
    """与仓库 my_nms 等价（含置信度列时的标准 NMS）。"""
    if len(boxes) == 0:
        return []
    idx = np.argsort(boxes[:, 4])[::-1]
    keep = []
    while idx.size > 0:
        i = idx[0]
        keep.append(i)
        if idx.size == 1:
            break
        rest = idx[1:]
        x1 = np.maximum(boxes[i, 0], boxes[rest, 0])
        y1 = np.maximum(boxes[i, 1], boxes[rest, 1])
        x2 = np.minimum(boxes[i, 2], boxes[rest, 2])
        y2 = np.minimum(boxes[i, 3], boxes[rest, 3])
        w = np.maximum(0, x2 - x1)
        h = np.maximum(0, y2 - y1)
        inter = w * h
        union = ((boxes[i, 2] - boxes[i, 0]) * (boxes[i, 3] - boxes[i, 1]) +
                 (boxes[rest, 2] - boxes[rest, 0]) * (boxes[rest, 3] - boxes[rest, 1]))
        iou = inter / np.maximum(union - inter, 1e-9)
        idx = rest[np.where(iou <= iou_thresh)[0]]
    return keep


def post_process(dets, r, left, top, conf_thresh, iou_thresh, nc=2):
    """与 onnx_infer.post_precessing 逐行等价。"""
    dets = dets[dets[:, :, 4] > conf_thresh]
    if len(dets) == 0:
        return np.zeros((0, 14), dtype=np.float32)
    dets = dets.copy()
    dets[:, 13:15] *= dets[:, 4:5]
    boxes = xywh2xyxy(dets[:, :4])
    score = np.max(dets[:, 13:15], axis=-1, keepdims=True)
    index = np.argmax(dets[:, 13:15], axis=-1).reshape(-1, 1)
    output = np.concatenate((boxes, score, dets[:, 5:13], index), axis=1)
    keep = my_nms(output, iou_thresh)
    output = output[keep]
    # 还原到原图坐标
    output[:, [0, 2, 5, 7, 9, 11]] = (output[:, [0, 2, 5, 7, 9, 11]] - left) / r
    output[:, [1, 3, 6, 8, 10, 12]] = (output[:, [1, 3, 6, 8, 10, 12]] - top) / r
    return output


def rec_pre(img):
    img = cv2.resize(img, (168, 48)).astype(np.float32)
    img = (img / 255 - MEAN) / STD
    img = img.transpose(2, 0, 1)
    return img.reshape(1, *img.shape)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--onnx", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "onnx"))
    ap.add_argument("--video", required=True)
    ap.add_argument("--frames", type=int, default=40, help="均匀采样帧数")
    ap.add_argument("--conf", type=float, default=0.25)
    ap.add_argument("--nc", type=int, default=2)
    a = ap.parse_args()

    det = ort.InferenceSession(os.path.join(a.onnx, "plate_detect.onnx"),
                               providers=["CPUExecutionProvider"])
    rec = ort.InferenceSession(os.path.join(a.onnx, "plate_rec_color.onnx"),
                               providers=["CPUExecutionProvider"])
    print("detect in :", det.get_inputs()[0].shape, "-> out", det.get_outputs()[0].shape)
    print("rec    in :", rec.get_inputs()[0].shape, "-> out",
          [o.shape for o in rec.get_outputs()])

    cap = cv2.VideoCapture(a.video)
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    fps = cap.get(cv2.CAP_PROP_FPS)
    print("video: %d 帧, %.1f fps, 采样 %d 帧" % (total, fps, a.frames))
    step = max(1, total // a.frames)

    n_hit_frames = 0
    n_used = 0
    plates = []          # (frame_idx, text, color, conf, w, h, sharpness)
    idx = 0
    while n_used < a.frames:
        ok, frame = cap.read()
        if not ok:
            break
        if idx % step:
            idx += 1
            continue
        idx += 1
        n_used += 1

        blob, r, left, top = letterbox(frame)
        out = det.run(None, {det.get_inputs()[0].name: blob})[0]
        res = post_process(out, r, left, top, a.conf, 0.5, a.nc)
        if len(res) == 0:
            continue
        n_hit_frames += 1

        for row in res:
            rect = row[:4]
            lms = row[5:13].reshape(4, 2)
            label = int(row[13])
            conf = float(row[4])
            crop = four_point_transform(frame, lms)
            if crop is None or crop.size == 0:
                continue
            if label == 1:  # 双层牌
                h, w, _ = crop.shape
                up = crop[0:int(5 / 12 * h), :]
                low = crop[int(1 / 3 * h):, :]
                up = cv2.resize(up, (low.shape[1], low.shape[0]))
                crop = np.hstack((up, low))
            po, co = rec.run(None, {rec.get_inputs()[0].name: rec_pre(crop)})
            pi = np.argmax(po, axis=-1)[0]
            ci = int(np.argmax(co))
            text = decode_plate(pi)
            gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
            sharp = float(cv2.Laplacian(gray, cv2.CV_64F).var())
            w = float(rect[2] - rect[0])
            h = float(rect[3] - rect[1])
            plates.append((idx, text, COLOR_LIST[ci], conf, w, h, sharp, label))
        if n_used >= a.frames:
            break
    cap.release()

    print("\n=== 检测结果 ===")
    print("采样帧数        : %d" % n_used)
    print("有车牌检出的帧数: %d  (命中率 %.1f%%)" % (n_hit_frames, 100.0 * n_hit_frames / max(1, n_used)))
    print("车牌候选总数    : %d" % len(plates))
    if not plates:
        print("\n!! 未检出任何车牌。说明该检测器不适用于本视频的车牌尺度/视角。")
        return

    widths = np.array([p[4] for p in plates])
    heights = np.array([p[5] for p in plates])
    confs = np.array([p[3] for p in plates])
    sharps = np.array([p[6] for p in plates])
    print("\n车牌框尺寸(px) : 宽 min %.0f / 中位 %.0f / max %.0f"
          % (widths.min(), np.median(widths), widths.max()))
    print("                 高 min %.0f / 中位 %.0f / max %.0f"
          % (heights.min(), np.median(heights), heights.max()))
    print("检测置信度     : min %.3f / 中位 %.3f / max %.3f"
          % (confs.min(), np.median(confs), confs.max()))
    print("清晰度(拉普拉斯方差): min %.1f / 中位 %.1f / max %.1f"
          % (sharps.min(), np.median(sharps), sharps.max()))

    print("\n=== 识别结果（前 30 条）===")
    for p in plates[:30]:
        print("  f%-4d %-10s %-4s conf=%.2f %4.0fx%-4.0f sharp=%7.1f %s"
              % (p[0], p[1] or "(空)", p[2], p[3], p[4], p[5], p[6],
                 "双层" if p[7] == 1 else "单层"))

    texts = [p[1] for p in plates if p[1]]
    print("\n=== 文本分布 ===")
    from collections import Counter
    for t, c in Counter(texts).most_common(15):
        print("  %-12s x%d" % (t, c))


if __name__ == "__main__":
    main()
