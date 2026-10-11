"""轨迹归并：把"同一辆车被拆碎"的多条轨迹合并为一个案例。

问题背景（实测）：
    用 IoU 跟踪时，车辆一旦被遮挡或连续漏检超过阈值，轨迹会断开并新建 ID。
    结果同一辆车被献成 2-4 条轨迹，而每条又被当成一个"案例"报给用户——
    实测 30 秒的视频因此报出 10 条案例，用户人眼只记录了 5 辆车。

归并必须靠**车辆框位置**，不能只看车牌横向距离：
    同一条轨迹可能一个车牌都没识别出来（cands 为空），此时无法比较车牌；
    而密集车流中左右相邻的两辆车会同时出现在画面里，车牌 x 差也可能很小。
    物理约束是：**同一辆车不可能同时在两个位置**。

因此本脚本用与主流程相同的 IoU 跟踪算法重建"轨迹 → 逐帧车辆框"索引，
再据此判断两条轨迹在时间重叠处是否位置一致。

用法：
    python merge_tracks.py --result <result.json> --veh-jsonl <veh.jsonl> --out <out.json>
"""
import argparse
import json
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import runmeta  # noqa: E402

sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def norm(s):
    if not s:
        return ""
    return "".join(ch for ch in str(s).upper()
                   if ch.isalnum() or "\u4e00" <= ch <= "\u9fff")


def box_iou(a_, b_):
    x1, y1 = max(a_[0], b_[0]), max(a_[1], b_[1])
    x2, y2 = min(a_[2], b_[2]), min(a_[3], b_[3])
    if x1 >= x2 or y1 >= y2:
        return 0.0
    inter = (x2 - x1) * (y2 - y1)
    un = (a_[2]-a_[0])*(a_[3]-a_[1]) + (b_[2]-b_[0])*(b_[3]-b_[1]) - inter
    return inter / un if un > 0 else 0.0


def rebuild_tracks(veh_jsonl, fps=30.0, iou_thresh=0.25, max_gap=20):
    """重建与主流程一致的轨迹，返回 {track_id: {"first","last","boxes":[(frame,box)]}}。

    注意：track_id 的编号顺序与主流程一致（按帧序、按置信度desc新建），
    因此可以直接与 result.json 里的 track_id 对齐。
    """
    rows = [json.loads(l) for l in open(veh_jsonl, encoding="utf-8")]
    by_frame = {}
    for r in rows:
        by_frame.setdefault(r["frame"], []).append(r)

    tracks, nid = [], 0
    for fr in sorted(by_frame):
        used = set()
        for d in sorted(by_frame[fr], key=lambda z: -z["conf"]):
            best, bi = 0.0, -1
            for i, t in enumerate(tracks):
                if i in used or fr - t["last"] > max_gap:
                    continue
                v = box_iou(d["box"], t["box"])
                if v > best:
                    best, bi = v, i
            if bi >= 0 and best >= iou_thresh:
                t = tracks[bi]
                t["boxes"].append((fr, d["box"]))
                t["box"] = d["box"]
                t["last"] = fr
                used.add(bi)
            else:
                tracks.append({"id": nid, "boxes": [(fr, d["box"])],
                               "box": d["box"], "last": fr, "first": fr})
                nid += 1
    return {t["id"]: {"first": t["first"], "last": t["last"], "boxes": t["boxes"]}
            for t in tracks}


def min_box_distance(boxes_a, boxes_b, time_tol_frames=8):
    """两组 (frame, box) 在时间相近时的最小中心距离（归一化：除以平均框宽）。"""
    if not boxes_a or not boxes_b:
        return None
    best = None
    for f1, b1 in boxes_a:
        for f2, b2 in boxes_b:
            if abs(f1 - f2) > time_tol_frames:
                continue
            cx1, cy1 = (b1[0]+b1[2])/2, (b1[1]+b1[3])/2
            cx2, cy2 = (b2[0]+b2[2])/2, (b2[1]+b2[3])/2
            d = ((cx1-cx2)**2 + (cy1-cy2)**2) ** 0.5
            w = ((b1[2]-b1[0]) + (b2[2]-b2[0])) / 2.0
            dn = d / max(20.0, w)
            if best is None or dn < best:
                best = dn
    return best


def merge(items, track_index, gap_max=3.0, dist_max=1.2, time_tol_frames=8):
    """反复合并直到稳定。判定同车需满足：

    A. 时间重叠：车辆框在相近帧上中心距离 <= dist_max（以框宽为单位）
    B. 不重叠但间隔 <= gap_max：车牌完全一致
    另外：两车车牌**明确不同且都非空**时，一律不合并。
    """
    changed = True
    while changed:
        changed = False
        items.sort(key=lambda z: z["t_first"])
        out = []
        for it in items:
            hit = None
            for k in out:
                overlap = min(k["t_last"], it["t_last"]) - max(k["t_first"], it["t_first"])
                gap = it["t_first"] - k["t_last"]
                same_plate = (k["plate"] and it["plate"]
                              and norm(k["plate"]) == norm(it["plate"]))
                if k["plate"] and it["plate"] and not same_plate:
                    continue                       # 车牌明确不同 → 不同车

                boxes_k = [b for tid in k["src"] for b in track_index.get(tid, {}).get("boxes", [])]
                boxes_i = [b for tid in it["src"] for b in track_index.get(tid, {}).get("boxes", [])]
                dn = min_box_distance(boxes_k, boxes_i, time_tol_frames)

                if overlap > 0:
                    if dn is not None and dn <= dist_max:
                        hit = k
                    elif dn is None and same_plate:
                        hit = k
                elif gap <= gap_max and same_plate:
                    hit = k
                if hit is not None:
                    break
            if hit is None:
                out.append(it)
            else:
                hit["src"] += it["src"]
                hit["t_first"] = min(hit["t_first"], it["t_first"])
                hit["t_last"] = max(hit["t_last"], it["t_last"])
                hit["n_frames"] += it["n_frames"]
                hit["max_overlap"] = max(hit["max_overlap"], it["max_overlap"])
                hit["cands"] += it["cands"]
                hit["texts"] += it["texts"]
                if it["plate_conf"] > hit["plate_conf"]:
                    for f in ("plate", "plate_conf", "status", "reason",
                              "best_frame", "best_box"):
                        hit[f] = it[f]
                changed = True
        items = out
    return items


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--result", required=True)
    ap.add_argument("--veh-jsonl", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--gap-max", type=float, default=3.0)
    ap.add_argument("--dist-max", type=float, default=1.2,
                    help="同车判定：车辆框中心距离上限（以框宽为单位）")
    ap.add_argument("--time-tol-frames", type=int, default=8)
    a = ap.parse_args()

    d = json.load(open(a.result, encoding="utf-8"))
    cases = d["results"]
    track_index = rebuild_tracks(a.veh_jsonl)
    print("原始轨迹 %d 条（重建索引 %d 条）" % (len(cases), len(track_index)))
    for c in cases:
        print("   轨迹%-3d %6.2f-%6.2f (%2d帧)  %s" % (
            c["track_id"], c["t_first"], c["t_last"], c["n_frames"],
            c["plate"] or "(未识别)"))

    items = []
    for c in cases:
        items.append({
            "src": [c["track_id"]],
            "t_first": c["t_first"], "t_last": c["t_last"],
            "n_frames": c["n_frames"],
            "plate": c.get("plate"), "plate_conf": c.get("plate_conf", 0.0),
            "status": c.get("status"), "reason": c.get("reason"),
            "max_overlap": c.get("max_overlap", 0.0),
            "cands": c.get("cands") or [],
            "best_frame": c.get("best_frame"), "best_box": c.get("best_box"),
            "texts": Counter(c.get("texts") or {}),
        })
    merged = merge(items, track_index, gap_max=a.gap_max, dist_max=a.dist_max,
                   time_tol_frames=a.time_tol_frames)

    print("\n归并后 %d 个案例：" % len(merged))
    for i, m in enumerate(merged, 1):
        cnt = Counter(norm(c["text"]) for c in m["cands"]) if m["cands"] else Counter()
        if cnt:
            top_txt, top_n = cnt.most_common(1)[0]
            top_conf = max((c["conf"] for c in m["cands"]
                            if norm(c["text"]) == top_txt), default=0.0)
            plate, conf = top_txt, round(top_conf, 4)
            if top_conf >= 0.90 and top_n >= 2:
                status, reason = "CONFIRMED", "置信 %.2f 且 %d 次一致" % (top_conf, top_n)
            else:
                status, reason = "SUSPECT", "置信 %.2f、%d 次（需人工确认）" % (top_conf, top_n)
        else:
            plate, conf = m["plate"], m["plate_conf"]
            status, reason = m["status"], m["reason"]
        m["plate"], m["plate_conf"], m["status"], m["reason"] = plate, conf, status, reason
        m["texts"] = dict(cnt.most_common(6))
        print("   案例%d  %6.2f - %6.2f s  (%2d帧)  合并轨迹%-20s → %-12s 【%s】"
              % (i, m["t_first"], m["t_last"], m["n_frames"], str(m["src"]),
                 plate or "(未识别)", status))

    out = dict(d)
    out["results"] = merged
    out["n_cases_before_merge"] = len(cases)
    out["n_cases_after_merge"] = len(merged)
    out["n_confirmed"] = sum(1 for m in merged if m["status"] == "CONFIRMED")
    out["n_suspect"] = sum(1 for m in merged if m["status"] == "SUSPECT")
    out["n_unreadable"] = sum(1 for m in merged if m["status"] == "UNREADABLE")
    os.makedirs(os.path.dirname(os.path.abspath(a.out)) or ".", exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=2)
    print("\n归并结果 -> %s" % a.out)
    print("案例数 %d -> %d" % (len(cases), len(merged)))

    # 归并参数一并落盘。上游 result.json 里已有的 run 元数据（video/roi/上游参数）保留，
    # 这里追加 merge 段，形成「上游怎么跑的 + 归并用什么参数」的完整链路。
    meta = dict((d.get("run") or {}))
    m_meta = runmeta.collect(
        __file__, a, inputs={"result_in": a.result, "veh_jsonl": a.veh_jsonl},
        extra={"n_cases_before_merge": len(cases), "n_cases_after_merge": len(merged),
               "outputs": {"result_out": os.path.abspath(a.out),
                           "size": os.path.getsize(a.out),
                           "sha256": runmeta.sha256_file(a.out)}})
    meta["merge"] = m_meta
    meta["version"] = m_meta["version"]
    meta["script_chain"] = list(dict.fromkeys(
        ([d["run"].get("script")] if d.get("run") else []) + [m_meta["script"]]))
    out["run"] = meta
    with open(a.out, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=2)
    runmeta.write(a.out + ".runmeta.json", meta)


if __name__ == "__main__":
    main()
