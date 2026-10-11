"""递归解析 plate_detect.pt（YOLOv5 训练 checkpoint）里的 model 对象，
找到检测头输出层，从输出通道数反推类别数 nc：
  YOLOv5 with landmarks:  out_ch = na * (5 + nc + 8)   (na=3 anchors, 8 = 4 关键点 x,y)
  YOLOv5 标准:            out_ch = na * (5 + nc)
"""
import io
import os
import re
import sys
import pickle
import pickletools
import zipfile

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

TENSOR = "__tensor__"


def make_unpickler(raw):
    def _mk(size):
        return {TENSOR: True, "shape": tuple(size)}

    def rebuild_tensor_v2(storage, storage_offset, size, stride, requires_grad,
                          backward_hooks, *extra, **kw):
        return _mk(size)

    def rebuild_tensor(storage, storage_offset, size, stride, *a, **kw):
        return _mk(size)

    class Stub:
        """占位对象；__setstate__ 收到的 dict 挂在 _state 上供递归。"""
        def __init__(self, *a, **k):
            self._a = a
        def __setstate__(self, st):
            self._state = st
        def __repr__(self):
            return "<%s>" % type(self).__name__

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


def walk(node, path="", depth=0, out=None):
    """递归收集所有张量形状及其路径。"""
    if out is None:
        out = []
    if depth > 12:
        return out
    if isinstance(node, dict):
        if node.get(TENSOR):
            out.append((path, node["shape"]))
            return out
        for k, v in node.items():
            walk(v, "%s.%s" % (path, k) if path else str(k), depth + 1, out)
    elif isinstance(node, (list, tuple)):
        for i, v in enumerate(node):
            walk(v, "%s[%d]" % (path, i), depth + 1, out)
    else:
        st = getattr(node, "_state", None)
        if isinstance(st, dict):
            for k, v in st.items():
                walk(v, "%s.%s" % (path, k) if path else str(k), depth + 1, out)
    return out


def main():
    base = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "clpr", "Chinese_license_plate_detection_recognition-main", "weights")
    path = os.path.join(base, "plate_detect.pt")
    with zipfile.ZipFile(path) as z:
        pk = [n for n in z.namelist() if n.endswith("data.pkl")][0]
        raw = z.read(pk)

    obj = make_unpickler(raw).load()
    print("顶层键:", list(obj.keys()))
    print("epoch:", obj.get("epoch"))

    model = obj.get("model")
    st = getattr(model, "_state", None)
    print("model._state 键:", list(st.keys()) if isinstance(st, dict) else type(st).__name__)

    shapes = walk(model, "model")
    print("递归收集到张量:", len(shapes))
    if not shapes:
        # 退化：直接在整棵树里抓
        shapes = walk(obj, "root")
        print("退化为整树收集:", len(shapes))

    # 打印最后 30 个（检测头在最后）
    print("--- 末尾 30 个张量 ---")
    for p, s in shapes[-30:]:
        print("  %-58s %s" % (p, s))

    # 找输出层：长度 1 的最后一维、通道数较大者
    print("--- 候选检测头输出层（末维=1 的卷积权重）---")
    for p, s in shapes:
        if len(s) == 4 and s[-1] == 1 and s[0] > 6:
            print("  %-58s %s" % (p, s))
            ch = s[0]
            for na in (3,):
                for lm in (0, 8):
                    if (ch / na - 5 - lm) > 0 and abs(ch / na - 5 - lm - round(ch / na - 5 - lm)) < 1e-9:
                        print("      => na=%d, landmarks=%d 时 nc = %d" % (na, lm, round(ch / na - 5 - lm)))

    # 兜底：从原始 pickle 文本里找 cv2/cv3 相关键名
    txt = io.StringIO()
    pickletools.dis(raw, txt)
    body = txt.getvalue()
    for kw in ("cv2", "cv3", "m.", "classifier", "color_classifier", "newCnn", "nc"):
        n = body.count(kw)
        if n:
            print("  pickle 流中 %r 出现 %d 次" % (kw, n))


if __name__ == "__main__":
    main()
