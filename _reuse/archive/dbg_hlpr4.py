import sys, cv2, time
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from hyperlpr3 import LicensePlateCatcher
vid = r"downloads\【广州开车日常】北环东圃特大桥应急车道行车30S连过5台车 [BV1X84y1K74W]_前30s.mp4"
cap = cv2.VideoCapture(vid); fps = cap.get(cv2.CAP_PROP_FPS)

def grab(t):
    cap.set(cv2.CAP_PROP_POS_FRAMES, int(t*fps)); ok,f = cap.read(); return f if ok else None

for lvl, name in ((0,"320x"), (1,"640x")):
    c = LicensePlateCatcher(detect_level=lvl)
    print("=== detect_level=%d (%s) ===" % (lvl, name))
    for t in (2.0, 11.0, 14.0, 23.0, 25.0):
        f = grab(t)
        if f is None: continue
        t0=time.time(); res = c(f); el=time.time()-t0
        out=[]
        for r in res:
            code = getattr(r,"code",None)
            conf = getattr(r,"confidence",None)
            out.append((code, round(float(conf),3) if conf is not None else None,
                        getattr(r,"type",None), getattr(r,"box",None)))
        print("  t=%5.1f 整帧: %d 个车牌  用时 %.2fs  %s" % (t, len(res), el, out[:4]))
cap.release()
