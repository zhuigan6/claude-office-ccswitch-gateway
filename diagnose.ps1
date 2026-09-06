# diagnose.ps1 —— 一键诊断：收集网关健康/通道/进程/自启/日志尾部，自动脱敏，方便贴进 Issue
# 用法：
#   powershell -ExecutionPolicy Bypass -File .\diagnose.ps1              # 输出到屏幕
#   powershell -ExecutionPolicy Bypass -File .\diagnose.ps1 -OutFile diagnose-report.txt
[CmdletBinding()]
param(
    [int]$Port = 0,
    [string]$OutFile = ""
)

$ErrorActionPreference = "Continue"
$root = (Resolve-Path -LiteralPath $PSScriptRoot).Path
if ($Port -eq 0) {
    $Port = 8790
    $envPath = Join-Path $root ".env"
    if (Test-Path -LiteralPath $envPath) {
        $m = Select-String -LiteralPath $envPath -Pattern '^\s*EDGE_PORT\s*=\s*(\d+)' -ErrorAction SilentlyContinue
        if ($m) { $Port = [int]$m.Matches[0].Groups[1].Value }
    }
}

$lines = New-Object System.Collections.Generic.List[string]
function Add-Line($text) { $script:lines.Add([string]$text) }
function Add-Section($title) {
    Add-Line ""
    Add-Line ("==== " + $title + " ====")
}

# 脱敏：令牌/密钥一律打码（避免用户把报告贴出去时泄漏）
function Hide-Secret([string]$s) {
    if (-not $s) { return $s }
    $s = $s -replace '(?i)"token"\s*:\s*"[^"]*"', '"token": "***REDACTED***"'
    $s = $s -replace '(?i)(authorization"\s*:\s*"?)(bearer\s+)?[^",\r\n]{8,}', '$1***REDACTED***'
    $s = $s -replace '\b(?:edge|ccs)-[0-9a-fA-F]{12,}\b', '***REDACTED***'
    $s = $s -replace '\bsk-[A-Za-z0-9_-]{10,}\b', '***REDACTED***'
    return $s
}

Add-Line ("Claude Office Gateway 诊断报告  " + (Get-Date -Format "yyyy-MM-dd HH:mm:ss"))
Add-Line ("仓库目录: $root")

Add-Section "系统"
Add-Line ("OS: " + [System.Environment]::OSVersion.VersionString)
Add-Line ("PS: " + $PSVersionTable.PSVersion)
$pyExe = Join-Path $root "_python\python.exe"
if (Test-Path -LiteralPath $pyExe) { Add-Line "运行时: 自带 _python（无需系统 Python）" }
else {
    $sysPy = Get-Command python -ErrorAction SilentlyContinue
    Add-Line ("运行时: 系统 Python = " + $(if ($sysPy) { $sysPy.Source } else { "未找到" }))
}

Add-Section "网关健康 (http://127.0.0.1:$Port/healthz)"
try {
    $h = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/healthz" -TimeoutSec 5
    Add-Line ("status=" + $h.status + " version=" + $h.version + " channel=" + $h.ccswitch_channel +
        " provider=" + $h.active_provider)
    Add-Line ("model_routes=" + (($h.model_routes.PSObject.Properties | ForEach-Object { "$($_.Name)=$($_.Value)" }) -join ", "))
} catch { Add-Line ("healthz 不可达: " + $_.Exception.Message) }

Add-Section "CC Switch 状态（令牌已打码）"
try {
    $s = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/status/ccswitch" -TimeoutSec 5
    $s.token = "***REDACTED***"
    Add-Line (Hide-Secret (ConvertTo-Json $s -Depth 5 -Compress))
} catch { Add-Line ("status/ccswitch 不可达: " + $_.Exception.Message) }

Add-Section "进程"
Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -match 'office_edge\.py|supervisor\.py|Supervisor\.ps1' } |
    ForEach-Object { Add-Line ("pid=" + $_.ProcessId + "  " + $_.CommandLine) }
if (-not (Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -match 'office_edge\.py|supervisor\.py|Supervisor\.ps1' })) {
    Add-Line "（无相关进程在运行）"
}

Add-Section "端口监听"
Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue | Where-Object { $_.LocalPort -eq $Port } |
    ForEach-Object { Add-Line ("pid=" + $_.OwningProcess + "  addr=" + $_.LocalAddress + ":" + $_.LocalPort) }

Add-Section "开机自启"
$task = Get-ScheduledTask -TaskName "Claude Office Gateway" -ErrorAction SilentlyContinue
Add-Line ("计划任务 'Claude Office Gateway': " + $(if ($task) { $task.State } else { "不存在" }))
$runVal = (Get-ItemProperty -Path "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run" -ErrorAction SilentlyContinue)."Claude Office Gateway"
Add-Line ("HKCU Run 'Claude Office Gateway': " + $(if ($runVal) { $runVal } else { "不存在" }))
Add-Line ("HKCU Run 旧键 'CCSwitchOfficeSupervisor': " +
    $(if ((Get-ItemProperty -Path "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run" -ErrorAction SilentlyContinue)."CCSwitchOfficeSupervisor") { "仍存在（旧版残留）" } else { "不存在" }))

Add-Section "WebView2 / Office 加载项环境"
$pv = (Get-ItemProperty -Path "HKLM:\SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}" -ErrorAction SilentlyContinue).pv
if (-not $pv) { $pv = (Get-ItemProperty -Path "HKLM:\SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}" -ErrorAction SilentlyContinue).pv }
Add-Line ("WebView2 Runtime: " + $(if ($pv) { $pv } else { "未检测到（Office 加载项将无法运行）" }))
$wef = Join-Path $env:LOCALAPPDATA "Microsoft\Office\16.0\Wef"
if (Test-Path -LiteralPath $wef) {
    $wv2 = Join-Path $wef "webview2"
    $slots = if (Test-Path -LiteralPath $wv2) { (Get-ChildItem -LiteralPath $wv2 -Directory -ErrorAction SilentlyContinue).Count } else { 0 }
    Add-Line ("Office Wef 目录存在；WebView 数据槽位数量: " + $slots + "（仅统计，不读取内容）")
    Add-Line "提示：聊天历史保存在对应槽位的 IndexedDB 中；槽位数量突然变化通常意味着 Office 升级换了存储槽。"
} else {
    Add-Line "Office Wef 目录不存在（Office 可能未运行过加载项）"
}

Add-Section "版本检查（按需联网，无后台外呼）"
try {
    $rel = Invoke-RestMethod -Uri "https://api.github.com/repos/zhuigan6/claude-office-ccswitch-gateway/releases/latest" -TimeoutSec 6
    $local = $null
    try { $local = (Invoke-RestMethod -Uri "http://127.0.0.1:$Port/healthz" -TimeoutSec 2).version } catch { }
    Add-Line ("本地网关: " + $(if ($local) { $local } else { "未运行" }) + "   最新 Release: " + $rel.tag_name)
    if ($local -and (($rel.tag_name -replace '^v', '') -ne [string]$local)) {
        Add-Line "提示：有新版本。升级方式见 docs\DEPLOYMENT-WINDOWS.md（git pull 或重新下载解压后重跑 install.ps1）。"
    }
} catch {
    Add-Line ("跳过（离线或 GitHub API 不可达）: " + $_.Exception.Message)
}

Add-Section "日志尾部（已脱敏）"
foreach ($f in @("edge-sup.err", "edge-sup.out")) {
    $p = Join-Path $root ("runtime\" + $f)
    Add-Line "--- $f ---"
    if (Test-Path -LiteralPath $p) {
        Get-Content -LiteralPath $p -Tail 30 -Encoding UTF8 | ForEach-Object { Add-Line (Hide-Secret $_) }
    } else { Add-Line "（不存在）" }
}
$up = Join-Path $root "runtime\last-upstream-error.txt"
Add-Line "--- last-upstream-error.txt（前 20 行）---"
if (Test-Path -LiteralPath $up) {
    Get-Content -LiteralPath $up -TotalCount 20 -Encoding UTF8 | ForEach-Object { Add-Line (Hide-Secret $_) }
} else { Add-Line "（不存在，说明最近没有上游 4xx）" }

Add-Line ""
Add-Line "==== 报告结束（以上内容已自动打码令牌/密钥）===="

if ($OutFile) {
    [System.IO.File]::WriteAllLines((Join-Path $root $OutFile), $lines,
        [System.Text.UTF8Encoding]::new($false))
    Write-Host "诊断报告已写入: $(Join-Path $root $OutFile)"
    Write-Host "贴 Issue 前请再通读一遍，确认没有个人信息。"
} else {
    $lines | ForEach-Object { Write-Host $_ }
}
