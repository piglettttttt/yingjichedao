"""证据帧选择：在一条车辆轨迹内，挑选"车牌最清晰可读"的那一帧。

为什么不能取首帧：
    应急车道车辆进入画面时往往只露出车尾侧面/被遮挡，车牌不可见；
    等它转正、变近时车牌才清晰。实测轨迹 11 的首帧(t=1.73) 车牌几乎不可见，
    而 t≈2.0-2.4 时车牌清楚可读——用首帧做证据会失去证明力。

打分依据（全部可解释）：
    - 车牌检测置信度（plate_detect.onnx）
    - 车牌在原帧中的像素面积（越大越可读）
    - 车牌清晰度（拉普拉斯方差）
    - 车牌是否落在 ROI 内（必须）
最终分 = 置信度 × 面积归一化 × 清晰度归一化，取最大者。

用法：
    python pick_evidence.py --video <mp4> --roi <roi.json> --outdir <dir> \
        [--tracks 11,30,47,66,75]
"""
import argparse
import json
import os
import sys

import cv2
import numpy as np
import onnxruntime as ort

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from run_pipeline import point_in_polygon  # noqa: E402
from ultralytics import YOLO  # noqa: E402


def letterbox(img, size=640):
    """等比缩放 + 灰边填充；返回 blob 与还原参数（与 run_pipeline 一致）。"""
    h, w = img.shape[:2]
    r = min(size / h, size / w)
    nh, nw = int(h * r), int(w * r)
    top, left = int((size - nh) / 2), int((size - nw) / 2)
    bottom, right = size - nh - top, size - nw - left
    resized = cv2.resize(img, (nw, nh))
    padded = cv2.copyMakeBorder(resized, top, bottom, left, right,
                                cv2.BORDER_CONSTANT, value=(114, 114, 114))
    blob = padded[:, :, ::-1].transpose(2, 0, 1).copy().astype(np.float32) / 255.0
    return blob.reshape(1, *blob.shape), r, left, top


def nms(boxes, iou_thresh):
    if len(boxes) == 0:
        return []
    idx = np.argsort(boxes[:, 4])[::-1]
    keep = []
    while idx.size:
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


def xywh2xyxy(b):
    o = b.copy()
    o[:, 0] = b[:, 0] - b[:, 2] / 2
    o[:, 1] = b[:, 1] - b[:, 3] / 2
    o[:, 2] = b[:, 0] + b[:, 2] / 2
    o[:, 3] = b[:, 1] + b[:, 3] / 2
    return o


def post_process(dets, r, left, top, conf_thresh, iou_thresh, nc=2):
    dets = dets[dets[:, :, 4] > conf_thresh]
    if len(dets) == 0:
        return np.zeros((0, 14), dtype=np.float32)
    dets = dets.copy()
    dets[:, 13:13 + nc] *= dets[:, 4:5]
    boxes = xywh2xyxy(dets[:, :4])
    score = np.max(dets[:, 13:13 + nc], axis=-1, keepdims=True)
    index = np.argmax(dets[:, 13:13 + nc], axis=-1).reshape(-1, 1)
    out = np.concatenate((boxes, score, dets[:, 5:13], index), axis=1)
    out = out[nms(out, iou_thresh)]
    out[:, [0, 2, 5, 7, 9, 11]] = (out[:, [0, 2, 5, 7, 9, 11]] - left) / r
    out[:, [1, 3, 6, 8, 10, 12]] = (out[:, [1, 3, 6, 8, 10, 12]] - top) / r
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", required=True)
    ap.add_argument("--roi", required=True)
    ap.add_argument("--veh-jsonl", required=True)
    ap.add_argument("--plate-onnx", default="onnx")
    ap.add_argument("--outdir", required=True)
    ap.add_argument("--weights", default="yolo11n.pt")
    ap.add_argument("--fps", type=float, default=15.0)
    ap.add_argument("--conf", type=float, default=0.30)
    ap.add_argument("--plate-conf", type=float, default=0.20)
    ap.add_argument("--overlap-thresh", type=float, default=0.35)
    ap.add_argument("--min-frames", type=int, default=3)
    a = ap.parse_args()

    roi = json.load(open(a.roi, encoding="utf-8"))
    cap = cv2.VideoCapture(a.video)
    fw = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    fh = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    if "polygon_norm" in roi:
        poly = [(x * fw, y * fh) for x, y in roi["polygon_norm"]]
    else:
        poly = [tuple(p) for p in roi["polygon"]]
    poly = [(float(x), float(y)) for x, y in poly]

    det = ort.InferenceSession(os.path.join(a.plate_onnx, "plate_detect.onnx"),
                               providers=["CPUExecutionProvider"])
    dn = det.get_inputs()[0].name
    model = YOLO(a.weights)

    # 车辆跟踪（与 run_pipeline 同一套轻量 IoU 跟踪）
    rows = [json.loads(l) for l in open(a.veh_jsonl, encoding="utf-8")]
    by_frame = {}
    for r in rows:
        by_frame.setdefault(r["frame"], []).append(r)

    def iou(a_, b_):
        x1, y1 = max(a_[0], b_[0]), max(a_[1], b_[1])
        x2, y2 = min(a_[2], b_[2]), min(a_[3], b_[3])
        if x1 >= x2 or y1 >= y2:
            return 0.0
        inter = (x2 - x1) * (y2 - y1)
        un = (a_[2]-a_[0])*(a_[3]-a_[1]) + (b_[2]-b_[0])*(b_[3]-b_[1]) - inter
        return inter / un if un > 0 else 0.0

    tracks, next_id = [], 0
    for fr in sorted(by_frame):
        used = set()
        for d in sorted(by_frame[fr], key=lambda z: -z["conf"]):
            best, bi = 0.0, -1
            for i, t in enumerate(tracks):
                if i in used or fr - t["last"] > 30:
                    continue
                v = iou(d["box"], t["box"])
                if v > best:
                    best, bi = v, i
            if bi >= 0 and best >= 0.25:
                t = tracks[bi]
                t["dets"].append(d); t["box"] = d["box"]; t["last"] = fr
                t["in_roi"] = t["in_roi"] or d.get("in_emergency_lane", d.get("in_roi", False))
                used.add(bi)
            else:
                tracks.append({"id": next_id, "dets": [d], "box": d["box"],
                               "last": fr, "in_roi": d.get("in_emergency_lane", d.get("in_roi", False))})
                next_id += 1

    sel = [t for t in tracks if t["in_roi"] and len(t["dets"]) >= a.min_frames]
    sel.sort(key=lambda t: t["dets"][0]["t"])
    print("ROI 内轨迹 %d 条，逐条挑选最佳证据帧\n" % len(sel))
    print("%-6s %-8s %-8s %-34s %s" % ("轨迹", "时间范围", "最佳t", "最佳帧的车牌检测", "得分"))
    print("-" * 100)

    os.makedirs(a.outdir, exist_ok=True)
    picks = []
    for tr in sel:
        best = None
        for d in tr["dets"]:
            fi = d["frame"]
            cap.set(cv2.CAP_PROP_POS_FRAMES, fi)
            ok, frame = cap.read()
            if not ok:
                continue
            blob, r, left, top = letterbox(frame)
            out = det.run(None, {dn: blob})[0]
            res = post_process(out, r, left, top, a.plate_conf, 0.5)
            for row in res:
                x1, y1, x2, y2 = [float(v) for v in row[:4]]
                cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
                if not point_in_polygon((cx, cy), poly):
                    continue          # 必须是 ROI 内的车牌
                pw, ph = x2 - x1, y2 - y1
                if min(pw, ph) < 8:
                    continue
                # 裁剪算清晰度
                cw1, ch1 = max(0, int(x1)), max(0, int(y1))
                cw2, ch2 = min(fw, int(x2)), min(fh, int(y2))
                if cw2 - cw1 < 4 or ch2 - ch1 < 4:
                    continue
                g = cv2.cvtColor(frame[ch1:ch2, cw1:cw2], cv2.COLOR_BGR2GRAY)
                sharp = float(cv2.Laplacian(g, cv2.CV_64F).var())
                # 打分规则（改版，2026-10）
                # 旧公式 置信度 × √面积 × 清晰度 是错的：车驶离时车牌变小但更清晰，
                # 面积与清晰度相互抵消，结果会选中小车牌的那一帧（用户实测反馈）。
                # 现在：**车牌像素尺寸是第一优先**（越大越可能看清）；
                # 清晰度只作为同尺寸下的次级依据；并且必须过最小尺寸门槛。
                if pw < 24 or ph < 9:
                    continue                      # 太小，没有证明力
                score = (pw * ph) ** 0.9 * min(sharp, 3000.0) ** 0.1 * float(row[4])
                cand = {"frame": fi, "t": d["t"], "score": score,
                        "conf": float(row[4]), "pw": pw, "ph": ph,
                        "sharp": sharp, "box": [x1, y1, x2, y2]}
                if best is None or score > best["score"]:
                    best = cand
        if best is None:
            print("%-6d %6.2f-%-6.2f  %s" % (tr["id"], tr["dets"][0]["t"],
                                             tr["dets"][-1]["t"], "（该轨迹未检出 ROI 内车牌）"))
            picks.append({"track_id": tr["id"], "best": None})
            continue
        print("%-6d %6.2f-%-6.2f %6.2f   conf=%.2f %4.0fx%-3.0f sharp=%6.0f   %8.0f"
              % (tr["id"], tr["dets"][0]["t"], tr["dets"][-1]["t"], best["t"],
                 best["conf"], best["pw"], best["ph"], best["sharp"], best["score"]))
        picks.append({"track_id": tr["id"],
                      "t_first": tr["dets"][0]["t"], "t_last": tr["dets"][-1]["t"],
                      "n_frames": len(tr["dets"]), "best": best})

    with open(os.path.join(a.outdir, "evidence_pick.json"), "w", encoding="utf-8") as fh:
        json.dump(picks, fh, ensure_ascii=False, indent=2)
    print("\n-> %s" % os.path.join(a.outdir, "evidence_pick.json"))
    cap.release()


if __name__ == "__main__":
    main()
