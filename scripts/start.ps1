# 启动服务（Windows PowerShell）
# 用法：.\scripts\start.ps1
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

$py = $null
foreach ($c in @("D:\miniconda3\python.exe", "python")) {
  if (Get-Command $c -ErrorAction SilentlyContinue) { $py = $c; break }
  if (Test-Path $c) { $py = $c; break }
}
if (-not $py) { Write-Host "找不到 Python"; exit 1 }

Write-Host "使用: $py"
& $py -m uvicorn app.main:app --host 0.0.0.0 --port 8000
