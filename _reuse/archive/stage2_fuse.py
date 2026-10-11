"""阶段二：候选 → 车牌轨迹分组 → 多帧字符级加权融合 → 车辆数与车牌结论。

分三层，逐层可单独测试：
  1. track_by_iou   把逐帧候选按 IoU + 时间连续性聚成"同一块车牌"的轨迹
  2. fuse_plate     轨迹内做**字符级加权投票**（权重 = 字符概率 × 该帧质量分）
  3. verdict        按一致性与共识度判 CONFIRMED / UNCERTAIN

为什么要字符级投票而不是"整串出现次数最多"：
    同一车牌在不同帧会被读成 粤A532GU / 粤53330 / 苏53803 / 湘53803，
    整串投票会被高频错读整串吃掉；逐字符投票能让"只错某一位"的帧贡献正确字符。
    省份字（粤/苏/湘/浙/川）正好是最容易错、也最适合投票纠正的位置。

用法：
    python stage2_fuse.py --jsonl candidates.jsonl [--out summary.json] [--pretty]
"""
import argparse
import json
import sys
from collections import Counter, defaultdict

import numpy as np

sys.stdout.reconfigure(encoding="utf-8", errors="replace")


# ---------------------------------------------------------------- 1. 分组

def iou_xyxy(a, b):
    x1 = max(a[0], b[0]); y1 = max(a[1], b[1])
    x2 = min(a[2], b[2]); y2 = min(a[3], b[3])
    iw = max(0.0, x2 - x1); ih = max(0.0, y2 - y1)
    inter = iw * ih
    if inter <= 0:
        return 0.0
    ua = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / ua if ua > 0 else 0.0


def _center(box):
    return ((box[0] + box[2]) / 2.0, (box[1] + box[3]) / 2.0)


def _center_dist(a, b):
    ax, ay = _center(a)
    bx, by = _center(b)
    return ((ax - bx) ** 2 + (ay - by) ** 2) ** 0.5


def track_by_iou(cands, iou_thresh=0.3, max_gap_frames=25, max_center_jump=90.0,
                 predict=True, max_scale=2.5):
    """把候选按时间顺序聚成轨迹。

    为什么需要运动预测：
        抽帧后（例如 30fps 视频按 5fps 采样）相邻两帧间隔 6 帧，跟车时前车在
        画面里位移很大，同一车牌的 IoU 会掉到阈值以下，于是同一条轨迹被切成
        十几条碎片（实测：一个车牌被切成 10 条，省份在 粤/津/川/苏/赣 之间跳）。
        因此用轨迹最近两点的**常速度外推**去预测当前位置，再与预测框比 IoU，
        并放宽中心距离（随帧间隔线性放大），才能把同一车牌粘住。

    max_scale：车牌在画面中的尺寸变化上限（跟车靠近会变大），超过视为不同目标。
    """
    tracks = []
    by_frame = defaultdict(list)
    for c in cands:
        by_frame[c["frame"]].append(c)
    # 同一帧内先按置信度降序，作为无匹配时的新建顺序
    for f in by_frame:
        by_frame[f].sort(key=lambda x: -x["det_conf"])

    for frame in sorted(by_frame):
        dets = by_frame[frame]

        # 1) 为每个活跃轨迹外推当前位置
        active = []            # (track_index, predicted_box, gap)
        for ti, tr in enumerate(tracks):
            gap = frame - tr["last_frame"]
            if gap > max_gap_frames or gap <= 0:
                continue
            prev = tr["cands"][-1]
            pbox = list(prev["box"])
            if predict and len(tr["cands"]) >= 2:
                p2 = tr["cands"][-2]
                dgap = max(1, prev["frame"] - p2["frame"])
                vx = (prev["box"][0] - p2["box"][0]) / dgap
                vy = (prev["box"][1] - p2["box"][1]) / dgap
                pbox = [pbox[0] + vx * gap, pbox[1] + vy * gap,
                        pbox[2] + vx * gap, pbox[3] + vy * gap]
            active.append((ti, pbox, gap))

        # 2) 构造代价矩阵（基于 IoU，带尺寸突变与中心位移闸门）
        assigned = set()
        if active:
            cost = np.zeros((len(dets), len(active)), dtype=np.float64)
            for di, c in enumerate(dets):
                aw = max(1.0, c["box"][2] - c["box"][0])
                for ai, (ti, pbox, gap) in enumerate(active):
                    prev = tracks[ti]["cands"][-1]
                    pw = max(1.0, prev["box"][2] - prev["box"][0])
                    ratio = max(aw / pw, pw / aw)
                    jump = max_center_jump * (1.0 + 0.5 * gap)
                    v = iou_xyxy(c["box"], pbox)
                    if ratio > max_scale or _center_dist(c["box"], pbox) > jump:
                        v = 0.0
                    cost[di, ai] = 1.0 - v
            try:
                from scipy.optimize import linear_sum_assignment
                ri, ci = linear_sum_assignment(cost)
                pairs = list(zip(ri.tolist(), ci.tolist()))
            except Exception:
                # 退化：贪心
                pairs = []
                used_c = set()
                for di in np.argsort(cost.min(axis=1)):
                    ai = int(np.argmin(cost[di]))
                    if ai not in used_c:
                        used_c.add(ai)
                        pairs.append((int(di), ai))
            # 3) 只接受 IoU 达标的配对；一帧内一个轨迹最多吃一个检测
            taken_det = set()
            for di, ai in pairs:
                if cost[di, ai] > 1.0 - iou_thresh:
                    continue
                ti = active[ai][0]
                if ti in assigned or di in taken_det:
                    continue
                tr = tracks[ti]
                tr["cands"].append(dets[di])
                tr["last_frame"] = frame
                assigned.add(ti)
                taken_det.add(di)
        else:
            taken_det = set()

        # 4) 未匹配的检测新建轨迹
        for di, c in enumerate(dets):
            if di in taken_det:
                continue
            tracks.append({"cands": [c], "last_frame": frame})
    return tracks


# ---------------------------------------------------------------- 2b. 应急车道判定

def point_in_polygon(pt, poly):
    """射线法：判断点是否在多边形内（pty 任意顶点顺序）。"""
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


def vehicle_footprint(cand, up_ratio=0.35, width_mult=3.4):
    """由车牌框反推车辆在地面上的占位区间。

    车牌贴在车尾上部，直接用车牌判定会把"车已压线但车牌还没过线"的车漏掉。
    这里做两个近似：
      1) 竖直方向：从车牌底边向上偏移 up_ratio×车牌高，估计车轮着地线；
      2) 水平方向：车身宽 ≈ 车牌宽 × width_mult，以车牌中心展开。
    返回 (水平区间 [xa, xb], 着地 y)。这是近似值，对边界附近的车不可靠，
    所以判定用"水平区间与应急车道多边形的重叠比例"，而不是单点内/外。
    """
    x1, y1, x2, y2 = cand["box"]
    cx = (x1 + x2) / 2.0
    w = max(1.0, x2 - x1)
    h = max(1.0, y2 - y1)
    y_ground = y2 - up_ratio * h
    half = 0.5 * w * width_mult
    return (cx - half, cx + half), y_ground


def lane_overlap_ratio(cand, poly, poly_area, up_ratio, width_mult, samples=25):
    """车辆水平区间落在应急车道内的比例（0~1）。

    实现：沿着地线在该车的水平区间内等距取点，统计落在多边形内的比例。
    若多边形整体在该 y 处不与车水平区间相交，则比例为 0。
    """
    (xa, xb), y = vehicle_footprint(cand, up_ratio, width_mult)
    if xb <= xa:
        return 0.0
    hit = 0
    for i in range(samples):
        x = xa + (xb - xa) * (i + 0.5) / samples
        if point_in_polygon((x, y), poly):
            hit += 1
    return hit / float(samples)


def polygon_area(poly):
    s = 0.0
    n = len(poly)
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        s += x1 * y2 - x2 * y1
    return abs(s) / 2.0


# ---------------------------------------------------------------- 2. 融合

def quality_score(c, max_area):
    """帧质量分，全部归一到 (0,1) 后相乘。

    只用可解释的客观量：检测置信度、字符概率均值、清晰度、车牌面积、亮度。
    注意：**不使用超分辨率或增强图的分数**——识别必须基于原始帧。
    """
    sharp = min(1.0, c["sharpness"] / 3000.0)          # 3000 以上视为足够清晰
    area = min(1.0, (c["w"] * c["h"]) / max(1.0, max_area))
    cc = c["char_conf"]
    ocred = sum(cc) / len(cc) if cc else 0.0
    bright = 1.0 - abs(c["brightness"] - 128.0) / 128.0
    bright = max(0.0, min(1.0, bright))
    return (max(1e-6, c["det_conf"]) * max(1e-6, ocred) *
            max(1e-6, sharp) * max(1e-6, area) * max(1e-6, bright))


def fuse_plate(cands):
    """字符级加权投票。

    先按最长串对齐（以多数长度为准），再逐位累加 (字符 -> 权重)，
    权重 = 该帧质量分 × 该字符自身的 OCR 概率。
    这样"字符概率高且画面清晰"的帧说话更响，但单帧无法一手遮天。
    """
    if not cands:
        return None
    max_area = max(c["w"] * c["h"] for c in cands)
    scored = [(c, quality_score(c, max_area)) for c in cands]

    lens = Counter(len(c["text"]) for c in cands if c["text"])
    if not lens:
        return {"plate": None, "status": "UNREADABLE", "n": len(cands)}
    L = lens.most_common(1)[0][0]

    votes = [defaultdict(float) for _ in range(L)]
    total_w = 0.0
    agree_full = Counter()
    for c, q in scored:
        t = c["text"]
        if not t:
            continue
        total_w += q
        agree_full[t] += q
        if len(t) != L:
            continue
        for i, ch in enumerate(t):
            p = c["char_conf"][i] if i < len(c["char_conf"]) else 0.5
            votes[i][ch] += q * p

    fused, per_char_conf = [], []
    for i in range(L):
        if not votes[i]:
            fused.append("?")
            per_char_conf.append(0.0)
            continue
        tot = sum(votes[i].values())
        ch, w = max(votes[i].items(), key=lambda kv: kv[1])
        fused.append(ch)
        per_char_conf.append(w / tot if tot > 0 else 0.0)

    text = "".join(fused)
    plate_conf = sum(per_char_conf) / L if L else 0.0
    # 与融合结果完全一致的帧所占权重比 —— 衡量"多帧共识"
    consensus = agree_full.get(text, 0.0) / total_w if total_w > 0 else 0.0

    # 最短冲突位：分歧最大的字符位置
    worst = min(per_char_conf) if per_char_conf else 0.0
    return {
        "plate": text,
        "plate_conf": round(plate_conf, 4),
        "consensus": round(consensus, 4),
        "char_conf": [round(v, 4) for v in per_char_conf],
        "min_char_conf": round(worst, 4),
        "n_cands": len(cands),
        "weighted_votes": round(total_w, 4),
    }


def verdict(fused, min_cands=3, min_plate_conf=0.75, min_consensus=0.30):
    """判定 CONFIRMED / UNCERTAIN。

    原则（宁可少识别也不制造错误车牌号）：
      - 候选帧太少 → UNCERTAIN
      - 平均字符置信度不足 → UNCERTAIN
      - 有任意一位字符分歧过大 → UNCERTAIN
      - 出现 '?' 占位 → UNCERTAIN
    """
    if fused is None or fused.get("plate") is None:
        return "UNREADABLE", "无有效识别结果"
    if "?" in fused["plate"]:
        return "UNCERTAIN", "存在无法确定的字符位"
    if fused["n_cands"] < min_cands:
        return "UNCERTAIN", "有效候选帧不足 %d（实际 %d）" % (min_cands, fused["n_cands"])
    if fused["plate_conf"] < min_plate_conf:
        return "UNCERTAIN", "平均字符置信度 %.2f < %.2f" % (fused["plate_conf"], min_plate_conf)
    if fused["min_char_conf"] < 0.45:
        return "UNCERTAIN", "最弱字符置信度 %.2f 过低" % fused["min_char_conf"]
    if fused["consensus"] < min_consensus:
        return "UNCERTAIN", "多帧共识度 %.2f < %.2f" % (fused["consensus"], min_consensus)
    return "CONFIRMED", "多帧一致"


# ---------------------------------------------------------------- 3. 主流程

def load(path):
    out = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--jsonl", required=True)
    ap.add_argument("--out", default=None)
    ap.add_argument("--roi", default=None,
                    help="ROI 配置 yaml；不传则不做应急车道筛选（会报出全部车牌）")
    ap.add_argument("--iou", type=float, default=0.2)
    ap.add_argument("--min-cands", type=int, default=3)
    ap.add_argument("--min-duration", type=float, default=1.0, help="违章最小时长(秒)")
    ap.add_argument("--min-track-frames", type=int, default=5, help="违章最少帧数")
    ap.add_argument("--pretty", action="store_true")
    a = ap.parse_args()

    cands = load(a.jsonl)
    print("读入候选 %d 条，覆盖 %d 帧" % (len(cands), len({c["frame"] for c in cands})))
    if not cands:
        return 1

    # ---- ROI ----
    poly = None
    up_ratio, width_mult, overlap_thresh = 0.35, 3.4, 0.35
    if a.roi:
        import yaml
        with open(a.roi, encoding="utf-8") as fh:
            rc = yaml.safe_load(fh)
        el = rc.get("emergency_lane", {})
        poly = [tuple(p) for p in el.get("polygon", [])]
        kp = el.get("keypoint", {}) or {}
        up_ratio = float(kp.get("up_ratio", up_ratio))
        width_mult = float(el.get("vehicle_width_from_plate", width_mult))
        overlap_thresh = float(el.get("overlap_thresh", overlap_thresh))
        print("ROI: %d 顶点, 面积=%.0f px², 重叠阈值=%.2f" % (len(poly), polygon_area(poly), overlap_thresh))
    else:
        print("!! 未提供 --roi：将不做应急车道筛选，仅做车牌识别")

    tracks = track_by_iou(cands, iou_thresh=a.iou)
    tracks.sort(key=lambda t: t["cands"][0]["t"])
    print("聚成车牌轨迹 %d 条\n" % len(tracks))

    results = []
    lane_tracks = []
    for i, tr in enumerate(tracks, 1):
        cs = tr["cands"]
        f = fuse_plate(cs)
        st, why = verdict(f, min_cands=a.min_cands)
        t0, t1 = cs[0]["t"], cs[-1]["t"]
        best = max(cs, key=lambda c: quality_score(c, max(x["w"] * x["h"] for x in cs)))
        colors = Counter(c["color"] for c in cs)

        lane = None
        if poly is not None:
            # 逐候选算重叠比例，再取整条轨迹的多数表决，避免单帧抖动
            ratios = [(c["t"], lane_overlap_ratio(c, poly, None, up_ratio, width_mult))
                      for c in cs]
            in_lane = [(t, r) for t, r in ratios if r >= overlap_thresh]
            frac = len(in_lane) / float(len(ratios))
            lane = {
                "n_in_lane": len(in_lane),
                "n_total": len(ratios),
                "in_lane_ratio": round(frac, 3),
                "max_overlap": round(max(r for _, r in ratios), 3),
                "first_in_lane_t": round(in_lane[0][0], 2) if in_lane else None,
                "last_in_lane_t": round(in_lane[-1][0], 2) if in_lane else None,
                "dwell": round(in_lane[-1][0] - in_lane[0][0], 2) if len(in_lane) > 1 else 0.0,
            }

        results.append({
            "track_no": i,
            "t_first": t0, "t_last": t1, "duration": round(t1 - t0, 2),
            "n_frames": len(cs),
            "box_first": cs[0]["box"], "box_last": cs[-1]["box"],
            "plate": f,
            "status": st, "status_reason": why,
            "color": colors.most_common(1)[0][0],
            "lane": lane,
            "best_frame": {"frame": best["frame"], "t": best["t"],
                           "sharpness": best["sharpness"],
                           "w": best["w"], "h": best["h"], "text": best["text"]},
        })

        # 违章判定：在应急车道内持续够久，且车牌状态 CONFIRMED
        if lane and lane["n_in_lane"] > 0:
            if (lane["in_lane_ratio"] >= 0.5 and
                    lane["dwell"] >= a.min_duration and
                    lane["n_in_lane"] >= a.min_track_frames):
                lane_tracks.append(results[-1])

    # ---- 报告 ----
    print("%-5s %-8s %-8s %-6s %-14s %-11s %-16s %s" %
          ("轨迹", "起(秒)", "止(秒)", "帧数", "融合车牌", "状态", "应急车道", "说明"))
    print("-" * 118)
    for r in results:
        p = r["plate"]
        pl = p["plate"] if p and p.get("plate") else "(无)"
        ln = r["lane"]
        if ln is None:
            lanestr = "(未筛选)"
        elif ln["n_in_lane"] == 0:
            lanestr = "否"
        else:
            lanestr = "是(%.0f%%,%.2fs)" % (ln["in_lane_ratio"] * 100, ln["dwell"])
        print("%-5d %-8.2f %-8.2f %-6d %-14s %-11s %-16s %s" %
              (r["track_no"], r["t_first"], r["t_last"], r["n_frames"],
               pl[:14], r["status"], lanestr, r["status_reason"]))

    print("\n=== 汇总 ===")
    print("车牌轨迹总数: %d" % len(results))
    if poly is not None:
        print("进入过应急车道的轨迹: %d" % sum(1 for r in results if r["lane"] and r["lane"]["n_in_lane"] > 0))
        print("判定为违章(持续%.1fs以上): %d" % (a.min_duration, len(lane_tracks)))
    print("  CONFIRMED : %d" % sum(1 for r in results if r["status"] == "CONFIRMED"))
    print("  UNCERTAIN : %d" % sum(1 for r in results if r["status"] == "UNCERTAIN"))
    print("  UNREADABLE: %d" % sum(1 for r in results if r["status"] == "UNREADABLE"))

    if lane_tracks:
        print("\n违章车辆（应急车道内持续行驶）：")
        for r in lane_tracks:
            print("  轨迹%d  %s  置信度 %.2f  进入 %.2fs - %.2fs（持续 %.2fs）"
                  % (r["track_no"], r["plate"]["plate"], r["plate"]["plate_conf"],
                     r["lane"]["first_in_lane_t"], r["lane"]["last_in_lane_t"],
                     r["lane"]["dwell"]))
    elif poly is not None:
        print("\n未发现应急车道占用车辆。")

    summary = {
        "source_jsonl": a.jsonl, "roi": a.roi,
        "n_candidates": len(cands), "n_tracks": len(results),
        "n_in_emergency_lane": sum(1 for r in results if r["lane"] and r["lane"]["n_in_lane"] > 0),
        "n_violations": len(lane_tracks),
        "n_confirmed": sum(1 for r in results if r["status"] == "CONFIRMED"),
        "n_uncertain": sum(1 for r in results if r["status"] == "UNCERTAIN"),
        "n_unreadable": sum(1 for r in results if r["status"] == "UNREADABLE"),
        "tracks": results,
    }
    if a.out:
        with open(a.out, "w", encoding="utf-8") as fh:
            json.dump(summary, fh, ensure_ascii=False, indent=2 if a.pretty else None)
        print("\n汇总已写出 -> %s" % a.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
