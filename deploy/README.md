# Supercut 部署说明（Linux + Caddy 子路径）

生产形态（与仓库中 `/pw`、`/supersys` 等服务一致的隧道模式，应用机不直接暴露公网端口）：

```
浏览器 ──HTTPS──> Caddy 反代机 (wkeeli.cn:443) ──> 127.0.0.1:18070 ──> frps
                                                              │
                                                        frpc 隧道
                                                              ▼
                                            应用机 127.0.0.1:8070 gunicorn ──> FFmpeg
```

若应用机与反代机同机、或反代机能直连应用机，也可以省略 frp，直接在 Caddy 里写
`reverse_proxy http://<app-host>:8070`。

frp 客户端（应用机 `/opt/frp/frpc.toml`）新增：

```toml
[[proxies]]
name = "supercut"
type = "tcp"
localIP = "127.0.0.1"
localPort = 8070
remotePort = 18070
```

重启 `frpc` 后，反代机 `curl -s http://127.0.0.1:18070/api/health` 应返回 JSON。

## 1. 准备应用机

```bash
sudo apt install -y ffmpeg python3-venv
sudo useradd --system --create-home --home-dir /var/lib/supercut --shell /usr/sbin/nologin supercut
sudo mkdir -p /opt/supercut /var/lib/supercut
sudo chown -R supercut:supercut /var/lib/supercut
```

同步代码到 `/opt/supercut`（`app.py`、`templates/`、`static/`、`requirements.txt` 即可）：

```bash
# 在本地项目目录执行（tar 管道不依赖 rsync）
tar --exclude=.git --exclude=instance --exclude=__pycache__ -czf - \
    app.py templates static requirements.txt | ssh <app-host> \
    "sudo tar -xzf - -C /opt/supercut"
```

创建 venv 并安装依赖：

```bash
python3 -m venv /opt/supercut/venv
/opt/supercut/venv/bin/pip install -r /opt/supercut/requirements.txt
```

## 2. systemd

```bash
sudo cp deploy/supercut.service /etc/systemd/system/supercut.service
sudo nano /etc/systemd/system/supercut.service   # 替换 SUPERSCUT_PASSWORD / SECRET_KEY
sudo systemctl daemon-reload
sudo systemctl enable --now supercut
curl -s http://127.0.0.1:8070/api/health
# {"ok":true,...}
```

## 3. Caddy 子路径

把 [`Caddyfile.snippet`](Caddyfile.snippet) 插入反代机 `/etc/caddy/Caddyfile` 的站点块内（在兜底
`reverse_proxy localhost:5000` 之前），把 `http://180.76.180.249:8070` 换成应用机实际地址，然后：

```bash
sudo cp /etc/caddy/Caddyfile /etc/caddy/Caddyfile.bak-$(date +%Y%m%d-%H%M%S)
sudo caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
sudo systemctl reload caddy
```

访问 `https://<domain>/supercut/`，用 systemd 中配置的账号密码登录。

## 4. 注意

- `SUPERSCUT_BEHIND_PROXY=1` 与 Caddy 的 `X-Forwarded-Prefix` 必须同时配置，否则登录跳转与静态资源会丢失子路径。
- 线上必须 `SUPERSCUT_COOKIE_SECURE=1`（HTTPS）。
- 上传/导出临时文件在 `SUPERSCUT_DATA_DIR`（默认 2 小时清理），部署升级时不要删除该目录。
- 视频处理 CPU 密集：转码时长约为视频时长的 0.3~1.5 倍（视分辨率与机器），gunicorn `--threads 4` 允许并发 4 个任务。
- 密码通过环境变量注入，不进入 Git；修改密码后 `systemctl restart supercut` 即可。
