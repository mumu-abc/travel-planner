# 跑消融评测（Windows PowerShell）
# 用法：
#   .\scripts\run_ablation.ps1              # 默认 3 case（较慢）
#   .\scripts\run_ablation.ps1 -Cases 1     # 快速验证
#   .\scripts\run_ablation.ps1 -Cases 5
param(
  [int]$Cases = 3,
  [string]$Output = "eval/report_ablation.md"
)
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

$py = $null
foreach ($c in @("D:\miniconda3\python.exe", "python")) {
  if (Get-Command $c -ErrorAction SilentlyContinue) { $py = $c; break }
  if (Test-Path $c) { $py = $c; break }
}
if (-not $py) { Write-Host "找不到 Python"; exit 1 }

Write-Host ">>> $py -m eval.eval_runner --cases $Cases --ablation --output $Output"
& $py -m eval.eval_runner --cases $Cases --ablation --output $Output
if ($LASTEXITCODE -eq 0) {
  Write-Host ""
  Write-Host "完成: $Output"
  Write-Host "请对照 eval\消融对照说明.md 填结论，并更新 docs\简历项目段.md"
}
