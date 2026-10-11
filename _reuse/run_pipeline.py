"""完整链路（一次跑通）：车辆检测 → ROI 归属 → 车牌识别 → 结果汇总。

**当前链路上的角色（重要）**：实际使用的顺序是
`stage1_vehicles.py → pipeline_v2.py → merge_tracks.py → make_outputs.py`。
本文件是**早期的一次性整链版本**，已被上面四步取代；它现在主要作为**函数库**被
`pipeline_v2.py` 复用（`load_polygon` / `polygon_mask` / `plate_in_polygon` /
`tighten_candidates` / `norm`）。改这些函数会直接影响正式链路。

架构（用户指定，已确认）：
    1. 车辆检测：不依赖车牌可见性，保证"有没有车"判对
    2. ROI 归属：车辆底边与应急车道多边形的重叠比例
    3. 车牌识别：HyperLPR3，喂**车辆区域**（外扩+放大）——
       实测关键：不能喂紧裁车牌，它的检测头会失效
    4. 分级：CONFIRMED / SUSPECT（需人工确认）/ UNREADABLE，绝不硬猜

用法：
    python run_pipeline.py --video <mp4> --roi <roi.json> --outdir <dir>
"""
import argparse
import json
import os
import sys
import time
from collections import Counter, defaultdict

import cv2
import numpy as np
from ultralytics import YOLO

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

VEHICLE_CLASSES = {2: "car", 3: "motorcycle", 5: "bus", 7: "truck"}


def norm(s):
    if not s:
        return ""
    return "".join(ch for ch in str(s).upper()
                   if ch.isalnum() or "\u4e00" <= ch <= "\u9fff")


def load_polygon(roi_path, fw, fh):
    with open(roi_path, encoding="utf-8") as fh_:
        r = json.load(fh_)
    if "polygon_norm" in r:
        return [(float(x) * fw, float(y) * fh) for x, y in r["polygon_norm"]]
    return [tuple(p) for p in r["polygon"]]


def point_in_polygon(pt, poly):
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


def bottom_overlap(bbox, poly, samples=21):
    """车辆底边落在 ROI 内的水平比例。"""
    x1, y1, x2, y2 = bbox
    y = y2 - 1.0
    if x2 <= x1:
        return 0.0
    hit = sum(1 for i in range(samples)
              if point_in_polygon((x1 + (x2 - x1) * (i + 0.5) / samples, y), poly))
    return hit / float(samples)


def polygon_mask(shape, poly):
    """生成 ROI 掩码：多边形内为 255，外部为 0。"""
    m = np.zeros(shape[:2], dtype=np.uint8)
    cv2.fillPoly(m, [np.array(poly, dtype=np.int32)], 255)
    return m


def plate_in_polygon(pb, poly, sc=1.0, ax1=0, ay1=0):
    """车牌框中心（换算回原图坐标后）是否落在 ROI 内。

    用途：即使裁剪区里混进了 ROI 之外的车牌，也不被采纳。
    """
    if not pb:
        return False
    cx = ax1 + (pb[0] + pb[2]) / 2.0 / sc
    cy = ay1 + (pb[1] + pb[3]) / 2.0 / sc
    return point_in_polygon((cx, cy), poly)


def iou(a, b):
    x1, y1 = max(a[0], b[0]), max(a[1], b[1])
    x2, y2 = min(a[2], b[2]), min(a[3], b[3])
    if x1 >= x2 or y1 >= y2:
        return 0.0
    inter = (x2 - x1) * (y2 - y1)
    ua = (a[2]-a[0])*(a[3]-a[1]) + (b[2]-b[0])*(b[3]-b[1]) - inter
    return inter / ua if ua > 0 else 0.0


def tighten_candidates(cands, factor=1.0):
    """剔除离群的车牌候选（属于其它车辆的）。

    依据：同一条车辆轨迹内，本车车牌的横向位置是连续渐变的；而画面中其它车辆
    的车牌会突然出现在很远的 x 处。以**横向中位数**为基准做聚类——
    中位数对离群点稳健，因此基准不会被少数错归属的候选带偏。

    factor：允许偏离中位数的幅度（以中位车牌宽度的倍数表示）。
            1.0 = 允许左右各偏离一个车牌宽度；越小越严。

    返回过滤后的候选列表；若过滤后为空则回退为原列表（避免误杀成 UNREADABLE）。
    """
    if len(cands) < 3:
        return list(cands)
    cx = [(c["plate_box"][0] + c["plate_box"][2]) / 2.0 for c in cands
          if c.get("plate_box")]
    if len(cx) < 3:
        return list(cands)
    med_x = float(np.median(cx))
    widths = [max(8.0, c["pw"]) for c in cands if c.get("plate_box")]
    med_w = float(np.median(widths)) if widths else 40.0

    # 两轮：先按横向距离聚类，再按车牌尺寸合理性二次收紧
    tol = factor * med_w
    kept = [c for c in cands
            if c.get("plate_box")
            and abs((c["plate_box"][0] + c["plate_box"][2]) / 2.0 - med_x) <= tol]
    if kept and len(kept) >= max(2, int(0.25 * len(cands))):
        # 尺寸合理性：车牌宽不应偏离中位数太多（不同车辆的牌尺寸差别明显）
        kx = [c["pw"] for c in kept]
        if kx:
            mw = float(np.median(kx))
            kept2 = [c for c in kept if 0.5 * mw <= c["pw"] <= 2.0 * mw]
            if len(kept2) >= 2:
                return kept2
        return kept
    return list(cands)


def check_norm():
    """norm：车牌比对用的规范化——只留字母/数字/汉字，且转大写。"""
    assert norm("粤aX285e") == "粤AX285E", norm("粤aX285e")
    assert norm(" 粤·AX-285E ") == "粤AX285E", norm(" 粤·AX-285E ")
    assert norm(None) == "" and norm("") == "", "空值必须安全"
    print("  [PASS] norm")


def check_tighten_candidates():
    """tighten_candidates：剔除远处离群候选，但不能把正常的误杀成空。

    真实场景：同一车辆轨迹里混进了画面中另一辆车的车牌（横向位置很远）。
    """
    def c(x, w=40, conf=0.9):
        return {"plate_box": [x, 100, x + w, 115], "pw": w, "conf": conf, "text": "粤A1"}

    # 3 个聚集 + 1 个远处离群 → 离群被剔除
    near = [c(500), c(505), c(510)]
    out = tighten_candidates(near + [c(1600)], factor=1.0)
    assert len(out) == 3, "远处离群候选应被剔除，得 %d" % len(out)
    assert all(x["plate_box"][0] < 1000 for x in out), "留下的必须是本车的"
    # 候选数不足 3 → 原样返回（不做判断，避免误杀）
    two = [c(500), c(1600)]
    assert len(tighten_candidates(two)) == 2, "候选太少时应原样返回"
    # 全是离群（分布均匀）→ 不能清空
    assert len(tighten_candidates([c(100), c(900), c(1800)])) >= 1, "不能清空候选"
    print("  [PASS] tighten_candidates")


def check_plate_in_polygon():
    """plate_in_polygon：车牌框中心换算回原图后是否落在 ROI 内（含缩放/偏移换算）。"""
    poly = [(0.0, 0.0), (100.0, 0.0), (100.0, 100.0), (0.0, 100.0)]
    assert plate_in_polygon([40, 40, 60, 60], poly), "中心在内应为 True"
    assert not plate_in_polygon([200, 200, 260, 260], poly), "中心在外应为 False"
    assert not plate_in_polygon(None, poly), "空框应为 False"
    # sc=2：框坐标是放大图里的，除以 2 再回落到 (10,10) 原点 → 中心 (35,35) 在内
    assert plate_in_polygon([40, 40, 60, 60], poly, sc=2.0, ax1=10, ay1=10), \
        "缩放/偏移换算应回到原图坐标"
    # 同一框，不换算偏移就落到 (35,35)+0 —— 反过来验证偏移确实生效
    assert not plate_in_polygon([40, 40, 60, 60], poly, sc=2.0, ax1=-100, ay1=-100), \
        "偏移必须参与换算"
    print("  [PASS] plate_in_polygon")


def check_run_pipeline_logic():
    check_norm()
    check_tighten_candidates()
    check_plate_in_polygon()
    print("RESULT: ALL PASSED (run_pipeline)")


def track_iou(dets_by_frame, iou_thresh=0.25, max_gap=20):
    """轻量 IoU 跟踪：给车辆稳定 ID（用于聚合多帧车牌候选）。

    按帧依次匹配，用最近一次框做 IoU（视频为 30fps，相邻采样帧位移不大）。
    """
    tracks = []
    for frame in sorted(dets_by_frame):
        dets = dets_by_frame[frame]
        used = set()
        for d in dets:
            best, bi = 0.0, -1
            for i, t in enumerate(tracks):
                if i in used or frame - t["last"] > max_gap:
                    continue
                v = iou(d["box"], t["box"])
                if v > best:
                    best, bi = v, i
            if bi >= 0 and best >= iou_thresh:
                t = tracks[bi]
                t["dets"].append(d)
                t["box"] = d["box"]
                t["last"] = frame
                t["in_roi"] = t["in_roi"] or d["in_roi"]
                used.add(bi)
            else:
                tracks.append({"id": len(tracks), "dets": [d], "box": d["box"],
                               "last": frame, "first": frame, "in_roi": d["in_roi"]})
    return tracks


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", required=True)
    ap.add_argument("--roi", required=True)
    ap.add_argument("--outdir", required=True)
    ap.add_argument("--weights", default="yolo11n.pt")
    ap.add_argument("--fps", type=float, default=15.0, help="车辆检测采样帧率")
    ap.add_argument("--conf", type=float, default=0.30)
    ap.add_argument("--imgsz", type=int, default=640)
    ap.add_argument("--overlap-thresh", type=float, default=0.35)
    ap.add_argument("--min-veh-frames", type=int, default=3, help="车辆最少出现帧数")
    ap.add_argument("--pad", type=float, default=0.0,
                    help="车牌识别时车辆框外扩比例（按用户要求默认 0=不外扩，"
                         "避免把 ROI 之外的邻车车牌纳入识别范围）")
    ap.add_argument("--up", type=float, default=3.0, help="车牌识别时区域放大倍数")
    ap.add_argument("--det-level", type=int, default=1)
    ap.add_argument("--high-conf", type=float, default=0.90)
    ap.add_argument("--tighten", type=float, default=1.0,
                    help="归属收紧幅度：允许车牌横向偏离中位数的倍数（车牌宽度为单位）。"
                         "0 = 关闭收紧（旧行为）")
    ap.add_argument("--mask-mode", choices=["mask", "crop"], default="mask",
                    help="mask=先把 ROI 之外涂黑再裁剪（严格只用 ROI 内像素）；"
                         "crop=直接裁剪但在采纳候选时校验车牌中心在 ROI 内")
    a = ap.parse_args()

    os.makedirs(a.outdir, exist_ok=True)
    cap = cv2.VideoCapture(a.video)
    if not cap.isOpened():
        raise SystemExit("打不开视频: %s" % a.video)
    fw = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    fh = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    poly = load_polygon(a.roi, fw, fh)
    mask = polygon_mask((fh, fw), poly)
    print("=" * 76)
    print("视频 %dx%d @%.1ffps  %.1fs (%d 帧)" % (fw, fh, fps, total / fps, total))
    print("ROI 多边形: %s" % [[round(x), round(y)] for x, y in poly])
    print("车牌识别模式: %s（pad=%.2f, 放大%.1fx）"
          % ("ROI 掩码后裁剪" if a.mask_mode == "mask" else "直接裁剪+ROI校验",
             a.pad, a.up))

    # ---------- 阶段 1：车辆检测 + ROI 归属 ----------
    print("\n[1/3] 车辆检测 + 应急车道归属判定 ...")
    model = YOLO(a.weights)
    step = max(1, int(round(fps / a.fps)))
    dets_by_frame = {}
    t0 = time.time()
    for fi in range(0, total, step):
        cap.set(cv2.CAP_PROP_POS_FRAMES, fi)
        ok, frame = cap.read()
        if not ok:
            break
        res = model.predict(frame, conf=a.conf, imgsz=a.imgsz,
                            classes=list(VEHICLE_CLASSES.keys()), verbose=False)[0]
        if res.boxes is None or len(res.boxes) == 0:
            continue
        lst = []
        for bx in res.boxes:
            x1, y1, x2, y2 = [float(v) for v in bx.xyxy[0].tolist()]
            ov = bottom_overlap((x1, y1, x2, y2), poly)
            lst.append({"frame": fi, "t": round(fi / fps, 3),
                        "cls": VEHICLE_CLASSES[int(bx.cls.item())],
                        "conf": round(float(bx.conf.item()), 4),
                        "box": [round(x1, 1), round(y1, 1), round(x2, 1), round(y2, 1)],
                        "roi_overlap": round(ov, 3),
                        "in_roi": ov >= a.overlap_thresh})
        if lst:
            dets_by_frame[fi] = lst
    el1 = time.time() - t0
    n_det = sum(len(v) for v in dets_by_frame.values())
    print("    %d 帧有检出，共 %d 个车辆框，用时 %.1fs" % (len(dets_by_frame), n_det, el1))

    tracks = track_iou(dets_by_frame)
    roi_tracks = [t for t in tracks
                  if t["in_roi"] and len(t["dets"]) >= a.min_veh_frames]
    roi_tracks.sort(key=lambda t: t["first"])
    print("    车辆轨迹 %d 条，其中【在应急车道内且≥%d帧】的 %d 条"
          % (len(tracks), a.min_veh_frames, len(roi_tracks)))

    # ---------- 阶段 2：车牌识别（对每个 ROI 轨迹）----------
    print("\n[2/3] 车牌识别（HyperLPR3，喂车辆区域）...")
    from hyperlpr3 import LicensePlateCatcher
    catcher = LicensePlateCatcher(detect_level=a.det_level)
    cap2 = cv2.VideoCapture(a.video)

    # 顺序读取缓存（性能关键）
    #
    # 坑：原先用 cap.set(CAP_PROP_POS_FRAMES, fi) 逐帧随机跳转，在 **AV1** 编码的
    # 视频上极慢——AV1 定位到任意帧要从关键帧起解码整段，每次跳转都很贵。
    # 实测同一批任务从分钟级拖到十几分钟（H.264 视频没这个问题）。
    # 办法：把所有要用的帧号排序后**单向顺序读取**，一次遍历取齐。
    _cache = {}

    def prefetch_all(frames):
        """按帧号升序单向读取，缓存所需帧。"""
        want = sorted(set(int(f) for f in frames))
        if not want:
            return
        cap2.set(cv2.CAP_PROP_POS_FRAMES, 0)
        pos, i, n = 0, 0, len(want)
        while i < n:
            ok, f = cap2.read()
            if not ok:
                break
            if pos == want[i]:
                _cache[pos] = f
                i += 1
                while i < n and want[i] == pos:      # 去重后不会重复，稳妥起见
                    i += 1
            elif pos > want[i]:
                i += 1                                # 该帧读不到（超出范围）
            pos += 1

    def frame_at(fi):
        fi = int(fi)
        if fi in _cache:
            return _cache[fi]
        cap2.set(cv2.CAP_PROP_POS_FRAMES, fi)
        ok, f = cap2.read()
        return f if ok else None

    # 预取：先算出所有轨迹要用到的帧号（每个轨迹最多 12 帧，均匀取样）
    _picks = {}
    _all_frames = []
    for tr in roi_tracks:
        ds = tr["dets"]
        pk = ds if len(ds) <= 12 else [ds[i] for i in
                                      np.linspace(0, len(ds) - 1, 12).astype(int)]
        _picks[tr["id"]] = pk
        _all_frames.extend(d["frame"] for d in pk)
    print("    预取 %d 帧（顺序读取，避免 AV1 随机跳帧）..." % len(set(_all_frames)))
    t_pf = time.time()
    prefetch_all(_all_frames)
    print("    预取完成，用时 %.1fs，命中 %d 帧" % (time.time() - t_pf, len(_cache)))

    results = []
    for tr in roi_tracks:
        cands = []
        n_rejected = 0
        pick = _picks.get(tr["id"], tr["dets"])
        for d in pick:
            f = frame_at(d["frame"])
            if f is None:
                continue
            x1, y1, x2, y2 = d["box"]
            bw, bh = x2 - x1, y2 - y1
            ax1 = max(0, int(x1 - bw * a.pad)); ay1 = max(0, int(y1 - bh * a.pad))
            ax2 = min(fw, int(x2 + bw * a.pad)); ay2 = min(fh, int(y2 + bh * a.pad))
            # 用户要求二：只把 ROI 范围内的图形内容送去车牌检测器。
            # mask 模式把 ROI 之外涂黑，使识别器完全看不到邻车像素。
            if a.mask_mode == "mask":
                masked = f.copy()
                masked[mask == 0] = 0
                crop = masked[ay1:ay2, ax1:ax2]
            else:
                crop = f[ay1:ay2, ax1:ax2]
            if crop.size == 0 or min(crop.shape[:2]) < 24:
                continue
            img = crop if a.up == 1.0 else cv2.resize(
                crop, None, fx=a.up, fy=a.up, interpolation=cv2.INTER_CUBIC)
            if max(img.shape[:2]) > 4200:
                img = crop
                a_up_used = 1.0
            else:
                a_up_used = a.up
            try:
                for r in catcher(img):
                    txt, conf = str(r[0]), float(r[1])
                    pb = r[3] if len(r) > 3 else None
                    # 双重保险：crop 模式下也要确保车牌中心在 ROI 内
                    if not plate_in_polygon(pb, poly, a_up_used, ax1, ay1):
                        n_rejected += 1
                        continue
                    if pb:
                        ox1 = ax1 + pb[0] / a_up_used; oy1 = ay1 + pb[1] / a_up_used
                        ox2 = ax1 + pb[2] / a_up_used; oy2 = ay1 + pb[3] / a_up_used
                        pw, ph = ox2 - ox1, oy2 - oy1
                    else:
                        ox1 = oy1 = ox2 = oy2 = pw = ph = 0
                    # 清晰度（拉普拉斯方差）：选证据帧时作为轻微调节项
                    sharp = 0.0
                    if pb and a_up_used > 0:
                        gx1 = max(0, int(ox1)); gy1 = max(0, int(oy1))
                        gx2 = min(fw, int(ox2)); gy2 = min(fh, int(oy2))
                        if gx2 - gx1 >= 4 and gy2 - gy1 >= 4:
                            gg = cv2.cvtColor(f[gy1:gy2, gx1:gx2], cv2.COLOR_BGR2GRAY)
                            sharp = float(cv2.Laplacian(gg, cv2.CV_64F).var())
                    cands.append({"t": d["t"], "frame": d["frame"], "text": txt,
                                  "conf": round(conf, 4),
                                  "plate_box": [round(ox1), round(oy1), round(ox2), round(oy2)],
                                  "pw": round(pw), "ph": round(ph),
                                  "sharp": round(sharp, 1),
                                  "veh_box": d["box"]})
            except Exception:
                pass
        # 汇总该轨迹的车牌候选
        #
        # 收紧归属（关键）：同一条车辆轨迹内，本车车牌在画面上的横向位置是渐变的；
        # 而"路过"画面的其它车辆车牌会突然出现在很远的 x 处，形成离群点。
        # 实测：不收紧时最终投票混成 蒙A1G0V91（不存在的号码），
        # 而该车在最佳帧上其实能读出 粤A130V1/粤A131V9（6/7 字正确）。
        # 做法：以车牌中心横向中位数为基准做聚类，中位数对离群点天然稳健。
        n_before = len(cands)
        cands = tighten_candidates(cands, factor=a.tighten)
        n_dropped = n_before - len(cands)

        cnt = Counter(norm(c["text"]) for c in cands)
        # 分级：最高置信 + 该文本出现次数
        if not cands:
            status, plate, conf, reason = "UNREADABLE", None, 0.0, "无识别结果"
        else:
            top_txt, top_n = cnt.most_common(1)[0]
            top_conf = max(c["conf"] for c in cands if norm(c["text"]) == top_txt)
            plate, conf = top_txt, top_conf
            if top_conf >= a.high_conf and top_n >= 2:
                status, reason = "CONFIRMED", "置信 %.2f 且 %d 次一致" % (top_conf, top_n)
            else:
                status, reason = "SUSPECT", "置信 %.2f、%d 次（需人工确认）" % (top_conf, top_n)
        # ---------- 选择证据帧 ----------
        # 旧做法：取**识别置信度最高**的那个候选。这是错的（实测反馈）：
        #   识别置信度衡量的是"识别器有多确信"，不等于"这一帧有多清楚"；
        #   而且置信度会饱和（多帧都是 0.9），并不能区分车牌大小。
        # 另一版旧公式 conf×√面积×清晰度 也是错的：
        #   车驶离时车牌变小但更清晰，**面积与清晰度相互抵消**，会选中小车牌帧。
        # 现在的依据（按用户要求：提高车牌面积权重）：
        #   score = 面积^0.9 × 清晰度^0.1 × 置信度
        #   面积占绝对主导——通常车牌越大也越清晰；清晰度只做轻微调节。
        #   并设最小尺寸门槛，太小的车牌没有证明力，不参与选帧。
        def ev_score(c):
            pw, ph = c.get("pw", 0), c.get("ph", 0)
            if pw < 24 or ph < 9:
                return -1.0
            sharp = c.get("sharp", 0.0)
            return ((pw * ph) ** 0.9) * (min(sharp, 3000.0) ** 0.1) * c["conf"]

        scored = [c for c in cands if ev_score(c) > 0]
        pool = scored if scored else cands
        best = max(pool, key=ev_score) if pool else None

        results.append({
            "track_id": tr["id"],
            "t_first": tr["dets"][0]["t"], "t_last": tr["dets"][-1]["t"],
            "n_frames": len(tr["dets"]),
            "cls": Counter(d["cls"] for d in tr["dets"]).most_common(1)[0][0],
            "max_overlap": max(d["roi_overlap"] for d in tr["dets"]),
            "plate": plate, "plate_conf": round(conf, 4), "status": status,
            "reason": reason, "n_cands": len(cands),
            "n_cands_raw": n_before, "n_dropped_by_tighten": n_dropped,
            "texts": dict(cnt.most_common(6)),
            "cands": cands,
            "best_frame": best["frame"] if best else None,
            "best_box": best["plate_box"] if best else None,
        })
        print("   轨迹 %-3d t=%6.2f..%6.2f (%2d帧) overlap=%.2f 候选%d→%d  →  %-12s 【%s】"
              % (tr["id"], tr["dets"][0]["t"], tr["dets"][-1]["t"], len(tr["dets"]),
                 max(d["roi_overlap"] for d in tr["dets"]), n_before, len(cands),
                 plate or "(无)", status))
    cap.release()
    cap2.release()

    # ---------- 阶段 3：汇总 ----------
    print("\n[3/3] 汇总")
    conf = [r for r in results if r["status"] == "CONFIRMED"]
    susp = [r for r in results if r["status"] == "SUSPECT"]
    unread = [r for r in results if r["status"] == "UNREADABLE"]

    print("\n" + "=" * 76)
    print("在应急车道内行驶的车辆（轨迹）共 %d 条" % len(results))
    print("  CONFIRMED  %d" % len(conf))
    print("  SUSPECT    %d  ← 需人工确认" % len(susp))
    print("  UNREADABLE %d  ← 车牌无法识别（车辆仍记录在案）" % len(unread))

    print("\n【车牌号 + 时间 检测成果】")
    print("%-4s %-18s %-6s %-12s %-9s %s" % ("#", "出现时间段(秒)", "帧数", "车牌号", "状态", "备注"))
    print("-" * 92)
    for i, r in enumerate(results, 1):
        print("%-4d %6.2f - %-9.2f %-6d %-12s %-9s %s"
              % (i, r["t_first"], r["t_last"], r["n_frames"],
                 r["plate"] or "(未识别)", r["status"], r["reason"]))

    out = {"video": os.path.abspath(a.video), "roi": os.path.abspath(a.roi),
           "video_size": [fw, fh], "polygon": [[round(x), round(y)] for x, y in poly],
           "n_roi_tracks": len(results), "n_confirmed": len(conf),
           "n_suspect": len(susp), "n_unreadable": len(unread),
           "results": results}
    p = os.path.join(a.outdir, "result.json")
    with open(p, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=2)
    print("\n结果 -> %s" % p)
    return 0


if __name__ == "__main__":
    sys.exit(main())
