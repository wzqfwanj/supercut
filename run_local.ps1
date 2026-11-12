# 本地开发启动（仅 Windows；生产用 gunicorn + systemd）
# 用法：powershell -ExecutionPolicy Bypass -File .\run_local.ps1 [-Port 8080]
param(
    [int]$Port = 8080
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (-not $env:SUPERSCUT_SECRET_KEY) {
    $env:SUPERSCUT_SECRET_KEY = -join ((48..57) + (97..122) | Get-Random -Count 40 | ForEach-Object { [char]$_ })
}
if (-not $env:SUPERSCUT_USERNAME) { $env:SUPERSCUT_USERNAME = "admin" }
if (-not $env:SUPERSCUT_PASSWORD) { $env:SUPERSCUT_PASSWORD = "supercut-dev" }
if (-not $env:SUPERSCUT_MAX_UPLOAD_MB) { $env:SUPERSCUT_MAX_UPLOAD_MB = "512" }
$env:SUPERSCUT_HOST = "127.0.0.1"
$env:SUPERSCUT_PORT = "$Port"

Write-Host "Supercut 本地开发服务器: http://127.0.0.1:$Port" -ForegroundColor Cyan
Write-Host "用户名: $env:SUPERSCUT_USERNAME" -ForegroundColor Cyan
Write-Host "密码:   $env:SUPERSCUT_PASSWORD" -ForegroundColor Cyan

python app.py
