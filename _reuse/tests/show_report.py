"""抽查报告的**结构**（不看 base64 内容），确认三张证据帧与标题都在。"""
import os
import re
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def summarize(path):
    with open(path, encoding="utf-8") as fh:
        html = fh.read()
    size = len(html.encode("utf-8"))
    labels = re.findall(r'<div class="k"[^>]*>([^<]+)</div>', html)
    plates = re.findall(r'<div class="plate">([^<]+)</div>', html)
    srcs = re.findall(r'<img[^>]*src="([^"]+)"', html)
    notes = re.findall(r'<p class="note">([^<]+)</p>', html)
    print("=== %s" % path)
    print("  体积 %d 字节 | 图片 %d 张 | 内嵌 base64: %s"
          % (size, len(srcs), "base64" in html))
    print("  区块标签: %s" % " / ".join(labels))
    print("  车牌: %s" % ", ".join(plates))
    for s in srcs:
        print("    src=%s" % (s if not s.startswith("data:") else "(base64 内联)"))
    for n in notes:
        print("  提示: %s" % n.strip()[:60])
    return 0


def main():
    args = sys.argv[1:]
    if not args:
        base = os.path.join("downloads", "xt_v021", "人工审核结果")
        args = [os.path.join(base, "违章检测报告_轻量版.html")]
    for p in args:
        if not os.path.exists(p):
            print("找不到: %s" % p)
            return 1
        summarize(p)
    return 0


if __name__ == "__main__":
    sys.exit(main())
