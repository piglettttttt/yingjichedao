import json, sys
from collections import Counter
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
rows=[json.loads(l) for l in open(r"_reuse\run\cands_15fps.jsonl",encoding="utf-8")]
print("候选总数:", len(rows))
bottom=[r for r in rows if r["box"][1] > 950]
left  =[r for r in rows if r["box"][0] < 130 and r["box"][1] <= 950]
print("自车固定物 (y>950)   : %d 条 (%.1f%%)" % (len(bottom), 100*len(bottom)/len(rows)))
print("   文本 Top8:", [t for t,_ in Counter(r["text"] for r in bottom).most_common(8)])
print("左边缘 (x<130)       : %d 条 (%.1f%%)" % (len(left), 100*len(left)/len(rows)))
print("   文本 Top8:", [t for t,_ in Counter(r["text"] for r in left).most_common(8)])
real=[r for r in rows if r["box"][1] <= 950 and r["box"][0] >= 130]
print("其余(真路面车辆)     : %d 条 (%.1f%%)" % (len(real), 100*len(real)/len(rows)))
