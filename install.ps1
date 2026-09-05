# install.ps1 —— 一键安装：环境自检 -> 生成 .env -> 注册开机自启 -> 启动并等待健康
# 用法：
#   powershell -ExecutionPolicy Bypass -File .\install.ps1                # 标准安装
#   powershell -ExecutionPolicy Bypass -File .\install.ps1 -WithExtras    # 额外安装 PDF/Office 增强解析依赖
#   powershell -ExecutionPolicy Bypass -File .\install.ps1 -NoAutostart   # 不注册开机自启
[CmdletBinding()]
param(
    [int]$Port = 8790,
    [string]$TaskName = "Claude Office Gateway",
    [string]$PythonCommand = "python",
    [switch]$WithExtras,
    [switch]$NoAutostart
)

$ErrorActionPreference = "Stop"
$root = (Resolve-Path -LiteralPath $PSScriptRoot).Path

# 1) 安装位置检查：OneDrive/临时目录/下载目录会导致运行不稳
if ($root -match "(?i)\\OneDrive\\|\\AppData\\Local\\Temp\\|\\Downloads\\") {
    throw "请把本仓库放到固定目录（不要放 OneDrive/临时/下载目录）后再安装。当前路径：$root"
}

# 2) Python 自检（网关纯标准库，无需 pip 依赖）
$python = $null
foreach ($candidate in @((Get-Command "py" -ErrorAction SilentlyContinue), (Get-Command $PythonCommand -ErrorAction SilentlyContinue))) {
    if ($candidate) {
        $ver = & $candidate.Source -c "import sys;print('%d.%d'%sys.version_info[:2])" 2>$null
        if ($LASTEXITCODE -eq 0 -and $ver) {
            $major, $minor = $ver.Split('.')
            if ([int]$major -ge 3 -and [int]$minor -ge 9) { $python = $candidate.Source; break }
        }
    }
}
if (-not $python) { throw "未找到 Python 3.9+。请先安装 Python（python.org），勾选 'Add to PATH'。" }
$pythonw = $python -replace 'python\.exe$', 'pythonw.exe'
if (-not (Test-Path -LiteralPath $pythonw)) { $pythonw = $python }
Write-Host "[1/5] Python OK: $python"

# 3) 可选增强依赖（PDF/Office 文本提取质量更好；不装也能跑，自动退回内置解析）
if ($WithExtras) {
    Write-Host "[2/5] 安装可选增强依赖 pypdf python-docx openpyxl python-pptx ..."
    & $python -m pip install --disable-pip-version-check pypdf python-docx openpyxl python-pptx
    if ($LASTEXITCODE -ne 0) { Write-Warning "可选依赖安装失败，网关将使用内置标准库解析（功能略简化）。" }
} else {
    Write-Host "[2/5] 跳过可选增强依赖（-WithExtras 可安装）"
}

# 4) 生成 .env（已有则保留，绝不覆盖用户配置）
$envPath = Join-Path $root ".env"
if (-not (Test-Path -LiteralPath $envPath)) {
    $template = Get-Content -LiteralPath (Join-Path $root ".env.example") -Raw -Encoding UTF8
    $template = $template -replace '(?m)^EDGE_PORT=.*$', "EDGE_PORT=$Port"
    [System.IO.File]::WriteAllText($envPath, $template, [System.Text.UTF8Encoding]::new($false))
    Write-Host "[3/5] 已从 .env.example 生成 .env（端口 $Port）"
} else {
    Write-Host "[3/5] 已存在 .env，保持不变"
}

# 5) 开机自启：优先用户级计划任务（免管理员）；失败则回退 HKCU Run 注册表键
$autostart = "task"
if (-not $NoAutostart) {
    Write-Host "[4/5] 注册开机自启 ..."
    try {
        $identity = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
        $action = New-ScheduledTaskAction -Execute $pythonw -Argument ('"{0}"' -f (Join-Path $root "supervisor.py")) -WorkingDirectory $root
        $trigger = New-ScheduledTaskTrigger -AtLogOn -User $identity
        $principal = New-ScheduledTaskPrincipal -UserId $identity -LogonType Interactive -RunLevel Limited
        $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1)
        Register-ScheduledTask -TaskName $TaskName -Description "Claude for Office x CC Switch local gateway (supervised) on 127.0.0.1:$Port" -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force | Out-Null
    } catch {
        Write-Warning "计划任务注册失败（$($_.Exception.Message)），改用 HKCU Run 回退方案。"
        $runKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run"
        $runValue = '"' + $pythonw + '" "' + (Join-Path $root "supervisor.py") + '"'
        if (-not (Get-ItemProperty -Path $runKey -Name $TaskName -ErrorAction SilentlyContinue)) {
            New-ItemProperty -Path $runKey -Name $TaskName -Value $runValue -PropertyType String -Force | Out-Null
        }
        $autostart = "runkey"
    }
} else {
    Write-Host "[4/5] 跳过开机自启（-NoAutostart）"
    $autostart = "none"
}

# 6) 启动并等待健康
Write-Host "[5/5] 启动网关并等待 /healthz ..."
if ($autostart -eq "task") { Start-ScheduledTask -TaskName $TaskName }
elseif ($autostart -eq "runkey") { Start-Process -FilePath $pythonw -ArgumentList ('"{0}"' -f (Join-Path $root "supervisor.py")) -WorkingDirectory $root }
else { Start-Process -FilePath $pythonw -ArgumentList ('"{0}"' -f (Join-Path $root "supervisor.py")) -WorkingDirectory $root }

$deadline = [DateTime]::UtcNow.AddSeconds(30)
$healthy = $false
do {
    Start-Sleep -Seconds 1
    try {
        $health = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/healthz" -TimeoutSec 3
        if ($health.status -eq "ok") { $healthy = $true }
    } catch { }
} while (-not $healthy -and [DateTime]::UtcNow -lt $deadline)

if (-not $healthy) { throw "网关已启动流程完成，但 /healthz 在 30 秒内未就绪。请查看 runtime\edge-sup.err 与 runtime\edge-sup.out。" }

Write-Host ""
Write-Host "=== 安装完成：网关运行于 http://127.0.0.1:$Port （自启方式：$autostart）==="
Write-Host "下一步（Office 侧二选一，详见 docs\OFFICE-ONBOARDING.md）："
Write-Host "  A. 官方 Claude 加载项的 Gateway 配置里填：URL http://127.0.0.1:$Port | Token PROXY_MANAGED | Header x-api-key | Format Anthropic Messages"
Write-Host "  B. 老版本 Office 没有该配置界面时：powershell -ExecutionPolicy Bypass -File .\scripts\Install-DeveloperSideload.ps1"
Write-Host "验收：powershell -ExecutionPolicy Bypass -File .\verify.ps1"
