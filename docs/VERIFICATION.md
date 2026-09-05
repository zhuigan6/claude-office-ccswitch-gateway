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

多为工具定义兼容或上游 SSE 异常。网关已自动做 `tools[].custom` 解包与一次深清洗重试；仍失败说明上游对该请求无兼容形态。把 `runtime\last-upstream-error.txt`（**先抹掉密钥**）与 `.env` 里 `EDGE_LOG_SHAPE=1` 开启后的形状日志（不含正文）用于反馈。

### 模型列表只有一个 / 切换供应商后不更新

`/v1/models` 是实时的——若 curl 已返回新列表而 Office 没变，是 Office 侧栏缓存，重开侧栏。若 curl 也没变，去 CC Switch 确认当前供应商配置了 Claude 模型槽位（或 claudeDesktopModelRoutes）。

### 要求重新登录 / 聊天历史消失

这是 Office WebView 存储槽位变化，**不是网关问题**。⚠️ 绝不清缓存/删 IndexedDB——先备份，参照 [OFFICE-ONBOARDING.md](OFFICE-ONBOARDING.md) 的历史保护一节。

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
