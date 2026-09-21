# 跑消融评测（Windows PowerShell）
# 用法：
#   .\scripts\run_ablation.ps1              # 默认 3 case（约 1 小时）
#   .\scripts\run_ablation.ps1 -Cases 1     # 快速验证（约 20 分钟）
#   .\scripts\run_ablation.ps1 -Cases 5     # 更稳（约 2 小时）
#
# 跑之前会先做一次极小的 LLM 调用做「配额自检」：
# 配额没恢复时直接终止，避免跑一小时产出全是离线降级的无效样本。
param(
  [int]$Cases = 3,
  [string]$Output = "eval/report_ablation.md",
  [switch]$SkipPreflight
)
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

# 找「装了项目依赖」的 Python，而不是第一个能启动的 Python。
# 踩过的坑：系统 python 缺 faiss / pulp，一路跑到工具调用才炸，白等很久。
$candidates = @(
  (Join-Path $env:USERPROFILE ".workbuddy\binaries\python\envs\default\Scripts\python.exe"),
  "D:\miniconda3\python.exe",
  ".venv\Scripts\python.exe",
  "python",
  "py"
)

$py = $null
foreach ($c in $candidates) {
  $cmd = $null
  if (Test-Path $c) { $cmd = (Resolve-Path $c).Path }
  elseif (Get-Command $c -ErrorAction SilentlyContinue) { $cmd = $c }
  if (-not $cmd) { continue }

  & $cmd -c "import faiss, pulp, openai, fastapi, sentence_transformers" 2>$null
  if ($LASTEXITCODE -eq 0) { $py = $cmd; break }
}

if (-not $py) {
  Write-Host ""
  Write-Host "[X] 找不到装了项目依赖的 Python（需要 faiss / pulp / openai / fastapi）"
  Write-Host "    请先安装依赖： pip install -r requirements.txt"
  exit 1
}
Write-Host "Python: $py"
Write-Host ""

$extra = @()
if ($SkipPreflight) { $extra += "--skip-preflight" }

Write-Host ">>> $py -m eval.eval_runner --cases $Cases --ablation --output $Output $extra"
& $py -m eval.eval_runner --cases $Cases --ablation --output $Output @extra

if ($LASTEXITCODE -eq 0) {
  Write-Host ""
  Write-Host "完成: $Output"
  Write-Host ""
  Write-Host "下一步："
  Write-Host "  1) 对照 eval\消融对照说明.md 读结论（重点看 multi vs single 分差与波动）"
  Write-Host "  2) 把 3 case 的均值写进 $Output，去掉 n=1 的表述"
} elseif ($LASTEXITCODE -eq 2) {
  Write-Host ""
  Write-Host "配额未恢复，已终止（本次没有产出无效报告）。恢复后重跑本命令即可。"
}
