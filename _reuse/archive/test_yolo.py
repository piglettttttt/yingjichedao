import sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from ultralytics import YOLO
print("ultralytics OK")
m = YOLO("yolo11n.pt")
print("model loaded, names:", {k: m.names[k] for k in list(m.names)[:10]})
