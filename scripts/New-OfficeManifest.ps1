# New-OfficeManifest.ps1 —— 生成 Office 加载项旁加载清单（Word/Excel/PPT 三合一）
# 适用于官方加载项没有 Gateway 配置界面的老版本 Office。
# 用法：
#   powershell -ExecutionPolicy Bypass -File .\scripts\New-OfficeManifest.ps1 -OutFile .\sideload\claude-gateway.xml
#   # 自定义端口/令牌： -GatewayUrl http://127.0.0.1:8790 -Token PROXY_MANAGED
[CmdletBinding()]
param(
    [string]$GatewayUrl = "http://127.0.0.1:8790",
    [string]$Token = "PROXY_MANAGED",
    [string]$OutFile = ""
)

$ErrorActionPreference = "Stop"
$root = (Resolve-Path -LiteralPath $PSScriptRoot).Path
if (-not $OutFile) { $OutFile = Join-Path $root "sideload\claude-office-ccswitch-gateway.xml" }

$template = @"
<?xml version="1.0" encoding="UTF-8"?>
<OfficeApp xmlns="http://schemas.microsoft.com/office/appforoffice/1.1"
           xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
           xmlns:bt="http://schemas.microsoft.com/office/officeappbasictypes/1.0"
           xmlns:ov="http://schemas.microsoft.com/office/taskpaneappversionoverrides"
           xsi:type="TaskPaneApp">
  <Id>b3f1a2e4-0c5d-4a6b-9e7f-1a2b3c4d5e6f</Id>
  <Version>1.0.0.0</Version>
  <ProviderName>Local Gateway</ProviderName>
  <DefaultLocale>en-US</DefaultLocale>
  <DisplayName DefaultValue="Claude (Local Gateway)"/>
  <Description DefaultValue="Claude web experience powered by your local CC Switch gateway."/>
  <IconUrl DefaultValue="__GATEWAY_URL__/assets/icon-32.png"/>
  <HighResolutionIconUrl DefaultValue="__GATEWAY_URL__/assets/icon-80.png"/>
  <SupportUrl DefaultValue="https://github.com/"/>
  <AppDomains>
    <AppDomain>__GATEWAY_URL__</AppDomain>
    <AppDomain>https://pivot.claude.ai</AppDomain>
  </AppDomains>
  <Hosts>
    <Host Name="Workbook"/>
    <Host Name="Document"/>
    <Host Name="Presentation"/>
  </Hosts>
  <Requirements><Sets DefaultMinVersion="1.1"><Set Name="DialogApi"/></Sets></Requirements>
  <DefaultSettings>
    <SourceLocation DefaultValue="__GATEWAY_URL__/index.html"/>
  </DefaultSettings>
  <Permissions>ReadWriteDocument</Permissions>
  <VersionOverrides xmlns="http://schemas.microsoft.com/office/taskpaneappversionoverrides" xsi:type="VersionOverridesV1_0">
    <WebApplicationInfo>
      <Id>b3f1a2e4-0c5d-4a6b-9e7f-1a2b3c4d5e6f</Id>
      <Resource>api://__GATEWAY_NO_SCHEME__/b3f1a2e4</Resource>
      <Scopes><Scope>openid</Scope></Scopes>
    </WebApplicationInfo>
    <Hosts>
      <Host xsi:type="Workbook">
        <AllFormFactors>
          <ExtensionPoint xsi:type="CustomFunctions">
            <FunctionFile Resid="RN" />
          </ExtensionPoint>
        </AllFormFactors>
      </Host>
    </Hosts>
  </VersionOverrides>
</OfficeApp>
"@

$manifest = $template.Replace("__GATEWAY_URL__", $GatewayUrl).Replace("__GATEWAY_NO_SCHEME__", ($GatewayUrl -replace '^https?://', ''))
$dir = Split-Path -Parent $OutFile
if ($dir -and -not (Test-Path -LiteralPath $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
[System.IO.File]::WriteAllText($OutFile, $manifest, [System.Text.UTF8Encoding]::new($false))
Write-Host "清单已生成：$OutFile"
Write-Host "提示：这是旁加载壳清单，指向网关地址 $GatewayUrl；实际加载项前端仍由 pivot.claude.ai 提供。"
Write-Host "写注册表生效：powershell -ExecutionPolicy Bypass -File .\scripts\Install-DeveloperSideload.ps1 -Manifest '$OutFile'"
