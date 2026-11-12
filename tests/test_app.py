"""Supercut 后端测试：鉴权 / health / 上传探测 / 剪片段 / 剪窗口范围 / 下载 / 清理。

全部离线：测试视频由本机 ffmpeg（imageio-ffmpeg）现场生成，DATA_DIR 指向临时目录。
"""

from __future__ import annotations

import io
import json
import os
import re
import time
from pathlib import Path

import pytest
from flask import template_rendered

USERNAME = "admin"
PASSWORD = "test-password"
HEX_ID = re.compile(r"^[0-9a-f]{32}$")


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------


def _login(client, username: str = USERNAME, password: str = PASSWORD):
    return client.post("/login", data={"username": username, "password": password})


def _upload(client, sample_video: Path, filename: str = "sample.mp4"):
    return client.post(
        "/api/upload",
        data={"file": (io.BytesIO(sample_video.read_bytes()), filename)},
        content_type="multipart/form-data",
    )


def _uploaded_info(auth_client, sample_video: Path) -> dict:
    response = _upload(auth_client, sample_video)
    assert response.status_code == 200, response.get_data(as_text=True)
    return response.get_json()


def _assert_json_error(response, status: int):
    assert response.status_code == status, response.get_data(as_text=True)
    assert response.mimetype == "application/json"
    body = response.get_json()
    assert isinstance(body, dict) and set(body) == {"error"}
    assert isinstance(body["error"], str) and body["error"]


# ---------------------------------------------------------------------------
# 鉴权与页面
# ---------------------------------------------------------------------------


def test_health_is_public(client):
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.mimetype == "application/json"
    assert response.content_type == "application/json; charset=utf-8"
    body = response.get_json()
    assert set(body) == {"ok", "ffmpeg", "max_upload_mb"}
    assert body["ok"] is True
    assert body["ffmpeg"] is True
    assert body["max_upload_mb"] == 512


def test_page_requires_login(client):
    response = client.get("/")
    assert response.status_code == 302
    assert "/login" in response.headers["Location"]


def test_login_page_renders(client):
    response = client.get("/login")
    assert response.status_code == 200
    assert response.mimetype == "text/html"


def test_api_requires_login(client, sample_video):
    _assert_json_error(client.get("/api/download/" + "0" * 32), 401)
    _assert_json_error(
        client.post("/api/trim", json={"id": "0" * 32, "start": 0, "end": 1}), 401
    )
    _assert_json_error(
        client.post(
            "/api/upload",
            data={"file": (io.BytesIO(sample_video.read_bytes()), "sample.mp4")},
            content_type="multipart/form-data",
        ),
        401,
    )
    _assert_json_error(
        client.post(
            "/api/crop",
            json={"id": "0" * 32, "x": 0, "y": 0, "width": 16, "height": 16},
        ),
        401,
    )


def test_login_success(client):
    response = _login(client)
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/")
    assert client.get("/").status_code == 200
    # 已登录访问登录页 → 302 到首页
    assert client.get("/login").status_code == 302


def test_login_failure_rerenders_with_error(app, client):
    captured: list[tuple[str, dict]] = []

    def record(sender, template, context, **extra):
        captured.append((template.name, context))

    template_rendered.connect(record, app)
    try:
        response = _login(client, password="wrong-password")
    finally:
        template_rendered.disconnect(record, app)

    assert response.status_code == 200
    assert response.mimetype == "text/html"
    assert captured, "登录失败必须重新渲染登录页"
    template_name, context = captured[-1]
    assert template_name == "login.html"
    assert context["error"] == "用户名或密码错误"
    assert context["username"] == USERNAME
    # 失败后仍未登录
    assert client.get("/").status_code == 302


def test_login_wrong_username(app, client):
    response = _login(client, username="root")
    assert response.status_code == 200
    assert client.get("/").status_code == 302


def test_logout_clears_session(client):
    assert _login(client).status_code == 302
    response = client.post("/logout")
    assert response.status_code == 302
    assert "/login" in response.headers["Location"]
    assert client.get("/").status_code == 302


def test_session_cookie_flags(client):
    response = _login(client)
    cookie = response.headers.get("Set-Cookie", "")
    assert "HttpOnly" in cookie
    assert "SameSite=Lax" in cookie


def test_cookie_secure_flag(make_app):
    application = make_app(cookie_secure=True)
    client = application.test_client()
    response = _login(client)
    assert response.status_code == 302
    assert "Secure" in response.headers.get("Set-Cookie", "")


def test_proxy_fix_restores_script_root(make_app, sample_video):
    application = make_app(behind_proxy=True)
    client = application.test_client()
    headers = {
        "X-Forwarded-For": "203.0.113.7",
        "X-Forwarded-Proto": "https",
        "X-Forwarded-Host": "wkeeli.cn",
        "X-Forwarded-Prefix": "/supercut",
    }
    response = client.post(
        "/login",
        data={"username": USERNAME, "password": PASSWORD},
        headers=headers,
    )
    assert response.status_code == 302
    assert response.headers["Location"].startswith("/supercut/")

    upload = client.post(
        "/api/upload",
        data={
            "file": (
                io.BytesIO(sample_video.read_bytes()),
                "sample.mp4",
            )
        },
        content_type="multipart/form-data",
        headers=headers,
    )
    assert upload.status_code == 200, upload.get_data(as_text=True)
    video_id = upload.get_json()["id"]

    trim = client.post(
        "/api/trim",
        json={"id": video_id, "start": 0.0, "end": 1.0},
        headers=headers,
    )
    assert trim.status_code == 200, trim.get_data(as_text=True)
    assert trim.get_json()["url"].startswith("/supercut/api/download/")


def test_app_requires_password(make_app):
    with pytest.raises(RuntimeError):
        make_app(with_password=False)


def test_app_requires_secret_key(make_app):
    with pytest.raises(RuntimeError):
        make_app(with_secret=False)


# ---------------------------------------------------------------------------
# 上传
# ---------------------------------------------------------------------------


def test_upload_returns_metadata(auth_client, sample_video):
    response = _upload(auth_client, sample_video)
    assert response.status_code == 200, response.get_data(as_text=True)
    body = response.get_json()
    assert set(body) == {
        "id",
        "name",
        "duration",
        "width",
        "height",
        "size",
        "max_upload_mb",
    }
    assert body["name"] == "sample.mp4"
    assert HEX_ID.fullmatch(body["id"])
    assert body["width"] == 320
    assert body["height"] == 240
    assert abs(body["duration"] - 2.0) <= 0.25
    assert body["size"] == sample_video.stat().st_size
    assert body["max_upload_mb"] == 512

    data_dir = Path(auth_client.application.config["DATA_DIR"])
    source = data_dir / "uploads" / f"{body['id']}.mp4"
    assert source.is_file()
    assert source.stat().st_size == body["size"]
    meta_path = data_dir / "uploads" / f"{body['id']}.json"
    metadata = json.loads(meta_path.read_text(encoding="utf-8"))
    assert set(metadata) == {
        "name",
        "duration",
        "width",
        "height",
        "size",
        "ext",
        "created",
    }
    assert metadata["ext"] == "mp4"
    assert metadata["size"] == body["size"]


def test_upload_rejects_bad_extension(auth_client):
    response = auth_client.post(
        "/api/upload",
        data={"file": (io.BytesIO(b"hello"), "note.txt")},
        content_type="multipart/form-data",
    )
    _assert_json_error(response, 400)


def test_upload_rejects_unparseable_video(auth_client):
    response = auth_client.post(
        "/api/upload",
        data={"file": (io.BytesIO(b"definitely not a video" * 64), "broken.mp4")},
        content_type="multipart/form-data",
    )
    _assert_json_error(response, 400)


def test_upload_rejects_empty_file(auth_client):
    response = auth_client.post(
        "/api/upload",
        data={"file": (io.BytesIO(b""), "empty.mp4")},
        content_type="multipart/form-data",
    )
    _assert_json_error(response, 400)


def test_upload_rejects_missing_file(auth_client):
    response = auth_client.post(
        "/api/upload", data={}, content_type="multipart/form-data"
    )
    _assert_json_error(response, 400)


def test_upload_rejects_oversized_file(make_app):
    application = make_app(max_upload_mb=1)
    client = application.test_client()
    assert _login(client).status_code == 302
    oversized = io.BytesIO(b"\x00" * (1024 * 1024 + 512 * 1024))
    response = client.post(
        "/api/upload",
        data={"file": (oversized, "big.mp4")},
        content_type="multipart/form-data",
    )
    _assert_json_error(response, 413)


def test_upload_rejects_request_over_max_content_length(make_app):
    """Werkzeug 在解析 multipart 前就会因 MAX_CONTENT_LENGTH 抛 413，也必须返回 JSON。"""
    application = make_app(max_upload_mb=1)
    client = application.test_client()
    assert _login(client).status_code == 302
    oversized = io.BytesIO(b"\x00" * (3 * 1024 * 1024 + 512 * 1024))
    response = client.post(
        "/api/upload",
        data={"file": (oversized, "huge.mp4")},
        content_type="multipart/form-data",
    )
    _assert_json_error(response, 413)


# ---------------------------------------------------------------------------
# 剪片段
# ---------------------------------------------------------------------------


def test_trim_exports_clip(auth_client, sample_video, probe_media):
    uploaded = _uploaded_info(auth_client, sample_video)
    response = auth_client.post(
        "/api/trim",
        json={"id": uploaded["id"], "start": 0.25, "end": 1.25},
    )
    assert response.status_code == 200, response.get_data(as_text=True)
    body = response.get_json()
    assert set(body) == {
        "token",
        "url",
        "download_name",
        "mode",
        "start",
        "end",
        "duration",
        "size",
    }
    assert body["mode"] == "trim"
    assert body["start"] == 0.25
    assert body["end"] == 1.25
    assert body["duration"] == 1.0
    assert body["download_name"] == "sample_clip.mp4"
    assert HEX_ID.fullmatch(body["token"])
    assert body["url"].endswith(f"/api/download/{body['token']}")

    data_dir = Path(auth_client.application.config["DATA_DIR"])
    output = data_dir / "outputs" / f"{body['token']}.mp4"
    assert output.is_file()
    assert output.stat().st_size == body["size"] > 0
    info = probe_media(output)
    assert (info["width"], info["height"]) == (320, 240)
    assert abs(info["duration"] - 1.0) <= 0.3

    meta_path = data_dir / "outputs" / f"{body['token']}.json"
    metadata = json.loads(meta_path.read_text(encoding="utf-8"))
    assert set(metadata) == {"download_name", "mode", "created"}
    assert metadata["mode"] == "trim"
    assert metadata["download_name"] == "sample_clip.mp4"


def test_trim_clamps_end_to_video_duration(auth_client, sample_video, probe_media):
    uploaded = _uploaded_info(auth_client, sample_video)
    response = auth_client.post(
        "/api/trim",
        json={"id": uploaded["id"], "start": 0.5, "end": 99},
    )
    assert response.status_code == 200, response.get_data(as_text=True)
    body = response.get_json()
    assert body["end"] <= uploaded["duration"] + 1e-6
    assert abs(body["duration"] - (uploaded["duration"] - 0.5)) <= 0.3
    data_dir = Path(auth_client.application.config["DATA_DIR"])
    info = probe_media(data_dir / "outputs" / f"{body['token']}.mp4")
    assert abs(info["duration"] - body["duration"]) <= 0.3


def test_trim_rejects_clip_shorter_than_minimum(auth_client, sample_video):
    uploaded = _uploaded_info(auth_client, sample_video)
    response = auth_client.post(
        "/api/trim",
        json={"id": uploaded["id"], "start": 1.0, "end": 1.05},
    )
    _assert_json_error(response, 400)


@pytest.mark.parametrize(
    "payload",
    [
        {"start": -1, "end": 1},
        {"start": 1, "end": 1},
        {"start": 2, "end": 1},
        {"start": "0", "end": 1},
        {"start": 0, "end": None},
        {"start": 0},
    ],
)
def test_trim_invalid_params(auth_client, sample_video, payload):
    uploaded = _uploaded_info(auth_client, sample_video)
    response = auth_client.post(
        "/api/trim", json={"id": uploaded["id"], **payload}
    )
    _assert_json_error(response, 400)


def test_trim_requires_json_object(auth_client, sample_video):
    _uploaded_info(auth_client, sample_video)
    response = auth_client.post(
        "/api/trim", data="not-json", content_type="text/plain"
    )
    _assert_json_error(response, 400)


def test_trim_unknown_id(auth_client):
    response = auth_client.post(
        "/api/trim", json={"id": "f" * 32, "start": 0, "end": 1}
    )
    _assert_json_error(response, 404)


def test_trim_malformed_id(auth_client):
    response = auth_client.post(
        "/api/trim", json={"id": "../../etc/passwd", "start": 0, "end": 1}
    )
    _assert_json_error(response, 400)


# ---------------------------------------------------------------------------
# 剪窗口范围
# ---------------------------------------------------------------------------


def test_crop_exports_region(auth_client, sample_video, probe_media):
    uploaded = _uploaded_info(auth_client, sample_video)
    response = auth_client.post(
        "/api/crop",
        json={"id": uploaded["id"], "x": 10, "y": 10, "width": 101, "height": 51},
    )
    assert response.status_code == 200, response.get_data(as_text=True)
    body = response.get_json()
    assert set(body) == {
        "token",
        "url",
        "download_name",
        "mode",
        "x",
        "y",
        "width",
        "height",
        "size",
    }
    assert body["mode"] == "crop"
    assert (body["x"], body["y"]) == (10, 10)
    # 101x51 向下取偶 → 100x50
    assert (body["width"], body["height"]) == (100, 50)
    assert body["download_name"] == "sample_crop.mp4"
    assert HEX_ID.fullmatch(body["token"])

    data_dir = Path(auth_client.application.config["DATA_DIR"])
    output = data_dir / "outputs" / f"{body['token']}.mp4"
    assert output.is_file()
    assert output.stat().st_size == body["size"] > 0
    info = probe_media(output)
    assert (info["width"], info["height"]) == (100, 50)


def test_crop_full_frame(auth_client, sample_video, probe_media):
    uploaded = _uploaded_info(auth_client, sample_video)
    response = auth_client.post(
        "/api/crop",
        json={"id": uploaded["id"], "x": 0, "y": 0, "width": 320, "height": 240},
    )
    assert response.status_code == 200, response.get_data(as_text=True)
    body = response.get_json()
    assert (body["width"], body["height"]) == (320, 240)
    data_dir = Path(auth_client.application.config["DATA_DIR"])
    info = probe_media(data_dir / "outputs" / f"{body['token']}.mp4")
    assert (info["width"], info["height"]) == (320, 240)


@pytest.mark.parametrize(
    "region",
    [
        {"x": 0, "y": 0, "width": 15, "height": 100},
        {"x": 0, "y": 0, "width": 100, "height": 15},
        {"x": -1, "y": 0, "width": 100, "height": 100},
        {"x": 0, "y": -1, "width": 100, "height": 100},
        {"x": 280, "y": 0, "width": 100, "height": 100},
        {"x": 0, "y": 200, "width": 100, "height": 100},
        {"x": 0.5, "y": 0, "width": 100, "height": 100},
        {"x": 0, "y": 0, "width": "100", "height": 100},
    ],
)
def test_crop_invalid_region(auth_client, sample_video, region):
    uploaded = _uploaded_info(auth_client, sample_video)
    response = auth_client.post(
        "/api/crop", json={"id": uploaded["id"], **region}
    )
    _assert_json_error(response, 400)


def test_crop_unknown_id(auth_client):
    response = auth_client.post(
        "/api/crop",
        json={"id": "a" * 32, "x": 0, "y": 0, "width": 16, "height": 16},
    )
    _assert_json_error(response, 404)


# ---------------------------------------------------------------------------
# 下载
# ---------------------------------------------------------------------------


def test_download_returns_attachment(auth_client, sample_video):
    uploaded = _uploaded_info(auth_client, sample_video)
    trim = auth_client.post(
        "/api/trim", json={"id": uploaded["id"], "start": 0.0, "end": 1.0}
    )
    assert trim.status_code == 200, trim.get_data(as_text=True)
    body = trim.get_json()

    response = auth_client.get(body["url"])
    assert response.status_code == 200
    assert response.mimetype == "video/mp4"
    disposition = response.headers["Content-Disposition"]
    assert "attachment" in disposition
    assert "sample_clip.mp4" in disposition
    data = response.get_data()
    assert len(data) == body["size"]
    assert b"ftyp" in data[:64]


def test_download_missing_token(auth_client):
    _assert_json_error(auth_client.get("/api/download/" + "a" * 32), 404)


def test_download_invalid_token(auth_client):
    _assert_json_error(auth_client.get("/api/download/not-a-token"), 404)


# ---------------------------------------------------------------------------
# TTL 清理
# ---------------------------------------------------------------------------


def test_startup_cleanup_removes_expired_files(make_app, tmp_path):
    data_dir = tmp_path / "ttl-startup"
    uploads = data_dir / "uploads"
    outputs = data_dir / "outputs"
    uploads.mkdir(parents=True)
    outputs.mkdir(parents=True)

    expired_stamp = time.time() - 3 * 3600
    old_video = uploads / ("d" * 32 + ".mp4")
    old_meta = uploads / ("d" * 32 + ".json")
    old_video.write_bytes(b"old")
    old_meta.write_text("{}", encoding="utf-8")
    for path in (old_video, old_meta):
        os.utime(path, (expired_stamp, expired_stamp))

    fresh_output = outputs / ("e" * 32 + ".mp4")
    fresh_output.write_bytes(b"fresh")

    make_app(data_dir=data_dir, ttl_hours=2)

    assert not old_video.exists()
    assert not old_meta.exists()
    assert fresh_output.exists()


def test_opportunistic_cleanup_after_upload(make_app, sample_video):
    application = make_app(ttl_hours=0.0005)  # 1.8 秒
    client = application.test_client()
    assert _login(client).status_code == 302

    uploads = Path(application.config["DATA_DIR"]) / "uploads"
    expired_stamp = time.time() - 3600
    stale = uploads / ("c" * 32 + ".mp4")
    stale.write_bytes(b"stale")
    os.utime(stale, (expired_stamp, expired_stamp))

    assert _upload(client, sample_video).status_code == 200
    assert not stale.exists()


# ---------------------------------------------------------------------------
# 错误格式
# ---------------------------------------------------------------------------


def test_unknown_api_route_returns_json_error(auth_client):
    _assert_json_error(auth_client.get("/api/does-not-exist"), 404)
