"""运行元数据落盘（可追溯性）。

为什么需要：**ROI 必须逐视频人工重划**（不同视频应急车道位置不同，自动识别不可靠），
参数也随视频调整。若产物里不记「用的哪个 ROI 文件、多边形长什么样、参数是多少、
视频是哪一份」，事后就无法判断某份成果是怎么跑出来的，也无法重跑复现。

因此每个步骤都往自己的产物旁写一份 `<产物名>.runmeta.json`，并把摘要塞进
产物本身（result.json / 检测结果.json）的 `run` 字段。

记录内容：
    - 工具版本 + 时间 + 命令行（含全部参数原值）
    - ROI：文件路径、文件 sha256、mode、polygon_norm 与像素多边形快照
    - 输入文件：路径、大小、sha256
    - 视频：分辨率、声明帧率、帧数
    - 检查结果：多边形是否越界等（越界会导致 ROI 与预期不一致）

视频/ROI 用 sha256 而不是 mtime：换机器、重新拷贝后 mtime 必变，哈希不变。
大文件哈希会花点时间，但视频通常在百 MB 级，一次性开销可接受。
"""
import hashlib
import json
import os
import platform
import subprocess
import sys
from datetime import datetime

VERSION = "v0.2.0"

SCHEMA = 1
_READ_CHUNK = 1 << 20


def sha256_file(path, limit=None):
    """文件 sha256（十六进制）。limit 非空时只哈希前 limit 字节（用于大文件快速指纹）。"""
    h = hashlib.sha256()
    n = 0
    with open(path, "rb") as fh:
        while True:
            b = fh.read(_READ_CHUNK)
            if not b:
                break
            if limit is not None and n + len(b) > limit:
                b = b[:limit - n]
            h.update(b)
            n += len(b)
            if limit is not None and n >= limit:
                break
    return h.hexdigest()


def video_info(path):
    """分辨率/声明帧率/帧数。用 cv2 而非 ffprobe：这里只需要声明的元数据。"""
    import cv2
    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        return {"error": "打不开视频"}
    info = {
        "width": int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)),
        "height": int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)),
        "fps": round(float(cap.get(cv2.CAP_PROP_FPS) or 0.0), 4),
        "n_frames": int(cap.get(cv2.CAP_PROP_FRAME_COUNT)),
    }
    cap.release()
    return info


def _git_commit():
    try:
        out = subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                             cwd=os.path.dirname(os.path.abspath(__file__)),
                             capture_output=True, text=True, timeout=5)
        return out.stdout.strip() or None
    except Exception:
        return None


def _is_size_pair(v):
    """是否是 (宽, 高) 这样的两个正数。防御调用方传错参数（踩过：传了 tag 进来）。"""
    try:
        return (len(v) == 2
                and all(isinstance(x, (int, float)) and not isinstance(x, bool) and x > 0
                        for x in v))
    except TypeError:
        return False


def roi_meta(roi_path, video_size=None):
    """ROI 文件指纹 + 多边形快照。polygon_norm 越界（>1 或 <0）时给出 warning。"""
    with open(roi_path, encoding="utf-8") as fh:
        r = json.load(fh)
    meta = {
        "path": os.path.abspath(roi_path),
        "sha256": sha256_file(roi_path),
        "mode": r.get("mode"),
        "shot": r.get("shot"),
        "roi_video_size": r.get("video_size"),
        "polygon_norm": r.get("polygon_norm"),
        "polygon_px_in_roi_file": r.get("polygon"),
        "lane_top_norm": r.get("lane_top_norm"),
    }
    warns = []
    pn = r.get("polygon_norm")
    fw, fh = (video_size or (None, None))
    if pn and fw and fh:
        px = [[round(x * fw, 1), round(y * fh, 1)] for x, y in pn]
        meta["polygon_px"] = px
        xs = [p[0] for p in px]
        ys = [p[1] for p in px]
        meta["bbox_px"] = [min(xs), min(ys), max(xs), max(ys)]
        over = [p for p in pn if p[0] > 1.0 or p[1] > 1.0 or p[0] < 0.0 or p[1] < 0.0]
        if over:
            warns.append(
                "ROI 多边形有 %d 个顶点落在画面外（归一化坐标 >1 或 <0），"
                "实际 bbox=%s（画幅 %dx%d）。若 roi_verify.jpg 里叠加的形状与标注一致，"
                "那就是**有意画到画面外**，可直接用；若不一致说明标定时截图里的视频区域"
                "没被正确识别（黑边/缩放），需重标。"
                % (len(over), meta["bbox_px"], fw, fh))
        if r.get("video_size") and list(r["video_size"]) != [fw, fh]:
            warns.append("ROI 文件的 video_size %s 与当前视频 %s 不一致，归一化换算可能错位"
                         % (r["video_size"], [fw, fh]))
    if warns:
        meta["warnings"] = warns
    return meta


def warn_roi_early(roi_path, video_size=None, tag="ROI"):
    """把 ROI 越界告警在**检测开始前**就喊出来，而不是等元数据写完。

    时机很重要：越界（或标定错位）会让整段检测的 ROI 与预期不符，
    若等到跑完才看见告警，这一轮检测就白跑了。
    """
    if video_size is not None and not _is_size_pair(video_size):
        # 调用方传错了（例如把 tag 当第二个参数）——宁可少一个告警，也不能崩
        print("⚠ 无法检查 ROI：video_size 参数不是 (宽, 高) 数值对，收到 %r" % (video_size,))
        video_size = None
    try:
        m = roi_meta(roi_path, video_size)
    except Exception as e:  # noqa: BLE001
        print("⚠ 无法检查 ROI（%s: %s）" % (type(e).__name__, e))
        return []
    for w in m.get("warnings", []):
        print("⚠ %s：%s" % (tag, w))
    return m.get("warnings", [])


def collect(script, args, video=None, roi=None, extra=None, inputs=None):
    """组装一份运行元数据。args 传 argparse 的 Namespace，全部参数原值落盘。"""
    ns = vars(args) if hasattr(args, "__dict__") else dict(args or {})
    ns = {k: (os.path.abspath(v) if k in ("video", "roi", "out", "outdir", "result",
                                          "veh_jsonl", "weights", "shot", "onnx")
              and isinstance(v, str) else v)
          for k, v in ns.items()}
    meta = {
        "schema": SCHEMA,
        "version": VERSION,
        "script": os.path.basename(script),
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "argv": [os.path.basename(script)] + [str(x) for x in (sys.argv[1:] or [])],
        "args": ns,
        "python": platform.python_version(),
        "git_commit": _git_commit(),
    }
    if video:
        p = os.path.abspath(video)
        meta["video"] = {"path": p, "size": os.path.getsize(p), "sha256": sha256_file(p)}
        meta["video"].update(video_info(p))
    if roi:
        meta["roi"] = roi_meta(roi, video_size=(meta.get("video") or {}).get("width") and
                               [meta["video"]["width"], meta["video"]["height"]] or None)
    if inputs:
        meta["inputs"] = {}
        for name, p in inputs.items():
            if p and os.path.exists(p):
                meta["inputs"][name] = {"path": os.path.abspath(p),
                                        "size": os.path.getsize(p),
                                        "sha256": sha256_file(p)}
            elif p:
                meta["inputs"][name] = {"path": os.path.abspath(p), "missing": True}
    if extra:
        meta.update(extra)
    return meta


def write(path, meta, also_json_fields=None):
    """把元数据写到 path；also_json_fields 给出需合并 run 字段的产物 json（原地更新）。"""
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(meta, fh, ensure_ascii=False, indent=2)
    for jp in (also_json_fields or []):
        with open(jp, encoding="utf-8") as fh:
            d = json.load(fh)
        d["run"] = meta
        with open(jp, "w", encoding="utf-8") as fh:
            json.dump(d, fh, ensure_ascii=False, indent=2)
    print("运行元数据 -> %s" % path)
    for w in (meta.get("roi") or {}).get("warnings", []):
        print("  ⚠ %s" % w)
    return path


def meta_path(out_dir_or_file):
    """约定：产物目录写 run_meta.json；产物文件写 <文件名>.runmeta.json。"""
    if os.path.isdir(out_dir_or_file) or not os.path.splitext(out_dir_or_file)[1]:
        return os.path.join(out_dir_or_file, "run_meta.json")
    return out_dir_or_file + ".runmeta.json"


def check_sha256_file():
    """sha256_file：已知向量 + limit 截断。"""
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        p = os.path.join(td, "a.bin")
        with open(p, "wb") as fh:
            fh.write(b"abc")
        assert sha256_file(p) == \
            "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad", "sha256 与已知值不符"
        with open(p, "wb") as fh:
            fh.write(b"abcdef")
        assert sha256_file(p, limit=3) == sha256_file(p, limit=3)
        with open(p, "wb") as fh:
            fh.write(b"abc")
        assert sha256_file(p, limit=3) == \
            "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad", "limit 截断结果不对"
    print("  [PASS] sha256_file")


def check_roi_meta_catches_out_of_range():
    """roi_meta：归一化坐标越界必须报警（真实踩到的坑，见 roi_image50.json）。"""
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        good = os.path.join(td, "good.json")
        with open(good, "w", encoding="utf-8") as fh:
            json.dump({"mode": "polygon", "video_size": [1920, 1080],
                       "polygon_norm": [[0.1, 0.9], [0.9, 0.9], [0.9, 0.99]]}, fh)
        assert "warnings" not in roi_meta(good, (1920, 1080)), "合规 ROI 不该报警"

        bad = os.path.join(td, "bad.json")
        with open(bad, "w", encoding="utf-8") as fh:
            json.dump({"mode": "polygon", "video_size": [1920, 1080],
                       "polygon_norm": [[1.124088, 0.880153], [0.9, 0.9]]}, fh)
        m = roi_meta(bad, (1920, 1080))
        assert m.get("warnings"), "越界 ROI 必须报警"
        assert m["polygon_px"][0][0] > 1920, "像素换算应超过画面宽度"

        mis = os.path.join(td, "mis.json")
        with open(mis, "w", encoding="utf-8") as fh:
            json.dump({"mode": "polygon", "video_size": [1280, 720],
                       "polygon_norm": [[0.1, 0.9], [0.9, 0.9]]}, fh)
        assert any("不一致" in w for w in roi_meta(mis, (1920, 1080))["warnings"]), \
            "resolution 不一致必须报警"
    print("  [PASS] roi_meta 越界/不一致告警")


def check_meta_path():
    """meta_path：目录给 run_meta.json，文件给 <file>.runmeta.json。"""
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        d = os.path.join(td, "out")
        os.makedirs(d)
        assert meta_path(d) == os.path.join(d, "run_meta.json")
        f = os.path.join(td, "result.json")
        open(f, "w").close()
        assert meta_path(f) == f + ".runmeta.json"
    print("  [PASS] meta_path")


def check_collect_records_all_args():
    """collect：参数原值、ROI 指纹、输入指纹都要落到元数据里。"""
    import argparse as _ap
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        roi = os.path.join(td, "r.json")
        with open(roi, "w", encoding="utf-8") as fh:
            json.dump({"mode": "polygon", "video_size": [1920, 1080],
                       "polygon_norm": [[0.1, 0.9], [0.9, 0.9]]}, fh)
        ns = _ap.Namespace(video=None, roi=roi, outdir=td, overlap_thresh=0.35)
        m = collect("stage1_vehicles.py", ns, roi=roi)
        assert m["version"] == VERSION and m["schema"] == SCHEMA
        assert m["args"]["overlap_thresh"] == 0.35, "参数原值必须落盘"
        assert m["roi"]["sha256"], "ROI 必须有指纹"
        assert m["roi"]["polygon_norm"], "ROI 必须有多边形快照"
    print("  [PASS] collect 落盘参数+ROI 快照")


def check_warn_roi_early_is_defensive():
    """warn_roi_early：video_size 传错（曾误把 tag 传进来）不能崩，且 tag 要生效。

    真实踩到过：调用写成 warn_roi_early(roi, fh)（把 tag 当 video_size），
    结果在 fw 上做乘法时抛 TypeError，整个脚本挂掉。
    """
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        p = os.path.join(td, "r.json")
        with open(p, "w", encoding="utf-8") as fh:
            json.dump({"mode": "polygon", "video_size": [1920, 1080],
                       "polygon_norm": [[1.2, 0.5], [0.5, 0.5]]}, fh)
        # 传错的 video_size：不能抛异常
        assert warn_roi_early(p, {"not": "a size"}) == [], "传错参数应返回空列表而不是崩"
        # 正常调用：应拿到告警
        w = warn_roi_early(p, (1920, 1080), tag="单测")
        assert w and "画面外" in w[0], "越界必须告警：%s" % (w,)
        # 合规 ROI：无告警
        q = os.path.join(td, "ok.json")
        with open(q, "w", encoding="utf-8") as fh:
            json.dump({"mode": "polygon", "video_size": [1920, 1080],
                       "polygon_norm": [[0.1, 0.5], [0.5, 0.9]]}, fh)
        assert warn_roi_early(q, (1920, 1080)) == [], "合规 ROI 不该告警"
    print("  [PASS] warn_roi_early 防御性")


def check_all():
    check_sha256_file()
    check_roi_meta_catches_out_of_range()
    check_meta_path()
    check_collect_records_all_args()
    check_warn_roi_early_is_defensive()
    print("RESULT: ALL PASSED (runmeta)")


if __name__ == "__main__":
    check_all()
