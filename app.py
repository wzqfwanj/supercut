"""Supercut 后端：Flask 应用工厂 + 登录 + 上传 / 剪片段 / 剪窗口 / 下载。

接口与环境变量严格遵循 docs/CONTRACT.md。不依赖数据库、网络或进程内状态：
上传与导出文件均落盘到 SUPERSCUT_DATA_DIR，可直接用 gunicorn（1 worker + 多线程）运行。
"""

from __future__ import annotations

import functools
import hmac
import json
import math
import os
import re
import shutil
import subprocess
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from flask import (
    Flask,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    session,
    url_for,
)
from werkzeug.exceptions import HTTPException
from werkzeug.middleware.proxy_fix import ProxyFix

# 允许上传的视频扩展名（契约 4. /api/upload）
ALLOWED_EXTENSIONS = frozenset(
    {"mp4", "m4v", "mov", "mkv", "webm", "avi", "flv", "wmv", "ts", "mts", "3gp"}
)

# 服务器生成的 id / token 均为 uuid4().hex，固定 32 位小写十六进制。
_ID_RE = re.compile(r"^[0-9a-f]{32}$")
# ffmpeg -i stderr 中的时长与视频分辨率
_DURATION_RE = re.compile(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)")
_VIDEO_SIZE_RE = re.compile(r"(\d{2,5})x(\d{2,5})")

_SUBPROCESS_TIMEOUT = 15 * 60  # 契约：子进程超时 15 分钟
_PROBE_TIMEOUT = 60
_READ_CHUNK = 1024 * 1024
_MULTIPART_SLACK = 1024 * 1024  # 给 multipart 边界/表单字段留出的余量

_OK_EXT_HINT = "/".join(sorted(ALLOWED_EXTENSIONS))


class ApiError(Exception):
    """业务错误：由统一错误处理器转成 {"error": "..."}。"""

    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status = status


class ProbeError(Exception):
    """无法解析视频元数据。"""


# ---------------------------------------------------------------------------
# 环境变量 / 可执行文件探测
# ---------------------------------------------------------------------------


def _env_required(name: str) -> str:
    value = os.environ.get(name)
    if value is None or value == "":
        raise RuntimeError(f"缺少必需环境变量 {name}，请先设置后再启动 Supercut。")
    return value


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        return int(str(raw).strip())
    except ValueError:
        raise RuntimeError(f"环境变量 {name} 必须为整数，当前值：{raw!r}") from None


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        return float(str(raw).strip())
    except ValueError:
        raise RuntimeError(f"环境变量 {name} 必须为数字，当前值：{raw!r}") from None


def _env_flag(name: str) -> bool:
    return str(os.environ.get(name) or "").strip() == "1"


def _data_dir_from_env(app_root: Path) -> Path:
    raw = (os.environ.get("SUPERSCUT_DATA_DIR") or "").strip()
    path = Path(raw).expanduser() if raw else app_root / "instance"
    if not path.is_absolute():
        # 相对路径按项目根目录解析，避免受启动时工作目录影响。
        path = app_root / path
    return path


def _resolve_ffmpeg() -> str | None:
    """探测顺序：SUPERSCUT_FFMPEG → PATH 中的 ffmpeg → imageio_ffmpeg。"""
    candidate = (os.environ.get("SUPERSCUT_FFMPEG") or "").strip().strip('"')
    if candidate:
        path = Path(candidate).expanduser()
        if path.is_file():
            return str(path)
    found = shutil.which("ffmpeg")
    if found:
        return found
    try:  # 可选依赖，仅本地开发使用
        import imageio_ffmpeg  # type: ignore[import-not-found]

        exe = imageio_ffmpeg.get_ffmpeg_exe()
        if exe and Path(exe).is_file():
            return str(exe)
    except Exception:
        pass
    return None


def _resolve_ffprobe(ffmpeg: str | None) -> str | None:
    """探测顺序：PATH 中的 ffprobe → 与 ffmpeg 同目录的 ffprobe。"""
    found = shutil.which("ffprobe")
    if found:
        return found
    if ffmpeg:
        directory = Path(ffmpeg).parent
        for name in ("ffprobe", "ffprobe.exe"):
            candidate = directory / name
            if candidate.is_file():
                return str(candidate)
    return None


# ---------------------------------------------------------------------------
# FFmpeg 探测 / 转码
# ---------------------------------------------------------------------------


def _parse_ffmpeg_stderr(text: str) -> dict:
    """从 `ffmpeg -i` 的 stderr 中解析时长与分辨率（无 ffprobe 时的可靠路径）。"""
    duration = None
    match = _DURATION_RE.search(text)
    if match:
        hours, minutes, seconds = match.groups()
        duration = int(hours) * 3600 + int(minutes) * 60 + float(seconds)

    width = height = None
    for line in text.splitlines():
        if "Stream #" not in line or "Video:" not in line:
            continue
        if "attached pic" in line:  # 跳过封面图
            continue
        size_match = _VIDEO_SIZE_RE.search(line)
        if size_match:
            width, height = int(size_match.group(1)), int(size_match.group(2))
            break

    if duration is None or duration <= 0 or not math.isfinite(duration):
        raise ProbeError("无法解析视频时长")
    if not width or not height or width <= 0 or height <= 0:
        raise ProbeError("无法解析视频分辨率")
    return {"duration": round(duration, 3), "width": width, "height": height}


def _parse_ffprobe_json(text: str) -> dict:
    try:
        data = json.loads(text or "{}")
    except ValueError:
        raise ProbeError("无法解析视频信息") from None

    duration = None
    fmt = data.get("format") or {}
    for candidate in (fmt.get("duration"),):
        if candidate not in (None, "", "N/A"):
            try:
                duration = float(candidate)
            except (TypeError, ValueError):
                duration = None
            if duration:
                break

    width = height = None
    for stream in data.get("streams") or []:
        w, h = stream.get("width"), stream.get("height")
        if w and h:
            width, height = int(w), int(h)
            if duration is None and stream.get("duration") not in (None, "N/A"):
                try:
                    duration = float(stream["duration"])
                except (TypeError, ValueError):
                    duration = None
            break

    if duration is None or duration <= 0 or not math.isfinite(duration):
        raise ProbeError("无法解析视频时长")
    if not width or not height or width <= 0 or height <= 0:
        raise ProbeError("无法解析视频分辨率")
    return {"duration": round(duration, 3), "width": width, "height": height}


def _run_capture(cmd: list[str], timeout: int) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        check=False,
    )


def _probe_with_ffprobe(ffprobe: str, path: Path) -> dict:
    cmd = [
        ffprobe,
        "-v",
        "error",
        "-show_entries",
        "format=duration:stream=width,height,duration",
        "-of",
        "json",
        str(path),
    ]
    try:
        proc = _run_capture(cmd, _PROBE_TIMEOUT)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ProbeError(f"ffprobe 调用失败：{exc.__class__.__name__}") from None
    if proc.returncode != 0:
        raise ProbeError("无法解析视频信息")
    return _parse_ffprobe_json(proc.stdout.decode("utf-8", "replace"))


def _probe_with_ffmpeg(ffmpeg: str, path: Path) -> dict:
    # ffmpeg 只给 -i 而没有输出文件时退出码为 1，但 stderr 包含元数据，属正常现象。
    cmd = [ffmpeg, "-hide_banner", "-nostdin", "-i", str(path)]
    try:
        proc = _run_capture(cmd, _PROBE_TIMEOUT)
    except subprocess.TimeoutExpired:
        raise ProbeError("解析视频超时") from None
    except OSError:
        raise ProbeError("无法启动 FFmpeg") from None
    return _parse_ffmpeg_stderr(proc.stderr.decode("utf-8", "replace"))


def probe_video(ffmpeg: str | None, ffprobe: str | None, path: Path) -> dict:
    if ffprobe:
        try:
            return _probe_with_ffprobe(ffprobe, path)
        except ProbeError:
            pass  # ffprobe 不可用时回退到 ffmpeg stderr 解析
    if not ffmpeg:
        raise ProbeError("服务器未找到 FFmpeg")
    return _probe_with_ffmpeg(ffmpeg, path)


def _short_reason(stderr: bytes, hidden_paths: list[str]) -> str:
    """把 FFmpeg stderr 压缩成一句简短原因，去掉命令中的绝对路径。"""
    text = (stderr or b"").decode("utf-8", "replace")
    for hidden in hidden_paths:
        if hidden:
            text = text.replace(hidden, "文件")
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return "FFmpeg 处理失败"
    reason = lines[-1]
    if len(reason) > 200:
        reason = reason[:200] + "…"
    return reason


def _safe_unlink(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    except OSError:
        pass


def _run_encode(cmd: list[str], src: Path, out: Path) -> None:
    try:
        proc = _run_capture(cmd, _SUBPROCESS_TIMEOUT)
    except subprocess.TimeoutExpired:
        _safe_unlink(out)
        raise ApiError("视频处理失败：处理超时", 500) from None
    except OSError:
        _safe_unlink(out)
        raise ApiError("视频处理失败：无法启动 FFmpeg", 500) from None
    if proc.returncode != 0:
        _safe_unlink(out)
        reason = _short_reason(proc.stderr, [str(src), str(out)])
        raise ApiError(f"视频处理失败：{reason}", 500)
    if not out.is_file() or out.stat().st_size == 0:
        _safe_unlink(out)
        raise ApiError("视频处理失败：未生成输出文件", 500)


# ---------------------------------------------------------------------------
# 文件与元数据工具
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _write_json(path: Path, payload: dict) -> None:
    """先写临时文件再原子替换，避免多线程下读到半份元数据。"""
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    os.replace(tmp, path)


def _read_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _cleanup_expired(uploads_dir: Path, outputs_dir: Path, ttl_hours: float) -> None:
    """按 mtime 清理超过 TTL 的上传/导出文件（含元数据），失败静默。"""
    cutoff = time.time() - ttl_hours * 3600
    for directory in (uploads_dir, outputs_dir):
        try:
            entries = list(directory.iterdir())
        except OSError:
            continue
        for entry in entries:
            try:
                if entry.is_file() and entry.stat().st_mtime < cutoff:
                    entry.unlink()
            except OSError:
                continue


def _basename(filename: str) -> str:
    """只取文件名部分，兼容浏览器可能带上来的 Windows 路径。"""
    name = str(filename or "").replace("\\", "/").split("/")[-1]
    name = name.replace("\x00", "").strip()
    return name


def _safe_stem(name: str) -> str:
    stem = Path(str(name or "video")).stem
    stem = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", stem).strip()
    return (stem or "video")[:80]


def _json_payload() -> dict:
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        raise ApiError("请求格式错误，需为 JSON 对象")
    return payload


def _number_param(payload: dict, key: str) -> float:
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ApiError(f"参数 {key} 必须为数字")
    number = float(value)
    if not math.isfinite(number):
        raise ApiError(f"参数 {key} 必须为有限数字")
    return number


def _int_param(payload: dict, key: str) -> int:
    value = payload.get(key)
    if isinstance(value, bool):
        raise ApiError(f"参数 {key} 必须为整数")
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    raise ApiError(f"参数 {key} 必须为整数")


def _is_authenticated() -> bool:
    return bool(session.get("authenticated"))


def _login_required(view):
    """API 未登录返回 401 JSON；页面未登录 302 到登录页。"""

    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        if not _is_authenticated():
            if request.path.startswith("/api/"):
                return jsonify(error="未登录"), 401
            return redirect(url_for("login"))
        return view(*args, **kwargs)

    return wrapped


# ---------------------------------------------------------------------------
# 应用工厂
# ---------------------------------------------------------------------------


def create_app() -> Flask:
    app = Flask(__name__)

    password = _env_required("SUPERSCUT_PASSWORD")
    secret_key = _env_required("SUPERSCUT_SECRET_KEY")
    username = (os.environ.get("SUPERSCUT_USERNAME") or "").strip() or "admin"
    max_upload_mb = _env_int("SUPERSCUT_MAX_UPLOAD_MB", 512)
    if max_upload_mb < 1:
        raise RuntimeError("环境变量 SUPERSCUT_MAX_UPLOAD_MB 必须为正整数。")
    ttl_hours = _env_float("SUPERSCUT_TTL_HOURS", 2.0)
    if not math.isfinite(ttl_hours) or ttl_hours <= 0:
        raise RuntimeError("环境变量 SUPERSCUT_TTL_HOURS 必须为正数。")

    app_root = Path(app.root_path)
    data_dir = _data_dir_from_env(app_root)
    uploads_dir = data_dir / "uploads"
    outputs_dir = data_dir / "outputs"

    ffmpeg = _resolve_ffmpeg()
    ffprobe = _resolve_ffprobe(ffmpeg)

    behind_proxy = _env_flag("SUPERSCUT_BEHIND_PROXY")
    cookie_secure = _env_flag("SUPERSCUT_COOKIE_SECURE")

    app.config.update(
        SECRET_KEY=secret_key,
        USERNAME=username,
        PASSWORD=password,
        DATA_DIR=str(data_dir),
        UPLOADS_DIR=str(uploads_dir),
        OUTPUTS_DIR=str(outputs_dir),
        FFMPEG=ffmpeg,
        FFPROBE=ffprobe,
        MAX_UPLOAD_MB=max_upload_mb,
        MAX_CONTENT_LENGTH=max_upload_mb * 1024 * 1024 + _MULTIPART_SLACK,
        TTL_HOURS=ttl_hours,
        BEHIND_PROXY=behind_proxy,
        COOKIE_SECURE=cookie_secure,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=cookie_secure,
        SESSION_COOKIE_NAME="supercut_session",
    )
    app.json.ensure_ascii = False
    # 契约 4：所有 JSON 响应 application/json; charset=utf-8
    app.json.mimetype = "application/json; charset=utf-8"

    try:
        uploads_dir.mkdir(parents=True, exist_ok=True)
        outputs_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise RuntimeError(
            f"无法创建数据目录 {data_dir}，请检查 SUPERSCUT_DATA_DIR：{exc}"
        ) from None

    # 启动时清理一次过期文件（契约 4. 存储布局）
    _cleanup_expired(uploads_dir, outputs_dir, ttl_hours)

    if behind_proxy:
        # Caddy 子路径反代：/supercut/ → 去掉前缀后转发，用 X-Forwarded-Prefix 还原 script_root。
        app.wsgi_app = ProxyFix(
            app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1
        )

    # -- 鉴权 ---------------------------------------------------------------

    def _check_credentials(input_username: str, input_password: str) -> bool:
        expected_user = app.config["USERNAME"].encode("utf-8")
        expected_pass = app.config["PASSWORD"].encode("utf-8")
        user_ok = hmac.compare_digest(input_username.encode("utf-8"), expected_user)
        pass_ok = hmac.compare_digest(input_password.encode("utf-8"), expected_pass)
        return user_ok and pass_ok

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if request.method == "GET":
            if _is_authenticated():
                return redirect(url_for("index"))
            return render_template("login.html", error=None, username="")

        input_username = (request.form.get("username") or "").strip()
        input_password = request.form.get("password") or ""
        if _check_credentials(input_username, input_password):
            session.clear()
            session["authenticated"] = True
            session["username"] = input_username
            return redirect(url_for("index"))
        # 简单防爆破：失败时短暂延时再重渲染登录页（状态码保持 200）。
        time.sleep(0.6)
        return render_template(
            "login.html", error="用户名或密码错误", username=input_username
        )

    @app.post("/logout")
    def logout():
        session.clear()
        return redirect(url_for("login"))

    @app.get("/")
    @_login_required
    def index():
        return render_template("index.html")

    # -- 健康检查（公开） ---------------------------------------------------

    @app.get("/api/health")
    def health():
        return jsonify(
            ok=True,
            ffmpeg=bool(app.config["FFMPEG"]),
            max_upload_mb=app.config["MAX_UPLOAD_MB"],
        )

    # -- 上传 ---------------------------------------------------------------

    @app.post("/api/upload")
    @_login_required
    def upload():
        if not app.config["FFMPEG"]:
            raise ApiError("视频处理失败：服务器未找到 FFmpeg", 500)

        uploaded = request.files.get("file")
        original_name = _basename(uploaded.filename if uploaded else "")
        if uploaded is None or not original_name:
            raise ApiError("未选择文件")

        ext = Path(original_name).suffix.lower().lstrip(".")
        if ext not in ALLOWED_EXTENSIONS:
            raise ApiError(f"不支持的视频格式，允许：{_OK_EXT_HINT}")

        video_id = uuid.uuid4().hex
        dest = uploads_dir / f"{video_id}.{ext}"
        max_bytes = app.config["MAX_UPLOAD_MB"] * 1024 * 1024
        size = 0
        too_large = False
        try:
            with dest.open("wb") as handle:
                while True:
                    chunk = uploaded.stream.read(_READ_CHUNK)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > max_bytes:
                        too_large = True
                        break
                    handle.write(chunk)
        except OSError:
            _safe_unlink(dest)
            raise ApiError("保存上传文件失败", 500) from None

        if too_large:
            _safe_unlink(dest)
            raise ApiError(
                f"文件超过上传上限（{app.config['MAX_UPLOAD_MB']} MB）", 413
            )
        if size == 0:
            _safe_unlink(dest)
            raise ApiError("上传文件为空")

        try:
            info = probe_video(app.config["FFMPEG"], app.config["FFPROBE"], dest)
        except ProbeError as exc:
            _safe_unlink(dest)
            raise ApiError(f"无法解析视频：{exc}") from None

        metadata = {
            "name": original_name,
            "duration": info["duration"],
            "width": info["width"],
            "height": info["height"],
            "size": size,
            "ext": ext,
            "created": _now_iso(),
        }
        try:
            _write_json(uploads_dir / f"{video_id}.json", metadata)
        except OSError:
            _safe_unlink(dest)
            raise ApiError("保存上传元数据失败", 500) from None

        _cleanup_expired(uploads_dir, outputs_dir, app.config["TTL_HOURS"])

        return jsonify(
            id=video_id,
            name=metadata["name"],
            duration=metadata["duration"],
            width=metadata["width"],
            height=metadata["height"],
            size=metadata["size"],
            max_upload_mb=app.config["MAX_UPLOAD_MB"],
        )

    # -- 剪片段 -------------------------------------------------------------

    def _load_upload(video_id: object) -> tuple[Path, dict]:
        if not isinstance(video_id, str) or not _ID_RE.fullmatch(video_id):
            raise ApiError("无效的视频 ID")
        meta_path = uploads_dir / f"{video_id}.json"
        if not meta_path.is_file():
            raise ApiError("视频不存在或已过期", 404)
        metadata = _read_json(meta_path)
        ext = str(metadata.get("ext") or "")
        if ext not in ALLOWED_EXTENSIONS:
            raise ApiError("视频不存在或已过期", 404)
        source = uploads_dir / f"{video_id}.{ext}"
        if not source.is_file():
            raise ApiError("视频不存在或已过期", 404)
        return source, metadata

    def _write_output_meta(token: str, mode: str, download_name: str) -> None:
        try:
            _write_json(
                outputs_dir / f"{token}.json",
                {"download_name": download_name, "mode": mode, "created": _now_iso()},
            )
        except OSError:
            _safe_unlink(outputs_dir / f"{token}.mp4")
            raise ApiError("保存导出元数据失败", 500) from None

    def _encode_command() -> list[str]:
        return [
            app.config["FFMPEG"],
            "-y",
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
        ]

    _ENCODE_TAIL = [
        "-map",
        "0:v:0",
        "-map",
        "0:a?",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "20",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-movflags",
        "+faststart",
    ]

    @app.post("/api/trim")
    @_login_required
    def trim():
        if not app.config["FFMPEG"]:
            raise ApiError("视频处理失败：服务器未找到 FFmpeg", 500)
        payload = _json_payload()
        source, metadata = _load_upload(payload.get("id"))

        start = round(_number_param(payload, "start"), 3)
        end = round(_number_param(payload, "end"), 3)
        total = float(metadata.get("duration") or 0)
        if total <= 0:
            raise ApiError("视频时长无效", 500)
        if start < 0:
            raise ApiError("开始时间不能小于 0")
        if start >= total:
            raise ApiError("开始时间超出视频时长")
        end = min(end, round(total, 3))
        if end <= start or end - start < 0.1:
            raise ApiError("片段长度不能小于 0.1 秒")

        clip_duration = round(end - start, 3)
        download_name = f"{_safe_stem(str(metadata.get('name') or 'video'))}_clip.mp4"
        token = uuid.uuid4().hex
        out = outputs_dir / f"{token}.mp4"
        cmd = _encode_command() + [
            "-ss",
            f"{start:.3f}",
            "-i",
            str(source),
            "-t",
            f"{clip_duration:.3f}",
        ] + _ENCODE_TAIL + [str(out)]
        _run_encode(cmd, source, out)
        _write_output_meta(token, "trim", download_name)

        try:
            size = out.stat().st_size
        except OSError:
            _safe_unlink(out)
            raise ApiError("视频处理失败：无法读取输出文件", 500) from None
        _cleanup_expired(uploads_dir, outputs_dir, app.config["TTL_HOURS"])

        return jsonify(
            token=token,
            url=url_for("download", token=token),
            download_name=download_name,
            mode="trim",
            start=start,
            end=end,
            duration=clip_duration,
            size=size,
        )

    # -- 剪窗口范围 ---------------------------------------------------------

    @app.post("/api/crop")
    @_login_required
    def crop():
        if not app.config["FFMPEG"]:
            raise ApiError("视频处理失败：服务器未找到 FFmpeg", 500)
        payload = _json_payload()
        source, metadata = _load_upload(payload.get("id"))

        x = _int_param(payload, "x")
        y = _int_param(payload, "y")
        width = _int_param(payload, "width")
        height = _int_param(payload, "height")
        if x < 0 or y < 0:
            raise ApiError("裁剪起点不能为负数")
        if width < 16 or height < 16:
            raise ApiError("裁剪宽高不能小于 16 像素")
        width -= width % 2  # 向下取偶，H.264 要求偶数宽高
        height -= height % 2
        if width < 16 or height < 16:
            raise ApiError("裁剪宽高不能小于 16 像素")

        video_width = int(metadata.get("width") or 0)
        video_height = int(metadata.get("height") or 0)
        if video_width <= 0 or video_height <= 0:
            raise ApiError("视频分辨率无效", 500)
        if x + width > video_width or y + height > video_height:
            raise ApiError("裁剪区域超出视频画面")

        download_name = f"{_safe_stem(str(metadata.get('name') or 'video'))}_crop.mp4"
        token = uuid.uuid4().hex
        out = outputs_dir / f"{token}.mp4"
        cmd = _encode_command() + [
            "-i",
            str(source),
            "-vf",
            f"crop={width}:{height}:{x}:{y}",
        ] + _ENCODE_TAIL + [str(out)]
        _run_encode(cmd, source, out)
        _write_output_meta(token, "crop", download_name)

        try:
            size = out.stat().st_size
        except OSError:
            _safe_unlink(out)
            raise ApiError("视频处理失败：无法读取输出文件", 500) from None
        _cleanup_expired(uploads_dir, outputs_dir, app.config["TTL_HOURS"])

        return jsonify(
            token=token,
            url=url_for("download", token=token),
            download_name=download_name,
            mode="crop",
            x=x,
            y=y,
            width=width,
            height=height,
            size=size,
        )

    # -- 下载 ---------------------------------------------------------------

    @app.get("/api/download/<token>")
    @_login_required
    def download(token: str):
        if not _ID_RE.fullmatch(token):
            raise ApiError("文件不存在或已过期", 404)
        path = outputs_dir / f"{token}.mp4"
        if not path.is_file():
            raise ApiError("文件不存在或已过期", 404)
        metadata = _read_json(outputs_dir / f"{token}.json")
        download_name = str(metadata.get("download_name") or "")
        if not download_name.lower().endswith(".mp4"):
            download_name = f"{token}.mp4"
        return send_file(
            path,
            mimetype="video/mp4",
            as_attachment=True,
            download_name=download_name,
        )

    # -- 统一错误格式 {"error": "..."} --------------------------------------

    @app.errorhandler(ApiError)
    def _handle_api_error(exc: ApiError):
        return jsonify(error=exc.message), exc.status

    @app.errorhandler(413)
    def _handle_too_large(_exc):
        return (
            jsonify(error=f"文件超过上传上限（{app.config['MAX_UPLOAD_MB']} MB）"),
            413,
        )

    @app.errorhandler(HTTPException)
    def _handle_http_exception(exc: HTTPException):
        if request.path.startswith("/api/"):
            return jsonify(error=exc.description or "请求错误"), exc.code or 500
        return exc.get_response(request.environ)

    @app.errorhandler(500)
    def _handle_server_error(_exc):
        return jsonify(error="服务器内部错误"), 500

    return app


app = create_app()


if __name__ == "__main__":
    host = (os.environ.get("SUPERSCUT_HOST") or "").strip() or "127.0.0.1"
    port = _env_int("SUPERSCUT_PORT", 8080)
    app.run(host=host, port=port, debug=False, threaded=True)
