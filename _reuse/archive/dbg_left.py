import json, sys, os
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import cv2, numpy as np
rows=[json.loads(l) for l in open(r"_reuse\run\cands_15fps.jsonl",encoding="utf-8")]
# 取左边缘那条（x<130）看清它到底是什么
sel=[r for r in rows if r["box"][0] < 130]
print("左边缘候选数:", len(sel))
from collections import Counter
print("文本分布 Top12:")
for t,c in Counter(r["text"] for r in sel).most_common(12):
    print("   %-12s x%d" % (t or "(空)", c))
print("尺寸中位: %.0fx%.0f" % (np.median([r["w"] for r in sel]), np.median([r["h"] for r in sel])))
print("y 范围: %.0f - %.0f" % (min(r["box"][1] for r in sel), max(r["box"][3] for r in sel)))
