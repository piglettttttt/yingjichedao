import json, sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
s=json.load(open(r"_reuse\run\summary_15fps.json",encoding="utf-8"))
print("候选 %d 条 -> 轨迹 %d 条 (CONFIRMED %d / UNCERTAIN %d / UNREADABLE %d)" % (
    s["n_candidates"], s["n_tracks"], s["n_confirmed"], s["n_uncertain"], s["n_unreadable"]))
print()
print("%-4s %-14s %-7s %-6s %-6s %-7s %-22s %s" % ("#","融合车牌","置信度","帧数","时长","状态","画面位置(首->末)","说明"))
print("-"*118)
for t in s["tracks"]:
    p=t["plate"] or {}
    pl=p.get("plate") or "(无)"
    pc="%.2f"%p["plate_conf"] if p.get("plate") else " -  "
    b0,b1=t["box_first"],t["box_last"]
    pos="x%.0f->%.0f y%.0f->%.0f"%(b0[0],b1[0],b0[1],b1[1])
    print("%-4d %-14s %-7s %-6d %-6.2f %-7s %-22s %s" % (
        t["track_no"], pl, pc, t["n_frames"], t["duration"], t["status"], pos, t["status_reason"]))
