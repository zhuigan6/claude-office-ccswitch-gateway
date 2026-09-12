# Office 接入：两种方式

网关装好后，需要让 Office 里的 Claude 加载项指向它。按你的 Office 加载项版本二选一。

## 方式 A：官方加载项自带 Gateway 配置界面（推荐）

较新版本的 Claude Office 加载项在侧栏设置里有 Gateway / 自定义端点配置：

```text
URL:         http://127.0.0.1:8790     （.env 里 EDGE_PORT 改过就对应改）
Token:       PROXY_MANAGED             （占位令牌，见下方说明）
Auth Header: x-api-key
API Format:  Anthropic Messages
```

Word、Excel、PowerPoint 三个软件共用这一套配置。填完发一条消息即通。

**Token 说明**：
- 新版 CC Switch（claude 通道）：`PROXY_MANAGED` 直接可用；
- 旧版 CC Switch（claude-desktop 通道）：未设置 `EDGE_TOKEN` 时也填 `PROXY_MANAGED`；数据库中的实时令牌仅在网关内部用于上游请求，状态接口不再提供令牌；
- 想更进一步收紧（多人共机）：在 `.env` 设 `EDGE_TOKEN=你的秘密值`，Office 填这个值，错误令牌一律 401。

两个通道同时存在时，`auto` 仍优先 `claude-desktop`。若你希望跟随 Claude Code 分类，请显式配置 `CCSWITCH_CHANNEL=claude`。升级保留原端口及 Office 登录数据；无需重新旁加载。

默认只接受 `https://pivot.claude.ai` 的跨域请求。自建前端需在 `EDGE_ALLOWED_ORIGINS` 中配置精确 Origin（逗号分隔），不支持通配 `*`。

## 方式 B：开发者旁加载清单（老版加载项没有配置界面时）

原理：给 Office 旁加载一个清单壳，把加载项前端的 Gateway 参数注入进去。全程免管理员（写 HKCU 注册表）。

```powershell
# 1. 生成清单（默认指向 http://127.0.0.1:8790；改过端口加 -GatewayUrl）
powershell -ExecutionPolicy Bypass -File .\scripts\New-OfficeManifest.ps1

# 2. 写入注册表旁加载
powershell -ExecutionPolicy Bypass -File .\scripts\Install-DeveloperSideload.ps1 -Manifest .\sideload\claude-office-ccswitch-gateway.xml

# 3. 完全退出并重启 Word/Excel/PowerPoint，在 插入 -> 我的加载项（开发者区域）看到 "Claude (Local Gateway)" 即成功
```

卸载旁加载：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\Install-DeveloperSideload.ps1 -Remove
```

## 切换供应商后模型不刷新？

网关按请求实时读取 CC Switch 状态，**无需重启网关**。但 Office 侧栏有自己的模型列表缓存：

- 切换供应商后，**关闭并重新打开 Claude 侧栏**（或重开 Office 文档）即可拉到新列表；
- 若 `curl http://127.0.0.1:8790/v1/models` 已是新模型而 Office 没变，纯粹是 Office 缓存，重开侧栏解决。

## ⚠️ 聊天历史保护（最高优先级铁律）

你的逐字聊天记录**只**存在于本机 Office WebView 数据目录（IndexedDB）：

- **任何情况下不要**通过"清除加载项缓存/重置 Office WebView/删除 Wef 目录"来修连接问题——历史会永久丢失；
- 连接问题先跑 `.\verify.ps1` 分层定位（见 [VERIFICATION.md](VERIFICATION.md)）；
- 若 Office 升级后产生了新的 WebView 数据槽导致历史"消失"，那不是网关问题——不要清任何数据，先完整备份 WebView 目录，再只迁移 `pivot.claude.ai` 对应槽位的 IndexedDB 与 .blob（**绝不**连带 LocalStorage，其中缓存着旧地址会指向死链）。
