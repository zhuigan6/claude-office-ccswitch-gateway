# install.ps1 —— 一键安装：旧版迁移 -> 环境自检 -> 生成 .env -> 注册开机自启 -> 启动并等待健康
# 用法：
#   双击 install.bat（等价于下面的命令）
#   powershell -ExecutionPolicy Bypass -File .\install.ps1                # 标准安装
#   powershell -ExecutionPolicy Bypass -File .\install.ps1 -WithExtras    # 额外安装 PDF/Office 增强解析依赖
#   powershell -ExecutionPolicy Bypass -File .\install.ps1 -NoAutostart   # 不注册开机自启
#   powershell -ExecutionPolicy Bypass -File .\install.ps1 -Port 8787     # 换端口
[CmdletBinding()]
param(
    [int]$Port = 8790,
    [string]$TaskName = "Claude Office Gateway",
    [string]$PythonCommand = "python",
    [string]$LegacyPath = "",
    [switch]$WithExtras,
    [switch]$NoAutostart
)

$ErrorActionPreference = "Stop"
$root = (Resolve-Path -LiteralPath $PSScriptRoot).Path

# 1) 安装位置检查：OneDrive/临时目录/下载目录会导致运行不稳
if ($root -match "(?i)\\OneDrive\\|\\AppData\\Local\\Temp\\|\\Downloads\\") {
    throw "请把本仓库放到固定目录（不要放 OneDrive/临时/下载目录）后再安装。当前路径：$root"
}

# 2) 旧版/残留迁移：仅清理属于本目录（或 -LegacyPath 显式指定的旧版目录）的进程，
#    按"先守护后网关"顺序；多副本安装互不影响——别的目录里的网关不受本安装影响
foreach ($pattern in @('supervisor\.py|Supervisor\.ps1', 'office_edge\.py')) {
    $oldProcs = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
        Where-Object {
            $_.CommandLine -and $_.CommandLine -match $pattern -and
            (($_.CommandLine -like "*$root*") -or ($LegacyPath -and $_.CommandLine -like "*$LegacyPath*"))
        }
    foreach ($p in $oldProcs) {
        try {
            Stop-Process -Id $p.ProcessId -Force -ErrorAction Stop
            Write-Host ("已停止旧网关/守护进程 pid={0}" -f $p.ProcessId)
        } catch { }  # 父进程终止时子进程可能已一并退出
    }
    if ($oldProcs) { Start-Sleep -Seconds 1 }
}
$legacyRun = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run"
if (Get-ItemProperty -Path $legacyRun -Name "CCSwitchOfficeSupervisor" -ErrorAction SilentlyContinue) {
    Remove-ItemProperty -Path $legacyRun -Name "CCSwitchOfficeSupervisor"
    Write-Host "已移除旧版自启键 CCSwitchOfficeSupervisor（迁移到新的自启方式）"
}

# 3) 端口占用检查：剩余占用者若非本项目进程，直接报错而不是抢端口
$listeners = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if ($listeners) {
    foreach ($c in $listeners) {
        $proc = Get-Process -Id $c.OwningProcess -ErrorAction SilentlyContinue
        throw "端口 $Port 已被占用（pid=$($c.OwningProcess) $($proc.ProcessName)）。请关闭该程序，或用 -Port 换一个端口重试。"
    }
}

# 4) Python 运行时：自带 _python 优先 -> 系统 Python -> 自动下载官方嵌入式运行时（约 11MB，仅本项目使用）
$bundled = Join-Path $root "_python\python.exe"
if (Test-Path -LiteralPath $bundled) {
    $python = $bundled
    Write-Host "[1/5] 使用自带运行时: $python（无需安装 Python）"
} else {
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
    if (-not $python) {
        # 自动下载官方嵌入式 Python（PSF 许可证允许再分发；解压到 _python\，不写系统、不需要管理员）
        $pyver = "3.13.15"
        $url = "https://www.python.org/ftp/python/$pyver/python-$pyver-embed-amd64.zip"
        Write-Host "[1/5] 未检测到系统 Python，正在下载官方嵌入式运行时（约 11MB，仅本项目使用）..."
        try {
            [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
            $zipPath = Join-Path $root "_python-download.zip"
            Invoke-WebRequest -Uri $url -OutFile $zipPath -UseBasicParsing
            $expected = "d1f04d990aee1253d8569e8e5104e30fa9f5fa830899f14843448872d936a2cf"
            if ((Get-FileHash -LiteralPath $zipPath -Algorithm SHA256).Hash -ne $expected) {
                throw "Python runtime checksum mismatch"
            }
            $pyDir = Join-Path $root "_python"
            if (Test-Path -LiteralPath $pyDir) { Remove-Item -LiteralPath $pyDir -Recurse -Force }
            Expand-Archive -LiteralPath $zipPath -DestinationPath $pyDir -Force
            Remove-Item -LiteralPath $zipPath -Force
            $python = $bundled
        } catch {
            throw "自动下载运行时失败（$($_.Exception.Message)）。请安装 Python 3.9+（python.org，勾选 Add to PATH）后重试。"
        }
        Write-Host "[1/5] 嵌入式运行时就绪: $python"
    } else {
        Write-Host "[1/5] Python OK: $python"
    }
}
$pythonw = $python -replace 'python\.exe$', 'pythonw.exe'
if (-not (Test-Path -LiteralPath $pythonw)) { $pythonw = $python }
$usingBundled = ($python -eq $bundled)

# 5) 可选增强依赖（PDF/Office 文本提取质量更好；不装也能跑，自动退回内置解析）
if ($WithExtras) {
    if ($usingBundled) {
        Write-Warning "自带运行时不支持 pip 安装增强依赖；如需增强解析，请安装系统 Python 3.9+ 后重跑 -WithExtras。"
    } else {
        Write-Host "[2/5] 安装可选增强依赖 pypdf python-docx openpyxl python-pptx ..."
        & $python -m pip install --disable-pip-version-check pypdf python-docx openpyxl python-pptx
        if ($LASTEXITCODE -ne 0) { Write-Warning "可选依赖安装失败，网关将使用内置标准库解析（功能略简化）。" }
    }
} else {
    Write-Host "[2/5] 跳过可选增强依赖（-WithExtras 可安装）"
}

# 6) 生成 .env（已有则保留，绝不覆盖用户配置）
$envPath = Join-Path $root ".env"
if (-not (Test-Path -LiteralPath $envPath)) {
    $template = Get-Content -LiteralPath (Join-Path $root ".env.example") -Raw -Encoding UTF8
    $template = $template -replace '(?m)^EDGE_PORT=.*$', "EDGE_PORT=$Port"
    [System.IO.File]::WriteAllText($envPath, $template, [System.Text.UTF8Encoding]::new($false))
    Write-Host "[3/5] 已从 .env.example 生成 .env（端口 $Port）"
} else {
    Write-Host "[3/5] 已存在 .env，保持不变"
}

# 7) 开机自启：优先用户级计划任务（免管理员）；失败则回退 HKCU Run 注册表键
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

# 8) 启动并等待健康
Write-Host "[5/5] 启动网关并等待 /healthz ..."
if ($autostart -eq "task") { Start-ScheduledTask -TaskName $TaskName }
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

if (-not $healthy) { throw "网关已启动流程完成，但 /healthz 在 30 秒内未就绪。请运行 .\diagnose.ps1 查看诊断，或查看 runtime\edge-sup.err。" }

Write-Host ""
Write-Host "=== 安装完成：网关运行于 http://127.0.0.1:$Port （自启方式：$autostart）==="
Write-Host "下一步（Office 侧二选一，详见 docs\OFFICE-ONBOARDING.md）："
Write-Host "  A. 官方 Claude 加载项的 Gateway 配置里填：URL http://127.0.0.1:$Port | Token PROXY_MANAGED | Header x-api-key | Format Anthropic Messages"
Write-Host "  B. 老版本 Office 没有该配置界面时：powershell -ExecutionPolicy Bypass -File .\scripts\Install-DeveloperSideload.ps1"
Write-Host "验收：powershell -ExecutionPolicy Bypass -File .\verify.ps1"
