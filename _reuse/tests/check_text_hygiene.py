"""文本卫生检查：一眼拦住那几类"改完才发现"的机械错误。

为什么需要它：这些错误本身毫无难度，但每次都要靠"编译/跑链路"才发现，一轮
就是几十秒到几分钟。它们是**文本约定**问题，用 1 秒钟扫一遍就能拦住：

  1. `.bat` 必须纯 ASCII + CRLF
       - cmd.exe 用 OEM 代码页（中文 Windows = GBK）读 .bat，UTF-8 中文会把
         **命令本身**搞坏；
       - LF-only 的 .bat 在 if/for/goto 里会被解析错乱（实测：整个脚本炸成
         "xxx is not recognized as an internal or external command"）。
  2. `PlateReview.cpp` 必须纯 ASCII（项目铁律：中文只以 \\uXXXX 写在宽字面量里）
  3. 其它 .py / .md / .txt / .cpp 允许 UTF-8，但不应带 BOM（除 CSV/报告输出，
     那些是程序生成的，不在这里检查）

跑法：
    .venv\\Scripts\\python.exe _reuse\\tests\\check_text_hygiene.py
返回码非 0 表示有违规。
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
REUSE = os.path.join(ROOT, "_reuse")

# 必须纯 ASCII 的文件（相对仓库根）；.bat 另外要求 CRLF
ASCII_ONLY = ["_reuse/PlateReview.cpp"]
BAT_FILES = ["_reuse/run_pipeline.bat", "_reuse/parse_roi.bat",
             "_reuse/test_python.bat", "_reuse/build_c.bat"]


def scan_ascii(path):
    """返回 (非ASCII字节数, 首个违规位置示例)。"""
    with open(path, "rb") as fh:
        b = fh.read()
    bad = [(i, c) for i, c in enumerate(b) if c > 127]
    if not bad:
        return 0, ""
    i = bad[0][0]
    snippet = b[max(0, i - 30):i + 30].decode("utf-8", "replace").replace("\n", "|")
    return len(bad), snippet


def scan_line_endings(path):
    """返回裸 LF 的个数（应为 0：全部 CRLF）。"""
    with open(path, "rb") as fh:
        b = fh.read()
    bare = 0
    for i, c in enumerate(b):
        if c == 10 and (i == 0 or b[i - 1] != 13):
            bare += 1
    return bare


def main():
    problems = []
    for rel in ASCII_ONLY:
        p = os.path.join(ROOT, rel)
        n, snip = scan_ascii(p)
        if n:
            problems.append("%s 含 %d 个非 ASCII 字节（必须纯 ASCII）：…%s…"
                            % (rel, n, snip))
    for rel in BAT_FILES:
        p = os.path.join(ROOT, rel)
        if not os.path.exists(p):
            problems.append("%s 不存在" % rel)
            continue
        n, snip = scan_ascii(p)
        if n:
            problems.append("%s 含 %d 个非 ASCII 字节（cmd 会读坏命令）：…%s…"
                            % (rel, n, snip))
        bare = scan_line_endings(p)
        if bare:
            problems.append("%s 有 %d 处裸 LF（cmd 需要 CRLF，否则解析错乱）"
                            % (rel, bare))

    if problems:
        print("文本卫生检查未通过：")
        for x in problems:
            print("  !! %s" % x)
        return 1
    print("  [PASS] 文本卫生：%d 个纯 ASCII 文件 + %d 个 .bat 均为 CRLF/ASCII"
          % (len(ASCII_ONLY), len(BAT_FILES)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
