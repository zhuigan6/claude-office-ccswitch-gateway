# uninstall.ps1 —— 移除自启与停掉网关进程；保留源码、.env 与数据目录
[CmdletBinding()]
param([string]$TaskName = "Claude Office Gateway")

$ErrorActionPreference = "Continue"
$root = (Resolve-Path -LiteralPath $PSScriptRoot).Path

# 1) 计划任务（若存在）
$task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($task) {
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "已移除计划任务：$TaskName"
} else {
    Write-Host "计划任务不存在：$TaskName"
}

# 2) HKCU Run 回退键（若存在）
$runKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run"
if (Get-ItemProperty -Path $runKey -Name $TaskName -ErrorAction SilentlyContinue) {
    Remove-ItemProperty -Path $runKey -Name $TaskName
    Write-Host "已移除 HKCU Run 自启键：$TaskName"
}

# 3) 停掉残留的 supervisor / gateway 进程（只杀命令行里带本仓库路径的，避免误伤）
$procs = Get-CimInstance Win32_Process -Filter "Name like 'python%'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -and $_.CommandLine -like "*$root*" }
foreach ($p in $procs) {
    Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
    Write-Host ("已停止进程 pid={0}" -f $p.ProcessId)
}

Write-Host "完成。源码、.env 与 runtime\ 数据目录均已保留；如需彻底删除请手动移除整个目录。"
