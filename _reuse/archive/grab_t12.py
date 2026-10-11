import cv2, sys
cap=cv2.VideoCapture(sys.argv[1]); fps=cap.get(cv2.CAP_PROP_FPS)
for t in (12.0,):
    cap.set(cv2.CAP_PROP_POS_FRAMES,int(t*fps)); ok,fr=cap.read()
    cv2.imwrite(r"_reuse\run\f12.jpg" % (), fr, [cv2.IMWRITE_JPEG_QUALITY,95])
    print("saved t",t, ok)
cap.release()
