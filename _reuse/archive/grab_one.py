import cv2, sys, numpy as np
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
vid = sys.argv[1]
cap = cv2.VideoCapture(vid)
fps = cap.get(cv2.CAP_PROP_FPS)
for t in (3.0,):
    cap.set(cv2.CAP_PROP_POS_FRAMES, int(t*fps))
    ok, fr = cap.read()
    print("t=%.1f ok=%s shape=%s" % (t, ok, fr.shape if ok else None))
    cv2.imwrite(r"_reuse\run\ref_t%04.1f.jpg" % t, fr, [cv2.IMWRITE_JPEG_QUALITY, 92])
cap.release()
