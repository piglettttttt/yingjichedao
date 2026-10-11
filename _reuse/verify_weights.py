"""一次性验证脚本：解析权重结构 → 导出 ONNX → 用真实视频帧实测类别与精度。

目的：回答"plate_detect.pt 到底检测车牌还是车牌+车辆"，决定架构分叉。
这不是交付代码，只用于验证；结论写入 ai_task 文档后再决定正式实现。
"""
import os
import sys
import json
import argparse

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.join(HERE, "clpr", "Chinese_license_plate_detection_recognition-main")
sys.path.insert(0, REPO)

WEIGHTS = os.path.join(REPO, "weights")
VIDEO = os.path.join(os.path.dirname(HERE), "downloads",
                     "【广州开车日常】北环东圃特大桥应急车道行车30S连过5台车 [BV1X84y1K74W].mp4")


def step1_inspect():
    """只读 checkpoint 元数据，不加载模型。"""
    import torch
    print("torch", torch.__version__)
    for name in ("plate_detect.pt", "plate_rec_color.pth"):
        p = os.path.join(WEIGHTS, name)
        print("=" * 72)
        print(name, "->", p)
        ckpt = torch.load(p, map_location="cpu", weights_only=False)
        print("  type:", type(ckpt).__name__)
        if isinstance(ckpt, dict):
            print("  top-level keys:", list(ckpt.keys())[:20])
            sd = ckpt.get("state_dict", ckpt)
            if isinstance(sd, dict):
                keys = list(sd.keys())
                print("  n_params:", len(keys))
                print("  cfg:", ckpt.get("cfg"))
                # 找分类头：形状含类别数的层
                for k in keys:
                    v = sd[k]
                    if hasattr(v, "shape") and v.ndim in (2, 4) and 3 <= v.shape[0] <= 128:
                        if k.endswith(("weight", "bias")) or "Cnn" in k or "classifier" in k or "color" in k:
                            print("    %-52s %s" % (k, tuple(v.shape)))
                print("  --- 全部 key（截前 40）---")
                for k in keys[:40]:
                    v = sd[k]
                    print("    %-52s %s" % (k, tuple(v.shape) if hasattr(v, "shape") else type(v).__name__))
        del ckpt


def build_detector():
    """按 YOLOv5 结构重建检测模型（cfg 从 checkpoint 或 yaml 读）。"""
    import torch
    from models.yolo import Model
    p = os.path.join(WEIGHTS, "plate_detect.pt")
    ckpt = torch.load(p, map_location="cpu", weights_only=False)
    cfg = ckpt.get("cfg")
    sd = ckpt.get("model", ckpt.get("state_dict", ckpt))
    if hasattr(sd, "state_dict"):
        model = sd
        print("[detect] checkpoint 直接含模型对象，nc =", model.model[-1].nc)
    else:
        if cfg is None:
            cfg = os.path.join(REPO, "models", "yolov5s.yaml")
            print("[detect] 无 cfg，回退 yolov5s.yaml")
        model = Model(cfg)
        missing, unexpected = model.load_state_dict(sd, strict=False)
        print("[detect] missing:", len(missing), "unexpected:", len(unexpected))
        print("[detect] nc =", model.model[-1].nc)
    model.eval()
    return model


def step2_export():
    import torch
    os.makedirs(os.path.join(HERE, "onnx"), exist_ok=True)
    dummy = torch.zeros(1, 3, 640, 640)

    # ---- 检测模型 ----
    det = build_detector()
    det_out = os.path.join(HERE, "onnx", "plate_detect.onnx")
    torch.onnx.export(det, dummy, det_out, opset_version=12,
                      input_names=["images"], output_names=["output"],
                      dynamic_axes={"images": {0: "b"}, "output": {0: "b"}})
    print("[detect] exported ->", det_out)

    # ---- 识别模型 ----
    from plate_recognition.plateNet import myNet_ocr_color, myNet_ocr
    p = os.path.join(WEIGHTS, "plate_rec_color.pth")
    ckpt = torch.load(p, map_location="cpu", weights_only=False)
    sd = ckpt["state_dict"]
    cfg = ckpt["cfg"]
    print("[rec] cfg =", cfg, " has_color =", any("color" in k for k in sd))
    model = myNet_ocr_color(cfg=cfg, num_classes=78, export=True, color_num=5)
    model.load_state_dict(sd, strict=False)
    model.eval()
    dummy2 = torch.zeros(1, 3, 48, 168)
    rec_out = os.path.join(HERE, "onnx", "plate_rec_color.onnx")
    torch.onnx.export(model, dummy2, rec_out, opset_version=12,
                      input_names=["input"], output_names=["plate", "color"])
    print("[rec] exported ->", rec_out)


def prep_frame(img, size=(640, 640)):
    """YOLOv5 风格 letterbox 前处理，与 onnx_infer.py 保持一致。"""
    import cv2
    h, w, c = img.shape
    r = min(size[0] / h, size[1] / w)
    new_h, new_w = int(h * r), int(w * r)
    top = int((size[0] - new_h) / 2)
    left = int((size[1] - new_w) / 2)
    bottom, right = size[0] - new_h - top, size[1] - new_w - left
    resized = cv2.resize(img, (new_w, new_h))
    padded = cv2.copyMakeBorder(resized, top, bottom, left, right,
                               borderType=cv2.BORDER_CONSTANT, value=(114, 114, 114))
    blob = padded[:, :, ::-1].transpose(2, 0, 1).copy().astype(np.float32) / 255.0
    return blob.reshape(1, *blob.shape), r, left, top


def step3_measure(frames=20):
    """用真实视频帧统计：各类别检出数量。这是判断'检不检车辆'的直接证据。"""
    import cv2
    import onnxruntime as ort

    names = ["single_plate", "double_plate", "car"]
    sess = ort.InferenceSession(os.path.join(HERE, "onnx", "plate_detect.onnx"),
                                providers=["CPUExecutionProvider"])
    print("[measure] input :", sess.get_inputs()[0].shape, sess.get_inputs()[0].name)
    print("[measure] output:", sess.get_outputs()[0].shape, sess.get_outputs()[0].name)

    cap = cv2.VideoCapture(VIDEO)
    if not cap.isOpened():
        print("!! 无法打开视频:", VIDEO)
        return
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    step = max(1, total // frames)

    tally = {n: 0 for n in names}
    unknown = 0
    conf_list = []
    idx = 0
    used = 0
    while used < frames:
        ok, frame = cap.read()
        if not ok:
            break
        if idx % step:
            idx += 1
            continue
        idx += 1
        blob, r, left, top = prep_frame(frame)
        out = sess.run(None, {sess.get_inputs()[0].name: blob})[0]
        # out: [1, N, 5+nc(+landmarks)]
        pred = out[0]
        conf = pred[:, 4]
        keep = conf > 0.3
        n_nc = pred.shape[1] - 5
        for row in pred[keep]:
            score = row[4]
            cls_logits = row[5:5 + n_nc]
            ci = int(np.argmax(cls_logits))
            nm = names[ci] if ci < len(names) else "idx%d" % ci
            if nm in tally:
                tally[nm] += 1
            else:
                unknown += 1
            conf_list.append(float(score))
        used += 1
    cap.release()

    print("[measure] 采样帧数:", used)
    print("[measure] 输出维度解析: 5 + nc, nc =", pred.shape[1] - 5)
    print("[measure] 各类别检出累计:")
    for k, v in tally.items():
        print("           %-14s %d" % (k, v))
    if unknown:
        print("           %-14s %d" % ("<索引越界>", unknown))
    if conf_list:
        a = np.array(conf_list)
        print("[measure] 置信度: n=%d min=%.3f mean=%.3f max=%.3f" % (len(a), a.min(), a.mean(), a.max()))
    print("[measure] 判定:", "含 car 类 → 可同时做车辆检测" if tally.get("car", 0) > 0
          else "未见 car 检出 → 很可能只检车牌")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage", default="all",
                    choices=["inspect", "export", "measure", "all"])
    ap.add_argument("--frames", type=int, default=20)
    a = ap.parse_args()
    if a.stage in ("inspect", "all"):
        step1_inspect()
    if a.stage in ("export", "all"):
        step2_export()
    if a.stage in ("measure", "all"):
        step3_measure(a.frames)
