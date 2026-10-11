import sys, cv2, json
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from hyperlpr3 import LicensePlateCatcher
cap = cv2.VideoCapture(r"downloads\【广州开车日常】北环东圃特大桥应急车道行车30S连过5台车 [BV1X84y1K74W]_前30s.mp4")
fps = cap.get(cv2.CAP_PROP_FPS)
cap.set(cv2.CAP_PROP_POS_FRAMES, int(2.0*fps)); ok, fr = cap.read()
cap.release()
print("frame:", ok, fr.shape)
catcher = LicensePlateCatcher()
# 用用户记录的第一辆车附近的裁剪
x1,y1,x2,y2 = 1214,600,1516,787
crop = fr[max(0,y1-30):min(fr.shape[0],y2+40), max(0,x1-40):min(fr.shape[1],x2+40)]
print("crop:", crop.shape)
res = catcher(crop)
print("type:", type(res), "len:", len(res) if hasattr(res,'__len__') else '?')
for i, r in enumerate(res):
    print("  [%d] type=%s" % (i, type(r)))
    print("      repr:", repr(r)[:400])
    for attr in ("code","confidence","type","box","points","detect_conf","rec_conf"):
        if hasattr(r, attr):
            print("      .%s = %r" % (attr, getattr(r, attr)))
