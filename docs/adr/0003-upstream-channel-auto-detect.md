# ADR-0003：CC Switch 通道自动识别（双代数据库结构）

- 状态：已采纳（2026-09）
- 关联：`gateway/office_edge.py` 的 `read_ccswitch_state()`；`CCSWITCH_CHANNEL` 配置

## 背景

两台实测机器上的 CC Switch 版本不同，同一件事（"当前启用的 Claude 供应商"）在数据库里是两种结构：

- 旧版（claude-desktop 通道）：`providers.app_type='claude-desktop'`，网关令牌在 `settings.claude_desktop_gateway_token`，模型路由在 `meta.claudeDesktopModelRoutes`，上游路径前缀 `/claude-desktop`（裸 `/v1/messages` 走 Claude Code 通道会 503）；
- 新版（claude 通道）：`providers.app_type='claude'`，模型槽位在 `settings_config.env` 的 `ANTHROPIC_*_MODEL`，上游路径无前缀，令牌由 CC Switch 全权托管（Office 里填占位 `PROXY_MANAGED`）。

两台机器曾因这个差异各自硬编码，互不相通。

## 决策

`CCSWITCH_CHANNEL=auto`（默认）：按"当前启用供应商行存在于哪个 app_type 下"自动判定通道，取到对应的令牌来源、模型槽位来源与上游路径前缀；也可在 `.env` 显式指定 `claude-desktop` 或 `claude` 覆盖。数据库仍然**只读**（`mode=ro`），每请求实时读取。

## 被否掉的方案

- **只支持一种结构**：等于宣判另一批 CC Switch 版本的用户无法使用；
- **探测上游路径**（先打 `/claude-desktop/v1/messages` 再打 `/v1/messages` 看响应）：两通道对错误请求都可能返回非 404，区分度差且浪费请求；数据库本身就是判定依据，无需猜。

## 后果

- 用户零配置适配两代 CC Switch；上游路径不再硬编码（`upstream_prefix()` 统一取）；
- 新义务：CC Switch 未来改库结构时，此处是最先碎的点——已在首次 Issue 清单里列入"数据库结构变化检测"；
- `/healthz` 返回识别出的通道与供应商，方便一眼确认判定结果。
