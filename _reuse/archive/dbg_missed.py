import json, sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
rows=[json.loads(l) for l in open(r"_reuse\run\cands_15fps.jsonl",encoding="utf-8")]
targets = ["A8R902","8R902","A7M7M4","7M7M4","AAG4884","AG4884","A130V9","SVZ600"]
print("=== 在全量候选(1071条)里搜用户记录的5辆车牌 ===")
for tg in targets:
    hits=[r for r in rows if tg in r["text"].replace("粤","")]
    print("  %-8s 命中 %d 条 %s" % (tg, len(hits), [(r["frame"],r["text"]) for r in hits[:4]]))
print()
print("=== t=11.0s 与 t=23.0s / t=25.0s 附近所有候选 ===")
for t in (11.0, 23.0, 25.0):
    near=[r for r in rows if abs(r["t"]-t)<=0.35]
    print("--- t=%.1f (±0.35s) 共 %d 条 ---" % (t, len(near)))
    for r in sorted(near, key=lambda x:-x["det_conf"])[:12]:
        print("   f%-5d t=%.2f box=[%6.0f,%6.0f,%6.0f,%6.0f] %-12s det=%.2f" % (
            r["frame"], r["t"], r["box"][0],r["box"][1],r["box"][2],r["box"][3], r["text"], r["det_conf"]))
