import json, sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
d = json.load(open(r"_reuse\run\scan_region4.json", encoding="utf-8"))
for r in d:
    print("t=%.1f 真值=%s" % (r["t"], r["truth"]))
    print("   投票阶段用的车辆框 veh_box =", r.get("veh_box"))
    # 看该框与正前方大车牌粤A532GU的关系
    van = [c for c in r["cands"] if "532GU" in c["text"]]
    if van:
        import statistics
        pbs = [c["plate_box"] for c in van]
        xs = [ (p[0]+p[2])/2 for p in pbs ]; ys=[(p[1]+p[3])/2 for p in pbs]
        print("   粤A532GU 候选 %d 条, 中心范围 x %.0f..%.0f  y %.0f..%.0f, 车牌宽 %.0f..%.0f"
              % (len(van), min(xs), max(xs), min(ys), max(ys),
                 min(abs(p[2]-p[0]) for p in pbs), max(abs(p[2]-p[0]) for p in pbs)))
    vb = r.get("veh_box")
    if vb:
        print("   车辆框 x %.0f..%.0f y %.0f..%.0f  (宽%.0f 高%.0f)"
              % (vb[0], vb[2], vb[1], vb[3], vb[2]-vb[0], vb[3]-vb[1]))
    print()
