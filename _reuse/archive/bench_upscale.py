"""放大帧 + HyperLPR3 基准，并输出**置信度分级**供人工复核。

两个目的：
  1. 放大能否提升小目标车牌识别（HyperLPR3 内部检测分辨率固定，
     把整帧放大等于提升车牌的相对像素占比）
  2. 对识别结果分级标记——任务书要求"宁可少识别也不制造错误车牌号"，
     置信度不足的必须提示用户确认，而不是当成结论

分级规则（三条同时给出，便于用户判断）：
  CONFIRMED   置信度 >= high_conf，且多组合结果一致
  SUSPECT     有识别结果但置信度不足，或不同放大/档位结果分歧 → **需人工确认**
  UNREADABLE  完全无结果（车牌太小/模糊）

用法：
    python bench_upscale.py --video <mp4> --out <json> [--scales 1.0 1.5 2.0]
"""
import argparse
import json
import sys
import time
from collections import Counter, defaultdict

import cv2

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

TRUTH = [(2.0, "粤A130V9"), (11.0, "粤A8R902"), (14.0, "粤SVZ600"),
         (23.0, "粤A7M7M4"), (25.0, "粤AAG4884")]


def norm(s):
    if not s:
        return ""
    return "".join(ch for ch in str(s).upper() if ch.isalnum() or "\u4e00" <= ch <= "\u9fff")


def classify(cands, high_conf=0.90, min_votes=2):
    """按置信度与一致性分级。

    为什么不能只看置信度：实测同一车牌在不同放大倍数下会得到不同读法
    （苏A8K512 / 吉A8K511 / 苏A8K9V1），单看最高分容易选中错读。
    因此要求"高置信 + 至少两个组合给出同一结果"才算 CONFIRMED。
    """
    if not cands:
        return {"status": "UNREADABLE", "plate": None, "best_conf": 0.0,
                "votes": {}, "reason": "无识别结果"}
    votes = Counter(norm(c["text"]) for c in cands)
    best_text, best_n = votes.most_common(1)[0]
    # 该文本的最高置信度
    best_conf = max((c["conf"] for c in cands if norm(c["text"]) == best_text), default=0.0)
    top_conf = max(c["conf"] for c in cands)

    if best_conf >= high_conf and best_n >= min_votes:
        return {"status": "CONFIRMED", "plate": best_text, "best_conf": round(best_conf, 4),
                "votes": dict(votes), "reason": "高置信 %d 票一致" % best_n}
    if best_conf >= high_conf and best_n < min_votes:
        return {"status": "SUSPECT", "plate": best_text, "best_conf": round(best_conf, 4),
                "votes": dict(votes),
                "reason": "置信度 %.2f 达标但仅 %d 票（需人工确认）" % (best_conf, best_n)}
    return {"status": "SUSPECT", "plate": best_text, "best_conf": round(best_conf, 4),
            "votes": dict(votes),
            "reason": "最高置信度仅 %.2f（需人工确认）" % best_conf}


def center_in(bbox, pb, sc):
    """车牌框中心是否落在车辆框内（pb 为放大图坐标，需除以 sc 还原）。"""
    if not pb:
        return False
    px = (pb[0] + pb[2]) / 2.0 / sc
    py = (pb[1] + pb[3]) / 2.0 / sc
    return bbox[0] <= px <= bbox[2] and bbox[1] <= py <= bbox[3]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--veh-jsonl", default=None,
                    help="车辆检测 jsonl；提供后只统计**落在目标车辆框内**的车牌，"
                         "否则前景大车牌（如正前方车）会淹没投票导致误判 CONFIRMED")
    ap.add_argument("--scales", nargs="+", type=float, default=[1.0, 1.5, 2.0])
    ap.add_argument("--levels", nargs="+", type=int, default=[0, 1], help="0=320x 1=640x")
    ap.add_argument("--window", type=float, default=2.0)
    ap.add_argument("--fps-scan", type=float, default=6.0)
    ap.add_argument("--high-conf", type=float, default=0.90)
    ap.add_argument("--veh-pad", type=float, default=0.25,
                    help="车辆框外扩比例；车牌可能落在检测框外（车尾/车头）")
    a = ap.parse_args()

    from hyperlpr3 import LicensePlateCatcher
    cats = {lv: LicensePlateCatcher(detect_level=lv) for lv in a.levels}

    veh = None
    if a.veh_jsonl:
        veh = [json.loads(l) for l in open(a.veh_jsonl, encoding="utf-8")]
        print("载入车辆检测 %d 条，用于限定车牌归属" % len(veh))

    cap = cv2.VideoCapture(a.video)
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    print("视频 %dx%d @%.1fps" % (int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)),
                                  int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)), fps))
    print("放大倍数 %s ; 检测档位 %s ; 高置信阈值 %.2f"
          % (a.scales, a.levels, a.high_conf))

    step = max(1, int(round(fps / a.fps_scan)))
    report = []
    t_all = time.time()

    for t0, truth in TRUTH:
        ntf = norm(truth)
        print("\n" + "=" * 74)
        print("真值 t=%.1fs  %s" % (t0, truth))
        # 目标车辆框（窗口内、在应急车道、置信度最高）
        target = None
        if veh is not None:
            c = [v for v in veh if abs(v["t"] - t0) <= a.window and v["in_emergency_lane"]]
            if not c:
                c = [v for v in veh if abs(v["t"] - t0) <= a.window]
            if c:
                target = max(c, key=lambda x: x["conf"])
                b = list(target["box"])
                pw_ = (b[2] - b[0]) * a.veh_pad
                ph_ = (b[3] - b[1]) * a.veh_pad
                target["box_padded"] = [b[0] - pw_ / 2, b[1] - ph_ / 2,
                                        b[2] + pw_ / 2, b[3] + ph_ / 2]
                print("  目标车辆: t=%.2f conf=%.2f box=%s 外扩后=%s"
                      % (target["t"], target["conf"],
                         [round(q) for q in b], [round(q) for q in target["box_padded"]]))
        cands = []
        n_other = 0
        frames = 0
        for fi in range(int((t0 - a.window) * fps), int((t0 + a.window) * fps), step):
            if fi < 0:
                continue
            cap.set(cv2.CAP_PROP_POS_FRAMES, fi)
            ok, f = cap.read()
            if not ok:
                continue
            frames += 1
            t = fi / fps
            for sc in a.scales:
                img = f if sc == 1.0 else cv2.resize(
                    f, None, fx=sc, fy=sc, interpolation=cv2.INTER_CUBIC)
                for lv, cat in cats.items():
                    try:
                        for r in cat(img):
                            txt, conf = str(r[0]), float(r[1])
                            pb = r[3] if len(r) > 3 else None
                            pw = ((pb[2] - pb[0]) / sc) if pb else 0
                            ph = ((pb[3] - pb[1]) / sc) if pb else 0
                            # 只保留属于目标车辆的车牌
                            if target is not None and not center_in(target["box_padded"], pb, sc):
                                n_other += 1
                                continue
                            cands.append({"t": round(t, 2), "text": txt,
                                          "conf": round(conf, 4), "scale": sc, "level": lv,
                                          "plate_w_orig": round(pw), "plate_h_orig": round(ph),
                                          "hit": norm(txt) == ntf})
                    except Exception:
                        pass
        if target is not None:
            print("  剔除不属于目标车辆的车牌 %d 条" % n_other)

        verdict = classify(cands, high_conf=a.high_conf)
        cnt = Counter(norm(c["text"]) for c in cands)
        print("  扫描 %d 帧，候选 %d 条（%d 种文本）" % (frames, len(cands), len(cnt)))
        for txt, c in cnt.most_common(6):
            ex = [x for x in cands if norm(x["text"]) == txt]
            mx = max(ex, key=lambda x: x["conf"])
            scs = sorted({x["scale"] for x in ex})
            print("     %-12s x%-3d 最高conf=%.3f 放大%s 车牌%dx%d %s"
                  % (txt, c, mx["conf"], scs, mx["plate_w_orig"], mx["plate_h_orig"],
                     "← 真值" if norm(txt) == ntf else ""))
        hits = [c for c in cands if c["hit"]]
        print("  命中真值: %s" % ("✅ %d 次" % len(hits) if hits else "❌"))
        print("  判定: 【%s】 %s" % (verdict["status"], verdict["reason"]))
        report.append({"t": t0, "truth": truth, "truth_norm": ntf,
                       "verdict": verdict, "hit": bool(hits),
                       "n_cands": len(cands), "texts": dict(cnt)})

    cap.release()
    n_hit = sum(1 for r in report if r["hit"])
    by = Counter(r["verdict"]["status"] for r in report)
    print("\n" + "=" * 74)
    print("命中 %d/%d = %.0f%%   耗时 %.1fs" % (n_hit, len(TRUTH),
                                              100.0 * n_hit / len(TRUTH), time.time() - t_all))
    print("分级分布: %s" % dict(by))
    print("\n【需人工确认的清单】")
    need = [r for r in report if r["verdict"]["status"] == "SUSPECT"]
    if not need:
        print("  （无）")
    for r in need:
        v = r["verdict"]
        print("  t=%5.1fs  参考真值 %-10s  识别=%-12s conf=%.2f  原因: %s"
              % (r["t"], r["truth"], v["plate"] or "(无)", v["best_conf"], v["reason"]))
    with open(a.out, "w", encoding="utf-8") as fh:
        json.dump({"scales": a.scales, "levels": a.levels,
                   "high_conf": a.high_conf, "results": report},
                  fh, ensure_ascii=False, indent=2)
    print("\n-> %s" % a.out)


if __name__ == "__main__":
    main()
