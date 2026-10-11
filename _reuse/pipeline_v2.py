"""车牌定位 → 围绕车牌裁剪 → 识别（两段式，替代"整块车辆框送识别"）。

为什么必须两段式：
    "把整个车辆框送进 HyperLPR3"对近处的车有效，对远处的车无效——
    远处的车在车辆框里只占几十像素，车牌更小，HyperLPR3 的检测头触发不了，
    结果就是"检不出车牌"（实测 5 条轨迹因此全部 UNREADABLE）。
    正确做法：先用**车牌检测器**在车辆区域内定位车牌，再围绕车牌裁一小块
    （外扩一定倍数）放大后送 HyperLPR3 识别——此时车牌在输入里占主导，
    正是它设计时的使用形态。
"""
import argparse
import json
import os
import sys

import cv2
import numpy as np
import onnxruntime as ort

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from run_pipeline import (load_polygon, polygon_mask, plate_in_polygon,  # noqa: E402
                          tighten_candidates, norm)
import runmeta  # noqa: E402

sys.stdout.reconfigure(encoding="utf-8", errors="replace")


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


def letterbox(img, size=640):
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
    ap.add_argument("--onnx", default="onnx")
    ap.add_argument("--outdir", required=True)
    ap.add_argument("--plate-conf", type=float, default=0.15)
    ap.add_argument("--pad-ratio", type=float, default=1.5,
                    help="围绕车牌外扩的比例（车牌宽/高的倍数）")
    ap.add_argument("--up", type=float, default=6.0, help="车牌区域放大倍数")
    ap.add_argument("--mask-dilate", type=int, default=0)
    ap.add_argument("--no-mask-detect", action="store_true",
                    help="车牌定位阶段用原图（不用掩码）。掩码的硬黑边会让车牌检测器"
                         "失效（实测多条轨迹检出 0 个车牌）；归属仍靠"
                         "「车牌中心必须在 ROI 内」来保证")
    ap.add_argument("--tighten", type=float, default=1.0)
    ap.add_argument("--high-conf", type=float, default=0.90)
    ap.add_argument("--max-frames", type=int, default=16)
    a = ap.parse_args()

    from hyperlpr3 import LicensePlateCatcher
    catcher = LicensePlateCatcher(detect_level=1)
    pdet = ort.InferenceSession(os.path.join(a.onnx, "plate_detect.onnx"),
                                providers=["CPUExecutionProvider"])
    dn = pdet.get_inputs()[0].name

    cap = cv2.VideoCapture(a.video)
    fw = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    fh = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    poly = load_polygon(a.roi, fw, fh)
    runmeta.warn_roi_early(a.roi, (fw, fh))
    m0 = polygon_mask((fh, fw), poly)
    if a.mask_dilate > 0:
        k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * a.mask_dilate + 1,) * 2)
        mask = cv2.dilate(m0, k)
    else:
        mask = m0

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

    tracks, nid = [], 0
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
                t["in_roi"] = t["in_roi"] or d.get("in_emergency_lane", False)
                used.add(bi)
            else:
                tracks.append({"id": nid, "dets": [d], "box": d["box"], "last": fr,
                               "in_roi": d.get("in_emergency_lane", False)})
                nid += 1

    sel = [t for t in tracks if t["in_roi"] and len(t["dets"]) >= 3]
    sel.sort(key=lambda t: t["dets"][0]["t"])
    print("ROI 内轨迹 %d 条，采用【车牌定位→围绕车牌裁剪→识别】两段式\n" % len(sel))

    results = []
    for tr in sel:
        ds = tr["dets"]
        pick = ds if len(ds) <= a.max_frames else [
            ds[i] for i in np.linspace(0, len(ds) - 1, a.max_frames).astype(int)]
        cands = []
        n_plate_found = 0
        for d in pick:
            cap.set(cv2.CAP_PROP_POS_FRAMES, d["frame"])
            ok, frame = cap.read()
            if not ok:
                continue
            masked = frame.copy()
            if not a.no_mask_detect:
                masked[mask == 0] = 0
            blob, r, left, top = letterbox(masked)
            out = pdet.run(None, {dn: blob})[0]
            pl = post_process(out, r, left, top, a.plate_conf, 0.5)
            for row in pl:
                px1, py1, px2, py2 = [float(v) for v in row[:4]]
                cx, cy = (px1 + px2) / 2, (py1 + py2) / 2
                if not plate_in_polygon([px1, py1, px2, py2], poly, 1.0, 0, 0):
                    continue
                n_plate_found += 1
                pw, ph = px2 - px1, py2 - py1
                if min(pw, ph) < 6:
                    continue
                # 围绕车牌外扩
                ex, ey = pw * a.pad_ratio, ph * a.pad_ratio
                rx1 = max(0, int(px1 - ex)); ry1 = max(0, int(py1 - ey))
                rx2 = min(fw, int(px2 + ex)); ry2 = min(fh, int(py2 + ey))
                crop = frame[ry1:ry2, rx1:rx2]
                if crop.size == 0 or min(crop.shape[:2]) < 10:
                    continue
                img = cv2.resize(crop, None, fx=a.up, fy=a.up,
                                 interpolation=cv2.INTER_CUBIC)
                if max(img.shape[:2]) > 4000:
                    continue
                try:
                    for rr in catcher(img):
                        txt, conf = str(rr[0]), float(rr[1])
                        pb = rr[3] if len(rr) > 3 else None
                        # 车牌框换算回原图，并要求仍在 ROI 内
                        if pb:
                            bx1 = rx1 + pb[0] / a.up; by1 = ry1 + pb[1] / a.up
                            bx2 = rx1 + pb[2] / a.up; by2 = ry1 + pb[3] / a.up
                            if not plate_in_polygon([bx1, by1, bx2, by2], poly, 1.0, 0, 0):
                                continue
                        else:
                            bx1 = bx2 = px1; by1 = by2 = py1
                        cands.append({"t": d["t"], "frame": d["frame"], "text": txt,
                                      "conf": round(conf, 4),
                                      "plate_box": [round(bx1), round(by1), round(bx2), round(by2)],
                                      "pw": round(bx2 - bx1), "ph": round(by2 - by1),
                                      "veh_box": d["box"]})
                except Exception:
                    pass
        raw_n = len(cands)
        cands = tighten_candidates(cands, factor=a.tighten) if a.tighten > 0 else cands
        from collections import Counter
        cnt = Counter(norm(c["text"]) for c in cands)
        if not cands:
            plate, conf, status, reason = None, 0.0, "UNREADABLE", \
                "无识别结果（检出车牌 %d 个但未识别出）" % n_plate_found
        else:
            top_txt, top_n = cnt.most_common(1)[0]
            top_conf = max(c["conf"] for c in cands if norm(c["text"]) == top_txt)
            plate, conf = top_txt, top_conf
            if top_conf >= a.high_conf and top_n >= 2:
                status, reason = "CONFIRMED", "置信 %.2f 且 %d 次一致" % (top_conf, top_n)
            else:
                status, reason = "SUSPECT", "置信 %.2f、%d 次（需人工确认）" % (top_conf, top_n)
        best = max(cands, key=lambda c: c["conf"]) if cands else None
        results.append({
            "track_id": tr["id"],
            "t_first": ds[0]["t"], "t_last": ds[-1]["t"], "n_frames": len(ds),
            "cls": Counter(x["cls"] for x in ds).most_common(1)[0][0],
            "max_overlap": max(x["roi_overlap"] for x in ds),
            "plate": plate, "plate_conf": round(conf, 4), "status": status,
            "reason": reason, "n_cands": len(cands), "n_cands_raw": raw_n,
            "n_plate_found": n_plate_found,
            "texts": dict(cnt.most_common(6)), "cands": cands,
            "best_frame": best["frame"] if best else None,
            "best_box": best["plate_box"] if best else None,
        })
        print("   轨迹 %-3d t=%6.2f..%6.2f (%2d帧) 检出车牌%2d 候选%2d→%2d  →  %-12s 【%s】"
              % (tr["id"], ds[0]["t"], ds[-1]["t"], len(ds), n_plate_found,
                 raw_n, len(cands), plate or "(无)", status))
    cap.release()

    n_conf = sum(1 for r in results if r["status"] == "CONFIRMED")
    n_sus = sum(1 for r in results if r["status"] == "SUSPECT")
    n_un = sum(1 for r in results if r["status"] == "UNREADABLE")
    print("\n=== 汇总 ===")
    print("应急车道车辆 %d 条：CONFIRMED %d / SUSPECT %d / UNREADABLE %d"
          % (len(results), n_conf, n_sus, n_un))
    print("%-4s %-16s %-6s %-14s %-9s %s" % ("#", "时间段(秒)", "帧数", "车牌号", "状态", "备注"))
    print("-" * 96)
    for i, r in enumerate(results, 1):
        print("%-4d %6.2f - %-8.2f %-6d %-14s %-9s %s"
              % (i, r["t_first"], r["t_last"], r["n_frames"],
                 r["plate"] or "(未识别)", r["status"], r["reason"]))
    os.makedirs(a.outdir, exist_ok=True)
    meta = runmeta.collect(
        __file__, a, video=a.video, roi=a.roi,
        inputs={"veh_jsonl": a.veh_jsonl,
                "plate_detect_onnx": os.path.join(a.onnx, "plate_detect.onnx")},
        extra={"video_size": [fw, fh], "video_fps_declared": round(fps, 4),
               "n_roi_tracks": len(results), "n_confirmed": n_conf,
               "n_suspect": n_sus, "n_unreadable": n_un,
               "outputs": {"result_json": os.path.join(a.outdir, "result.json")}})
    out = {"video": os.path.abspath(a.video), "roi": os.path.abspath(a.roi),
           "video_size": [fw, fh],
           "polygon": [[round(x), round(y)] for x, y in poly],
           "n_roi_tracks": len(results), "n_confirmed": n_conf,
           "n_suspect": n_sus, "n_unreadable": n_un, "results": results,
           "run": meta}
    p = os.path.join(a.outdir, "result.json")
    with open(p, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=2)
    print("\n结果 -> %s" % p)
    runmeta.write(p + ".runmeta.json", meta)


if __name__ == "__main__":
    main()
