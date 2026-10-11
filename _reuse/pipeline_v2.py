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


MIN_EV_AREA = 400      # 车牌框面积下限（像素²）：低于此值不算「可辨识帧」
MIN_EV_GAP = 0.1       # 两个「附近」帧之间的最小时间间隔（秒）


def pick_evidence_frames(cands):
    """挑出三张证据帧：最早附近帧 / 原图（置信最高帧）/ 最晚附近帧。

    规则（用户定的）：
      - 原图     = 置信度最高的那一帧（报告正文用它）
      - 最早/最晚 = 在**可辨识帧**里时间最早、最晚的两帧，但**不取绝对首帧/末帧**
        （车辆刚进画面或快出画面时车牌常常看不清），所以取「最早那一侧的中间帧」
        与「最晚那一侧的中间帧」。
      - 三张必须来自不同帧号；帧不够时如实少给，不重复用同一帧充数。

    可辨识 = 车牌框面积 >= MIN_EV_AREA（同一帧号取面积最大者）。

    帧数不同时的实际结果（测试里逐条固定住）：
        5+ 帧 → 3 张（最早附近、原图、最晚附近）
        4  帧 → 2~3 张（取决于原图落在哪一帧）
        2~3 帧 → 1~2 张
        1  帧 → 仅原图 1 张

    返回 {"best":c|None, "early":c|None, "late":c|None, "note":str}
    """
    def area(c):
        return (c["plate_box"][2] - c["plate_box"][0]) * \
               (c["plate_box"][3] - c["plate_box"][1])

    usable = {}
    for c in cands or []:
        if not c.get("plate_box"):
            continue
        if area(c) < MIN_EV_AREA:
            continue
        k = c["frame"]
        if k not in usable or area(c) > area(usable[k]):
            usable[k] = c
    pool = sorted(usable.values(), key=lambda c: c["t"])
    if not pool:
        return {"best": None, "early": None, "late": None,
                "note": "没有可辨识帧（车牌框过小或无候选）"}

    best = max(pool, key=lambda c: (c["conf"], area(c)))

    # 首帧/末帧不允许用作「附近」帧；再排除与原图同帧的
    inner = pool[1:-1] if len(pool) >= 3 else []
    mid = [c for c in inner if c["frame"] != best["frame"]]
    early_pool = sorted(mid, key=lambda c: c["t"])          # 最早一侧，靠中间的在前
    late_pool = sorted(mid, key=lambda c: c["t"], reverse=True)

    def take(ranked, taken):
        for c in ranked:
            if c["frame"] in taken:
                continue
            return c
        return None

    taken = {best["frame"]}
    early = take(early_pool, taken)
    if early:
        taken.add(early["frame"])
    # 注意：late 必须把 early 也排除掉，否则「最晚附近」会与「最早附近」撞同一帧。
    # 首帧/末帧的排除早在 inner/mid 里做掉了，这里的池子是干净的。
    late = take(late_pool, taken)
    # 早/晚太近（只差不到 MIN_EV_GAP）就当没有独立的最晚帧
    if early and late and abs(late["t"] - early["t"]) < MIN_EV_GAP:
        late = None

    def desc(c):
        return "t=%.2fs(帧%d)" % (c["t"], c["frame"]) if c else "无"
    picked = [x for x in (early, best, late) if x]
    note = "可辨识帧 %d → 最早附近 %s / 原图(置信最高) %s / 最晚附近 %s" % (
        len(pool), desc(early), desc(best), desc(late))
    if len(picked) < 3:
        note += "（帧数不足，只出 %d 张，未硬凑）" % len(picked)
    return {"best": best, "early": early, "late": late, "note": note}


def check_pick_evidence_frames():
    """选帧规则：最早/最晚要往中间退一帧、原图取置信最高、帧不够时不硬凑。

    「不取绝对首帧/末帧」是关键约束，帧数少时会把候选挤空——那种情况必须
    如实少给，而不是回头去用首帧/末帧顶替。
    """
    def c(t, frame, conf=0.9, w=80, h=20):
        return {"t": t, "frame": frame, "conf": conf,
                "plate_box": [0, 0, w, h], "text": "粤A1"}

    def picked(r):
        return [x["frame"] for x in (r["early"], r["best"], r["late"]) if x]

    def invariant(r, pool):
        """所有帧数下都必须成立的四条：原图=置信最高、不取绝对首末帧、
        三张互不相同、给不出就留空（None 而不是拿别的帧顶替）。"""
        frames = [x["frame"] for x in pool]
        assert r["best"] is not None, "至少要给出原图"
        assert r["best"]["conf"] == max(x["conf"] for x in pool), "原图必须是置信最高帧"
        got = picked(r)
        assert len(set(got)) == len(got), "三张必须来自不同帧：%s" % (got,)
        assert frames[0] not in got, "绝对首帧不允许作为证据帧：%s" % (got,)
        assert frames[-1] not in got, "绝对末帧不允许作为证据帧：%s" % (got,)
        assert all(x is None or x["frame"] in frames
                   for x in (r["early"], r["best"], r["late"])), "帧必须来自候选池"

    # 5 帧：早=第 2、原图=置信最高、晚=倒数第 2，三张齐
    pool = [c(1.0, 10, 0.6), c(2.0, 20, 0.7), c(3.0, 30, 0.99),
            c(4.0, 40, 0.8), c(5.0, 50, 0.75)]
    r = pick_evidence_frames(pool)
    assert r["best"]["frame"] == 30, "原图应为置信最高帧"
    assert r["early"]["frame"] == 20, "最早附近应为第 2 帧（不取首帧）"
    assert r["late"]["frame"] == 40, "最晚附近应为倒数第 2 帧（不取末帧）"
    assert len(picked(r)) == 3, "5 帧应给满三张"
    invariant(r, pool)

    # 6 帧随机置信度：只验四条不变量（具体挑哪帧取决于原图落在哪）
    pool = [c(float(i), i * 10, conf) for i, conf in
            enumerate([0.5, 0.99, 0.7, 0.6, 0.8, 0.9], start=1)]
    invariant(pick_evidence_frames(pool), pool)

    # 4 帧：首末两帧都不许用，中间只剩 2 帧 → 最多 2 张（具体张数取决于原图位置）
    pool = [c(1.0, 10, 0.7), c(2.0, 20, 0.99), c(3.0, 30, 0.8), c(4.0, 40, 0.75)]
    r = pick_evidence_frames(pool)
    invariant(r, pool)
    assert len(picked(r)) <= 2, "4 帧最多只能出 2 张，得 %s" % (picked(r),)

    # 3 帧：只有中间那帧可用 → 最多 1~2 张
    pool = [c(1.0, 10, 0.7), c(2.0, 20, 0.99), c(3.0, 30, 0.8)]
    r = pick_evidence_frames(pool)
    assert r["best"]["frame"] == 20, "原图应为置信最高帧"
    assert len(picked(r)) <= 2, "3 帧最多 2 张"

    # 2 帧：两帧都是「绝对首/末帧」，按规则都不许用 → 只能出原图这一张
    r = pick_evidence_frames([c(1.0, 10, 0.6), c(2.0, 20, 0.9)])
    assert r["best"]["frame"] == 20, "原图应为置信最高帧"
    assert r["early"] is None and r["late"] is None, \
        "只有两帧时两帧都是首/末帧，应如实只出原图，得 %s" % (picked(r),)
    assert "未硬凑" in r["note"], "应在说明里注明未硬凑"

    # 1 帧：只有原图
    r = pick_evidence_frames([c(1.0, 10, 0.9)])
    assert r["best"]["frame"] == 10 and r["early"] is None and r["late"] is None

    # 车牌框过小的帧不算可辨识
    r = pick_evidence_frames([c(1.0, 10, 0.99, w=10, h=10), c(2.0, 20, 0.5, w=10, h=10)])
    assert r["best"] is None, "全是小框时应如实报告没有可辨识帧"

    # 同一帧号的重复候选只算一帧
    r = pick_evidence_frames([c(1.0, 10, 0.5), c(1.0, 10, 0.9)])
    assert r["best"]["frame"] == 10 and r["early"] is None, "同帧号重复不应凑成两帧"
    print("  [PASS] pick_evidence_frames")


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
        # 三张证据帧（用户定的规则）：最早附近帧 + 原图（置信最高帧）+ 最晚附近帧。
        # 「附近」= 不取绝对首帧/末帧（车辆刚进画面或快出画面时车牌常常看不清），
        # 在**可辨识帧**（车牌框够大）里往中间退一帧。帧不够时如实少给，不硬凑。
        ev = pick_evidence_frames(cands)
        results.append({
            "track_id": tr["id"],
            "t_first": ds[0]["t"], "t_last": ds[-1]["t"], "n_frames": len(ds),
            "cls": Counter(x["cls"] for x in ds).most_common(1)[0][0],
            "max_overlap": max(x["roi_overlap"] for x in ds),
            "plate": plate, "plate_conf": round(conf, 4), "status": status,
            "reason": reason, "n_cands": len(cands), "n_cands_raw": raw_n,
            "n_plate_found": n_plate_found,
            "texts": dict(cnt.most_common(6)), "cands": cands,
            "best_frame": ev["best"]["frame"] if ev["best"] else None,
            "best_box": ev["best"]["plate_box"] if ev["best"] else None,
            "early_frame": ev["early"]["frame"] if ev["early"] else None,
            "early_box": ev["early"]["plate_box"] if ev["early"] else None,
            "late_frame": ev["late"]["frame"] if ev["late"] else None,
            "late_box": ev["late"]["plate_box"] if ev["late"] else None,
            "ev_note": ev["note"],
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
