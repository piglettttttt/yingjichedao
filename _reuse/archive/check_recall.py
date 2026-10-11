"""用用户人眼记录（目押记录.xlsx）对账车辆检测召回率。

验收标准只有一个：**用户记录的那几辆车，在对应时间点是否被检出并判为占用应急车道**。
识别不出车牌不算失败（按既定架构，车牌是附加信息）；车辆漏检才算失败。
"""
import argparse
import json
import sys
from collections import defaultdict

import openpyxl

sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def mmss_to_sec(s):
    """解析用户记录里的时间。

    坑：xlsx 里存的是时间对象（Excel 显示 00:02，但 str() 得到 "00:02:00"），
    若按字符串三段解析会得到 120 秒而不是 2 秒——这一个 bug 会让对账全部失败。
    这里用**鸭子类型**（有 hour/minute/second 就按时间对象处理），
    避免 isinstance 在某些兼容层下不命中的问题。
    """
    if all(hasattr(s, a) for a in ("hour", "minute", "second")):
        print("      [dbg] %r type=%s -> %d:%d:%d" % (s, type(s).__name__,
              s.hour, s.minute, s.second))
        return int(s.hour) * 3600 + int(s.minute) * 60 + int(s.second)
    print("      [dbg] %r type=%s -> 走字符串分支" % (s, type(s).__name__))
    parts = [int(p) for p in str(s).strip().split(":")]
    if len(parts) == 3:
        return parts[0] * 3600 + parts[1] * 60 + parts[2]
    if len(parts) == 2:
        return parts[0] * 60 + parts[1]
    return parts[0]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--xlsx", required=True)
    ap.add_argument("--jsonl", required=True)
    ap.add_argument("--window", type=float, default=3.0, help="时间匹配窗口(秒)")
    a = ap.parse_args()

    wb = openpyxl.load_workbook(a.xlsx, data_only=True)
    ws = wb.worksheets[0]
    truth = []
    for row in ws.iter_rows(values_only=True):
        if row and row[0] and row[1]:
            truth.append((mmss_to_sec(row[0]), str(row[1]).strip()))
    print("用户记录 %d 辆车:" % len(truth))
    for t, p in truth:
        print("   %6.1fs  %s" % (t, p))

    veh = [json.loads(l) for l in open(a.jsonl, encoding="utf-8")]
    print("\n车辆检出记录 %d 条" % len(veh))

    print("\n=== 逐车对账（窗口 ±%.1fs）===" % a.window)
    hit = 0
    for t, plate in truth:
        near = [v for v in veh if abs(v["t"] - t) <= a.window]
        inroi = [v for v in near if v["in_emergency_lane"]]
        # 取该窗口内最接近 t 的检出
        closest = min(near, key=lambda v: abs(v["t"] - t)) if near else None
        status = "✅" if inroi else ("⚠有车但未判入应急车道" if near else "❌未检出车辆")
        if inroi:
            hit += 1
        print("  %6.1fs %-10s %s   窗口内检出 %d 辆 / 其中应急车道内 %d 辆"
              % (t, plate, status, len(near), len(inroi)))
        if closest:
            print("        最近检出: t=%.2f %s conf=%.2f box=%s overlap=%.2f"
                  % (closest["t"], closest["cls"], closest["conf"],
                     closest["box"], closest["roi_overlap"]))

    print("\n=== 召回率 ===")
    print("  %d/%d = %.0f%%" % (hit, len(truth), 100.0 * hit / max(1, len(truth))))

    # 反向：列出所有被判入应急车道的时段，看有没有用户没记的
    inroi_all = [v for v in veh if v["in_emergency_lane"]]
    print("\n=== 全部'在应急车道内'的检出，按时间归并 ===")
    times = sorted(v["t"] for v in inroi_all)
    groups = []
    for t in times:
        if groups and t - groups[-1][-1] <= 0.5:
            groups[-1].append(t)
        else:
            groups.append([t])
    print("共 %d 个时段：" % len(groups))
    for g in groups:
        seg = [v for v in inroi_all if g[0] <= v["t"] <= g[-1]]
        print("   %6.2f - %6.2f s (%4.2fs, %d 次检出，%d 个不同位置)"
              % (g[0], g[-1], g[-1] - g[0], len(seg),
                 len({tuple(v["box"][:2]) for v in seg})))


if __name__ == "__main__":
    main()
