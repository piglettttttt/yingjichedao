"""全帧扫描 + 按距离归属：绕开"车辆框是否精准"这个不确定环节。

动机：前一版按车辆框过滤后候选全变 0，但尺度诊断里 HyperLPR3 明明在
粤A8R902 附近检出过车牌（约 80x29px）。怀疑是车辆框偏窄把正确结果滤掉了。
因此这里**不做框过滤**，而是把该时段内所有车牌检出都列出来，
并用 YOLO 车辆框做"最近车辆"归属，人工即可判断哪个是本车的牌。

用法：
    python scan_fullframe.py --video <mp4> --veh-jsonl <jsonl> --out <json>
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", required=True)
    ap.add_argument("--veh-jsonl", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--scales", nargs="+", type=float, default=[1.0, 2.0])
    ap.add_argument("--levels", nargs="+", type=int, default=[0, 1])
    ap.add_argument("--window", type=float, default=2.0)
    ap.add_argument("--fps-scan", type=float, default=6.0)
    a = ap.parse_args()

    from hyperlpr3 import LicensePlateCatcher
    cats = {lv: LicensePlateCatcher(detect_level=lv) for lv in a.levels}
    veh = [json.loads(l) for l in open(a.veh_jsonl, encoding="utf-8")]

    cap = cv2.VideoCapture(a.video)
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    print("视频 %dx%d @%.1fps  放大 %s  档位 %s"
          % (int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)),
             int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)), fps, a.scales, a.levels))

    # 车辆框按帧索引，便于按时间取
    veh_by_t = defaultdict(list)
    for v in veh:
        veh_by_t[round(v["t"], 2)].append(v)
    veh_times = sorted(veh_by_t)

    step = max(1, int(round(fps / a.fps_scan)))
    report = []
    t_all = time.time()

    for t0, truth in TRUTH:
        ntf = norm(truth)
        print("\n" + "=" * 76)
        print("真值 t=%.1fs  %s" % (t0, truth))
        hits_found = []
        allplates = []
        for fi in range(int((t0 - a.window) * fps), int((t0 + a.window) * fps), step):
            if fi < 0:
                continue
            cap.set(cv2.CAP_PROP_POS_FRAMES, fi)
            ok, f = cap.read()
            if not ok:
                continue
            t = fi / fps
            # 该帧最近的车辆框，用于"车牌属于哪辆车"的粗判
            near_t = min(veh_times, key=lambda x: abs(x - t)) if veh_times else None
            boxes = veh_by_t.get(near_t, []) if near_t is not None else []
            for sc in a.scales:
                img = f if sc == 1.0 else cv2.resize(
                    f, None, fx=sc, fy=sc, interpolation=cv2.INTER_CUBIC)
                for lv, cat in cats.items():
                    try:
                        for r in cat(img):
                            txt, conf = str(r[0]), float(r[1])
                            pb = r[3] if len(r) > 3 else None
                            if not pb:
                                continue
                            # 换算回原图坐标
                            ox1, oy1 = pb[0] / sc, pb[1] / sc
                            ox2, oy2 = pb[2] / sc, pb[3] / sc
                            pw, ph = ox2 - ox1, oy2 - oy1
                            px, py = (ox1 + ox2) / 2, (oy1 + oy2) / 2
                            # 归属：包含该车牌中心的车辆框（取最小者，即最贴合的）
                            owner = None
                            cands = [b for b in boxes
                                     if b["box"][0] <= px <= b["box"][2]
                                     and b["box"][1] <= py <= b["box"][3]]
                            if cands:
                                owner = min(cands, key=lambda b: (b["box"][2] - b["box"][0]))
                            allplates.append({"t": round(t, 2), "text": txt, "conf": round(conf, 4),
                                              "scale": sc, "level": lv,
                                              "plate_box": [round(ox1), round(oy1), round(ox2), round(oy2)],
                                              "pw": round(pw), "ph": round(ph),
                                              "owner_in_roi": (owner or {}).get("in_emergency_lane"),
                                              "owner_conf": (owner or {}).get("conf")})
                            if norm(txt) == ntf:
                                hits_found.append(allplates[-1])
                    except Exception:
                        pass

        cnt = Counter(norm(p["text"]) for p in allplates)
        print("  检出车牌 %d 条（%d 种文本）" % (len(allplates), len(cnt)))
        for txt, c in cnt.most_common(10):
            ex = [p for p in allplates if norm(p["text"]) == txt]
            mx = max(ex, key=lambda p: p["conf"])
            inroi = sum(1 for p in ex if p["owner_in_roi"])
            print("     %-12s x%-3d 最高conf=%.3f 车牌%dx%d 位置%s 属于应急车道车辆 %d 次 %s"
                  % (txt, c, mx["conf"], mx["pw"], mx["ph"], mx["plate_box"], inroi,
                     "← 真值!" if norm(txt) == ntf else ""))
        print("  命中真值: %s" % ("✅ %d 次" % len(hits_found) if hits_found else "❌ 未出现"))
        if hits_found:
            h = max(hits_found, key=lambda p: p["conf"])
            print("     最佳命中: conf=%.3f 车牌框%dx%d 位置%s 放大%.1f"
                  % (h["conf"], h["pw"], h["ph"], h["plate_box"], h["scale"]))
        report.append({"t": t0, "truth": truth, "hit": bool(hits_found),
                       "n_plates": len(allplates), "texts": dict(cnt),
                       "best_hit": max(hits_found, key=lambda p: p["conf"]) if hits_found else None})

    cap.release()
    n = sum(1 for r in report if r["hit"])
    print("\n" + "=" * 76)
    print("命中 %d/%d = %.0f%%   耗时 %.1fs" % (n, len(TRUTH), 100.0 * n / len(TRUTH),
                                               time.time() - t_all))
    for r in report:
        print("  t=%5.1fs %-10s %s" % (r["t"], r["truth"], "✅" if r["hit"] else "❌"))
    with open(a.out, "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2)
    print("-> %s" % a.out)


if __name__ == "__main__":
    main()
