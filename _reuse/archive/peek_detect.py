"""零依赖解析 plate_detect.pt：定位 YOLOv5 检测头，反推类别数 nc。

torch.save 的 zip 中 archive/data.pkl 为 pickle 流，torch 张量经
_rebuild_tensor_v2(storage, offset, size, stride, ...) 重建，size 即形状。
本脚本不 import torch，只用自定义 Unpickler 拦截 torch 的类。
"""
import io
import os
import sys
import pickle
import zipfile

sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def make_unpickler(raw, log=None):
    def rebuild_tensor_v2(storage, storage_offset, size, stride, requires_grad,
                          backward_hooks, *extra, **kw):
        return {"__tensor__": True, "shape": tuple(size)}

    def rebuild_tensor(storage, storage_offset, size, stride, *a, **kw):
        return {"__tensor__": True, "shape": tuple(size)}

    class Stub:
        def __init__(self, *a, **k):
            self._a = a
            self._k = k
            if log is not None:
                log.append((type(self).__name__, a[:1]))
        def __repr__(self):
            return "<%s>" % type(self).__name__
        def __setstate__(self, st):
            self._state = st
        def __reduce__(self):
            return (Stub, ())

    class U(pickle.Unpickler):
        def find_class(self, module, name):
            if name == "_rebuild_tensor_v2":
                return rebuild_tensor_v2
            if name == "_rebuild_tensor":
                return rebuild_tensor
            if module == "collections" and name == "OrderedDict":
                return dict
            return Stub

        def persistent_load(self, pid):
            return {"__storage__": True}

    return U(io.BytesIO(raw))


def shape_of(v):
    if isinstance(v, dict) and v.get("__tensor__"):
        return v["shape"]
    if isinstance(v, (list, tuple)):
        return "seq(len=%d)" % len(v)
    return type(v).__name__


def dump_state_dict(sd, title):
    print("  --- %s：含小维度(潜在分类头)的层 ---" % title)
    keys = list(sd.keys()) if isinstance(sd, dict) else []
    for k in keys:
        shp = shape_of(sd[k])
        if isinstance(shp, tuple) and len(shp) in (2, 4) and 2 <= (shp[0] if shp else 0) <= 200:
            print("    %-56s %s" % (k, shp))
    print("  --- 全部 key（共 %d，截前 24）---" % len(keys))
    for k in keys[:24]:
        print("    %-56s %s" % (k, shape_of(sd[k])))


def main():
    base = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "clpr", "Chinese_license_plate_detection_recognition-main", "weights")
    path = os.path.join(base, "plate_detect.pt")
    print("=" * 74)
    print("文件:", path, "%.2f MB" % (os.path.getsize(path) / 1024 / 1024))

    with zipfile.ZipFile(path) as z:
        pk = [n for n in z.namelist() if n.endswith("data.pkl")][0]
        raw = z.read(pk)

    log = []
    obj = make_unpickler(raw, log).load()

    print("  顶层类型:", type(obj).__name__)
    if isinstance(obj, dict):
        print("  顶层键:", list(obj.keys())[:20])
        for k, v in obj.items():
            print("    %-20s -> %s" % (k, shape_of(v) if not isinstance(v, dict) or v.get("__tensor__") else type(v).__name__))
        for cand in ("state_dict", "model", "ema"):
            v = obj.get(cand)
            if isinstance(v, dict) and any(isinstance(x, dict) and x.get("__tensor__") for x in v.values()):
                print("  [命中] 用 %r 作为权重字典" % cand)
                dump_state_dict(v, cand)
                break
        sd = obj.get("state_dict")
        if isinstance(sd, dict):
            dump_state_dict(sd, "state_dict")
    else:
        print("  非 dict，尝试从其属性取 state_dict")
        st = getattr(obj, "_state", None)
        if st:
            print("  __setstate__ 键:", list(st.keys())[:20] if isinstance(st, dict) else type(st).__name__)

    # 兜底：直接从 pickle 流里抓形如 (nc+5)*3 的检测头形状
    print("  --- 原始 pickle 中的候选形状（反推 nc）---")
    out = io.StringIO()
    try:
        pickletools.dis(raw, out)
    except Exception as e:
        out.write("dis error: %r\n" % e)
    lines = out.getvalue().splitlines()
    hits = [l for l in lines if "_rebuild_tensor" in l]
    print("    _rebuild_tensor 出现次数:", len(hits))
    # 找 STOP 前的 int 元组模式：形如 (255,) / (39,) 之类的通道数
    import re
    cands = sorted({int(m) for l in lines for m in re.findall(r"^\s*\d+:\s+\((\d+),?\)", l)})
    print("    单元素元组候选值:", [c for c in cands if 6 <= c <= 512][:40])


if __name__ == "__main__":
    main()
