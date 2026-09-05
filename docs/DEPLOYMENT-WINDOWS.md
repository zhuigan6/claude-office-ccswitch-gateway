# Windows 部署指南

## 前提

- Windows 10/11
- [Python 3.9+](https://www.python.org/downloads/)（安装勾选 *Add python.exe to PATH*；网关纯标准库，不需要任何 pip 依赖）
- [CC Switch](https://github.com/farion1231/cc-switch) 已安装，并配置好至少一个 **Claude 分类**的供应商、设为当前启用
- Microsoft Office（Word/Excel/PPT）

## 安装

1. 把整个仓库放到**固定目录**（例：`C:\Tools\ClaudeOfficeGateway`）。
   ⚠️ 不要放 OneDrive、临时目录或下载目录——安装脚本会拒绝并提示。
2. 在该目录打开 PowerShell：

```powershell
powershell -ExecutionPolicy Bypass -File .\install.ps1
```

脚本会依次：检查 Python →（可选）安装增强依赖 → 生成 `.env` → 注册开机自启（用户级计划任务，免管理员；被组策略拒绝时自动回退注册表 Run 键）→ 启动网关 → 等待 `/healthz` 就绪。

可选参数：

```powershell
.\install.ps1 -WithExtras      # 同时安装 pypdf/python-docx/openpyxl/python-pptx（PDF/Office 提取更强）
.\install.ps1 -Port 8787       # 换端口（默认 8790）
.\install.ps1 -NoAutostart     # 不注册开机自启
```

## Office 接入（二选一）

见 [OFFICE-ONBOARDING.md](OFFICE-ONBOARDING.md)：

- **方式 A**：官方加载项自带 Gateway 配置界面（推荐，10 秒填完）
- **方式 B**：老版加载项没有该界面 → 用 `scripts\` 里的旁加载脚本

## 验收

```powershell
.\verify.ps1
```

十几项检查全部 `PASS`（健康/模型/CORS+PNA/鉴权/Files 生命周期/错误语义），最后输出 `VERIFICATION_GATE=PASS` 即可交付使用。加 `-RunInference` 会各调一次真实模型（非流式+流式，产生少量上游费用）。

最后一步永远是**在真实的 Word/Excel/PPT 里发一条消息、切换一次模型、传一张图片**——前四层验收绿了不代表 WebView 登录态正常。

## 升级

升级 = 只换代码，不动配置与数据：

```powershell
# 1. 停网关（守护进程会拦着自动重启，所以先停守护）
Get-CimInstance Win32_Process -Filter "Name like 'python%'" |
  Where-Object { $_.CommandLine -like "*ClaudeOfficeGateway*" } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force }

# 2. 覆盖 gateway\office_edge.py、supervisor.py（git pull 或下载新版覆盖）
#    .env 与 runtime\ 不要动

# 3. 重启
Start-Process -FilePath "<pythonw路径>" -ArgumentList '"C:\Tools\ClaudeOfficeGateway\supervisor.py"'
.\verify.ps1
```

破坏性变更（端口/令牌/配置格式）会在 [CHANGELOG](../CHANGELOG.md) 中以 MAJOR 版本显著标注并给迁移说明。

## 卸载

```powershell
.\uninstall.ps1    # 移除计划任务/Run 键、停进程；源码、.env、runtime\ 全部保留
```

## 排障入口

| 看什么 | 位置 |
| --- | --- |
| 实时状态（通道/供应商/模型槽位） | `curl.exe --noproxy "*" http://127.0.0.1:8790/status/ccswitch` |
| 守护与网关 stdout/stderr | `runtime\supervisor.lock`、`runtime\edge-sup.out`、`runtime\edge-sup.err` |
| 最近一次上游 4xx 原文 | `runtime\last-upstream-error.txt` |
| 访问日志（脱敏，2MB 轮转） | `runtime\edge-access.log` |

> 本机请求一律加 `--noproxy "*"`（或 curl 用 `curl.exe`），避免请求误入系统代理。
