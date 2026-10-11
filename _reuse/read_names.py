"""读取 plate_detect.pt 的类别名等元数据（零 torch 依赖）。"""
import io
import os
import sys
import pickle
import zipfile

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
T = "__tensor__"


def make_unpickler(raw):
    def rb(storage, off, size, stride, rg, bh, *e, **k):
        return {T: True, "shape": tuple(size)}

    class Stub:
        def __init__(self, *a, **k):
            pass
        def __setstate__(self, st):
            self._state = st

    class U(pickle.Unpickler):
        def find_class(self, m, n):
            if n in ("_rebuild_tensor_v2", "_rebuild_tensor"):
                return rb
            if m == "collections" and n == "OrderedDict":
                return dict
            return Stub

        def persistent_load(self, pid):
            return {"__storage__": True}

    return U(io.BytesIO(raw))


def main():
    base = os.path.join(os.path.dirname(os.path.abspath(__file__)), "clpr",
                        "Chinese_license_plate_detection_recognition-main", "weights")
    for fn in ("plate_detect.pt",):
        path = os.path.join(base, fn)
        with zipfile.ZipFile(path) as z:
            pk = [n for n in z.namelist() if n.endswith("data.pkl")][0]
            raw = z.read(pk)
        obj = make_unpickler(raw).load()
        st = obj["model"]._state
        print("=== %s ===" % fn)
        for k in ("nc", "names", "yaml", "stride", "class_weights", "save"):
            v = st.get(k)
            if isinstance(v, str) and len(v) > 600:
                v = v[:600] + " ...(截断)"
            print("%-16s = %r" % (k, v))


if __name__ == "__main__":
    main()
