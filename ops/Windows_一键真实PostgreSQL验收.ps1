# 在工程根目录运行。请先启动 Docker Desktop 并安装 requirements-postgres.txt。
# 此脚本不碰现有 PostgreSQL 数据；默认创建并销毁一个临时容器。
$ErrorActionPreference = "Stop"
Set-Location (Resolve-Path (Join-Path $PSScriptRoot '..'))
python ops/verify_fullchain_docker.py
exit $LASTEXITCODE
