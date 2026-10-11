"""构造单案例测试集，用于验证 PlateReview.exe 的报告链路。

做四件事：
  1. 从源成果的检测结果.csv 取**第一个案例**（可用 --plate 指定车牌），
     写成只含该案例的单案例 CSV
  2. 拷入带 ROI 标注的证据图（审核界面用）
  3. 按 CSV 的「证据目录」列，把该案例的证据帧目录**整份**拷过来
     （证据帧由 make_outputs.py 备齐，这里不重做、也不猜文件名）
  4. 打印产物清单

为什么不再固定要某个车牌：车牌能不能被检出取决于视频与参数，写死一个车牌会让
fixture 随参数变化而失效（实测踩过：`粤AX285E` 只在更早那次成果里出现过）。
测试要的是「一个真实案例的完整材料」，车牌是哪个并不重要。
"""
import csv
import os
import shutil
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

SRC = r"D:\Users\Lhh\Documents\dsh\yingjichedao\downloads\xt_v021"
DST = r"D:\Users\Lhh\Documents\dsh\yingjichedao\downloads\ax285e_test"


def main():
    args = sys.argv[1:]
    src = args[0] if len(args) > 0 else SRC
    plate = args[1] if len(args) > 1 else ""

    csvp = os.path.join(src, "检测结果.csv")
    if not os.path.exists(csvp):
        print("找不到源成果 CSV：%s" % csvp)
        return 1
    with open(csvp, encoding="utf-8-sig") as fh:
        rows = list(csv.DictReader(fh))
    if not rows:
        print("源成果没有数据行：%s" % csvp)
        return 1
    hit = [r for r in rows if r["车牌号"] == plate] if plate else rows
    if not hit:
        print("源成果里没有车牌 %s（可选：%s）"
              % (plate, ", ".join(r["车牌号"] or "(未识别)" for r in rows)))
        return 1
    row = hit[0]
    print("选中案例: 序号=%s 车牌=%s 时间=%s~%s 置信=%s"
          % (row["序号"], row["车牌号"], row["时间_起"], row["时间_止"],
             row["车牌置信度"]))

    if os.path.exists(DST):
        shutil.rmtree(DST)
    os.makedirs(os.path.join(DST, "证据图"), exist_ok=True)

    # 1. 单案例 CSV，原样保留列结构（审核程序按列名读）
    with open(os.path.join(DST, "检测结果.csv"), "w",
              encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(row.keys()))
        w.writeheader()
        w.writerow(row)

    # 2. 带标注的证据图（审核界面用）
    ev = row["证据图"]
    src_img = os.path.join(src, "证据图", ev)
    shutil.copyfile(src_img, os.path.join(DST, "证据图", ev))
    print("已拷入标注证据图: %s" % ev)

    # 3. 证据帧目录整份拷过来
    cdir = (row.get("证据目录") or "").strip()
    if not cdir:
        print("!! 源 CSV 没有『证据目录』列：这是 v0.3.0 之前的成果，"
              "请先用当前 make_outputs.py 重跑该视频")
        return 1
    src_dir = os.path.join(src, "证据图", cdir)
    if not os.path.isdir(src_dir):
        print("!! 源证据目录不存在: %s" % src_dir)
        return 1
    dst_dir = os.path.join(DST, "证据图", cdir)
    shutil.copytree(src_dir, dst_dir)
    print("已拷入证据帧目录: %s（%d 个文件）"
          % (cdir, len(os.listdir(dst_dir))))

    print("\n测试集: %s" % DST)
    for root, dirs, files in os.walk(DST):
        for f in files:
            p = os.path.join(root, f)
            print("   %-54s %d KB"
                  % (os.path.relpath(p, DST), os.path.getsize(p) // 1024))
    return 0


if __name__ == "__main__":
    sys.exit(main())
