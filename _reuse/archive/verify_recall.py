"""车辆检测召回率对账（干净实现，不依赖 xlsx 解析）。

用户记录（downloads/目押记录.xlsx，Excel 显示 00:02 等，实为第 2 秒起）：
    00:02  粤A130V9
    00:11  粤A8R902
    00:14  粤SVZ600
    00:23  粤A7M7M4
    00:25  粤AAG4884

验收标准：这些时间点附近是否**检出了车辆并判为在应急车道**。
车牌识别成功与否**不作为验收项**（按既定架构，车牌是附加信息）。
"""
import argparse
import json
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# 用户人眼记录的 5 辆车（时间单位：秒）
TRUTH = [(2.0, "粤A130V9"), (11.0, "粤A8R902"), (14.0, "粤SVZ600"),
         (23.0, "粤A7M7M4"), (25.0, "粤AAG4884")]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--jsonl", required=True)
    ap.add_argument("--window", type=float, default=2.5, help="时间匹配窗口(秒)")
    a = ap.parse_args()

    veh = [json.loads(l) for l in open(a.jsonl, encoding="utf-8")]
    print("车辆检出记录 %d 条，覆盖 t=%.2f..%.2f s"
          % (len(veh), min(v["t"] for v in veh), max(v["t"] for v in veh)))
    print("用户记录 %d 辆车（时间来自目押记录.xlsx）\n" % len(TRUTH))

    print("=== 逐车对账（窗口 ±%.1fs）===" % a.window)
    hit = 0
    for t, plate in TRUTH:
        near = [v for v in veh if abs(v["t"] - t) <= a.window]
        inroi = [v for v in near if v["in_emergency_lane"]]
        if inroi:
            hit += 1
            mark = "✅ 检出且判为应急车道"
        elif near:
            mark = "⚠ 有车但未判入应急车道"
        else:
            mark = "❌ 未检出车辆"
        print("  %5.1fs  %-10s %s   (窗口内 %d 辆 / 应急车道内 %d 辆)"
              % (t, plate, mark, len(near), len(inroi)))
        if near:
            c = min(near, key=lambda v: abs(v["t"] - t))
            print("          最近检出 t=%.2f %s conf=%.2f overlap=%.2f bottom_center=%s"
                  % (c["t"], c["cls"], c["conf"], c["roi_overlap"], c["bottom_center"]))

    print("\n=== 车辆检出召回率 ===")
    print("  %d/%d = %.0f%%" % (hit, len(TRUTH), 100.0 * hit / len(TRUTH)))

    # 列出所有"在应急车道内"的车辆时段，检查是否有用户未记录的
    inroi_all = sorted([v for v in veh if v["in_emergency_lane"]], key=lambda v: v["t"])
    print("\n=== 全部判为'在应急车道内'的检出，按时间归并 ===")
    groups = []
    for v in inroi_all:
        if groups and v["t"] - groups[-1][-1]["t"] <= 0.4:
            groups[-1].append(v)
        else:
            groups.append([v])
    print("共 %d 个时段：" % len(groups))
    for g in groups:
        t0, t1 = g[0]["t"], g[-1]["t"]
        # 用底边中心 x 判断是否同一辆车（位置是否漂移）
        xs = [v["bottom_center"][0] for v in g]
        print("   t=%6.2f..%6.2f (%5.2fs, %3d 次)  底边中心x范围 %.0f..%.0f  %s"
              % (t0, t1, t1 - t0, len(g), min(xs), max(xs),
                 "← 跨度大，可能是多辆车" if max(xs) - min(xs) > 200 else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
