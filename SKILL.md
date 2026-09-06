# SKILL.md — Claude Office × CC Switch 本地网关（AI 代理自助手册）

> 给 AI 代理（Claude Code / Cursor / 其他）看的一页式操作手册。你的用户想安装、配置、升级或排障本网关时，按本文件执行。全部操作在本机完成，无需管理员权限。

## 项目一句话

让 Word/Excel/PowerPoint 里的官方 Claude 加载项，通过本机网关（`127.0.0.1:8790`）使用 CC Switch 当前选中的供应商（DeepSeek/Kimi/GLM 等），CC Switch 里切换供应商下一句话即生效。纯 Python 标准库，Release 包自带运行时。

## 铁律（任何操作前先读）

1. `~/.cc-switch/cc-switch.db` **只读**——绝不写入、绝不移动、绝不删除
2. **绝不**通过清空 Office WebView/IndexedDB（`%LOCALAPPDATA%\Microsoft\Office\16.0\Wef\`）来修连接问题——用户的聊天历史只存在那里，清了永久丢失
3. 不把密钥、`.env`、`runtime/` 内容发到任何外部渠道；贴日志前先跑 `diagnose.ps1`（自动脱敏）
4. 本机请求一律绕过系统代理（curl 加 `--noproxy "*"`）
5. 只监听 `127.0.0.1`，不要建议用户改成对外监听

## A. 安装（Windows）

1. 环境自检（PowerShell）：
   - `Get-Command python` 有无均可——没有运行时会自动下载嵌入式版本
   - 确认 CC Switch 已安装且配置了 **Claude 分类**供应商并启用：`Get-Process | Where-Object Name -like '*cc-switch*'`
2. 获取程序（二选一）：
   - **推荐**：`https://github.com/zhuigan6/claude-office-ccswitch-gateway/releases/latest` 下载 `ClaudeOfficeGateway-vX.Y.Z-windows-x64.zip`，解压到固定目录（如 `C:\Tools\ClaudeOfficeGateway`；禁止 OneDrive/临时/下载目录）
   - 或 `git clone` 后进入目录
3. 安装：`powershell -ExecutionPolicy Bypass -File .\install.ps1`
   - 脚本自动：停旧版进程 → 检查端口 → 准备运行时 → 生成 `.env` → 注册用户级计划任务自启 → 启动并等待健康
4. 验收：`powershell -ExecutionPolicy Bypass -File .\verify.ps1`（零费用；全 PASS 即成功）

## B. Office 侧配置（二选一）

- **方式 A（新版加载项有 Gateway 配置界面）**：`URL http://127.0.0.1:8790`、`Token PROXY_MANAGED`、`Header x-api-key`、`Format Anthropic Messages`。若发消息报 401：读取 `http://127.0.0.1:8790/status/ccswitch` 返回 JSON 的 `token` 字段填入（**此值是机密，不要外传**）
- **方式 B（老版加载项无界面）**：`.\scripts\New-OfficeManifest.ps1` 生成清单 → `.\scripts\Install-DeveloperSideload.ps1 -Manifest .\sideload\claude-office-ccswitch-gateway.xml` → 重启 Office

## C. 升级

进入安装目录：`git pull`（或重新下载 Release 解压覆盖）→ 重跑 `install.ps1`（自动停旧进程、保留 `.env` 与 `runtime\` 数据）→ `.\verify.ps1`。

## D. 排障决策树

```text
Office 弹「安装加载项时出错：安装或加载所需资源失败」
  ├─ 先判定：侧栏还能正常对话吗？
  │    ├─ 能 → 无害弹窗。失败的是外围资源（图标/快捷键映射，全部托管在
  │    │      pivot.claude.ai，与本机网关无关）；核心功能无损。
  │    │      处置：完全退出该 Office 应用（所有窗口）再重开一次即自愈
  │    │      （每次启动会自动重试拉取）；无需任何修复。
  │    └─ 不能 → 按下方标准分层排障（healthz → verify → diagnose）
  ├─ 弹窗每次启动都出现且持续超过 1 天 → .\diagnose.ps1 -OutFile report.txt
  │    （自动脱敏）→ 附报告提 Issue；大概率是某资源被缓存为失败状态
  └─ ⚠️ 铁律：绝对禁止清除/删除 %LOCALAPPDATA%\Microsoft\Office\16.0\Wef\
       目录！网上常见的"清 Wef 缓存"偏方会把用户聊天历史（IndexedDB）
       一起清掉，永久丢失，不可恢复。

  背景知识（已实测验证）：
  · 清单声明的"所需资源"全部指向 https://pivot.claude.ai（图标 icon-*.png、
    快捷键映射 shortcuts.json、任务窗格页面本身），本机网关地址只是 URL 里
    的查询参数——网关健康与否与此弹窗无因果关系。
  · 三个 Office 应用各自独立缓存（Wef\AggregatedCache\ShortcutsMapping.
    <App>.zh-CN 等），一个应用失败不影响其他；失败的应用每次启动重试，
    服务恢复后自动重建，弹窗随之消失。
  · 实测案例（2026-09-06）：官方站点 14:39~14:41 重新部署期间，PowerPoint
    恰好第一个加载、shortcuts.json 拉取失败弹窗；Word/Excel 晚 1~2 分钟
    重建成功无弹窗；期间对话始终正常。
```

```text
Office 报 Could not reach gateway / Failed to fetch
  ├─ curl --noproxy "*" http://127.0.0.1:8790/healthz
  │    ├─ 200 且 status=ok → 网关正常；问题在 Office 侧配置 → 检查方式 A 的四项参数 / 方式 B 旁加载
  │    ├─ 连接失败 → Get-ScheduledTask 'Claude Office Gateway' 状态；看 runtime\edge-sup.err
  │    └─ 200 但 ccswitch_diagnosis 字段存在 → 按 suggestion 处理：
  │         no_current_provider → 用户需在 CC Switch 配置并启用 Claude 分类供应商
  │         ccswitch_schema_drift → 收集 diagnose.ps1 报告 + CC Switch 版本提 Issue
  └─ 都不行 → powershell -ExecutionPolicy Bypass -File .\diagnose.ps1 -OutFile report.txt（自动脱敏）→ 提 Issue 附上

Office 报 502 / inference gateway unreachable
  → CC Switch 没运行或当前供应商不可用；检查后重试。不要改 Office 配置。

发消息报 401 → 见 B 方式 A 的 Token 说明

模型列表只有一个/切换供应商不更新 → 关闭并重开 Claude 侧栏（Office 有列表缓存）；再查 /v1/models

上传附件报 415 → 归档(zip/rar/7z)不支持自动解压，请用户先解压；24h 前的旧附件已过期需重传
```

错误响应体带机器可读字段：`error.code`（如 `invalid_gateway_token`、`ccswitch_unreachable`、`quota_exceeded`）+ `error.suggestion`（下一步该做什么）——程序化排障请解析这两个字段。

## E. 常用诊断命令

```powershell
curl.exe --noproxy "*" http://127.0.0.1:8790/healthz          # 版本/通道/供应商/诊断
curl.exe --noproxy "*" http://127.0.0.1:8790/status/ccswitch  # 实时状态（含令牌，勿外传）
Get-NetTCPConnection -State Listen -LocalPort 8790            # 端口监听
.\diagnose.ps1 -OutFile report.txt                            # 一键全量诊断（脱敏）
```

## F. 卸载

`powershell -ExecutionPolicy Bypass -File .\uninstall.ps1`（只移除自启与进程；源码、`.env`、数据保留）。

## 更多文档

架构 `docs/ARCHITECTURE.md` · 部署 `docs/DEPLOYMENT-WINDOWS.md` · Office 接入 `docs/OFFICE-ONBOARDING.md` · 验收 `docs/VERIFICATION.md` · 设计决策 `docs/adr/`
