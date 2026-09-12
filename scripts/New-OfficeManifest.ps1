# New-OfficeManifest.ps1 —— 生成 Office 加载项旁加载清单（Word/Excel/PPT 三合一）
[CmdletBinding()]
param(
    [string]$GatewayUrl = "http://127.0.0.1:8790",
    [string]$Token = "PROXY_MANAGED",
    [string]$OutFile = ""
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$uri = $null
if (-not [Uri]::TryCreate($GatewayUrl, [UriKind]::Absolute, [ref]$uri) -or
    $uri.Scheme -notin @('http', 'https') -or $uri.UserInfo -or $uri.Query -or $uri.Fragment) {
    throw 'GatewayUrl must be an absolute HTTP(S) base URL without credentials, query or fragment.'
}
if ([string]::IsNullOrWhiteSpace($Token)) { throw 'A nonempty gateway token is required.' }
if (-not $OutFile) { $OutFile = Join-Path $root 'sideload\claude-office-ccswitch-gateway.xml' }
$OutFile = [IO.Path]::GetFullPath($OutFile)
$template = Get-Content -LiteralPath (Join-Path $root 'templates\claude-office.xml') -Raw -Encoding UTF8
$encodedUrl = [Uri]::EscapeDataString($GatewayUrl.TrimEnd('/'))
$encodedToken = [Uri]::EscapeDataString($Token)
$manifest = $template.Replace('__ADDIN_ID__', 'b3f1a2e4-0c5d-4a6b-9e7f-1a2b3c4d5e6f').
    Replace('__ADDIN_VERSION__', '1.0.0.12').
    Replace('__DISPLAY_NAME__', 'Claude (Local Gateway)').
    Replace('__GATEWAY_URL_ENC__', $encodedUrl).
    Replace('__GATEWAY_TOKEN__', $encodedToken).
    Replace('__API_FORMAT__', 'anthropic')
[void][xml]$manifest
$directory = Split-Path -Parent $OutFile
if (-not (Test-Path -LiteralPath $directory)) { New-Item -ItemType Directory -Path $directory | Out-Null }
if (Test-Path -LiteralPath $OutFile) {
    $backup = $OutFile + '.bak-' + (Get-Date -Format 'yyyyMMdd-HHmmss-fff')
    Copy-Item -LiteralPath $OutFile -Destination $backup -ErrorAction Stop
}
[IO.File]::WriteAllText($OutFile, $manifest, [Text.UTF8Encoding]::new($false))
Write-Host "Manifest generated: $OutFile"
Write-Host 'The manifest contains your gateway token. Keep it private.'
