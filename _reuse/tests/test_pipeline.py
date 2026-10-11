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


def check_stage1_vehicles():
    import stage1_vehicles
    stage1_vehicles.check_load_roi_and_bottom_span()


def main():
    """不依赖 pytest：venv 里没装 pytest，直接顺序跑（pytest 装了也能跑）。"""
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    checks = [("runmeta", check_runmeta),
              ("run_pipeline", check_run_pipeline),
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
