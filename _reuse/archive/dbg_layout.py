import cv2, numpy as np, sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
img = cv2.imread(r"downloads\image_43.png")
H,W = img.shape[:2]
print("截图 %dx%d  宽高比=%.4f" % (W,H,W/H))
g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
print("\n=== 行方向：亮度/标准差（每 40 行采样）===")
for y in range(0,H,40):
    row = g[y]
    print("  y=%4d mean=%6.1f std=%6.1f  colmean[0:20]=%5.1f colmean[-20:]=%5.1f" % (
        y, row.mean(), row.std(), row[:20].mean(), row[-20:].mean()))
print("\n=== 列方向：亮度/标准差（每 60 列采样）===")
for x in range(0,W,60):
    col = g[:,x]
    print("  x=%4d mean=%6.1f std=%6.1f" % (x, col.mean(), col.std()))
