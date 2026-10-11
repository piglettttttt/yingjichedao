"""阶段一：抽帧 → 车牌检测 → 透视校正 → OCR → 落盘候选（JSONL）。

设计原则
- 只做"产生候选"，不做分组与判定：分组/融合交给 stage2，便于单独测试。
- 每条候选都落盘原始分项指标（检测置信度、OCR 每字符概率、清晰度、车牌像素尺寸），
  使融合层的分数可解释、可复算——这是证据链项目的基本要求。
- 关键点顺序：检测头 8 个值 reshape(4,2) 后是**相对 anchor 的偏移**，
  已在 post_process 里还原为原图坐标。

用法：
    python stage1_candidates.py --video <mp4> --out <jsonl> [--start 0] [--end 30] [--fps 5]
"""
import argparse
import json
import os
import sys
import time

import cv2
import numpy as np
import onnxruntime as ort

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# 与仓库 onnx_infer.py / plate_rec.py 保持一致的字典与归一化参数
PLATE_NAME = ("#京沪津渝冀晋蒙辽吉黑苏浙皖闽赣鲁豫鄂湘粤桂琼川贵云藏陕甘青宁新"
              "学警港澳挂使领民航危0123456789ABCDEFGHJKLMNPQRSTUVWXYZ险品")
COLOR_LIST = ["黑色", "蓝色", "绿色", "白色", "黄色"]
MEAN, STD = 0.588, 0.193
IMG_SIZE = 640
REC_SIZE = (168, 48)   # w, h


# ---------------------------------------------------------------- 基础图像工具

def letterbox(img, size=IMG_SIZE):
    """等比缩放 + 灰边填充到 size×size，返回 blob 与还原参数。"""
    h, w, _ = img.shape
    r = min(size / h, size / w)
    nh, nw = int(h * r), int(w * r)
    top, left = int((size - nh) / 2), int((size - nw) / 2)
    bottom, right = size - nh - top, size - nw - left
    resized = cv2.resize(img, (nw, nh))
    padded = cv2.copyMakeBorder(resized, top, bottom, left, right,
                                cv2.BORDER_CONSTANT, value=(114, 114, 114))
    blob = padded[:, :, ::-1].transpose(2, 0, 1).copy().astype(np.float32) / 255.0
    return blob.reshape(1, *blob.shape), r, left, top


def order_points(pts):
    """按 左上/右上/右下/左下 排序 4 个关键点。

    左上 = x+y 最小；右下 = x+y 最大；右上 = x-y 最小；左下 = x-y 最大。
    """
    rect = np.zeros((4, 2), dtype="float32")
    s = pts.sum(axis=1)
    diff = np.diff(pts, axis=1).ravel()      # x - y
    rect[0] = pts[np.argmin(s)]
    rect[2] = pts[np.argmax(s)]
    rect[1] = pts[np.argmin(diff)]
    rect[3] = pts[np.argmax(diff)]
    return rect


def four_point_transform(image, pts):
    """把 4 个关键点围成的四边形拉成标准矩形（车牌透视校正）。"""
    rect = order_points(pts)
    tl, tr, br, bl = rect
    wa = np.hypot(br[0] - bl[0], br[1] - bl[1])
    wb = np.hypot(tr[0] - tl[0], tr[1] - tl[1])
    maxw = max(int(wa), int(wb))
    ha = np.hypot(tr[0] - br[0], tr[1] - br[1])
    hb = np.hypot(tl[0] - bl[0], tl[1] - bl[1])
    maxh = max(int(ha), int(hb))
    if maxw < 2 or maxh < 2:
        return None
    dst = np.array([[0, 0], [maxw - 1, 0], [maxw - 1, maxh - 1], [0, maxh - 1]],
                   dtype="float32")
    return cv2.warpPerspective(image, cv2.getPerspectiveTransform(rect, dst),
                               (maxw, maxh))


def split_double_plate(img):
    """双层车牌上下列拆分后左右拼接，便于单行识别。"""
    h, w, _ = img.shape
    up = img[0:int(5 / 12 * h), :]
    low = img[int(1 / 3 * h):, :]
    if up.size == 0 or low.size == 0:
        return img
    up = cv2.resize(up, (low.shape[1], low.shape[0]))
    return np.hstack((up, low))


# ---------------------------------------------------------------- 检测后处理

def _xywh2xyxy(b):
    o = b.copy()
    o[:, 0] = b[:, 0] - b[:, 2] / 2
    o[:, 1] = b[:, 1] - b[:, 3] / 2
    o[:, 2] = b[:, 0] + b[:, 2] / 2
    o[:, 3] = b[:, 1] + b[:, 3] / 2
    return o


def nms(boxes, iou_thresh):
    """标准 NMS（boxes 第 4 列为分数）。"""
    if len(boxes) == 0:
        return []
    idx = np.argsort(boxes[:, 4])[::-1]
    keep = []
    while idx.size > 0:
        i = idx[0]
        keep.append(int(i))
        if idx.size == 1:
            break
        rest = idx[1:]
        x1 = np.maximum(boxes[i, 0], boxes[rest, 0])
        y1 = np.maximum(boxes[i, 1], boxes[rest, 1])
        x2 = np.minimum(boxes[i, 2], boxes[rest, 2])
        y2 = np.minimum(boxes[i, 3], boxes[rest, 3])
        inter = np.maximum(0, x2 - x1) * np.maximum(0, y2 - y1)
        union = ((boxes[i, 2] - boxes[i, 0]) * (boxes[i, 3] - boxes[i, 1]) +
                 (boxes[rest, 2] - boxes[rest, 0]) * (boxes[rest, 3] - boxes[rest, 1]))
        iou = inter / np.maximum(union - inter, 1e-9)
        idx = rest[np.where(iou <= iou_thresh)[0]]
    return keep


def post_process(dets, r, left, top, conf_thresh, iou_thresh, nc=2):
    """把 [1,25200,15] 原始输出解成原图坐标下的候选。

    输出列：x1 y1 x2 y2 score 然后 8 个关键点坐标，最后一列类别索引。
    """
    dets = dets[dets[:, :, 4] > conf_thresh]
    if len(dets) == 0:
        return np.zeros((0, 14), dtype=np.float32)
    dets = dets.copy()
    dets[:, 13:13 + nc] *= dets[:, 4:5]          # 类别分数 × 置信度
    boxes = _xywh2xyxy(dets[:, :4])
    score = np.max(dets[:, 13:13 + nc], axis=-1, keepdims=True)
    index = np.argmax(dets[:, 13:13 + nc], axis=-1).reshape(-1, 1)
    output = np.concatenate((boxes, score, dets[:, 5:13], index), axis=1)
    output = output[nms(output, iou_thresh)]
    output[:, [0, 2, 5, 7, 9, 11]] = (output[:, [0, 2, 5, 7, 9, 11]] - left) / r
    output[:, [1, 3, 6, 8, 10, 12]] = (output[:, [1, 3, 6, 8, 10, 12]] - top) / r
    return output


# ---------------------------------------------------------------- 识别后处理

def decode_plate(probs_idx):
    """CTC 式去重解码：跳过 blank(0) 并合并连续重复。"""
    pre, out, pos = 0, [], []
    for i, p in enumerate(probs_idx):
        if p != 0 and p != pre:
            out.append(int(p))
            pos.append(i)
        pre = p
    return "".join(PLATE_NAME[i] for i in out), out, pos


def softmax(x, axis=-1):
    x = x - x.max(axis=axis, keepdims=True)
    e = np.exp(x)
    return e / e.sum(axis=axis, keepdims=True)


def rec_pre(img):
    img = cv2.resize(img, REC_SIZE).astype(np.float32)
    img = (img / 255 - MEAN) / STD
    return img.transpose(2, 0, 1).reshape(1, 3, REC_SIZE[1], REC_SIZE[0])


def ocr_plate(sess, crop):
    """识别车牌，返回 (文本, 每字符概率列表, 颜色, 颜色概率)。

    注意：ONNX 输出是 **raw logits**，原 torch 版是 softmax 后取概率的，
    这里必须显式补 softmax，否则拿不到可用的字符置信度（融合层要用）。
    """
    logits, color_logits = sess.run(None, {sess.get_inputs()[0].name: rec_pre(crop)})
    probs = softmax(logits, axis=-1)[0]                  # (21, 78)
    idx = np.argmax(probs, axis=-1)
    text, chars, pos = decode_plate(idx)
    char_conf = [float(probs[p, c]) for c, p in zip(chars, pos)]
    cprob = softmax(color_logits, axis=-1)[0]
    ci = int(np.argmax(cprob))
    return text, char_conf, COLOR_LIST[ci], float(cprob[ci])


# ---------------------------------------------------------------- 主流程

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", required=True)
    ap.add_argument("--onnx", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "onnx"))
    ap.add_argument("--out", required=True, help="候选 JSONL 输出路径")
    ap.add_argument("--start", type=float, default=0.0, help="起始秒")
    ap.add_argument("--end", type=float, default=30.0, help="结束秒")
    ap.add_argument("--fps", type=float, default=15.0, help="检测采样帧率")
    ap.add_argument("--conf", type=float, default=0.25)
    ap.add_argument("--iou", type=float, default=0.5)
    ap.add_argument("--min-plate-px", type=float, default=16.0,
                    help="车牌框最短边小于此值直接丢弃（像素太少无识别价值）")
    ap.add_argument("--ego-exclude-bottom", type=float, default=950.0,
                    help="车牌框顶部 y 大于该值视为自车固定物，丢弃（0=不启用）")
    ap.add_argument("--save-crops", default=None, help="可选：保存车牌 crop 的目录")
    a = ap.parse_args()

    det = ort.InferenceSession(os.path.join(a.onnx, "plate_detect.onnx"),
                               providers=["CPUExecutionProvider"])
    rec = ort.InferenceSession(os.path.join(a.onnx, "plate_rec_color.onnx"),
                               providers=["CPUExecutionProvider"])
    dn, rn = det.get_inputs()[0].name, rec.get_inputs()[0].name

    cap = cv2.VideoCapture(a.video)
    if not cap.isOpened():
        print("!! 无法打开视频:", a.video)
        return 1
    vfps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    f0 = int(a.start * vfps)
    f1 = min(int(a.end * vfps), total)
    step = max(1, int(round(vfps / a.fps)))
    print("视频 %.1f fps, 共 %d 帧；分析 %s–%s 秒 = 帧 %d–%d，每 %d 帧取 1（≈%.1f fps）"
          % (vfps, total, a.start, a.end, f0, f1, step, vfps / step))

    if a.save_crops:
        os.makedirs(a.save_crops, exist_ok=True)
    os.makedirs(os.path.dirname(os.path.abspath(a.out)) or ".", exist_ok=True)

    cap.set(cv2.CAP_PROP_POS_FRAMES, f0)
    n_frames = n_det = n_ego = 0
    t0 = time.time()
    with open(a.out, "w", encoding="utf-8") as fh:
        for fi in range(f0, f1):
            ok, frame = cap.read()
            if not ok:
                break
            if (fi - f0) % step:
                continue
            n_frames += 1
            ts = fi / vfps
            blob, r, left, top = letterbox(frame)
            out = det.run(None, {dn: blob})[0]
            res = post_process(out, r, left, top, a.conf, a.iou)
            for row in res:
                x1, y1, x2, y2 = [float(v) for v in row[:4]]
                score = float(row[4])
                lms = row[5:13].reshape(4, 2).astype(np.float32)
                label = int(row[13])
                if min(x2 - x1, y2 - y1) < a.min_plate_px:
                    continue
                # 自车固定物排除：这类目标稳定出现在画面底部、OCR 乱跳且置信度虚高，
                # 是本项目最危险的假阳性来源，必须在候选阶段就剔除。
                if a.ego_exclude_bottom > 0 and y1 > a.ego_exclude_bottom:
                    n_ego += 1
                    continue
                crop = four_point_transform(frame, lms)
                if crop is None or crop.size == 0 or min(crop.shape[:2]) < 4:
                    continue
                if label == 1:
                    crop = split_double_plate(crop)
                text, char_conf, color, color_conf = ocr_plate(rec, crop)
                gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
                sharp = float(cv2.Laplacian(gray, cv2.CV_64F).var())
                bright = float(gray.mean())
                rec_ = {
                    "frame": fi, "t": round(ts, 3),
                    "box": [round(x1, 1), round(y1, 1), round(x2, 1), round(y2, 1)],
                    "w": round(x2 - x1, 1), "h": round(y2 - y1, 1),
                    "det_conf": round(score, 4),
                    "text": text, "char_conf": [round(c, 4) for c in char_conf],
                    "color": color, "color_conf": round(color_conf, 4),
                    "sharpness": round(sharp, 2), "brightness": round(bright, 2),
                    "double": bool(label == 1),
                    "landmarks": [[round(float(p), 1) for p in q] for q in lms],
                }
                fh.write(json.dumps(rec_, ensure_ascii=False) + "\n")
                n_det += 1
                if a.save_crops:
                    cv2.imwrite(os.path.join(a.save_crops, "f%06d_%s.jpg" % (fi, text or "blank")), crop)
            if n_frames % 50 == 0 and n_frames:
                el = time.time() - t0
                print("  已处理 %d 帧 / 检出 %d，用时 %.1fs (%.2f 帧/秒)"
                      % (n_frames, n_det, el, n_frames / el), flush=True)
    cap.release()
    el = time.time() - t0
    print("完成：处理 %d 帧，检出 %d 条候选，剔除自车固定物 %d 条，用时 %.1fs"
          % (n_frames, n_det, n_ego, el))
    print("候选落盘 ->", a.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
