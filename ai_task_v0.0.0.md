# 用户指令

初始化环节：
- 在下方章节`# 任务口述`中，确认用户指定的当前工作目录 `d:\Users\Lhh\Documents\dsh\yingjichedao`，未指定时默认使用当前目录
- 尝试在 `d:\Users\Lhh\Documents\dsh\yingjichedao` 里寻找格式为 `ai_task_vX.Y.Z.md`
  - 版本号 `vX.Y.Z` 为主版本、次版本以及补丁版本。无版本时，默认为`v0.0.0`
  - 指导文件 `ai_task_<版本号>.md` 记录了对应版本号的在迭代开始前的要求、迭代中与用户的沟通、以及迭代后的总结
- 若找到多个指导文件，则阅读每个版本的首发版，以及当前版本的全部补丁版，了解任务以及改动历程

行动环节：
- 阅读下方章节`# 任务口述`，判断改动程度，拟定新的版本号，并创建对应的分支
- 阅读当前工作目录的`README.md`，便于复现项目、复用代码
- 将 `# 任务口述` 转为无歧义的可执行说明；对模糊、矛盾或明显错误处给出修改方案，供用户选，将用户的互动输入整理后，追加写入 `# 用户反馈`
- 必须先与用户进行充分讨论后执行，与用户的互动结论，写入文末 `# 任务复述` 后，执行，尽量复用现有代码
- 项目应符合轻量化、精简、可复用的原则。提倡和允许你简化任务口述的部分流程，但要与用户讨论。
- 若通过文件或者函数定义的内容过于常见，则不创建。注释或者解释中过于常见的内容，可省去
- 核心函数须覆盖单元测试，主流程须通过分钟级冒烟测试。测试函数以 `check_<func>` 为前缀，内联于被测函数下方

收尾环节：
- 更新 `README.md`，描述项目结构，并在开头记录当前版本号及修改时间
- 将冒烟测试的结果，追加写入 `# 冒烟测试`，若用户正式运行了或者提供了正式运行结果，则追加写入 `# 点火运行`
- 运行 `git commit -m "vX.Y.Z 简短描述更新内容"` 提交代码


---

# 任务口述
1. 项目目标

开发一个可复用的本地 Skill，用于处理行车记录仪 MP4 视频，在指定时间范围内：

检测道路上的车辆。
判断车辆是否进入并持续行驶于右侧应急车道区域。
为每一辆疑似违法车辆建立持续 Track ID。
在该车辆整个出现期间寻找车牌位置。
从多个视频帧中提取车牌候选。
对多个候选帧进行质量评分、OCR 和结果融合。
输出：
车辆首次进入应急车道时间
车辆离开应急车道时间
车牌号
车牌识别置信度
车牌最佳证据帧
第二张独立证据帧
保存两张能够清楚显示车牌的证据图片。
对无法可靠确认的车牌标记为 UNCERTAIN，绝不强行猜测。
2. 最关键的技术问题：模糊车牌怎么解决

这是整个项目最重要的地方。

不推荐的方案

不要：

视频
↓
每隔 1 秒截一帧
↓
OCR
↓
得到车牌

因为行车记录仪视频中的车牌可能存在：

运动拖影
JPEG/H.264 压缩
夜间噪声
远距离导致像素不足
反光
车牌倾斜
前车遮挡
车辆震动
摄像机自身运动
某些帧刚好被灯光覆盖

因此可能出现：

Frame 100:
A8?31B

Frame 101:
A83?1B

Frame 102:
A83?1B

Frame 103:
A8331B

Frame 104:
A8331B

真正应该做的是：

同一车辆 Track
      │
      ├── Frame 100 → OCR → A8?31B
      ├── Frame 101 → OCR → A83?1B
      ├── Frame 102 → OCR → A83?1B
      ├── Frame 103 → OCR → A8331B
      ├── Frame 104 → OCR → A8331B
      ├── Frame 105 → OCR → A8331B
      │
      ▼
多个候选结果聚类
      │
      ▼
加权字符投票
      │
      ▼
A8331B
      │
      ▼
选择最清晰的 2 帧作为证据

这应该成为 Skill 的核心机制。

3. 总体架构
                 MP4
                  │
                  ▼
          Video Reader
        OpenCV / FFmpeg
                  │
                  ▼
           时间范围裁剪
                  │
                  ▼
          Vehicle Detection
                  │
                  ▼
          Multi Object Tracking
              ByteTrack
             / BoT-SORT
                  │
                  ▼
       ┌──────────────────────┐
       │ 判断车辆是否位于      │
       │ 应急车道 ROI          │
       └──────────┬───────────┘
                  │
           是 ────┘
                  │
                  ▼
             建立 Track
                  │
                  ▼
         Plate Detection
                  │
                  ▼
       车牌透视校正 / Crop
                  │
                  ▼
       ┌──────────────────────┐
       │ 多帧车牌候选缓冲区    │
       │                      │
       │ crop + 时间 + 质量   │
       │ OCR + confidence     │
       └──────────┬───────────┘
                  │
                  ▼
       OCR 多帧结果融合
                  │
                  ▼
       车牌最终候选结果
                  │
            ┌─────┴─────┐
            │           │
          高置信       低置信
            │           │
            ▼           ▼
        输出结果      UNCERTAIN
            │
            ▼
       选择最佳两张证据
            │
            ▼
     JSON / CSV + JPG
4. 不应该让 DeepSeek 直接负责所有视觉识别

这里建议把职责分开。

DeepSeek

主要负责：

Skill 编排
Python 代码生成/修改
参数调整
日志分析
OCR 候选融合逻辑
异常情况分析
根据检测结果判断下一步处理策略
CV 模型

负责：

车辆检测
车辆追踪
车牌检测
OCR

也就是说：

DeepSeek
    ↓
调用视觉工具
    ↓
YOLO / Tracker / Plate Detector / OCR
    ↓
返回结构化结果
    ↓
DeepSeek 做逻辑处理

不要让 DeepSeek 看着整段 MP4，然后试图“自己读所有车牌”。

尤其是车牌只有十几到几十像素的时候，大模型视觉能力很容易出现“看起来像是这个号码”的幻觉。

5. 第一层：视频处理

视频输入：

input.mp4

参数：

video: input.mp4

start_time: "00:15:30"
end_time: "00:18:00"

timezone: local

视频读取可以使用 OpenCV 的 VideoCapture，它本身支持打开视频文件并逐帧读取。

FFmpeg 可以用于更加高效的时间区间截取和帧率控制。其 fps filter 支持从源视频按指定 FPS 进行抽帧。

6. 不要全程使用最高采样率

视频分析可以分两阶段。

第一阶段：粗检测

例如：

视频 30 FPS

粗检测：
5 FPS

目的只是：

找出“哪些时间段出现了可能的应急车道车辆”。

这样计算量会降低很多。

第二阶段：精查

例如：

发现 Track 123
时间：
00:15:42.3 - 00:15:48.7

只分析这个时间窗口：

10~20 FPS

甚至可以直接处理原始 FPS。

也就是说：

30 FPS 视频
       ↓
5 FPS 全局搜索
       ↓
发现异常车辆
       ↓
10~30 FPS 局部重新扫描
       ↓
车牌检测
       ↓
多帧 OCR

这是比“全程 30 FPS + OCR”更加合理的架构。

7. 第二层：车辆检测 + Tracking

车辆检测建议采用 YOLO 系列模型。

关键不是“检测到车”，而是：

必须让同一辆车在连续视频帧中拥有稳定的 Track ID。

例如：

Frame 100 → Car #17
Frame 101 → Car #17
Frame 102 → Car #17
...
Frame 150 → Car #17

Ultralytics 当前提供 ByteTrack、BoT-SORT、OC-SORT、Deep OC-SORT、TrackTrack 等多种 Tracker。官方文档中，ByteTrack适合作为简单高效基线，而 BoT-SORT 对移动相机、相机运动补偿和 ReID 场景更有优势。

对于行车记录仪，我建议：

第一版：
BoT-SORT

备选：
ByteTrack

原因是：

行车记录仪
    ↓
摄像机本身一直在移动
    ↓
道路整体发生画面运动
    ↓
BoT-SORT 的 Camera Motion Compensation 更有意义
8. 第三层：如何判断“应急车道”

这里存在一个非常重要的问题：

不能简单地认为“画面最右边 = 应急车道”。

因为：

画面右边可能是普通行车道
弯道情况下几何关系会变化
出入口会改变道路结构
摄像机可能不是正对道路中心
不同道路的应急车道位置不同

因此 Skill 应支持两种模式。

Mode A：人工配置 ROI

这是 MVP 最推荐的。

例如：

emergency_lane:
  type: polygon

  points:
    - [1180, 350]
    - [1910, 420]
    - [1910, 1080]
    - [1050, 1080]

实际运行：

车辆 bbox
     ↓
取车辆底部中心点
     ↓
point-in-polygon
     ↓
是否位于 emergency_lane polygon

这是最稳妥的。

Mode B：自动道路/车道识别

以后再增加：

Lane Detection
       ↓
Road Boundary
       ↓
Rightmost Lane
       ↓
Emergency Shoulder

这是第二阶段功能。

MVP 不建议一开始就自动判断应急车道，否则会把项目从：

“车牌识别 Skill”

变成：

“复杂道路场景理解系统”。

9. 判断“行驶在应急车道”，而不是“短暂压线”

这个规则也很重要。

例如：

车辆 Track #17

00:15:42.1
进入 ROI

00:15:42.3
仍在 ROI

00:15:42.5
仍在 ROI

...

00:15:48.1
离开 ROI

可以定义：

violation:
  min_duration: 1.0
  min_track_frames: 5

因此：

只是瞬间压线
        ↓
不记录

持续进入应急车道
        ↓
记录
10. 第四层：车牌检测

这里不要直接对整个画面做 OCR。

应该：

完整视频帧
     ↓
车辆 Track
     ↓
vehicle bbox
     ↓
vehicle crop
     ↓
plate detector
     ↓
plate bbox
     ↓
plate crop

这样能极大降低 OCR 搜索空间。

11. OCR 选择

OCR 建议采用 PaddleOCR 体系作为第一版候选。

目前官方 PaddleOCR 文档中的 PP-OCRv5 是新一代 OCR 方案，支持多种文本类型，并提供 server/mobile 等不同模型；官方文档也提供了文本检测和文本识别的独立模块。

但这里有一个非常重要的区别：

通用 OCR ≠ 专门的车牌 OCR。

所以推荐：

Vehicle Detection
        ↓
Plate Detection
        ↓
Plate Crop
        ↓
Plate-specific preprocessing
        ↓
OCR

第一版可以使用：

YOLO/custom plate detector
+
PaddleOCR

以后如果有大量自己的行车记录仪样本，再训练：

自定义 License Plate Detector
+
车牌专用 Recognition Model
12. 真正解决模糊问题的核心：Track-Level Evidence Buffer

每一个车辆 Track 都维护一个缓存。

例如：

track = {
    "track_id": 17,

    "first_seen": 952.3,
    "last_seen": 958.7,

    "plate_candidates": [
        {
            "timestamp": 953.10,
            "frame_id": 28593,
            "text": "粤B8?31B",
            "ocr_conf": 0.61,
            "sharpness": 72.4,
            "plate_area": 921,
            "brightness": 0.73,
            "blur": 0.31
        },
        ...
    ]
}

也就是说：

不是一个 Frame 对应一个车牌，而是一个 Track 对应一个“车牌证据集合”。

这是整个 Skill 最重要的数据结构。

13. 每一个候选车牌都进行 Quality Score

不能单纯使用 OCR confidence。

建议：

Quality Score
=
OCR Confidence
×
Sharpness
×
Plate Area
×
Brightness
×
Geometry Score

例如：

Frame A

OCR        0.88
Sharpness  0.92
Area       0.71
Brightness 0.90

最终：
0.88 × 0.92 × 0.71 × 0.90
= 0.516

而：

Frame B

OCR        0.81
Sharpness  0.98
Area       0.94
Brightness 0.95

= 0.704

虽然 Frame B OCR 原始置信度更低，但：

Frame B 反而更适合作为最终证据。

14. 车牌清晰度评分

建议至少计算：

1. Laplacian Variance

用于估计运动模糊 / 清晰度。

cv2.Laplacian(gray, cv2.CV_64F).var()
2. Plate Area

车牌面积越大，一般越适合作为证据。

plate_width × plate_height
3. Aspect Ratio

例如：

width / height

过滤明显错误的 plate detection。

4. Brightness

检测：

过暗
过曝
反光
5. Perspective Quality

车牌如果严重倾斜：

原始：
/-------/

应该先：

Perspective Transform
        ↓
标准矩形
        ↓
OCR
15. 不要直接“超分辨率后读号码”

这一点我特别建议在 Skill 里写成硬规则。

例如：

原图：

粤B8?31B

经过 AI Super Resolution 后可能变成：

粤B8331B

看起来“更清楚”了。

但：

超分辨率不能凭空恢复原始视频中不存在的信息。

模型有可能只是“猜出了一个看起来合理的字符”。

因此：

原始帧

用于事实识别。

图像增强

用于辅助 OCR 和视觉阅读。

Super Resolution

用于视觉展示，可以保存。

但是最终识别必须满足：

Original frame evidence
+
Multi-frame consistency

不能仅因为 SR 图像显示得更漂亮就确认车牌。

16. 多帧 OCR 怎么融合

这是我最建议 DeepSeek 实现的部分。

例如一个 Track 得到：

Frame 01:
A8?31B   conf=0.72

Frame 02:
A8331B   conf=0.85

Frame 03:
A833IB   conf=0.78

Frame 04:
A8331B   conf=0.90

Frame 05:
A?331B   conf=0.68

第一步：

Normalization

处理：

空格
大小写
标点
OCR 常见误识

然后进行字符串相似度聚类。

例如：

A8331B
A833IB
A8?31B
A?331B

它们明显是一个 Cluster。

17. 字符级投票

更进一步，不是简单地“哪个字符串出现次数最多”。

应该：

位置 1:
A A A A A
↓
A

位置 2:
8 8 8 ? ?
↓
8

位置 3:
3 3 3 3 3
↓
3

位置 4:
3 3 3 3 3
↓
3

位置 5:
1 I 1 1 1
↓
1

位置 6:
B B B B B
↓
B

得到：

A8331B

每个字符都使用：

OCR confidence
+
frame quality
+
出现次数

进行加权。

18. 应该保留“无法确定”的状态

例如：

Frame 01:
A8?31B

Frame 02:
A8?3?B

Frame 03:
A?831B

Frame 04:
A8?31B

这时候不能：

猜成 A8831B

应该：

{
  "plate": "A8?31B",
  "status": "UNCERTAIN"
}

或者：

{
  "plate": null,
  "status": "UNREADABLE"
}

这是一个非常重要的设计原则：

宁可少识别，也不要制造错误车牌号。

19. 什么时候算“可靠识别”

可以设置类似：

plate_confirmation:

  min_ocr_frames: 3

  min_consistent_frames: 2

  min_consensus_score: 0.80

  max_character_disagreement: 1

例如：

5 个候选帧
      ↓
4 个识别成：
A8331B

1 个识别成：
A83?1B

可以：

CONFIRMED

但是：

A8331B
A8338B
A8391B
A8?31B

应该：

UNCERTAIN

而不是随便选一个。

20. 为什么必须“同一 Track 内融合”

因为否则很容易出现：

车 A：
A8331B

车 B：
A8338B

两个车非常接近

OCR 结果混在一起

所以架构一定要：

Vehicle Track
      │
      ├── plate candidate 1
      ├── plate candidate 2
      ├── plate candidate 3
      └── plate candidate 4

而不是：

视频
 ↓
所有 OCR 结果
 ↓
全局投票

后者非常容易把两台车的号码混起来。

21. 两张最终截图怎么选

我建议不是简单：

Score 最高
Score 第二高

而应该：

第一张：
最高质量 Frame

第二张：
另一时间点的高质量 Frame

例如：

Frame 20450
Frame 20453

虽然这两帧都非常清楚，但其实几乎是同一个瞬间。

更好的策略：

Evidence #1
00:15:44.23

Evidence #2
00:15:45.10

两张来自不同时间点。

这样可以证明：

同一车辆
+
同一车牌
+
连续行为
22. 两张证据图建议采用不同用途
Evidence 1：原始全景图
原始视频帧
+
车辆框
+
车牌框
+
时间戳
+
Track ID

用于证明：

这辆车在哪里。

Evidence 2：车牌近景
原始车牌 crop
+
轻度增强
+
车牌识别结果
+
时间戳

用于证明：

车牌到底是什么。

因此最终：

evidence_01_context.jpg
evidence_02_plate.jpg

比两张完全一样的车牌 Crop 更有价值。

同时应该始终保留未修改的原始帧，不要用画框、锐化、放大后的图片替代原始证据。

23. 推荐的输出结构

最终不要只输出 CSV。

建议：

result/
│
├── summary.json
├── violations.csv
│
├── vehicle_001/
│   ├── evidence_context.jpg
│   ├── evidence_plate.jpg
│   ├── candidates.json
│   └── metadata.json
│
├── vehicle_002/
│   ├── evidence_context.jpg
│   ├── evidence_plate.jpg
│   ├── candidates.json
│   └── metadata.json
│
└── debug/
    ├── timeline.jpg
    └── tracking_preview.mp4
24. 最终 JSON

例如：

{
  "video": "dashcam_001.mp4",
  "analysis_range": {
    "start": "00:15:00",
    "end": "00:20:00"
  },
  "violations": [
    {
      "track_id": 17,
      "first_seen": "00:15:42.31",
      "last_seen": "00:15:48.72",
      "duration": 6.41,

      "plate": "粤B8331B",
      "plate_status": "CONFIRMED",
      "plate_confidence": 0.91,

      "evidence": [
        {
          "timestamp": "00:15:44.23",
          "image": "vehicle_001/evidence_context.jpg"
        },
        {
          "timestamp": "00:15:45.10",
          "image": "vehicle_001/evidence_plate.jpg"
        }
      ]
    }
  ]
}
25. Skill 的输入接口

为了以后重复使用，我建议把输入控制在非常少的几个参数。

video: ./input.mp4

start_time: "00:15:00"
end_time: "00:20:00"

emergency_lane:
  type: polygon
  points:
    - [1180, 350]
    - [1910, 420]
    - [1910, 1080]
    - [1050, 1080]

sampling:
  coarse_fps: 5
  fine_fps: 15

violation:
  min_duration: 1.0

plate:
  min_quality: 0.55
  min_consistent_frames: 3

output_dir: ./result

这样换一个 MP4，只需要改：

video
start_time
end_time
ROI

就能重复执行。

26. Skill 的推荐目录

不需要做完整软件工程，可以非常轻量：

emergency-lane-plate-skill/
│
├── SKILL.md
│
├── config.yaml
│
├── run.py
│
├── video_reader.py
├── vehicle_tracker.py
├── lane_detector.py
├── plate_detector.py
├── plate_ocr.py
├── plate_fusion.py
├── evidence_selector.py
│
├── utils/
│   ├── image_quality.py
│   ├── timestamp.py
│   └── geometry.py
│
└── README.md

不需要：

REST API
Database Server
Frontend
Docker orchestration
User account system

完全没必要。

27. 更进一步：不要把所有东西做成一个 Python 文件

虽然项目很小，但最好不要：

run.py 2000 行

而应该保持几个明确模块：

video_reader
      ↓
vehicle_tracker
      ↓
lane_filter
      ↓
plate_detector
      ↓
plate_ocr
      ↓
plate_fusion
      ↓
evidence_selector

这样以后更换模型非常方便。

例如：

PaddleOCR
       ↓
换成其他 OCR

只需要替换：

plate_ocr.py

而不是修改整个项目。

28. DeepSeek 的具体执行方式

我建议把 DeepSeek 当成这个 Skill 的“开发者/编排器”。

执行过程：

用户：
分析这个视频 00:10:00～00:30:00

        ↓

DeepSeek
读取 SKILL.md

        ↓

检查视频信息

        ↓

执行粗检测

        ↓

发现 8 个可能车辆 Track

        ↓

逐 Track 精查

        ↓

生成车牌候选

        ↓

多帧融合

        ↓

生成 evidence

        ↓

输出 JSON / CSV

DeepSeek 最适合干的其实是：

根据 Skill 规则调用这些工具，并根据中间结果决定是否需要进一步取帧。

29. 一个非常重要的优化：自适应取帧

不要固定：

每 0.1 秒取一帧

建议：

普通阶段：
5 FPS

检测到车：
10 FPS

车辆进入应急车道：
15 FPS

发现车牌：
30 FPS

发现某几个字符不稳定：
针对该时间段重新扫描

例如：

00:15:42
发现车辆

00:15:43
进入应急车道

00:15:43 - 00:15:47
重点分析

00:15:45
OCR 不稳定

↓

自动回溯：
00:15:43.5
00:15:43.6
00:15:43.7
...
00:15:46.5

重新搜索最佳车牌帧

这个机制非常适合交给 DeepSeek 编排。

30. 最推荐的最终处理流程

最终我建议做成：

                    MP4
                     │
                     ▼
              指定时间段读取
                     │
                     ▼
                5 FPS 粗扫描
                     │
                     ▼
              Vehicle Detection
                     │
                     ▼
               Vehicle Tracking
                     │
                     ▼
          ┌──── 是否进入应急车道 ────┐
          │                         │
         NO                        YES
          │                         │
        丢弃                    创建 Track
                                    │
                                    ▼
                             10~30 FPS 精查
                                    │
                                    ▼
                             Plate Detection
                                    │
                                    ▼
                            Perspective Crop
                                    │
                                    ▼
                            Image Quality
                              Filtering
                                    │
                                    ▼
                               OCR × N
                                    │
                                    ▼
                         Track-level Candidate
                               Buffer
                                    │
                                    ▼
                          字符级加权融合
                                    │
                         ┌──────────┴──────────┐
                         │                     │
                     Confirmed              Uncertain
                         │                     │
                         ▼                     ▼
                   选择两张证据            输出人工复核
                         │
                         ▼
                  JSON + CSV + JPG
31. 第一版本不要做的事情

为了控制项目复杂度，V1 建议明确排除：

❌ 自动识别所有道路类型
❌ 自动识别所有车道线
❌ 自动判断任何国家/地区车牌
❌ 自动处理所有摄像机视角
❌ 自动解决极端夜间场景
❌ 训练大型端到端模型
❌ Web UI
❌ 数据库
❌ 云服务

第一版只解决：

固定行车记录仪 + 指定视频区间 + 指定右侧应急车道 ROI + 车辆 Track + 多帧车牌识别 + 两张证据图。

先把这个闭环做可靠。

32. V1 → V2 演进
V1
人工指定 Emergency Lane ROI
+
YOLO vehicle detection
+
BoT-SORT
+
Plate Detector
+
PaddleOCR
+
Multi-frame OCR Fusion

目标：

能够可靠处理一类固定视角行车记录仪视频。

V2

加入：

自动车道检测
+
动态 Emergency Lane ROI
+
更强 Plate Detector
+
夜间增强
+
车辆 ReID
+
更复杂遮挡处理
V3

加入：

多车型/多地区车牌支持
+
自动摄像机标定
+
批量视频处理
+
人工复核界面
+
统计报表
33. 成功标准

这个项目不应该以：

“代码能够运行”

作为完成标准。

应该定义：

Detection

能够发现大部分进入指定应急车道的车辆。

Tracking

同一车辆在其出现期间尽可能保持同一个 Track ID。

Plate Recognition

车牌识别只有两种：

CONFIRMED
UNCERTAIN

而不是强行返回一个数字。

Evidence

每个 CONFIRMED 车辆必须：

≥ 2 张独立时间点证据

并且：

原始画面中能够定位车辆
+
车牌区域足够清晰
+
两个证据帧的车牌结果一致
34. 最终关于“模糊车牌”的结论

你提出的这个问题，答案不是：

找一个更强的 OCR。

真正的方案应该是四层叠加：

第一层
Vehicle Tracking
↓
保证“这些帧是同一辆车”

第二层
Plate Candidate Collection
↓
从整个 Track 找所有可能的车牌帧

第三层
Image Quality Ranking
↓
从几十帧中找到最清楚的几帧

第四层
Multi-frame OCR Fusion
↓
不同帧互相纠错

再增加一个原则：

机器无法确认
        ↓
不要猜
        ↓
UNCERTAIN
        ↓
人工复核

这比单纯提升 OCR 模型的效果重要得多。

35. 我推荐的技术栈

第一版可以非常克制：

Python 3.x

OpenCV
FFmpeg

Ultralytics YOLO
    ↓
Vehicle Detection
    ↓
BoT-SORT / ByteTrack

自定义/现成 Plate Detector
    ↓
License Plate Detection

PaddleOCR
    ↓
OCR

NumPy
SciPy
Pillow

DeepSeek
    ↓
Skill 编排 / Coding Agent / 结果分析

Ultralytics 官方当前文档已经提供视频多目标跟踪和多种 Tracker，可直接作为车辆 Track 的基础。

PaddleOCR 当前官方文档也提供 PP-OCRv5 以及独立文本检测、文本识别模块，可以作为 OCR 层的基础。

36. 最终的 Skill 定位

所以我会把这个项目定义为：

Emergency Lane Plate Evidence Extraction Skill

而不是：

License Plate Recognition System

因为真正需要解决的是：

Video
 ↓
Event Detection
 ↓
Vehicle Tracking
 ↓
Emergency Lane Classification
 ↓
Plate Evidence Collection
 ↓
Multi-frame Plate Recognition
 ↓
Evidence Extraction

这一定义非常关键。

Skill 的核心资产不是某个 YOLO 模型，而是“Track → 多帧证据 → 融合 → 证据选择”这套流程。

这样以后换：

行车记录仪
监控摄像头
停车场录像
高速公路录像

甚至更换：

YOLO
OCR
Tracker

整个 Skill 的主体逻辑仍然可以复用。

---

# 用户反馈

（本区由 AI 整理用户在本轮迭代中的互动输入；追加写入，不覆盖历史）

#

---
