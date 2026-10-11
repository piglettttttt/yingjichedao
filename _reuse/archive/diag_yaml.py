"""诊断：检查 checkpoint 里 yaml 的结构，找出重建 Model 失败的原因。"""
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.join(HERE, "clpr", "Chinese_license_plate_detection_recognition-main")

sys.path.insert(0, HERE)
from export_onnx import install_stubs  # noqa: E402

install_stubs(REPO)
import torch  # noqa: E402

ckpt = torch.load(os.path.join(REPO, "weights", "plate_detect.pt"),
                  map_location="cpu", weights_only=False)
saved = ckpt["model"]
y = saved.yaml
print("=== saved.yaml 顶层键 ===")
print(list(y.keys()))
print("nc=%s width_multiple=%s depth_multiple=%s" % (
    y.get("nc"), y.get("width_multiple"), y.get("depth_multiple")))
print("\n=== backbone ===")
for i, it in enumerate(y["backbone"]):
    print(" ", i, it)
print("\n=== head ===")
for i, it in enumerate(y["head"]):
    print(" ", i, it)

last = y["head"][-1]
print("\n=== head 最后一项 ===")
print("type:", type(last), "len:", len(last))
for j, x in enumerate(last):
    print("  [%d] %r (%s)" % (j, x, type(x).__name__))
if len(last) >= 3:
    print("  args 部分:", last[-2], type(last[-2]))

print("\n=== 复现 parse_model 的宽度计算（只走到 StemBlock）===")
from models.yolo import parse_model
import copy
try:
    parse_model(copy.deepcopy(y), ch=[3])
except Exception as e:
    import traceback
    traceback.print_exc()

print("\n=== saved model 信息 ===")
print("len(saved.model) =", len(saved.model))
head = saved.model[-1]
print("Detect nc=%s na=%s no=%s nl=%s" % (head.nc, head.na, head.no, head.nl))
print("Detect.m out_channels:", [m.out_channels for m in head.m])
print("\n=== saved.model 各层 ===")
for i, m in enumerate(saved.model):
    print("  %2d %-16s f=%s" % (i, type(m).__name__, getattr(m, "f", "")))
