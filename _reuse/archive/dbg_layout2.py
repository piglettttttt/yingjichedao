import cv2, numpy as np, sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
img = cv2.imread(r"downloads\image_43.png")
H,W = img.shape[:2]
g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY).astype(np.float32)
rowmean = g.mean(axis=1)
colmean = g.mean(axis=0)
print("截图 %dx%d" % (W,H))
print("\n=== 行均值首尾（找黑边）===")
for y in list(range(0,140,10)) + list(range(1400,1520,10)) + list(range(1700,H,20)):
    print("  y=%4d mean=%6.1f" % (y, rowmean[y]))
print("\n=== 列均值首尾 ===")
for x in list(range(0,120,10)) + list(range(W-120,W,10)):
    print("  x=%4d mean=%6.1f" % (x, colmean[x]))
# 用"非黑"判定
nz_rows = np.nonzero(rowmean > 12)[0]
nz_cols = np.nonzero(colmean > 12)[0]
print("\n非黑行范围: %d .. %d  (高=%d)" % (nz_rows.min(), nz_rows.max(), nz_rows.max()-nz_rows.min()+1))
print("非黑列范围: %d .. %d  (宽=%d)" % (nz_cols.min(), nz_cols.max(), nz_cols.max()-nz_cols.min()+1))
print("该区域宽高比 = %.4f   视频为 %.4f" % (
    (nz_cols.max()-nz_cols.min()+1)/(nz_rows.max()-nz_rows.min()+1), 1920/1080))
print("\n若按 16:9 且高度=%d 推算宽度=%d (图宽 %d)" % (
    nz_rows.max()-nz_rows.min()+1, int((nz_rows.max()-nz_rows.min()+1)*16/9), W))
print("若按 16:9 且宽度=%d 推算高度=%d (图高 %d)" % (
    nz_cols.max()-nz_cols.min()+1, int((nz_cols.max()-nz_cols.min()+1)*9/16), H))
