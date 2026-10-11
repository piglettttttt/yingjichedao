"""Python 侧回归测试。

规范要求「核心函数须覆盖单元测试，测试函数以 `check_<func>` 为前缀，内联于被测函数下方」。
因此**测试体本身写在各自脚本里**（`run_pipeline.py` / `stage1_vehicles.py` / `runmeta.py`），
本文件只负责把它们收集起来统一执行，供 pytest 与 CI/批处理调用。

跑法（两种都行）：
    _reuse\\test_python.bat
    .venv\\Scripts\\python.exe -m pytest _reuse\\tests -q

C 侧回归测试（审核 GUI）在 `test_logic.cpp` / `test_report.cpp`，编译后分别跑
`dist\\test_logic.exe`（期望 27 passed）与 `dist\\test_report.exe`（期望 ALL PASSED）。
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REUSE = os.path.dirname(HERE)
if REUSE not in sys.path:
    sys.path.insert(0, REUSE)


def check_runmeta():
    import runmeta
    runmeta.check_all()


def check_run_pipeline():
    import run_pipeline
    run_pipeline.check_run_pipeline_logic()


def check_merge_tracks():
    import merge_tracks
    merge_tracks.check_merge_then_pick_frames()


def check_pipeline_v2():
    import pipeline_v2
    pipeline_v2.check_pick_evidence_frames()


def check_stage1_vehicles():
    import stage1_vehicles
    stage1_vehicles.check_load_roi_and_bottom_span()


def check_make_outputs():
    """--base-ts 解析：换视频必须重给，而且格式错要当场拦住。

    踩坑：曾把 Unix 时间戳当 --base-ts 传进去，报错发生在流水线**最后一步**，
    前面十几分钟的计算全部白跑，所以这里必须有测试。
    """
    import make_outputs
    ts = make_outputs.parse_base_ts("2023-03-04 15:38:02")
    assert ts.year == 2023 and ts.second == 2, "正常格式应解析成功"
    for bad in ("1770000000", "2023/03/04 15:38:02", "", "2023-03-04"):
        try:
            make_outputs.parse_base_ts(bad)
        except SystemExit:
            pass
        else:
            raise AssertionError("格式 %r 应该被拒绝" % (bad,))
    try:
        make_outputs.check_outdir_ascii("downloads/ok")
    except SystemExit:
        raise AssertionError("纯 ASCII 目录不应被拒")
    try:
        make_outputs.check_outdir_ascii("downloads/中文")
    except SystemExit:
        pass
    else:
        raise AssertionError("含中文的初成果目录必须被拒（审核 GUI 读不了）")
    print("  [PASS] parse_base_ts / check_outdir_ascii")


def check_evidence_materials(folder=None):
    """校验初成果目录里的**材料**是否备齐（exe 只复制，所以这里必须齐）。

    为什么要有这条：分工是「Python 备齐材料、审核 exe 只复制」。材料缺了，
    exe 不会报错、只是少一张图，问题会被拖到交材料时才暴露。所以这里硬校验：
      - CSV 有 证据目录/证据图1/2/3 列
      - 每行的证据目录真实存在
      - 目录里 CSV 点名的文件都在，且都是有效 JPEG（大小、开头 FFD8、结尾 FFD9）
      - 三张图确实互不相同（不是同一帧充三张）
    """
    import csv
    import glob
    import os

    if folder is None:
        # 默认拿最近一次跑出来的成果目录
        cands = sorted(glob.glob(os.path.join("downloads", "xt_v*")),
                       key=os.path.getmtime)
        if not cands:
            print("  [SKIP] 没有 downloads/xt_v* 成果目录可校验")
            return
        folder = cands[-1]
    csvp = os.path.join(folder, "检测结果.csv")
    assert os.path.exists(csvp), "找不到 %s" % csvp
    with open(csvp, encoding="utf-8-sig") as fh:
        rows = list(csv.DictReader(fh))
    assert rows, "成果 CSV 没有数据行"
    need = ["证据目录", "证据图1", "证据图2", "证据图3"]
    for col in need:
        assert col in rows[0], "CSV 缺列 %s（列名：%s）" % (col, list(rows[0].keys()))

    n3 = 0
    for r in rows:
        cdir = os.path.join(folder, "证据图", r["证据目录"].strip())
        assert os.path.isdir(cdir), "证据目录不存在: %s" % cdir
        blobs = []
        for col in ("证据图1", "证据图2", "证据图3"):
            name = (r.get(col) or "").strip()
            if not name:
                continue        # 帧不够时如实为空
            p = os.path.join(cdir, name)
            assert os.path.exists(p), "CSV 点名但文件不存在: %s" % p
            b = open(p, "rb").read()
            assert len(b) > 2000, "%s 太小，不像真图（%d 字节）" % (p, len(b))
            assert b[:2] == b"\xff\xd8", "%s 不是 JPEG（开头 %r）" % (p, b[:2])
            assert b[-2:] == b"\xff\xd9", "%s 不是完整 JPEG（结尾 %r）" % (p, b[-2:])
            blobs.append(b)
        assert blobs, "案例 %s 一张证据图都没有" % r["序号"]
        assert len(set(blobs)) == len(blobs), \
            "案例 %s 的证据帧有重复（同一帧充数）" % r["序号"]
        if len(blobs) == 3:
            n3 += 1
    print("  [PASS] 材料齐备：%d 个案例，其中 %d 个给满三张证据帧（%s）"
          % (len(rows), n3, folder))


def main():
    """不依赖 pytest：venv 里没装 pytest，直接顺序跑（pytest 装了也能跑）。"""
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    checks = [("runmeta", check_runmeta),
              ("run_pipeline", check_run_pipeline),
              ("pipeline_v2", check_pipeline_v2),
              ("merge_tracks", check_merge_tracks),
              ("make_outputs", check_make_outputs),
              ("evidence", check_evidence_materials),
              ("stage1_vehicles", check_stage1_vehicles)]
    failed = []
    for name, fn in checks:
        print("=" * 60)
        print("[%s]" % name)
        try:
            fn()
        except AssertionError as e:
            print("  [FAIL] %s" % e)
            failed.append(name)
        except Exception as e:  # noqa: BLE001
            print("  [ERROR] %s: %s" % (type(e).__name__, e))
            failed.append(name)
    print("=" * 60)
    if failed:
        print("RESULT: FAILED -> %s" % ", ".join(failed))
        return 1
    print("RESULT: ALL PASSED (%d 组 Python 检查)" % len(checks))
    return 0


if __name__ == "__main__":
    sys.exit(main())
