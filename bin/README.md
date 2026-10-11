# bin —— 自带命令行工具（不入库）

本目录是项目自带的外部可执行文件，**体积约 221 MB，因此不进 git 仓库**。
换机器复现时按下面的版本放回来即可，`run_pipeline.bat` / `使用规则.md` 里的路径都指向这里。

| 文件 | 版本（本机实测） | 用途 | 来源 |
|---|---|---|---|
| `ffmpeg.exe` + `av*.dll` / `sw*.dll` | `N-127226-gdc234edb2c-20261006`（gcc 16.2.0 构建） | AV1 → H.264 转码、证据视频裁剪、抽帧 | https://www.gyan.dev/ffmpeg/builds/ （essentials 包） |
| `ffprobe.exe` | 同 ffmpeg | 处理前先看编码（AV1 会让流水线慢约 20 倍） | 同上，与 ffmpeg 同包 |
| `ffplay.exe` | 同 ffmpeg | 人工核对视频时间用（可选，可删） | 同上 |
| `yt-dlp.exe` | `2026.08.19` | 抓取测试用公开视频 | https://github.com/yt-dlp/yt-dlp/releases |

## 注意

- **本机直连 github.com 不通**，但 `api.github.com` 与 bilibili 可通。
  从 GitHub 下资源走镜像前缀：`https://ghfast.top/<原始 url>`。
- 只用 `ffmpeg` / `ffprobe` 的话，删掉 `ffplay.exe` 与 `avdevice-63.dll` 可省约 23 MB。
- 这些是**外部工具**，不是项目代码：不参与 `build_c.bat`，也不参与 `test_python.bat`。
