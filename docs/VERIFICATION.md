# 验收与故障定位

## 一键验收

```powershell
.\verify.ps1                # 零费用，覆盖协议与 Files 全链路
.\verify.ps1 -RunInference  # 额外各调一次真实模型（非流式+流式），产生少量上游费用
```

默认检查项：`/healthz`、模型列表（claude 别名）、CORS+PNA 预检、错误令牌 401、非法 JSON 400、未知路由 404、归档 415、Files 上传→列表→元数据→下载（SHA-256 比对）→删除→删后 404。全部通过输出 `VERIFICATION_GATE=PASS`。

## 分层判断（排障按此顺序，逐层收敛）

| 层 | 检查 | 通过标准 |
| --- | --- | --- |
| 1 进程 | 计划任务/守护在跑？8790 在监听？ | `Get-NetTCPConnection -State Listen -LocalPort 8790` |
| 2 应用 | `/healthz` 5 秒内 200？ | `curl.exe --noproxy "*" http://127.0.0.1:8790/healthz` |
| 3 适配 | 模型/CORS/Files 过？ | `.\verify.ps1` |
| 4 上游 | 最小消息 `/v1/messages` 成功？ | `.\verify.ps1 -RunInference` |
| 5 客户端 | 真实 Office 里发消息/切模型/传附件/继续原聊天？ | 手工确认 |

**前四层全绿也不能证明 Office WebView 登录态正常——第五层必须在真实 Word/Excel/PowerPoint 里确认。**

## 常见现象 → 根因 → 处理

### `Could not reach gateway` / `Failed to fetch (127.0.0.1:8790)`

先跑第 1、2 层。端口在监听但 `/healthz` 失败 = 假活，守护进程会在连续 3 次失败（约 15~20 秒）后自动重启，稍等即可；持续失败看 `runtime\edge-sup.err`。

### `502` 且提示 inference gateway / upstream unreachable

网关已收到请求，但连不上 CC Switch（15721）或上游。检查：CC Switch 是否在运行、当前供应商是否可用、`runtime\last-upstream-error.txt` 内容。**不要先动 Office 配置。**

### `Something went wrong`（Office 内通用报错）

可能是工具定义兼容或上游 SSE 异常。网关解包 `tools[].custom`；只有明确拒绝可选字段的 400/422 才做有限重试。历史有损清洗需要显式开启，见 [升级说明](UPGRADE-3.4.md)。失败不等于模型完全不兼容，应查看具体错误。反馈错误和形状日志前人工检查隐私；不要上传真实请求或密钥。

### 模型列表只有一个 / 切换供应商后不更新

`/v1/models` 是实时的——若 curl 已返回新列表而 Office 没变，是 Office 侧栏缓存，重开侧栏。若 curl 也没变，去 CC Switch 确认当前供应商配置了 Claude 模型槽位（或 claudeDesktopModelRoutes）。

### 要求重新登录 / 聊天历史消失

这是 Office WebView 存储槽位变化，**不是网关问题**。⚠️ 绝不清缓存/删 IndexedDB——先备份，参照 [OFFICE-ONBOARDING.md](OFFICE-ONBOARDING.md) 的历史保护一节。

### 弹窗「安装加载项时出错：安装或加载所需资源失败」

清单声明的"所需资源"（图标/快捷键映射/任务窗格页面）全部托管在 `pivot.claude.ai`，**与本机网关无关**。先用判定法分流：

- **侧栏还能正常对话** → 无害弹窗：某外围资源拉取瞬断（实测案例：官方站点重新部署窗口，PowerPoint 第一个加载撞上、Word/Excel 晚 1~2 分钟重建成功）。处置：完全退出该 Office 应用重开一次即自愈（每次启动自动重试），无需任何修复。
- **侧栏无法对话** → 按分层判断走第 1~4 层。

弹窗每次启动都出现且持续超过 1 天 → `.\diagnose.ps1 -OutFile report.txt` 收集报告提 Issue。

⚠️ **任何情况下都不要清除/删除 `%LOCALAPPDATA%\Microsoft\Office\16.0\Wef\`**——网上常见"清 Wef 缓存"偏方会把用户聊天历史（IndexedDB）一起清掉，永久丢失。三个应用缓存彼此独立，一个失败不影响其他。

### 文件上传成功但发送失败

上传成功只证明 Files API。发送时还要解析与内联：归档 415（先解压）、无法解析 415、图片超限 413。错误信息会明确说明原因。

## 发布前必测矩阵（维护者）

| 场景 | Word | Excel | PowerPoint |
|---|:---:|:---:|:---:|
| 文本 非流式/流式 | 必测 | 必测 | 必测 |
| 切换供应商后模型路由 | 必测 | 必测 | 必测 |
| 工具调用与结果回传 | 必测 | 必测 | 必测 |
| 图片 | 必测 | 必测 | 必测 |
| PDF/DOCX/XLSX/PPTX | 抽测 | 抽测 | 抽测 |
| 重启后自愈/自启 | 系统级一次 | | |
| 原聊天继续 | 必测 | 必测 | 必测 |
