"""pytest 公共夹具：临时数据目录 + 离线现场生成测试视频。

本机可能只有 imageio-ffmpeg 提供的 ffmpeg（没有 ffprobe），
因此测试同时覆盖 stderr 解析路径；夹具与 app.py 使用相同的探测顺序。
"""

from __future__ import annotations

import atexit
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# 必须在导入 app 之前设置：模块级 ``app = create_app()`` 需要这些环境变量。
_IMPORT_DATA_DIR = Path(tempfile.mkdtemp(prefix="supercut-import-"))
atexit.register(shutil.rmtree, _IMPORT_DATA_DIR, ignore_errors=True)
os.environ["SUPERSCUT_USERNAME"] = "admin"
os.environ["SUPERSCUT_PASSWORD"] = "test-password"
os.environ["SUPERSCUT_SECRET_KEY"] = "test-secret-key"
os.environ["SUPERSCUT_DATA_DIR"] = str(_IMPORT_DATA_DIR)

_DURATION_RE = re.compile(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)")
_SIZE_RE = re.compile(r"(\d{2,5})x(\d{2,5})")

# 前端 templates/ 尚未就绪时（例如只跑后端测试）使用的极简占位模板，
# 保证后端测试不依赖他人文件；真实模板就绪后自动使用真实模板。
_FALLBACK_TEMPLATES = {
    "login.html": (
        "<!doctype html><title>登录</title>"
        "<p data-error>{{ error or '' }}</p>"
        "<form method=post action=login></form>"
    ),
    "index.html": (
        "<!doctype html><title>Supercut</title>"
        "<main data-script-root='{{ request.script_root }}'>Supercut</main>"
    ),
}


def find_ffmpeg() -> str | None:
    env_path = (os.environ.get("SUPERSCUT_FFMPEG") or "").strip().strip('"')
    if env_path:
        candidate = Path(env_path).expanduser()
        if candidate.is_file():
            return str(candidate)
    found = shutil.which("ffmpeg")
    if found:
        return found
    try:
        import imageio_ffmpeg  # type: ignore[import-not-found]

        candidate = Path(imageio_ffmpeg.get_ffmpeg_exe())
        if candidate.is_file():
            return str(candidate)
    except Exception:
        return None
    return None


def probe_media_with_ffmpeg(ffmpeg: str, path: Path) -> dict:
    """测试侧独立的媒体探测（不复用 app.py 的解析实现）。"""
    proc = subprocess.run(
        [ffmpeg, "-hide_banner", "-nostdin", "-i", str(path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=60,
        check=False,
    )
    text = proc.stderr.decode("utf-8", "replace")
    duration = None
    match = _DURATION_RE.search(text)
    if match:
        hours, minutes, seconds = match.groups()
        duration = int(hours) * 3600 + int(minutes) * 60 + float(seconds)
    width = height = None
    for line in text.splitlines():
        if "Stream #" in line and "Video:" in line and "attached pic" not in line:
            size_match = _SIZE_RE.search(line)
            if size_match:
                width, height = int(size_match.group(1)), int(size_match.group(2))
                break
    return {"duration": duration, "width": width, "height": height}


def _apply_template_fallback(application, tmp_path: Path) -> None:
    templates_dir = PROJECT_ROOT / "templates"
    if (templates_dir / "login.html").is_file() and (
        templates_dir / "index.html"
    ).is_file():
        return
    fallback_dir = Path(tmp_path) / "_fallback_templates"
    fallback_dir.mkdir(parents=True, exist_ok=True)
    for name, content in _FALLBACK_TEMPLATES.items():
        (fallback_dir / name).write_text(content, encoding="utf-8")
    application.template_folder = str(fallback_dir)


@pytest.fixture(scope="session")
def ffmpeg_exe() -> str:
    exe = find_ffmpeg()
    if not exe:
        pytest.skip("需要 ffmpeg：请安装系统 ffmpeg 或 pip install imageio-ffmpeg")
    return exe


@pytest.fixture(scope="session")
def sample_video(ffmpeg_exe: str, tmp_path_factory: pytest.TempPathFactory) -> Path:
    """生成 2 秒 320x240 测试视频（带音频，离线）。"""
    path = tmp_path_factory.mktemp("media") / "sample.mp4"
    proc = subprocess.run(
        [
            ffmpeg_exe,
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=320x240:rate=24:duration=2",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=2",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-shortest",
            str(path),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=120,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")
    assert path.is_file() and path.stat().st_size > 0
    return path


@pytest.fixture(scope="session")
def probe_media(ffmpeg_exe: str):
    def _probe(path) -> dict:
        return probe_media_with_ffmpeg(ffmpeg_exe, Path(path))

    return _probe


@pytest.fixture()
def make_app(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, ffmpeg_exe: str):
    """按给定配置创建独立 Flask app（独立临时 DATA_DIR）。"""
    from app import create_app

    def _make(
        *,
        data_dir=None,
        max_upload_mb: int = 512,
        ttl_hours: float = 2.0,
        behind_proxy: bool = False,
        cookie_secure: bool = False,
        with_password: bool = True,
        with_secret: bool = True,
    ):
        directory = Path(data_dir) if data_dir is not None else Path(tmp_path) / "data"
        monkeypatch.setenv("SUPERSCUT_USERNAME", "admin")
        monkeypatch.setenv("SUPERSCUT_FFMPEG", ffmpeg_exe)
        monkeypatch.setenv("SUPERSCUT_DATA_DIR", str(directory))
        monkeypatch.setenv("SUPERSCUT_MAX_UPLOAD_MB", str(max_upload_mb))
        monkeypatch.setenv("SUPERSCUT_TTL_HOURS", str(ttl_hours))
        if with_password:
            monkeypatch.setenv("SUPERSCUT_PASSWORD", "test-password")
        else:
            monkeypatch.delenv("SUPERSCUT_PASSWORD", raising=False)
        if with_secret:
            monkeypatch.setenv("SUPERSCUT_SECRET_KEY", "test-secret-key")
        else:
            monkeypatch.delenv("SUPERSCUT_SECRET_KEY", raising=False)
        if behind_proxy:
            monkeypatch.setenv("SUPERSCUT_BEHIND_PROXY", "1")
        else:
            monkeypatch.delenv("SUPERSCUT_BEHIND_PROXY", raising=False)
        if cookie_secure:
            monkeypatch.setenv("SUPERSCUT_COOKIE_SECURE", "1")
        else:
            monkeypatch.delenv("SUPERSCUT_COOKIE_SECURE", raising=False)

        application = create_app()
        _apply_template_fallback(application, Path(tmp_path))
        application.config["TESTING"] = True
        return application

    return _make


@pytest.fixture()
def app(make_app):
    return make_app()


@pytest.fixture()
def client(app):
    return app.test_client()


@pytest.fixture()
def auth_client(client):
    response = client.post(
        "/login", data={"username": "admin", "password": "test-password"}
    )
    assert response.status_code == 302, response.get_data(as_text=True)
    return client
