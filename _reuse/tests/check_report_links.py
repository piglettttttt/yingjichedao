"""校验轻量版报告的图片链接是否都能解析到真实文件。

轻量版用相对链接代替 base64（几 KB vs 几 MB）。链接一旦写错，浏览器里就是
一片破图，而这种错误不会让生成过程报任何错——所以必须有这条检查。
"""
import os
import re
import sys


def check_linked_report(path, base=None):
    """返回 (图片数, 缺失链接列表)。链接按报告所在目录解析。"""
    with open(path, encoding="utf-8") as fh:
        html = fh.read()
    if base is None:
        base = os.path.dirname(os.path.abspath(path))
    srcs = re.findall(r'<img[^>]*src="([^"]+)"', html)
    missing = []
    for s in srcs:
        if s.startswith("data:"):
            missing.append("(内联图，不该出现在轻量版) " + s[:30])
            continue
        full = os.path.join(base, s.replace("/", os.sep))
        if not os.path.exists(full):
            missing.append(s)
    return len(srcs), missing


def find_latest_review_report():
    """在 downloads/xt_v* 里找最近生成的**轻量版**报告（没有就返回 None）。"""
    import glob
    best = None
    for d in glob.glob(os.path.join("downloads", "xt_v*", "*", "违章检测报告_轻量版.html")):
        if best is None or os.path.getmtime(d) > os.path.getmtime(best):
            best = d
    return best


def main():
    args = sys.argv[1:]
    if args:
        paths = args
    else:
        latest = find_latest_review_report()
        if latest is None:
            print("  [SKIP] 还没有生成过轻量版报告"
                  "（先跑一次审核程序的『生成报告』，或 test_package.exe）")
            return 0
        paths = [latest]
    bad = 0
    for p in paths:
        if not os.path.exists(p):
            print("找不到: %s" % p)
            bad += 1
            continue
        n, missing = check_linked_report(p)
        size = os.path.getsize(p)
        print("%s" % p)
        print("  大小 %d 字节 | 图片 %d 张 | 缺失 %d 个" % (size, n, len(missing)))
        for m in missing:
            print("   !! %s" % m)
        # 轻量版应当远小于内联版（这里给一个宽松上界）
        if size > 200 * 1024:
            print("   !! 超过 200KB，不像轻量版")
            bad += 1
        if missing:
            bad += 1
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
