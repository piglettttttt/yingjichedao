import json, sys
from collections import Counter
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
rows=[json.loads(l) for l in open(r"_reuse\run\cands_15fps.jsonl",encoding="utf-8")]
print("=== 含 130V9 / A130 的候选 ===")
for r in rows:
    if "130" in r["text"] or "V9" in r["text"]:
        print("  f%-5d t=%6.2f box=[%6.0f,%6.0f,%6.0f,%6.0f] %-12s det=%.2f" % (
            r["frame"], r["t"], r["box"][0],r["box"][1],r["box"][2],r["box"][3], r["text"], r["det_conf"]))
print()
print("=== 画面右侧区域 (x2>1400) 的候选，看有哪些车在右边 ===")
right=[r for r in rows if r["box"][2]>1400]
print("数量:", len(right))
for t,c in Counter(r["text"] for r in right).most_common(15):
    print("   %-14s x%d" % (t or "(空)", c))
