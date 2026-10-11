"""零依赖解析 torch 权重：不装 torch，直接读 zip + pickle 拿到全部张量形状。

目的：从检测头输出通道数反推类别数 nc，判定 plate_detect.pt 是否含 car 类。
torch.save 的 zip 里 archive/data.pkl 是 pickle，张量经
torch._utils._rebuild_tensor_v2(storage, offset, size, stride, ...) 重建，
size 即形状；真实数值在 archive/data/<key> 里。
只读元数据，不还原数值。
"""
import io
import os
import pickle
import pickletools
import zipfile
from collections import OrderedDict


def load_zip_pickle(path):
    """取出 archive/data.pkl 并反序列化（需要 torch 的 unpickler 才能完整还原，
    这里用自定义 Unpickler 把 _rebuild_tensor_v2 替换成返回形状的轻量对象）。"""
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
        pk = [n for n in names if n.endswith("data.pkl")]
        if not pk:
            raise RuntimeError("no data.pkl in %s" % path)
        raw = z.read(pk[0])

    # 自定义 unpickler：拦截 torch 的重建函数
    class TorchStub:
        def __init__(self, *a, **k):
            self._a = a
        def __repr__(self):
            return "<stub>"

    def rebuild_tensor_v2(storage, storage_offset, size, stride, requires_grad,
                          backward_hooks, *extra, **kw):
        return ("TENSOR", tuple(size), str(storage))

    def rebuild_tensor(storage, storage_offset, size, stride, *a, **kw):
        return ("TENSOR", tuple(size), str(storage))

    def persistent_load(pid):
        # ('storage', dtype, key, location, numel)
        try:
            return "storage:%s" % (pid[2],)
        except Exception:
            return "storage:?"

    class U(pickle.Unpickler):
        def find_class(self, module, name):
            if name in ("_rebuild_tensor_v2", "_rebuild_tensor"):
                return rebuild_tensor_v2 if name == "_rebuild_tensor_v2" else rebuild_tensor
            if name == "OrderedDict":
                return OrderedDict
            # 其它 torch 类一律给个宽柔的桩，避免 import torch
            return TorchStub

        def persistent_load(self, pid):
            return persistent_load(pid)

    return U(io.BytesIO(raw)).load(), names


def summarize(path, label):
    print("=" * 74)
    print("%s  (%s, %.2f MB)" % (label, os.path.basename(path),
                                 os.path.getsize(path) / 1024 / 1024))
    obj, names = load_zip_pickle(path)
    print("  zip entries:", len(names))

    if not isinstance(obj, dict):
        print("  top-level type:", type(obj).__name__)
        return obj

    print("  top-level keys:", list(obj.keys())[:15])
    sd = obj.get("state_dict")
    if not isinstance(sd, dict):
        sd = obj.get("model")
    if hasattr(sd, "__dict__") and not isinstance(sd, dict):
        sd = getattr(sd, "state_dict", None) or getattr(sd, "_modules", None)
    if not isinstance(sd, dict):
        print("  未找到 state_dict；上层键值类型：",
              {k: type(v).__name__ for k, v in list(obj.items())[:12]})
        return obj

    keys = list(sd.keys())
    print("  n_state_entries:", len(keys))
    print("  cfg:", obj.get("cfg"))
    print("  --- 形状含小维度(潜在分类头/颜色头)的层 ---")
    for k in keys:
        v = sd[k]
        if isinstance(v, tuple) and v and v[0] == "TENSOR":
            shp = v[1]
            if len(shp) in (2, 4) and 2 <= (shp[0] if shp else 0) <= 200:
                if k.endswith(".weight") or "Cnn" in k or "classifier" in k or "color" in k:
                    print("    %-56s %s" % (k, shp))
    print("  --- 全部 key 及形状（截前 30）---")
    for k in keys[:30]:
        v = sd[k]
        shp = v[1] if isinstance(v, tuple) and v and v[0] == "TENSOR" else type(v).__name__
        print("    %-56s %s" % (k, shp))
    return obj


if __name__ == "__main__":
    base = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "clpr", "Chinese_license_plate_detection_recognition-main", "weights")
    for fn, lab in (("plate_detect.pt", "车牌检测器 YOLOv5"),
                    ("plate_rec_color.pth", "车牌识别 PlateNet")):
        try:
            summarize(os.path.join(base, fn), lab)
        except Exception as e:
            print("!! %s 解析失败: %r" % (fn, e))
