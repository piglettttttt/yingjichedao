import json, sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
rows=[json.loads(l) for l in open(r"_reuse\run\cands_0_30.jsonl",encoding="utf-8")]
print("=== 25.0-27.0s 全部候选（frame 750-810）===")
print("%-6s %-7s %-30s %-6s %-8s %-12s %s" % ("frame","t","box","det","size","text","sharp"))
for r in rows:
    if 25.0 <= r["t"] <= 27.0:
        print("%-6d %-7.2f [%6.0f,%6.0f,%6.0f,%6.0f] %-6.2f %4.0fx%-3.0f %-12s %.0f" % (
            r["frame"], r["t"], r["box"][0],r["box"][1],r["box"][2],r["box"][3],
            r["det_conf"], r["w"], r["h"], r["text"] or "(空)", r["sharpness"]))
print()
print("=== 同时刻每帧候选数 ===")
from collections import Counter
c=Counter(r["frame"] for r in rows if 25.0<=r["t"]<=27.0)
for f in sorted(c): print("  frame %d: %d 条" % (f,c[f]))
