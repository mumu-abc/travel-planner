# 跑快速测试
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)
$py = "D:\miniconda3\python.exe"
if (-not (Test-Path $py)) { $py = "python" }
& $py -m pytest tests/ -q -m "not slow"
