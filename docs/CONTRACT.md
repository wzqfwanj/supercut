# Supercut 开发契约（前后端共享）

> 本文件由 Lead 维护。前后端实现必须以本契约为准；如需变更，先通知 Lead。

## 1. 产品范围（只实现最小功能）

剪视频工具，两个功能：

1. **剪片段（trim）**：用户选择起止时间 `[start, end]`，服务端用 FFmpeg 截取该区间，导出新 MP4。
2. **剪窗口范围（crop）**：用户框选画面中的一个矩形区域 `(x, y, width, height)`，服务端用 FFmpeg 裁剪画面区域（如去黑边、改画幅），导出新 MP4。

之外一律不做：不加时间线多轨、不加水印、不做转码预设面板、不做用户注册/数据库。

视觉：极简；原生 HTML/CSS/JS，无构建步骤、无框架、无 CDN、无外部字体图标（图标用内联 SVG/字符）。
主题：明（light）/ 灰（gray）/ 暗（dark）三态切换，两页（登录页与主页面）都支持，选择存 `localStorage`。

## 2. 文件所有权（避免写冲突）

| 目录/文件 | 负责人 | 说明 |
|---|---|---|
| `app.py`、`tests/` | backend | Flask 应用与测试 |
| `templates/`、`static/` | frontend | 页面、样式、脚本 |
| `README.md`、`docs/`、`LICENSE`、`.gitignore`、`requirements*.txt`、`run_local.ps1` | lead | 文档与工程骨架 |
| `deploy/` | lead | 部署配置（不含密码/token） |

- 禁止修改非本人所有权的文件；Git 提交由 Lead 负责，teammate 不执行 `git commit/push`。
- 禁止把密码、token、服务器私密信息写入仓库任何文件。

## 3. 运行方式约定

- 入口：`app.py` 暴露 `create_app()` 工厂，同时模块级 `app = create_app()` 便于 `gunicorn app:app` 与 `python app.py` 直接启动。
- `python app.py` 时监听 `SUPERSCUT_HOST`（默认 `127.0.0.1`）、`SUPERSCUT_PORT`（默认 `8080`），关闭 debug。
- 环境变量：

| 变量 | 默认 | 说明 |
|---|---|---|
| `SUPERSCUT_USERNAME` | `admin` | 登录用户名 |
| `SUPERSCUT_PASSWORD` | 无 | 登录密码；未设置时应用启动失败并提示（安全要求，不允许默认密码） |
| `SUPERSCUT_SECRET_KEY` | 无 | Flask session 密钥；未设置时启动失败 |
| `SUPERSCUT_DATA_DIR` | `<项目>/instance` | 上传/导出文件与元数据目录（gitignore） |
| `SUPERSCUT_FFMPEG` | 自动探测 | ffmpeg 可执行文件路径 |
| `SUPERSCUT_MAX_UPLOAD_MB` | `512` | 单文件上传上限（MB） |
| `SUPERSCUT_TTL_HOURS` | `2` | 上传/导出文件保留时长（小时） |
| `SUPERSCUT_BEHIND_PROXY` | 空 | 设为 `1` 时启用 Werkzeug `ProxyFix`（`x_for=1, x_proto=1, x_host=1, x_prefix=1`），用于 Caddy 子路径 `/supercut/` 反代 |
| `SUPERSCUT_COOKIE_SECURE` | 空 | 设为 `1` 时 session cookie 加 `Secure`（线上 HTTPS 必须开） |

- FFmpeg 探测顺序：`SUPERSCUT_FFMPEG` → `shutil.which("ffmpeg")` → `imageio_ffmpeg.get_ffmpeg_exe()`（可选依赖，仅本地开发用）。ffprobe 可选：探测顺序 `PATH` 中的 `ffprobe`/与 ffmpeg 同目录的 `ffprobe`；没有 ffprobe 时用 `ffmpeg -i` 的 stderr 解析时长与分辨率（本地 Windows 走这条路径，必须可靠）。
- 生产依赖：`requirements.txt`（Flask + gunicorn）；开发/测试依赖：`requirements-dev.txt`（pytest、imageio-ffmpeg）。

## 4. HTTP API

所有 JSON 响应 `application/json; charset=utf-8`。错误统一 `{"error": "中文提示"}`，HTTP 状态码语义化。除 `GET /api/health` 与登录页外，全部要求已登录 session；未登录 API 返回 `401 {"error": "未登录"}`。

### 页面
- `GET /`：已登录 → `templates/index.html`；未登录 → 302 到登录页。
- `GET /login`：已登录 → 302 到 `/`；否则渲染 `templates/login.html`。
- `POST /login`（form：`username`、`password`）：成功 302 到 `/`；失败 200 重新渲染登录页并带错误文案。
- `POST /logout`：清 session，302 到登录页。

### API
- `GET /api/health`（公开）→ `{"ok": true, "ffmpeg": true, "max_upload_mb": 512}`
- `POST /api/upload`（form-data，字段名 `file`）→
  ```json
  {"id":"a1b2...", "name":"demo.mp4", "duration":12.345, "width":1920, "height":1080,
   "size":12345678, "max_upload_mb":512}
  ```
  - 校验扩展名（mp4/m4v/mov/mkv/webm/avi/flv/wmv/ts/mts/3gp）与可解析性；无法解析返回 400。
  - 超过上限返回 413。
- `POST /api/trim`（JSON）`{"id":"a1b2...", "start":1.0, "end":5.5}` →
  ```json
  {"token":"...", "url":"/api/download/<token>", "download_name":"demo_clip.mp4",
   "mode":"trim", "start":1.0, "end":5.5, "duration":4.5, "size":1234567}
  ```
  - 参数：`0 <= start < end`，`end-start >= 0.1`；`end` 允许略超总时长，服务端 clamp 到视频时长。
- `POST /api/crop`（JSON）`{"id":"a1b2...", "x":100, "y":50, "width":1280, "height":720}` →
  ```json
  {"token":"...", "url":"/api/download/<token>", "download_name":"demo_crop.mp4",
   "mode":"crop", "x":100, "y":50, "width":1280, "height":720, "size":1234567}
  ```
  - 整数像素；`width,height >= 16` 且强制为偶数（向下取偶）；`x+width <= 视频宽`、`y+height <= 视频高`，不满足返回 400。前端已保证，服务端仍需校验。
- `GET /api/download/<token>`（已登录）：以 `Content-Disposition: attachment` 返回 `outputs/<token>.mp4`；不存在/过期 404。

### 存储布局（`SUPERSCUT_DATA_DIR`）
```
uploads/<id>.<ext>      原始文件
uploads/<id>.json       {"name","duration","width","height","size","ext","created"}
outputs/<token>.mp4     导出结果
outputs/<token>.json    {"download_name","mode","created"}
```
- 每次成功上传/裁剪时机会式清理超过 `SUPERSCUT_TTL_HOURS` 的旧文件（按 mtime），启动时也清理一次。
- 并发：gunicorn 1 worker + 4 threads，禁止依赖进程内内存状态（元数据必须落盘）。
- 输出编码：H.264 + AAC，`-preset veryfast -crf 20 -movflags +faststart`；命令使用 `-map 0:v:0 -map 0:a?`。导出文件大小 < 上传体积上限的 2 倍即可。
- 子进程超时 15 分钟；失败返回 500 `{"error": "视频处理失败：..."}`（附简短原因，不带完整命令/路径）。

## 5. 前端要求

- 页面结构（templates/index.html）：
  1. 顶栏：品牌 `Supercut`、主题切换（明/灰/暗三选一）、退出登录。
  2. 上传区：点击/拖拽选择单个视频；上传中显示百分比（XHR upload.onprogress）；成功后显示文件名、时长、分辨率、大小。
  3. 编辑区（上传成功后出现）：
     - 模式切换：`剪片段` / `剪窗口范围`。
     - 视频预览：`<video controls>`，与上传的本地文件通过 `URL.createObjectURL(file)` 预览（不占服务器流量）。
     - 剪片段：双滑块时间范围 + `开始/结束` 数字输入（秒，保留 2 位）+ 当前播放时间“设为起点/设为终点”按钮 + 区间时长展示 + “导出片段”。
     - 剪窗口范围：在视频画面上拖拽框选矩形（覆盖层半透明遮罩，选区高亮），显示 `x/y/宽/高` 数字输入可微调，选区尺寸随视频等比映射；+ “全选”/“重置” + “导出窗口”。
     - 导出中：按钮禁用 + 转圈/耗时提示；成功后给出下载按钮（`url` 直接用作 `<a download>`）。
  4. 错误提示：页面内 toast 或状态条，中文。
- 统一使用相对路径或模板 `url_for`，保证挂载在 `/supercut/` 子路径时静态资源与 API 均正确（本地根路径运行也要正确）。推荐 `<base href="{{ request.script_root }}/">` 或全部用 `url_for`。
- 主题：`<html data-theme="light|gray|dark">`，CSS 变量定义三套配色；切换按钮三选一，存取 `localStorage["supercut.theme"]`；默认 `gray`；登录页同样可切换。
- 极简视觉：留白、细边框、低饱和配色、系统字体、圆角克制；不使用渐变大图/花哨动画（只允许轻量 transition）；移动端可用（320px 起）。
- JS 只用原生 fetch/XHR；上传上限从 `GET /api/health` 的 `max_upload_mb` 读取并展示。
- 登录页：居中卡片，用户名/密码，错误文案，主题切换。

## 6. 验收命令

后端：
```powershell
python -m pytest -q
```
（测试必须离线可跑：用 imageio-ffmpeg 生成小视频夹具；设置 `SUPERSCUT_FFMPEG`、`SUPERSCUT_PASSWORD`、`SUPERSCUT_SECRET_KEY`、`SUPERSCUT_DATA_DIR` 指向临时目录。）

本地联调：
```powershell
powershell -ExecutionPolicy Bypass -File .\run_local.ps1
# http://127.0.0.1:8080  用户名 admin / 密码 supercut-dev
```

前端 UI 验收可用本机 Playwright（已安装）：登录 → 上传 → 剪片段/裁剪 → 下载，页面无 console 错误；截图自行检查布局。
