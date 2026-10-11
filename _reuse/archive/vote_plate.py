"""多帧/多参数 字符级加权投票 —— 解决"字符读对但组合错"的失败模式。

为什么需要它（实测依据）：
    应急车道上那几辆车的识别失败并非"完全读不到"，而是**组合错误**：
        真值 粤SVZ600  → 读成 粤A8M902 / 粤A8R902
        真值 粤AAG4884 → 读成 粤A1M7M4 / 粤A747M4
    正确字符都出现在候选里，只是每次被拼到了不同位置。
    "取最高置信度单条"会把这类错误直接采纳；字符级投票能让多数帧互相纠错。

投票设计要点：
  1. **按长度分组**：7 位（普通车牌）与 8 位（新能源）不能混投，
     否则每次插入/漏读一个字符就会让后续所有位置错位、投票全废。
  2. **权重 = 识别置信度 × 质量分**，质量分用"车牌在原图中的有效高度"衡量
     （车牌越大越可信），并做归一化避免量纲影响。
  3. **按字符位归一化置信度**：某一位分歧大则该位置信度低，
     进而判为 SUSPECT 交人工确认——宁可不确认，也不报错号。
"""
import argparse
import json
import sys
from collections import Counter, defaultdict

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

TRUTH = [(2.0, "粤A130V9"), (11.0, "粤A8R902"), (14.0, "粤SVZ600"),
         (23.0, "粤A7M7M4"), (25.0, "粤AAG4884")]


def select_for_vehicle(cands, veh_box, expand=0.20, strict=True,
                       max_plate_w_ratio=0.55, max_plate_area_ratio=0.12):
    """只保留属于"该车辆"的车牌候选。

    两个必须做的过滤（都来自实测踩坑）：
      1) **位置**：车牌中心要落在（略外扩的）车辆框内。
         不筛会让正前方大车牌（粤A532GU，置信度 1.0）压倒一切，
         每辆车都投出 粤A532GU（实测 5/5 全错）。
      2) **尺寸比**：车牌宽度不能超过车辆框宽度的 max_plate_w_ratio，
         车牌面积不能超过车辆框面积的 max_plate_area_ratio。
         仅靠位置不够——正前方面包车离得近，它的车牌中心也会落进
         目标车辆的外扩框里；但它是"大牌占大车"，尺寸比会露馅。

    strict=True：框内找不到就返回空，由上层判 UNCERTAIN。
        早期版本会退化为"取最近的若干个"，结果把邻车车牌
        （如 湘DST356）投给目标车——报出别车车牌比说不确定更危险。
    """
    if not cands:
        return []
    x1, y1, x2, y2 = veh_box
    w, h = x2 - x1, y2 - y1
    if w <= 0 or h <= 0:
        return []
    ex1, ey1 = x1 - w * expand, y1 - h * expand
    ex2, ey2 = x2 + w * expand, y2 + h * expand

    inside = []
    for c in cands:
        pb = c.get("plate_box")
        if not pb:
            continue
        px, py = (pb[0] + pb[2]) / 2.0, (pb[1] + pb[3]) / 2.0
        if not (ex1 <= px <= ex2 and ey1 <= py <= ey2):
            continue
        pw = abs(pb[2] - pb[0])
        ph = abs(pb[3] - pb[1])
        if pw > w * max_plate_w_ratio:
            continue
        if (pw * ph) > (w * h) * max_plate_area_ratio:
            continue
        inside.append(c)
    return inside


def norm(s):
    """归一化：只保留中文与字母数字，去分隔符并统一大写。"""
    if not s:
        return ""
    return "".join(ch for ch in str(s).upper()
                   if ch.isalnum() or "\u4e00" <= ch <= "\u9fff")


def vote(cands, high_conf=0.90, min_votes=2, min_char_conf=0.55):
    """字符级加权投票，返回融合结果与分级。

    cands: [{"text":..., "conf":..., "pw":车牌宽, "ph":车牌高}, ...]
    """
    if not cands:
        return {"status": "UNREADABLE", "plate": None, "conf": 0.0,
                "reason": "无识别结果", "char_conf": []}

    # --- 1) 按长度分组 ---
    by_len = defaultdict(list)
    for c in cands:
        t = norm(c["text"])
        if t:
            by_len[len(t)].append((t, c))

    if not by_len:
        return {"status": "UNREADABLE", "plate": None, "conf": 0.0,
                "reason": "候选均为空", "char_conf": []}

    # 选取"总权重最高"的长度组作为主长度
    def group_weight(items):
        return sum(max(1e-6, it[1]["conf"]) for it in items)

    L = max(by_len, key=lambda k: group_weight(by_len[k]))
    items = by_len[L]

    # --- 2) 权重：识别置信度 × 车牌高度质量分 ---
    heights = [max(1.0, c.get("ph", 1.0)) for _, c in items]
    hmax = max(heights) if heights else 1.0
    votes = [defaultdict(float) for _ in range(L)]
    total_w = 0.0
    for (t, c) in items:
        q = max(1e-6, c.get("ph", 1.0)) / hmax      # 归一化质量分
        w = max(1e-6, float(c["conf"])) * q
        total_w += w
        for i, ch in enumerate(t):
            votes[i][ch] += w

    # --- 3) 逐位取最大权字符 ---
    plate, per_char = [], []
    for i in range(L):
        if not votes[i]:
            plate.append("?")
            per_char.append(0.0)
            continue
        tot = sum(votes[i].values())
        ch, w = max(votes[i].items(), key=lambda kv: kv[1])
        plate.append(ch)
        per_char.append(w / tot if tot > 0 else 0.0)

    text = "".join(plate)
    avg_conf = sum(per_char) / L if L else 0.0
    worst = min(per_char) if per_char else 0.0
    # 支持票数：与融合结果完全一致的原始读法出现次数
    exact = [t for t, _ in items if t == text]
    n_exact = len(exact)

    # --- 4) 分级 ---
    if "?" in text:
        status, reason = "SUSPECT", "存在无法确定的字符位"
    elif avg_conf >= high_conf and n_exact >= min_votes and worst >= min_char_conf:
        status, reason = "CONFIRMED", "平均位置信 %.2f，%d 条完全一致" % (avg_conf, n_exact)
    elif worst < min_char_conf:
        status, reason = "SUSPECT", "最弱字符位置信仅 %.2f（需人工确认）" % worst
    elif avg_conf < high_conf:
        status, reason = "SUSPECT", "平均位置信仅 %.2f（需人工确认）" % avg_conf
    else:
        status, reason = "SUSPECT", "仅 %d 条完全一致（需人工确认）" % n_exact

    return {"status": status, "plate": text, "conf": round(avg_conf, 4),
            "worst_char_conf": round(worst, 4), "n_exact": n_exact,
            "n_cands": len(items), "len": L,
            "char_conf": [round(v, 3) for v in per_char],
            "top_per_char": [dict(sorted(votes[i].items(), key=lambda kv: -kv[1])[:3])
                             for i in range(L)],
            "reason": reason}


def baseline(cands):
    """对照基线：取识别置信度最高的单条（不投票）。"""
    if not cands:
        return None, 0.0
    b = max(cands, key=lambda c: c["conf"])
    return norm(b["text"]), b["conf"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", required=True, help="scan_vehicle_region.py 输出的 json")
    ap.add_argument("--high-conf", type=float, default=0.90)
    ap.add_argument("--min-votes", type=int, default=2)
    ap.add_argument("--min-char-conf", type=float, default=0.55)
    ap.add_argument("--expand", type=float, default=0.20,
                    help="车辆框外扩比例；越大越容易收进邻车车牌")
    ap.add_argument("--loose", action="store_true",
                    help="框内无候选时退化为取最近的（不推荐：可能投出邻车车牌）")
    ap.add_argument("--recompute", action="store_true",
                    help="若 json 含原始候选则用它投票；否则用 texts 计数近似")
    a = ap.parse_args()

    data = json.load(open(a.json, encoding="utf-8"))
    print("源: %s" % a.json)
    print("分级阈值: 平均位置信>=%.2f, 完全一致>=%d 条, 最弱位>=%.2f\n"
          % (a.high_conf, a.min_votes, a.min_char_conf))

    n_base = n_vote = 0
    rows = []
    for r in data:
        t0, truth = r["t"], r["truth"]
        ntf = norm(truth)
        raw = r.get("cands") or []
        # 优先用"每个候选自己那一帧"的车辆框过滤；只有全局代表框时退回全局。
        # 车辆在时间窗内会移动，跨帧用同一个框会把正确候选误滤（实测过）。
        if raw and any(c.get("veh_box") for c in raw):
            kept = []
            for c in raw:
                vb = c.get("veh_box") or r.get("veh_box")
                if vb and select_for_vehicle([c], vb, expand=a.expand,
                                             strict=not a.loose):
                    kept.append(c)
            print("t=%5.1fs  候选 %d 条 → 归属该车 %d 条（逐候选按各自车辆框判定）"
                  % (t0, len(raw), len(kept)))
        elif raw and r.get("veh_box"):
            kept = select_for_vehicle(raw, r["veh_box"], expand=a.expand,
                                      strict=not a.loose)
            print("t=%5.1fs  候选 %d 条 → 归属该车 %d 条（用窗口代表框）"
                  % (t0, len(raw), len(kept)))
        else:
            kept = raw
        if kept:
            cands = [{"text": c["text"], "conf": c["conf"],
                      "pw": c.get("pw", 1.0), "ph": c.get("ph", 1.0),
                      "plate_box": c.get("plate_box")} for c in kept]
        else:
            # 退化：只有文本计数，则退化为计数投票（权重均等，信息量更低）
            cands = []
            for txt, cnt in (r.get("texts") or {}).items():
                for _ in range(int(cnt)):
                    cands.append({"text": txt, "conf": 1.0, "pw": 1.0, "ph": 1.0})
        v = vote(cands, a.high_conf, a.min_votes, a.min_char_conf)
        bt, bc = baseline(cands)
        b_ok = (bt == ntf)
        v_ok = (norm(v["plate"] or "") == ntf)
        n_base += 1 if b_ok else 0
        n_vote += 1 if v_ok else 0
        rows.append((t0, truth, bt, bc, v, b_ok, v_ok))

        print("t=%5.1fs  真值 %-10s" % (t0, truth))
        print("   基线(最高置信单条): %-12s %s" % (bt or "(无)", "✅" if b_ok else "❌"))
        print("   投票结果        : %-12s 【%s】 %s"
              % (v["plate"] or "(无)", v["status"], "✅" if v_ok else "❌"))
        print("      %s" % v["reason"])
        if v.get("char_conf"):
            print("      逐位置信: %s" % v["char_conf"])
            print("      逐位候选: %s" % v["top_per_char"])

    print("\n" + "=" * 74)
    print("基线命中 %d/%d ; 投票命中 %d/%d" % (n_base, len(rows), n_vote, len(rows)))
    print("\n【需人工确认清单（SUSPECT）】")
    any_s = False
    for t0, truth, bt, bc, v, b_ok, v_ok in rows:
        if v["status"] == "SUSPECT":
            any_s = True
            print("  t=%5.1fs 参考真值 %-10s 投票=%-12s conf=%.2f  %s"
                  % (t0, truth, v["plate"] or "(无)", v["conf"], v["reason"]))
    if not any_s:
        print("  （无）")


if __name__ == "__main__":
    main()
