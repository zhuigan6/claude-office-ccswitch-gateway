# Install-DeveloperSideload.ps1 —— 通过 HKCU 开发者旁加载注册表安装/卸载加载项清单（免管理员）
# 注册表结构经本机实测验证：WEF\Developer 下需要两条 REG_SZ——
#   ① 名字=加载项 GUID，数据=清单完整路径；② 名字=清单完整路径，数据=清单完整路径。
# 用法：
#   安装：powershell -ExecutionPolicy Bypass -File .\scripts\Install-DeveloperSideload.ps1 -Manifest .\sideload\claude-office-ccswitch-gateway.xml
#   卸载：powershell -ExecutionPolicy Bypass -File .\scripts\Install-DeveloperSideload.ps1 -Remove
[CmdletBinding()]
param(
    [string]$Manifest = "",
    [switch]$Remove
)

$ErrorActionPreference = "Stop"
$baseKey = "HKCU:\Software\Microsoft\Office\16.0\WEF\Developer"

if ($Remove) {
    if (-not (Test-Path $baseKey)) { Write-Host "未发现开发者旁加载注册表项，无需卸载。"; exit 0 }
    $props = Get-Item -Path $baseKey | Select-Object -ExpandProperty Property
    foreach ($name in $props) {
        $val = (Get-ItemProperty -Path $baseKey -Name $name).$name
        if ($name -match '\.xml$' -or $name -match '^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$') {
            Remove-ItemProperty -Path $baseKey -Name $name
            Write-Host "已移除：$name = $val"
        }
    }
    Write-Host "卸载完成。重启 Word/Excel/PowerPoint 后加载项消失。"
    exit 0
}

if (-not $Manifest) { throw "请用 -Manifest 指定清单文件，或用 -Remove 卸载。" }
$manifestPath = (Resolve-Path -LiteralPath $Manifest).Path
if (-not (Test-Path -LiteralPath $manifestPath)) { throw "清单文件不存在：$manifestPath" }

[xml]$xmlDoc = Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8
$guid = $xmlDoc.OfficeApp.Id
if (-not $guid) { throw "清单中未找到 OfficeApp/Id（GUID）。" }

if (-not (Test-Path $baseKey)) { New-Item -Path $baseKey -Force | Out-Null }
New-ItemProperty -Path $baseKey -Name $guid -Value $manifestPath -PropertyType String -Force | Out-Null
New-ItemProperty -Path $baseKey -Name $manifestPath -Value $manifestPath -PropertyType String -Force | Out-Null
Write-Host "已写入 HKCU 开发者旁加载："
Write-Host "  GUID: $guid -> $manifestPath"
Write-Host "  Path: $manifestPath -> $manifestPath"
Write-Host "重启 Word/Excel/PowerPoint，在 插入->我的加载项（开发者区域）看到 'Claude (Local Gateway)' 即成功。"
