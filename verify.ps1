# verify.ps1 —— 一键验收门（默认零费用；-RunInference 会通过 CC Switch 调一次真实模型，产生少量费用）
[CmdletBinding()]
param(
    [string]$BaseUrl = "",
    [string]$Token = "PROXY_MANAGED",
    [switch]$RunInference
)

$ErrorActionPreference = "Stop"
$root = (Resolve-Path -LiteralPath $PSScriptRoot).Path

if (-not $BaseUrl) {
    $port = 8790
    $envPath = Join-Path $root ".env"
    if (Test-Path -LiteralPath $envPath) {
        $m = Select-String -LiteralPath $envPath -Pattern '^\s*EDGE_PORT\s*=\s*(\d+)' -ErrorAction SilentlyContinue
        if ($m) { $port = [int]$m.Matches[0].Groups[1].Value }
    }
    $BaseUrl = "http://127.0.0.1:$port"
}

$python = $null
foreach ($candidate in @((Join-Path $root "_python\python.exe"), (Join-Path $root ".venv\Scripts\python.exe"), (Get-Command "py" -ErrorAction SilentlyContinue).Source, (Get-Command "python" -ErrorAction SilentlyContinue).Source)) {
    if ($candidate -and (Test-Path -LiteralPath $candidate)) { $python = $candidate; break }
}
if (-not $python) { throw "未找到 Python。" }

$arguments = @(
    (Join-Path $root "tools\verify_gateway.py"),
    "--base-url", $BaseUrl,
    "--token", $Token
)
if ($RunInference) { $arguments += "--inference" }

& $python @arguments
if ($LASTEXITCODE -ne 0) { throw "验收未通过（exit code $LASTEXITCODE）。" }
