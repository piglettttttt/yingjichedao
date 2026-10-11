"""从 pipeline 结果生成交付物：车牌 + 记录仪时间 + 证据图。

记录仪时间来源：视频右下角 LCD 叠字「2023-03-04 15:38:02」。
实测该时间随视频秒数线性递增（t=0→15:38:02, t=10→15:38:13），
因此用固定基准时间 + 视频秒数换算，比逐帧 OCR 更稳且无误差。

用法：
    python make_outputs.py --result <result.json> --video <mp4> --outdir <目录>
"""
import argparse
import csv
import datetime
import json
import os
import sys

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import runmeta  # noqa: E402

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# 记录仪时间基准（由视频首帧叠字确认）。
# 仅当视频有 LCD 叠字时使用；若视频**没有**叠字（如 B 站剪辑片段），
# 改用 --time-mode video，此时时间列直接写视频内的播放时间（MM:SS.s）。
BASE_TS = datetime.datetime(2023, 3, 4, 15, 38, 2)

# 时间表达方式：
#   clock = 记录仪时钟（基准时间 + 视频内秒数）
#   video = 视频内播放时间（无叠字视频用这个）
TIME_MODE = "clock"

# 每个案例的证据帧文件名（固定名，审核程序照抄即可，不需要判断内容）。
# 顺序 = 报告里的展示顺序：原图（置信最高帧）→ 最早附近帧 → 最晚附近帧。
EVIDENCE_NAMES = ("原图.jpg", "证据帧2.jpg", "证据帧3.jpg")


def fmt_video_time(sec):
    """视频内播放时间：MM:SS.s（超过一小时则 H:MM:SS.s）。"""
    sec = max(0.0, float(sec))
    h = int(sec // 3600)
    m = int((sec % 3600) // 60)
    s = sec % 60
    if h:
        return "%d:%02d:%04.1f" % (h, m, s)
    return "%02d:%04.1f" % (m, s)


def imwrite_unicode(path, img, quality=92):
    """写图到可能含中文的路径。

    坑：Windows 下 cv2.imwrite 处理含非 ASCII 的路径会**静默失败**（返回 False、
    不抛异常），导致证据图一张都没生成。必须用 imencode 编码后用 Python 写字节。
    """
    ok, buf = cv2.imencode(".jpg", img, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
    if not ok:
        return False
    with open(path, "wb") as fh:
        fh.write(buf.tobytes())
    return True


def ts_at(sec):
    if TIME_MODE == "video":
        return fmt_video_time(sec)
    return (BASE_TS + datetime.timedelta(seconds=float(sec))).strftime("%Y-%m-%d %H:%M:%S")


def ts_label():
    return "视频内时间" if TIME_MODE == "video" else "记录仪时间"


def check_outdir_ascii(path):
    """初成果目录必须是纯 ASCII。

    审核 GUI 是纯 Win32 宽字符程序，但把外部路径按窄字符读入，中文目录会读不到
    （用户要求「路径不得包含中文」，见《使用规则》第一节）。
    在这里拦下是最可靠的位置：cmd 侧的 findstr "[^ -~]" 在 GBK 代码页下
    实测**检测不出**中文路径，不能依赖。
    """
    bad = sorted({c for c in str(path) if ord(c) > 126 or ord(c) < 32})
    if bad:
        raise SystemExit(
            "[错误] 初成果目录含非 ASCII 字符 %s，审核 GUI 读不了：\n       %s"
            % ("".join(bad), path))
    print("  [PASS] 初成果目录为纯 ASCII")


def parse_base_ts(s):
    """解析记录仪基准时间。格式错误时给出明确提示，而不是抛 traceback。

    踩坑：曾把 Unix 时间戳（1770000000）当作 --base-ts 传进来，
    报错发生在**流水线最后一步**，前面十几分钟的计算全白跑。
    """
    try:
        return datetime.datetime.strptime(str(s), "%Y-%m-%d %H:%M:%S")
    except ValueError:
        raise SystemExit(
            "[错误] --base-ts 格式不对：%r\n"
            "       必须是 \"YYYY-MM-DD HH:MM:SS\"，例如 \"2023-03-04 15:38:02\""
            % (s,))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--result", required=True,
                    help="pipeline_v2.py（或归并后的 merge_tracks.py 输出）的 result.json")
    ap.add_argument("--video", required=True)
    ap.add_argument("--outdir", required=True,
                    help="初成果目录（审核 GUI 的输入），路径不得含中文")
    ap.add_argument("--base-ts", default=None,
                    help="记录仪基准时间，格式 2023-03-04 15:38:02。"
                         "**视频换了就必须重给**，否则时间列沿用上一个视频的基准")
    ap.add_argument("--time-mode", default="clock", choices=["clock", "video"],
                    help="clock=记录仪时钟（需 --base-ts）；"
                         "video=视频内播放时间（视频无记录仪叠字时用）")
    a = ap.parse_args()

    global BASE_TS, TIME_MODE
    TIME_MODE = a.time_mode
    if a.base_ts:
        BASE_TS = parse_base_ts(a.base_ts)
    elif TIME_MODE == "clock":
        print("⚠ 未给 --base-ts，沿用内置基准时间 %s。换视频必须重新给，"
              "否则时间列是错的（该基准只对内置的第一个测试视频成立）"
              % BASE_TS.strftime("%Y-%m-%d %H:%M:%S"))

    check_outdir_ascii(a.outdir)
    res = json.load(open(a.result, encoding="utf-8"))
    poly = np.array(res["polygon"], dtype=np.int32)
    os.makedirs(a.outdir, exist_ok=True)
    evd = os.path.join(a.outdir, "证据图")
    os.makedirs(evd, exist_ok=True)

    cap = cv2.VideoCapture(a.video)
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    W = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    H = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

    rows = []
    print("=" * 96)
    print("检测结果：车牌 + 记录仪时间")
    print("=" * 96)
    print("%-4s %-22s %-10s %-13s %-9s %s"
          % ("#", ts_label(), "出现时长", "车牌号", "状态", "备注"))
    print("-" * 96)

    for i, r in enumerate(res["results"], 1):
        t0, t1 = r["t_first"], r["t_last"]
        ts0, ts1 = ts_at(t0), ts_at(t1)
        merged_src = r.get("src")   # 归并后的案例：记录合并了哪些原始轨迹
        # 证据图优先取流水线选出的"最佳帧"（车牌最清晰的那一帧）。
        # 取首帧是错的：车辆刚进入画面时常常只露车顶/侧面，车牌不可见。
        bf = r.get("best_frame")
        f0 = int(bf) if bf is not None else int(round(t0 * fps))
        cap.set(cv2.CAP_PROP_POS_FRAMES, f0)
        ok, frame = cap.read()
        img_name = ""
        early_sec = late_sec = ""
        if ok:
            vis = frame.copy()
            ov = vis.copy()
            cv2.fillPoly(ov, [poly], (0, 140, 255))
            vis = cv2.addWeighted(ov, 0.20, vis, 0.80, 0)
            cv2.polylines(vis, [poly], True, (0, 0, 255), 3)
            # 车辆框（该轨迹首帧）
            if r.get("best_box"):
                bx1, by1, bx2, by2 = [int(v) for v in r["best_box"]]
                cv2.rectangle(vis, (bx1, by1), (bx2, by2), (0, 255, 0), 3)
            # 车牌放大插图（左上）
            #
            # 要点：放大倍数要**自适应**——小车牌要放大更多才看得清，
            # 且必须保证插图本身够大（否则"放大了还是看不清"）。
            # 同时限制不超过画面左上区域，避免遮住主画面里的目标车。
            if r.get("best_box"):
                bx1, by1, bx2, by2 = [int(v) for v in r["best_box"]]
                pad = max(14, int((by2 - by1) * 0.5))
                cx1, cy1 = max(0, bx1 - pad), max(0, by1 - pad)
                cx2, cy2 = min(W, bx2 + pad), min(H, by2 + pad)
                if cx2 > cx1 and cy2 > cy1:
                    src = frame[cy1:cy2, cx1:cx2]
                    sh, sw = src.shape[:2]
                    # 目标：插图宽度不小于画面宽的 26%（车牌才看得清）
                    target_w = max(int(W * 0.26), 320)
                    target_w = min(target_w, int(W * 0.46))
                    up = max(3.0, target_w / max(sw, 1))
                    up = min(up, 10.0)                 # 过度放大会放大噪点
                    zoom = cv2.resize(src, None, fx=up, fy=up,
                                      interpolation=cv2.INTER_CUBIC)
                    zh, zw = zoom.shape[:2]
                    # 插图不得超过画面左上区（宽 46%、高 34%）
                    maxw, maxh = int(W * 0.46), int(H * 0.34)
                    if zw > maxw or zh > maxh:
                        sc = min(maxw / zw, maxh / zh)
                        zoom = cv2.resize(zoom, None, fx=sc, fy=sc,
                                          interpolation=cv2.INTER_AREA)
                        zh, zw = zoom.shape[:2]
                    if zw > 0 and zh > 0:
                        vis[10:10 + zh, 10:10 + zw] = zoom
                        cv2.rectangle(vis, (10, 10), (10 + zw, 10 + zh),
                                      (0, 255, 0), 2)
                        cv2.putText(vis, "\u8F66\u724C\u653E\u5927 %.1fx" % up,
                                    (16, 10 + zh - 8),
                                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 0), 4)
                        cv2.putText(vis, "\u8F66\u724C\u653E\u5927 %.1fx" % up,
                                    (16, 10 + zh - 8),
                                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)
            # 文字信息
            lines = [
                "#%d  %s" % (i, r["plate"] or "(未识别)"),
                "%s %s" % (ts_label(), ts0),
                "状态 %s" % r["status"],
                "应急车道重叠 %.2f" % r.get("max_overlap", 0),
                "证据帧 t=%.2fs (帧%d)" % (f0 / fps, f0),
            ]
            if merged_src:
                lines.append("合并轨迹 %s" % merged_src)
            y = H - 30 - 26 * (len(lines) - 1)
            for ln in lines:
                cv2.putText(vis, ln, (14, y), cv2.FONT_HERSHEY_SIMPLEX, 0.72,
                            (0, 0, 0), 5)
                cv2.putText(vis, ln, (14, y), cv2.FONT_HERSHEY_SIMPLEX, 0.72,
                            (0, 255, 255), 2)
                y += 26
            safe = (r["plate"] or "UNREADABLE").replace("/", "_")
            stamp = ts0.replace(":", "").replace(" ", "_").replace(".", "")
            img_name = "%02d_%s_%s.jpg" % (i, safe, stamp)
            wrote = imwrite_unicode(os.path.join(evd, img_name), vis)
            if not wrote:
                print("   !! 证据图写入失败: %s" % img_name)

            # 三张**纯原帧**（零标注）放进本案例自己的子目录：
            #     证据图\案例NN_车牌\原图.jpg / 证据帧2.jpg / 证据帧3.jpg
            #
            # 分工（这是全部「挑帧」逻辑所在）：
            #   - 挑哪三帧，在流水线里用整条轨迹的候选定好（pipeline_v2 / merge_tracks）；
            #   - 这里只按帧号把图取出来、按固定文件名落盘；
            #   - 审核程序不再做任何判断，只把**确认车辆**的那个子目录整个复制到成果目录。
            # 文件名固定（不带时间戳），审核程序才能不看内容就照抄；
            # 带 ROI 与放大插图的那张仍留在 证据图\ 下，仅供人眼判断，不进报告。
            #
            # CSV 里写的名字**必须**是磁盘上真实存在的：审核程序照名字取图，
            # 写了不存在的文件只是白复制一次（更糟的是让人以为材料是齐的）。
            safe = (r["plate"] or "UNREADABLE").replace("/", "_")
            case_dir_name = "案例%02d_%s" % (i, safe)
            case_dir = os.path.join(evd, case_dir_name)
            os.makedirs(case_dir, exist_ok=True)
            ev_written = ["", "", ""]

            if imwrite_unicode(os.path.join(case_dir, EVIDENCE_NAMES[0]), frame, 95):
                ev_written[0] = EVIDENCE_NAMES[0]
            else:
                print("   !! 原图写入失败: %s" % case_dir_name)

            # 最早附近帧、最晚附近帧（帧号已在流水线里定好）
            for slot, key in ((1, "early_frame"), (2, "late_frame")):
                bfx = r.get(key)
                if bfx is None:
                    continue
                fx = int(bfx)
                cap.set(cv2.CAP_PROP_POS_FRAMES, fx)
                okx, frame_x = cap.read()
                if not okx:
                    print("   !! 取帧失败（%s 帧%d）" % (key, fx))
                    continue
                if not imwrite_unicode(os.path.join(case_dir, EVIDENCE_NAMES[slot]),
                                       frame_x, 95):
                    print("   !! 写入失败: %s / %s" % (case_dir_name,
                                                       EVIDENCE_NAMES[slot]))
                    continue
                ev_written[slot] = EVIDENCE_NAMES[slot]
                if slot == 1:
                    early_sec = round(fx / fps, 3)
                else:
                    late_sec = round(fx / fps, 3)
            if not ev_written[1]:
                early_sec = ""
            if not ev_written[2]:
                late_sec = ""
        cap.set(cv2.CAP_PROP_POS_FRAMES, 0)

        dur = t1 - t0
        print("%-4d %-22s %-10s %-13s %-9s %s"
              % (i, ts0, "%.2fs" % dur, r["plate"] or "(未识别)",
                 r["status"], r["reason"]))
        rows.append({
            "序号": i,
            "时间_起": ts0,
            "时间_止": ts1,
            "时间类型": "视频内播放时间" if TIME_MODE == "video" else "记录仪时间",
            "视频内时间_起_秒": t0,
            "视频内时间_止_秒": t1,
            "出现时长_秒": round(dur, 2),
            "帧数": r["n_frames"],
            "车牌号": r["plate"] or "",
            "车牌置信度": r["plate_conf"],
            "状态": r["status"],
            "应急车道重叠": r.get("max_overlap", 0),
            "备注": r["reason"],
            # 证据图：带 ROI 与放大插图的**审核用**图（仅供人眼判断，不进报告）
            "证据图": img_name,
            # 材料清单：审核程序照抄这三个文件名 + 目录名，不做任何判断
            # （v0.3.0 起 C++ 侧全部按列名读取，缺列则自动少一张）
            "证据目录": case_dir_name,
            "证据图1": ev_written[0],
            "证据图2": ev_written[1],
            "证据图2_时刻_秒": early_sec,
            "证据图3": ev_written[2],
            "证据图3_时刻_秒": late_sec,
            "取帧说明": r.get("ev_note", ""),
        })
    cap.release()

    # CSV（UTF-8 BOM，便于 Excel 直接打开）
    cp = os.path.join(a.outdir, "检测结果.csv")
    with open(cp, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()) if rows else [])
        w.writeheader()
        w.writerows(rows)
    # JSON
    jp = os.path.join(a.outdir, "检测结果.json")
    meta = dict((res.get("run") or {}))
    m_meta = runmeta.collect(
        __file__, a, video=a.video, inputs={"result_in": a.result},
        extra={"time_mode": TIME_MODE,
               "base_timestamp": (BASE_TS.strftime("%Y-%m-%d %H:%M:%S")
                                  if TIME_MODE == "clock" else None),
               "n_vehicles": len(rows),
               "outputs": {"csv": cp, "json": jp, "evidence_dir": evd}})
    meta["outputs"] = m_meta
    meta["version"] = m_meta["version"]
    meta["script_chain"] = list(dict.fromkeys(
        list(meta.get("script_chain") or []) + [m_meta["script"]]))
    with open(jp, "w", encoding="utf-8") as fh:
        json.dump({"video": os.path.abspath(a.video),
                   "time_mode": TIME_MODE,
                   "base_timestamp": (BASE_TS.strftime("%Y-%m-%d %H:%M:%S")
                                      if TIME_MODE == "clock" else None),
                   "roi_source": res.get("roi"),
                   "n_vehicles": len(rows),
                   "results": rows,
                   "run": meta}, fh, ensure_ascii=False, indent=2)
    # 成果目录旁留一份元数据：审核时能直接看到这批成果用了哪个 ROI、哪组参数
    runmeta.write(os.path.join(a.outdir, "run_meta.json"), meta)

    n_conf = sum(1 for r in rows if r["状态"] == "CONFIRMED")
    n_susp = sum(1 for r in rows if r["状态"] == "SUSPECT")
    n_un = sum(1 for r in rows if r["状态"] == "UNREADABLE")
    print("\n" + "=" * 96)
    print("合计 %d 辆在应急车道内行驶：CONFIRMED %d / SUSPECT %d（需人工确认）/ UNREADABLE %d"
          % (len(rows), n_conf, n_susp, n_un))
    print("CSV  -> %s" % cp)
    print("JSON -> %s" % jp)
    print("证据图 -> %s（%d 张）" % (evd, len([f for f in os.listdir(evd)
                                              if os.path.isfile(os.path.join(evd, f))])))


if __name__ == "__main__":
    main()
