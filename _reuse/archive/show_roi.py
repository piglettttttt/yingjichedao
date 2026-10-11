import cv2, sys, numpy as np, yaml
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
cfg = yaml.safe_load(open(r"_reuse\config_roi.yaml", encoding="utf-8"))
poly = np.array(cfg["emergency_lane"]["polygon"], dtype=np.int32)
cap = cv2.VideoCapture(sys.argv[1]); fps = cap.get(cv2.CAP_PROP_FPS)
tiles=[]
for t in (0.0, 3.0, 10.0, 20.0, 28.0):
    cap.set(cv2.CAP_PROP_POS_FRAMES, int(t*fps)); ok, fr = cap.read()
    if not ok: continue
    v = fr.copy()
    ov = v.copy(); cv2.fillPoly(ov, [poly], (0,140,255))
    v = cv2.addWeighted(ov, 0.30, v, 0.70, 0)
    cv2.polylines(v, [poly], True, (0,0,255), 3)
    cv2.putText(v, "t=%.0fs" % t, (30,80), cv2.FONT_HERSHEY_SIMPLEX, 2.0, (0,0,255), 4)
    tiles.append(cv2.resize(v, (640,360)))
cap.release()
rows=[np.hstack(tiles[i:i+3]) for i in range(0,len(tiles),3)]
if len(rows)>1:
    w=max(r.shape[1] for r in rows)
    rows=[np.pad(r,((0,0),(0,w-r.shape[1]),(0,0))) for r in rows]
    grid=np.vstack(rows)
else: grid=rows[0]
cv2.imwrite(r"_reuse\run\roi_overlay_grid.jpg", grid, [cv2.IMWRITE_JPEG_QUALITY, 90])
print("ok", grid.shape)
