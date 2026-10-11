import sys, cv2
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from hyperlpr3 import LicensePlateCatcher
vid = r"downloads\【广州开车日常】北环东圃特大桥应急车道行车30S连过5台车 [BV1X84y1K74W]_前30s.mp4"
cap = cv2.VideoCapture(vid); fps = cap.get(cv2.CAP_PROP_FPS)
cap.set(cv2.CAP_PROP_POS_FRAMES, int(11.0*fps)); ok, f = cap.read(); cap.release()
c = LicensePlateCatcher(detect_level=1)
res = c(f)
print("类型:", type(res), "数量:", len(res))
for i, r in enumerate(res):
    print("--- [%d] type=%s ---" % (i, type(r)))
    print("  repr:", repr(r)[:600])
    if hasattr(r, "__dict__"):
        print("  __dict__:", {k: (str(v)[:80]) for k,v in r.__dict__.items()})
    if isinstance(r, (list, tuple)):
        print("  元素:", [(type(x).__name__, str(x)[:60]) for x in r])
