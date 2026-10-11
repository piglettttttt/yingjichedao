# 应急车道车牌取证（Emergency Lane Plate Evidence）

**当前版本：v0.1.1** ｜ 修改时间：2026-10-11 19:21 ｜ git：`master` @ `0895d9f`

> **v0.1.1 改了什么**：只做工程化完善，**未改任何检测/审核逻辑**。
> ① 修复指导文档 `ai_task_v0.1.0.md` §3.3 里跑不通的命令（缺 `--veh-jsonl`、`merge_tracks.py` 参数名写错）；
> ② 建立 git 仓库（此前无版本控制）；
> ③ 新增本 README、`requirements.txt`、`bin/README.md`；
> ④ **每次运行把 ROI 指纹与全部参数落盘**（`run_meta.json`），因为 ROI 必须逐视频人工重划，
>    不记下来就无法判断某份成果是用哪份 ROI、哪组参数跑出来的；
> ⑤ 补 Python 侧 `check_<func>` 回归测试（此前 Python 侧 0 测试）；
> ⑥ 新增一键脚本 `_reuse\run_pipeline.bat`、`_reuse\parse_roi.bat`、`_reuse\test_python.bat`。

## 这个项目做什么

给**行车记录仪视频**做**高速公路应急车道违法占用取证**：
自动找出占用应急车道的车辆、识别车牌、产出证据图与结果表，再经**人工审核**确认后，
生成一份可交付的图文违章报告。

产出形态是「**两半 + 一个解耦目录**」：

```
视频 ──► [Python 检测流水线] ──► 初成果目录 ──► [C 写的人工审核 GUI] ──► 违章检测报告.html
            自动检测              CSV+证据图          人工确认/修正
```

- **不追求全自动**：识别不可靠就标 `SUSPECT` / `UNREADABLE` 交人判断，**绝不硬猜车牌**。
- **不是车牌识别系统**：核心是「车辆优先 → 轨迹聚合 → 多帧证据 → 选帧」这套流程。
- 设计决定与踩坑记录见 [`ai_task_v0.1.0.md`](ai_task_v0.1.0.md)（交接文档，含用户原话与结论）。
  操作规程见 [`_reuse/使用规则.md`](_reuse/使用规则.md)。

## 目录结构

```
d:\Users\Lhh\Documents\dsh\yingjichedao\
├── README.md                  ← 本文件
├── requirements.txt           Python 依赖（从实际 .venv 冻结）
├── requirements-torch.txt     torch/torchvision 单独装（`+cpu` 只有官方索引有）
├── ai_task_v0.1.0.md          ★ 交接文档（现行）；v0.0.0 是最初《任务口述》
├── .gitignore
├── .venv\                     Python 3.12.10 运行环境（不入库，按 requirements.txt 重建）
├── bin\                       外部工具 ffmpeg/ffprobe/yt-dlp（不入库，见 bin\README.md）
├── yolo11n.pt                 车辆检测权重（YOLO11n，COCO 车辆类）
├── downloads\                 测试视频与历次成果（视频不入库；两份人工真值表入库）
│   ├── 目押记录.xlsx            视频一的人工目押真值
│   ├── xiangtang\             视频二（湘塘）成果：检测结果.csv/.json、证据图/、漏检比对.csv、参考结果.xlsx
│   ├── xt_v011\               v0.1.1 端到端实跑成果（含 run_meta.json 样例）
│   └── ax285e_test\           单案例测试集（粤AX285E），用于验证「审核 → 报告」链路
└── _reuse\                    ★ 主要工作区
    ├── PlateReview.cpp        审核 GUI 源码（C++，**必须保持纯 ASCII**）
    ├── test_logic.cpp         逻辑回归测试（27 项）
    ├── test_report.cpp        报告 + 确认车牌回归测试
    ├── build_c.bat            一键编译三个 exe
    ├── run_pipeline.bat       ★ 一键端到端检测
    ├── parse_roi.bat          ROI 解析（第一步）
    ├── test_python.bat        Python + C 全部回归测试
    ├── tests\test_pipeline.py 收集各脚本内联的 check_<func>
    ├── runmeta.py             ★ 运行元数据落盘（ROI 指纹 + 参数）
    ├── dist\                  编译产物 PlateReview.exe / test_logic.exe / test_report.exe
    ├── 使用规则.md              操作规程（八节，必读）
    ├── UI_风格规范.md           极简扁平风设计规范
    ├── roi\                   已标定的 ROI（每个视频一份）
    ├── onnx\                  车牌检测 + 识别 ONNX 模型（**含 .data 外置权重，缺了跑不了**）
    ├── ref_hw\                参考项目（高速公路应急车道违章检测 v8.0）
    ├── clpr\                  参考项目（车牌检测识别，权重未采用）
    ├── archive\               47 个探索期调试脚本（已归档，一般不用）
    └── run\                   中间产物与调试日志（不入库）
```

## 环境

| 项 | 值 |
|---|---|
| 平台 | Windows x64，**显示器 200% 缩放**（LOGPIXELSX=192，物理 3072×1920） |
| Python | 项目内 `.venv`（3.12.10）。调用一律用全路径 `D:\Users\Lhh\Documents\dsh\yingjichedao\.venv\Scripts\python.exe` |
| 关键包 | torch 2.14.1+cpu、ultralytics 8.4.174、hyperlpr3 0.1.3、opencv 5.0.0.93、onnxruntime、pandas、Pillow、openpyxl、PyInstaller |
| 编译器 | MSVC 2022 Community（`vcvarsall.bat x64`），仅审核 GUI 需要 |
| 网络 | 直连 github.com 不通；`api.github.com`、bilibili 可通。下 GitHub 资源用镜像前缀 `https://ghfast.top/<url>` |

重建环境：

```
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

> `requirements.txt` 第一行 `-r requirements-torch.txt` 会把 torch 一起装上，而
> `requirements-torch.txt` 里自带 `--index-url https://download.pytorch.org/whl/cpu`。
> **这一步不能省**：`torch==2.14.1+cpu` 的 `+cpu` 是本地版本号，默认 PyPI 上找不到，
> 直接 `pip install torch==2.14.1+cpu` 会报 `No matching distribution found`。

## 快速开始

### 1. 跑回归测试（改代码后必做）

```
_reuse\test_python.bat
```
期望：Python 三组 `ALL PASSED`；C 侧 `RESULT: 27 passed, 0 failed` 与 `RESULT: ALL PASSED`。

> ⚠ **别并发跑**：两个 C 测试 exe 都用**写死的**固定目录
> `C:\Users\Lhh\pr_test2` / `pr_test3`（`test_logic.cpp` / `test_report.cpp` 内），
> 同时跑两遍会互相删目录而卡死（实测踩到过）。一个一个来。

### 2. 处理一个视频

每次开工必须先向用户索取三样东西（`_reuse\使用规则.md` 第一节）：

| # | 索取的项 | 要求 |
|---|---|---|
| 1 | 视频源 | 待检测视频路径 |
| 2 | ROI 参考图片 | 用户在视频截图上用**红色**划出应急车道区域（画线或闭合区域） |
| 3 | 初成果存放路径 | **不得包含中文** |

然后：

```
rem 第 0 步：ROI 解析（每个视频都要重做）
_reuse\parse_roi.bat <标注截图> <视频> _reuse\roi\<名字> polygon
rem  → 打开 _reuse\roi\<名字>\roi_verify.jpg 确认叠加的 ROI 与预期一致

rem 第 1~4 步：一键跑完检测并出初成果
_reuse\run_pipeline.bat <视频> _reuse\roi\<名字>\roi.json <初成果目录> [记录仪基准时间]
```

`<初成果目录>` 就是审核 GUI 的输入。

### 3. 人工审核 → 报告

1. 双击 `_reuse\dist\PlateReview.exe`（没有就先跑 `_reuse\build_c.bat`）
2. 入口页粘贴**初成果目录**（路径不得含中文）→「进入」
3. 逐案例核对：**检测车牌**（只读）→ 需要改就改**确认车牌** → 点 `是` / `否` / `跳过`
4. 全部确认后点「完成报告」
5. 弹出「违法地点与行驶方向」页：填 **≤200 字**（必填）→「生成报告」
6. 成果写在 `<初成果目录>\人工审核结果\`：
   `人工审核结果.csv`（仅「是」的案例）、`违章检测报告.html`（证据图 base64 内嵌，单文件可分发）、每个确认车牌一个空文件夹

> **证据图为什么有两份**：审核界面那张带 ROI 与放大插图是**给人判断用的**；
> 报告是交出去的材料，证据必须是**未经加工的原始画面**（`证据图\原图\`）。
> 这是用户明确要求，别改。

### 4. 四步流水线（需要单独跑某一步时）

| 步骤 | 脚本 | 关键参数 |
|---|---|---|
| 0 | `parse_roi.py` | `--shot <标注截图> --video <mp4> --out <目录> --mode polygon` |
| 1 | `stage1_vehicles.py` | `--video --roi --out <veh.jsonl> --weights yolo11n.pt`（默认 `--start 0 --end 30 --fps 15`） |
| 2 | `pipeline_v2.py` | `--video --roi **--veh-jsonl <veh.jsonl>** --outdir <目录> --no-mask-detect --onnx onnx` |
| 3 | `merge_tracks.py` | `--result <result.json> **--veh-jsonl <veh.jsonl>** --out <result_merged.json> --time-tol-frames 12 --dist-max 1.5` |
| 4 | `make_outputs.py` | `--result <result_merged.json> --video <mp4> --outdir <初成果目录> --time-mode clock --base-ts "<记录仪基准时间>"` |

`run_pipeline.py` 是**早期的一次性整链版本**，已被上面四步取代；它现在主要作为**函数库**被
`pipeline_v2.py` 复用（`load_polygon` / `polygon_mask` / `plate_in_polygon` / `tighten_candidates` / `norm`）。

## 运行元数据（run_meta.json）

**ROI 必须逐视频人工重划**（不同视频应急车道位置不同，自动识别实测不可靠：一次偏右 210px，
一次拟合出斜率 -6.19 的荒谬线）。参数也随视频调整。所以每一步都在产物旁写一份元数据：

| 产物 | 元数据文件 | 记了什么 |
|---|---|---|
| ROI 目录 | `run_meta.json` | 标注截图指纹、视频区域映射比例 |
| `veh_*.jsonl` | `veh_*.jsonl.runmeta.json` | 本次用的 ROI 文件 **sha256**、ROI 多边形快照、全部参数 |
| `result.json` | `result.json.runmeta.json` | 上游元数据 + 本步参数（`result.json` 内也有 `run` 字段） |
| `result_merged.json` | `...runmeta.json` | 归并参数 + `script_chain`（谁跑了谁） |
| 初成果目录 | `run_meta.json` | 全链路参数、ROI 指纹、视频指纹（sha256）、时间基准 |

示例（`downloads\xt_v011\run_meta.json`）：能直接看出这批成果用的是
`roi_image50.json`（附 sha256 与多边形）、`--overlap-thresh 0.35`、`--high-conf 0.9` 等。
**视频与 ROI 用 sha256 而不是修改时间**：换机器重新拷贝后 mtime 必变，哈希不变。

## 高频坑（前三条务必先看）

1. **AV1 编码会让流水线慢约 20 倍**（实测 H.264 22.8s / AV1 >962s 未跑完）。
   处理任何视频前先看编码；`run_pipeline.bat` 的 [0/4] 步会检查并直接拦下 AV1。
   转码：`bin\ffmpeg.exe -y -i <视频> -c:v libx264 -preset veryfast -crf 18 -c:a copy <输出>_h264.mp4`
2. **`.bat` 与 `.cpp` 文件必须保持纯 ASCII**。cmd.exe 用 OEM 代码页（GBK）读 `.bat`，
   UTF-8 中文会把**命令本身**搞乱；`PlateReview.cpp` 同理（中文只能以 `\uXXXX` 写在宽字面量里，
   编译已加 `/utf-8` 兜底）。
3. **跨进程无法驱动这个 GUI 的输入框**：`SetWindowTextW` / `WM_SETTEXT` 对别的进程的 EDIT
   读回为空，`PostMessage(WM_CHAR)` 也进不去。输入类功能**只能靠人在界面上点验**。
4. **换视频必须重给 `--base-ts`**，否则时间列沿用内置基准（只对第一个测试视频成立）→ 时间是错的。
   视频无记录仪叠字时改用 `--time-mode video`（此时时间不得当作违法时间用）。
5. **`onnx\` 目录必须带上 `.data` 文件**（`plate_detect.onnx.data` 等外置权重），只拷 `.onnx` 跑不了。
6. 编译时 exe 不能正在运行，否则 `LNK1104`；`gdiplus.h` 之前必须先 `#include <objbase.h>`。

## 当前状态与已知问题

**已完成**：主链路跑通并交付到人工审核；视频二（湘塘）**0 漏检**；C 侧回归测试全通过；
v0.1.1 一键脚本端到端实跑成功（`exit 0`，14 条轨迹经归并 → **10 个案例**，
`粤ACH5795` 3 条并 1、`粤GD38897` 2 条并 1、`粤C29V19` 2 条并 1）。

产物样例：`downloads\xt_v011\`（`检测结果.csv` / `.json` / `证据图\` + `证据图\原图\` / `run_meta.json`）。

**已知未修**（详见 [`ai_task_v0.1.0.md`](ai_task_v0.1.0.md) 第 6 章）：

| # | 问题 | 说明 |
|---|---|---|
| 1 | ROI 归属判定偏严 | 用底边采样比例 `--overlap-thresh 0.35`，车辆压线时可能被算成 0 重叠而剔除 |
| 2 | 自车仪表台伪影 | 画面右边缘有本车仪表台，可能干扰检测 |
| 3 | 疑似误报 `WK0L1981` | 长宽比 2:1 异常，疑似把非车牌当车牌（实测仍在 `run\xt` 结果里） |
| 4 | 同车牌重复案例 | `run\xt\result.json` 里 `粤ACH5795` 出现 3 次、`粤SYR159` 2 次，说明该次**未跑归并**；用 `run_pipeline.bat` 跑会带上归并步 |
| 5 | 5 条轨迹未取到车牌 | 需调 `pipeline_v2.py` 参数 |
| 6 | 未做：剪裁 30 秒违章视频片段 | 参考项目 v8.0 有此功能，用户未指示 |
| 7 | 未做：第二张独立证据帧 | 原《任务口述》要求两张，当前每案例 1 张（当时「先跑通链路」的简化） |
| 8 | 无数据合规约定 | 原始视频/证据图的保存期限、分发范围未成文 |

## 版本与交接

- 指导文档：`ai_task_vX.Y.Z.md`，记录该轮迭代开始前的要求、迭代中的沟通、迭代后的总结。
  接手先读 v0.1.0 的第 0 章「接手须知」与第 2 章「当前成果与关键决定」，再读第 6 章「未竟事项」。
- 本仓库已纳入 git（`master` 分支）。提交信息格式：`vX.Y.Z 简短描述`。
- 版本号约定：主版本=形态/架构变化，次版本=功能新增，补丁版本=修复与小改。
- **git 里有什么 / 没有什么**：代码、文档、ONNX 模型（3.5 MB）、`yolo11n.pt`（5.6 MB）、
  人工比对真值表（xlsx）、`xt_v011` 的 CSV/JSON/`run_meta.json` 已入库；
  视频、`.venv`（1.3 GB）、`bin\`（221 MB）、编译产物 exe、证据图、`_reuse\run\` 不入库
  （规则见 [`.gitignore`](.gitignore)，缺失件按本 README 与 `bin\README.md` 重建）。
  仓库 `.git` 约 7.5 MB。
