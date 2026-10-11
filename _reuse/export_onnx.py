"""把 plate_detect.pt / plate_rec_color.pth 导出为 ONNX。

这是"一次性"步骤：导出后推理只用 onnxruntime，不再需要 torch。

工程要点（踩坑记录）：
1. 仓库的 models/common.py 顶部链式 import 了大量训练期依赖
   （requests/PIL/cv2/tqdm/torchvision/matplotlib...），而导出只需要其中的
   **网络层定义**。因此这里先把 utils.* 换成桩模块，避免为一个纯推理需求
   安装整套 YOLOv5 训练栈。
2. plate_detect 的 Detect 头有 export_cat 开关，仓库里默认 False；
   onnx_infer.py 期望拼接后的 [1, 25200, 15]，导出前必须置 True。
3. checkpoint 的 yaml 里嵌了完整网络结构，但不含 Detect 头 —— 重建时必须补上
   head 最后一项，否则 load_state_dict 会缺少 model.21.* 全部权重。
"""
import argparse
import os
import sys
import types


def install_stubs(repo):
    """把仓库根加入 sys.path。

    踩坑记录：**不要**用桩模块替换依赖。
    torch 2.14 的 ONNX 导出走 torch.export，它会遍历 sys.modules 检查 __spec__，
    任何被桩替换过的模块都会失败（"pandas.__spec__ is None" / "tqdm.__spec__ is None"）。
    因此这里老老实实装真依赖，只做 sys.path 注入。
    """
    sys.path.insert(0, repo)


def build_detect_model(repo, ckpt_path):
    """取检测模型。

    踩坑记录：**不要**用 checkpoint 里的 yaml 重建 Model。
    该 yaml 的 width_multiple=0.5 与仓库 parse_model 的乘宽逻辑组合后，
    会在第一层 StemBlock 就得到 c2=None 而崩溃（checkpoint 是在另一版代码下
    训练出来的）。而 checkpoint 的 ckpt['model'] 本身就是一个完整可用的
    nn.Module，直接用它最稳，也最忠实。
    """
    import torch
    ckpt = torch.load(ckpt_path, map_location="cpu", weights_only=False)
    model = ckpt["model"]

    # 权重是 fp16（checkpoint 以 half 保存，这也是它只有 1.1MB 的原因之一）。
    # CPU 上必须转 fp32，否则 conv 会报 FloatTensor vs HalfTensor 类型不匹配。
    model.float()
    model.eval()

    head = model.model[-1]
    print("[detect] nc=%s names=%s na=%s no=%s -> out_ch=%s"
          % (head.nc, getattr(model, "names", None), head.na, head.no, head.no * head.na))
    print("[detect] 直接用 checkpoint 内的模型对象，未做 yaml 重建")

    # 关键：打开拼接分支，使输出为单一张量 [1, N, no*na]
    head.export_cat = True
    head.training = False
    if not hasattr(head, "stride") or head.stride is None:
        head.stride = torch.tensor([8., 16., 32.])

    # 关键：_make_grid_new 会把整块网格赋给 self.grid[i] / self.anchor_grid[i]，
    # 但当前仓库把 anchor_grid 用 register_buffer 注册成了固定形状 [nl,1,na,1,1,2]，
    # 赋值 [1,na,ny,nx,2] 时会报 expand size 不匹配。checkpoint 训练时它们只是
    # 普通 Python list，所以这里按 list 重置，并清掉注册的缓冲区。
    head.grid = [torch.zeros(1)] * head.nl
    if "anchor_grid" in head._buffers:
        del head._buffers["anchor_grid"]
    head.anchor_grid = [torch.zeros(1)] * head.nl
    return model


def export_detect(repo, weights, out_dir):
    import torch
    model = build_detect_model(repo, weights)
    head = model.model[-1]
    print("[detect] no=%s na=%s -> 输出通道=%s" % (head.no, head.na, head.no * head.na))
    dummy = torch.zeros(1, 3, 640, 640)
    out = os.path.join(out_dir, "plate_detect.onnx")
    with torch.no_grad():
        y = model(dummy)
    print("[detect] torch 输出形状:", tuple(y.shape) if hasattr(y, "shape") else type(y))
    torch.onnx.export(model, dummy, out, opset_version=12,
                      input_names=["images"], output_names=["output"],
                      dynamic_axes={"images": {0: "batch"}, "output": {0: "batch"}},
                      do_constant_folding=True)
    print("[detect] ->", out)
    return out


def export_rec(repo, weights, out_dir):
    import torch
    from plate_recognition.plateNet import myNet_ocr_color
    ckpt = torch.load(weights, map_location="cpu", weights_only=False)
    sd, cfg = ckpt["state_dict"], ckpt["cfg"]
    model = myNet_ocr_color(cfg=cfg, num_classes=78, export=True, color_num=5)
    miss, unexp = model.load_state_dict(sd, strict=False)
    print("[rec] cfg=%s missing=%d unexpected=%d" % (cfg, len(miss), len(unexp)))
    model.eval()
    dummy = torch.zeros(1, 3, 48, 168)
    out = os.path.join(out_dir, "plate_rec_color.onnx")
    torch.onnx.export(model, dummy, out, opset_version=12,
                      input_names=["input"], output_names=["plate_logits", "color_logits"],
                      dynamic_axes={"input": {0: "batch"}}, do_constant_folding=True)
    print("[rec] ->", out)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True)
    ap.add_argument("--weights", default=None)
    ap.add_argument("--out", required=True)
    ap.add_argument("--skip-detect", action="store_true")
    ap.add_argument("--skip-rec", action="store_true")
    a = ap.parse_args()

    repo = os.path.abspath(a.repo)
    wdir = os.path.abspath(a.weights) if a.weights else os.path.join(repo, "weights")
    out_dir = os.path.abspath(a.out)
    os.makedirs(out_dir, exist_ok=True)
    install_stubs(repo)

    if not a.skip_detect:
        export_detect(repo, os.path.join(wdir, "plate_detect.pt"), out_dir)
    if not a.skip_rec:
        export_rec(repo, os.path.join(wdir, "plate_rec_color.pth"), out_dir)

    import onnx
    for fn in sorted(os.listdir(out_dir)):
        if fn.endswith(".onnx"):
            p = os.path.join(out_dir, fn)
            m = onnx.load(p)
            onnx.checker.check_model(m)
            print("[check] %s OK  (%.2f MB)" % (fn, os.path.getsize(p) / 1024 / 1024))


if __name__ == "__main__":
    main()
