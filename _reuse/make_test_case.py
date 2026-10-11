"""构造 粤AX285E 单案例测试集，用于验证 PlateReview.exe 的报告链路。

做三件事：
  1. 从 xiangtang 的检测结果.csv 抽出 粤AX285E 那一行，写成单案例 CSV
  2. 拷入带 ROI 标注的证据图（审核界面用）
  3. 从视频抽出**同帧的纯原图**放到 证据图/原图/（报告用）

帧号来源：证据图文件名的末尾数字（make_outputs.py 以帧号命名）。
"""
import csv
import os
import shutil
import sys

import cv2

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

SRC = r"D:\Users\Lhh\Documents\dsh\yingjichedao\downloads\xiangtang"
DST = r"D:\Users\Lhh\Documents\dsh\yingjichedao\downloads\ax285e_test"
VID = (r"D:\Users\Lhh\Documents\dsh\yingjichedao\downloads"
       r"\BV1CFRGBVEpo_00m24s-00m59s_h264.mp4")
PLATE = "粤AX285E"


def main():
    if os.path.exists(DST):
        shutil.rmtree(DST)
    os.makedirs(os.path.join(DST, "证据图", "原图"), exist_ok=True)

    with open(os.path.join(SRC, "检测结果.csv"), encoding="utf-8-sig") as fh:
        rows = list(csv.DictReader(fh))
    hit = [r for r in rows if r["车牌号"] == PLATE]
    if not hit:
        print("未找到 %s" % PLATE)
        return 1
    row = hit[0]
    print("命中案例: 序号=%s 时间=%s~%s 置信=%s 证据图=%s"
          % (row["序号"], row["时间_起"], row["时间_止"],
             row["车牌置信度"], row["证据图"]))

    # 单案例 CSV，保持原列结构
    with open(os.path.join(DST, "检测结果.csv"), "w",
              encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(row.keys()))
        w.writeheader()
        w.writerow(row)

    # 带标注的证据图（审核界面用）
    ev = row["证据图"]
    src_img = os.path.join(SRC, "证据图", ev)
    shutil.copyfile(src_img, os.path.join(DST, "证据图", ev))
    print("已拷入标注证据图: %s" % ev)

    # 从文件名末尾取帧号
    stem = os.path.splitext(ev)[0]
    digits = ""
    for ch in reversed(stem):
        if ch.isdigit():
            digits = ch + digits
        else:
            break
    frame = int(digits) if digits else 0
    print("解析帧号: %d" % frame)

    # 抽纯原帧
    cap = cv2.VideoCapture(VID)
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    cap.set(cv2.CAP_PROP_POS_FRAMES, frame)
    ok, fr = cap.read()
    cap.release()
    if not ok:
        print("!! 抽帧失败")
        return 1
    plain = os.path.join(DST, "证据图", "原图", ev)
    okj, buf = cv2.imencode(".jpg", fr, [int(cv2.IMWRITE_JPEG_QUALITY), 95])
    if not okj:
        print("!! 编码失败")
        return 1
    with open(plain, "wb") as fh:
        fh.write(buf.tobytes())
    print("已写入纯原图: %s  (t=%.2fs, %dx%d)"
          % (os.path.join("证据图", "原图", ev), frame / fps,
             fr.shape[1], fr.shape[0]))

    print("\n测试集: %s" % DST)
    for root, dirs, files in os.walk(DST):
        for f in files:
            p = os.path.join(root, f)
            print("   %-52s %d KB"
                  % (os.path.relpath(p, DST), os.path.getsize(p) // 1024))
    return 0


if __name__ == "__main__":
    sys.exit(main())
