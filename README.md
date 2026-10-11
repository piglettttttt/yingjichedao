# 应急车道车牌取证（Emergency Lane Plate Evidence）

**当前版本：v0.3.0** ｜ 修改时间：2026-10-11 ｜ git 分支 `master`（最新提交见 `git log`）

> **v0.3.0 改了什么**：证据链重做，检测逻辑未动。
> ① **删除** 30 秒违章视频片段剪裁（用户要求删掉，代码与报告片段表全部移除）；
> ② **证据照片增至三张**：原图（置信度最高帧）+ 最早附近帧 + 最晚附近帧。
>    后两张在「可辨识帧」里取最早/最晚，但**不取绝对首帧/末帧**（车辆刚进画面或
>    快出画面时车牌常常看不清，往中间退一帧）；帧不够如实少给，绝不拿同一帧凑数；
> ③ **材料全在初处理阶段备齐**：三张帧写进 `证据图\案例NN_车牌\`，CSV 给出材料清单；
> ④ **审核 exe 只做筛选 + 生成报告**，删掉挑帧/猜文件名/回退逻辑，保持轻量（约 205 KB）；
> ⑤ 报告出两份：自包含版（base64，可分发）与**轻量版**（相对链接，约 5 KB）。
>
> **v0.2.0**（已被 v0.3.0 取代）：曾实现「第二张独立证据帧 + 30 秒违章片段剪裁」，
> 片段功能按用户要求删除，第二证据帧演进为三张证据帧。
>
> **v0.1.1**（工程化，未改检测/审核逻辑）：修文档 §3.3 跑不通的命令、建 git 仓库、
> 新增本 README / requirements / run_meta.json 运行元数据 / Python 回归测试 / 一键脚本。

## 这个项目做什么

> ### ⚠️ 只能在 Windows 上跑
>
> **人工审核程序是 Windows 专用**：它是用 C++（纯 Win32 + GDI+）编译出来的
> `PlateReview.exe`，**macOS / Linux 既不能运行也不能编译**。
> Python 检测流水线理论上跨平台，但整条链路（`.bat` 脚本、路径约定、审核程序）
> 都是按 Windows 设计的，**本项目只支持 Windows x64**。
> 换机器时不需要装 Visual Studio——`_reuse\dist\PlateReview.exe` 已在仓库里，clone 即可用。

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
│   ├── ax285e_test\           单案例测试集（由 make_test_case.py 生成，给 test_report 用）
│   ├── xiangtang\             视频二旧成果（v0.1.0 格式，保留作对照）
│   ├── xiangtang_new\        视频二完整 30 秒成果（v0.3.0 格式，含证据帧目录）
│   ├── xt_v021\               8 秒短跑成果（调链路用，含证据帧目录）
│   └── ax_fixture_src\        fixture 来源成果
└── _reuse\                    ★ 主要工作区
    ├── PlateReview.cpp        审核 GUI 源码（C++，**必须保持纯 ASCII**）
    ├── test_logic.cpp         逻辑回归测试（40 项）
    ├── test_report.cpp        报告 + 确认车牌回归测试
    ├── test_package.cpp       在真实产出上跑「生成报告」全链路
    ├── build_c.bat            一键编译（1 个 GUI + 3 个测试 exe）
    ├── run_pipeline.bat       ★ 一键端到端检测
    ├── parse_roi.bat          ROI 解析（第一步）
    ├── test_python.bat        Python + C 全部回归测试
    ├── tests\                 测试脚本（收集 check_<func>、文本卫生检查、报告链接检查）
    ├── runmeta.py             ★ 运行元数据落盘（ROI 指纹 + 参数）
    ├── dist\                  ★ **审核程序就在这**：PlateReview.exe（205 KB，双击即用）
    │                          + test_logic.exe / test_report.exe / test_package.exe
    │                          + README.md（界面说明书）。**exe 已入库**，clone 即可用
    ├── 使用规则.md              操作规程（八节，必读）
    ├── UI_风格规范.md           极简扁平风设计规范
    ├── roi\                   已标定的 ROI（每个视频一份）
    ├── onnx\                  车牌检测 + 识别 ONNX 模型（**含 .data 外置权重，缺了跑不了**）
    ├── ref_hw\                参考项目（高速公路应急车道违章检测 v8.0）
    ├── clpr\                  参考项目（车牌检测识别，权重未采用）
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

## 换一台电脑 clone 之后，要能跑需要做 5 件事

**前提：必须是 Windows x64**（见开头说明，审核程序只能在 Windows 跑）。

| # | 做什么 | 具体命令 / 说明 | 能不能省 |
|---|---|---|---|
| **1** | **装 Python 3.12，建虚拟环境装依赖** | `python -m venv .venv`<br>`.venv\Scripts\python.exe -m pip install -r requirements.txt` | 不能省。`.venv` 有 1.3 GB，不在仓库里 |
| **2** | **补 `bin\` 里的三个外部工具** | ffmpeg / ffprobe / yt-dlp，版本与下载地址见 [`bin\README.md`](bin/README.md)（约 221 MB，仓库不收二进制） | 不能省。缺了 `run_pipeline.bat` 在 [0/4] 编码检查就失败 |
| **3** | **确认权重与模型在位** | `yolo11n.pt` 与 `_reuse\onnx\` 的 4 个文件（含 `.onnx.data`）**已在仓库里**，clone 就有 | 不用做，但值得核一眼 |
| **4** | **审核程序 `PlateReview.exe`** | **已在仓库里**（`_reuse\dist\`），clone 即可双击运行 | 不用做。只有改了 C++ 源码才需要装 VS2022 跑 `build_c.bat` 重编 |
| **5** | **自备测试素材** | 一个行车记录仪视频 + 一张在上面用**红色**划出应急车道的截图；再准备一个**不含中文**的初成果目录 | 不能省。视频与截图不入库 |

**验证装好了**（约 1 分钟）：

```
_reuse\test_python.bat          rem 文本卫生 + Python 7 组 + C 侧三个测试程序
```

全绿就说明环境齐了（`ALL TESTS PASSED`）。其中 `test_report` 需要
`downloads\ax285e_test` fixture，缺失时会 SKIP 而不是报错。

> **路径提醒**：项目里大量命令写的是原机器的绝对路径
> `D:\Users\Lhh\Documents\dsh\yingjichedao`（例如 `.bat`、`README` 的示例、
> `make_test_case.py` 里的 `SRC`/`DST`）。换电脑或换目录时，这些地方要改成你的实际路径；
> 日常使用只需跑 `.bat`，它们用 `%~dp0` 自动定位，不受影响。

## 快速开始

### 1. 跑回归测试（改代码后必做）

```
_reuse\test_python.bat
```
期望：Python 四组 `ALL PASSED`；C 侧 `RESULT: 50 passed, 0 failed` 与 `RESULT: ALL PASSED`。

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

**分工原则（重要，别把职责挪回 exe）**：所有**材料**都在第一步 Python 里备齐
（挑帧、取帧、落盘、命名），审核 exe **只做两件事**：筛选 + 生成报告。
这样 exe 不含任何图像处理/选帧/文件猜测逻辑，轻量且职责单一。

1. 双击 `_reuse\dist\PlateReview.exe`（**已在仓库里，无需编译**；只有改了 C++ 源码才需要
   跑 `_reuse\build_c.bat` 重编，那需要 VS2022）
2. 入口页粘贴**初成果目录**（路径不得含中文）→「进入」
3. 逐案例核对：**检测车牌**（只读）→ 需要改就改**确认车牌** → 点 `是` / `否` / `跳过`
   （界面里显示的仍是带 ROI 与放大插图的审核图，仅供人眼判断）
4. 全部确认后点「完成报告」
5. 弹出「违法地点与行驶方向」页：填 **≤200 字**（必填）→「生成报告」
6. 成果写在 `<初成果目录>\人工审核结果\`：

```
人工审核结果\
├── 人工审核结果.csv              仅「是」的案例，附材料清单
├── 违章检测报告.html             ★ 自包含：证据图 base64 内嵌，单文件可分发（几 MB）
├── 违章检测报告_轻量版.html      ★ 只有几 KB，图片用相对链接（同一目录下浏览用）
├── 粤ACH5795\
│   ├── 01_原图.jpg               置信度最高那一帧（报告正文证据）
│   ├── 01_证据帧2.jpg            最早附近帧
│   └── 01_证据帧3.jpg            最晚附近帧
└── 粤Q6906T\
    └── 02_原图.jpg               该案例只有一帧可用，就只归档一帧
```

> **帧是「复制」不是「移动」**：第一步的材料要保持原样，才能重复审核、反复出报告。
>
> **三张证据帧的来路**（规则是用户定的，改之前先问）：
> ① `原图` = 置信度最高的那一帧；② `证据帧2` = **最早附近**帧；③ `证据帧3` = **最晚附近**帧。
> 后两张**不取绝对首帧/末帧**（车辆刚进画面或快出画面时车牌常常看不清），
> 而是在「可辨识帧」（车牌框面积 ≥ 400 px²）里往中间退一帧。
> 帧数不够时如实少给，**绝不拿同一帧凑三张**（CSV 里对应的名字会是空的）。
>
> **两份报告**：`违章检测报告.html` 自包含、可单文件分发（内嵌图，几 MB）；
> `违章检测报告_轻量版.html` 只有几 KB、图片走相对链接，适合本机反复打开看。
> 要交给别人时用前者，别把后者单独拷走（会全是破图）。
>
> **审核图不进报告**：界面那张带 ROI 与放大插图是**给人判断用的**；
> 报告是交出去的材料，证据必须是**未经加工的原始画面**。

### 3.5 材料由谁准备（v0.3.0 起的硬分工）

| 环节 | 谁做 | 说明 |
|---|---|---|
| 挑三帧（最早附近/置信最高/最晚附近） | Python | `pipeline_v2.py` 选，`merge_tracks.py` 归并后**按合并的候选池重选**（候选变多了，帧也该跟着变） |
| 取帧落盘 + 固定命名 + CSV 材料清单 | Python（`make_outputs.py`） | 写进 `证据图\案例NN_车牌\` 下的 `原图.jpg` / `证据帧2.jpg` / `证据帧3.jpg`；CSV 增列 `证据目录` `证据图1/2/3`（写的是**真实存在**的文件名） |
| 复制确认车辆的材料 + 生成报告 | C++（`PlateReview.cpp`，点「生成报告」时） | 照 CSV 的名字复制，**不挑帧、不猜名字、不做回退** |

> 为什么不让 exe 也参与挑帧：那会出现**两个地方决定证据集**，而它俩迟早不一致
> （文件夹里一套、报告里另一套）。选帧需要整条轨迹的候选信息，本来就只有 Python 有。
>
> C++ 侧曾经为了「兼容旧 CSV」写过按前缀猜文件名、找不到就回退的代码，已删除：
> 材料清单现在由 CSV 明写，猜错只会静默少图。
> 一条仍然保留的兜底：若 CSV 里某列**整列缺失**（v0.3.0 之前的旧成果），
> 第一张证据帧的名字按惯例取 `原图.jpg`。

### 4. 四步流水线（需要单独跑某一步时）

| 步骤 | 脚本 | 关键参数 |
|---|---|---|
| 0 | `parse_roi.py` | `--shot <标注截图> --video <mp4> --out <目录> --mode polygon` |
| 1 | `stage1_vehicles.py` | `--video --roi --out <veh.jsonl> --weights yolo11n.pt`（`--start 0 --end 30 --fps 15`） |
| 2 | `pipeline_v2.py` | `--video --roi **--veh-jsonl <veh.jsonl>** --outdir <目录> --no-mask-detect --onnx onnx` |
| 3 | `merge_tracks.py` | `--result <result.json> **--veh-jsonl <veh.jsonl>** --out <result_merged.json> --time-tol-frames 12 --dist-max 1.5` |
| 4 | `make_outputs.py` | `--result <result_merged.json> --video <mp4> --outdir <初成果目录> --time-mode clock --base-ts "<记录仪基准时间>"` |

> 调链路时**先用几秒**：`run_pipeline.bat` 的第 5 个参数是分析时长（默认 30 秒），
> 例如末尾加 `8` 就只跑前 8 秒，整条链路（含证据帧与报告）完全一样，但快得多。
> 交付前记得把时长调到需要的值，并核对 `run_meta.json` 里记的确实是期望值。

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

## 测试

```
_reuse\test_python.bat                      rem Python + C 基础回归
set PKG=downloads\xt_v021
_reuse\test_python.bat                      rem 追加「生成报告」端到端（归档证据 + 两份报告）
```

| 测试 | 覆盖 | 期望 |
|---|---|---|
| `tests\check_text_hygiene.py` | **1 秒级机械检查**：`.bat` 与 `PlateReview.cpp` 必须纯 ASCII、`.bat` 必须 CRLF | 通过 |
| `tests\test_pipeline.py` → 7 组 `check_<func>` | `runmeta`（哈希/ROI 告警/元数据）、`run_pipeline`（规范化/候选收紧/ROI 判定）、`pipeline_v2`（**三张证据帧规则**）、`merge_tracks`（归并后按合并候选池重挑）、`make_outputs`（`--base-ts` 与目录 ASCII 校验）、`evidence`（**材料齐备**：CSV 点名的文件都在、是完整 JPEG、三张不重复）、`stage1_vehicles` | 7 组 `ALL PASSED` |
| `dist\test_logic.exe` | 车牌格式、CJK 路径、CSV 解析（含材料清单列）、报告只写「是」、**三张证据帧的复制与计数器** | `40 passed, 0 failed` |
| `dist\test_report.exe` | 报告生成、确认车牌不被覆盖、内嵌图、**轻量版走链接**、CSS 无阴影/渐变 | `ALL PASSED` |
| `dist\test_package.exe`（需 `PKG`） | 用**真实产出**验「生成报告」：读材料清单、按车牌归档三张帧、两份报告 | `ALL PASSED` |
| `tests\check_report_links.py` | 轻量版报告的图片链接必须解析到真实文件、体积必须像轻量版 | 缺失 0 个 |

> ⚠ **别并发跑**：`test_logic.exe` / `test_report.exe` 用**写死的**固定目录
> `C:\Users\Lhh\pr_test2` / `pr_test3`，同时跑两遍会互相删目录而卡死（实测踩到过）。
> ⚠ `test_package.exe` 会**就地重生成** `<PKG>\人工审核结果\`，别指向还要保留的成果。

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
一键脚本端到端实跑成功（`exit 0`，14 条轨迹经归并 → **10 个案例**，
`粤ACH5795` 3 条并 1、`粤GD38897` 2 条并 1、`粤C29V19` 2 条并 1）；
v0.2.0 证据装包在真实产出上端到端通过（10/10 案例归档原图 + 剪出片段，
8/10 案例取到第二证据帧，2 个如实留空）。

产物样例：`downloads\xt_v020\`（检测产出 + `人工审核结果\` 下的按车牌归档证据）；
`downloads\xt_v011\`（v0.1.1 的检测产出，含 `run_meta.json`）。

**已知未修**（详见 [`ai_task_v0.1.0.md`](ai_task_v0.1.0.md) 第 6 章）：

| # | 问题 | 说明 |
|---|---|---|
| 1 | ROI 归属判定偏严 | 用底边采样比例 `--overlap-thresh 0.35`，车辆压线时可能被算成 0 重叠而剔除 |
| 2 | 自车仪表台伪影 | 画面右边缘有本车仪表台，可能干扰检测 |
| 3 | 疑似误报 `WK0L1981` | 长宽比 2:1 异常，疑似把非车牌当车牌。**v0.2.0 实跑里它没再出现**（归并步生效后 10 个案例中无此项），但规则未改，仍可能复发 |
| 4 | 同车牌重复案例 | `run\xt\result.json` 里 `粤ACH5795` 出现 3 次、`粤SYR159` 2 次，说明该次**未跑归并**；用 `run_pipeline.bat` 跑会带上归并步 |
| 5 | 5 条轨迹未取到车牌 | 需调 `pipeline_v2.py` 参数 |
| 6 | 2 个案例取不到第二/三张证据帧 | v0.3.0 实测 10 个案例里 7 个给满三张；`桂K11981` 与第 7 号 `粤C29V19` 只有一帧可辨识（如实只出原图），`粤SYR159` 出两张。想提高覆盖率需放宽 400px² 面积门槛或「不取绝对首/末帧」这条规则——那会削弱证据的独立性，待定 |
| 7 | 双份证据帧副本 | 审核程序把材料**复制**到 `人工审核结果\<车牌>\`，所以磁盘上同一帧存两份（每张约 0.5 MB）。这是「保留可重复审核」的代价；若要省空间可改成移动，但目录就不能二次出报告了 |
| 8 | 无数据合规约定 | 原始视频/证据图/视频片段的保存期限、分发范围未成文 |

## 版本与交接

- 指导文档：`ai_task_vX.Y.Z.md`，记录该轮迭代开始前的要求、迭代中的沟通、迭代后的总结。
  接手先读 v0.1.0 的第 0 章「接手须知」与第 2 章「当前成果与关键决定」，再读第 6 章「未竟事项」。
- 本仓库已纳入 git（`master` 分支）。提交信息格式：`vX.Y.Z 简短描述`。
- 版本号约定：主版本=形态/架构变化，次版本=功能新增，补丁版本=修复与小改。
- **git 里有什么 / 没有什么**（换机器 clone 前先看这里，详细步骤见
  「[换一台电脑 clone 之后，要能跑需要做 5 件事](#换一台电脑-clone-之后要能跑需要做-5-件事)」）：
  - **入库**：源码、`.bat`、文档、ONNX 模型（3.5 MB）、`yolo11n.pt`（5.4 MB）、
    人工比对真值表（xlsx）、几个成果目录的 CSV/JSON/`run_meta.json`、
    **`_reuse\dist\` 的 4 个 exe**（审核程序是刚需，clone 即可用）。
  - **不入库**：视频、`.venv`（1.3 GB）、`bin\`（221 MB）、证据图与审核产物、
    `_reuse\run\`（中间产物）、`_reuse\clpr` 的权重。
    规则见 [`.gitignore`](.gitignore)，缺失件按本 README 与 `bin\README.md` 补。
  - 仓库本体约 9.6 MB（110 个文件）。
