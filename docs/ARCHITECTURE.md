# 架构与关键设计

## 1. 为什么需要本地网关层

Office 里的 Claude 加载项不只调用 `/v1/messages`，还会探测模型列表、执行浏览器 CORS/PNA 预检、上传附件（Files API）。CC Switch 的内置代理只负责供应商路由，不应被迫承担某个 Office 客户端的全部私有兼容逻辑。两层职责分离：

```text
Office 客户端 (https://pivot.claude.ai, WebView2)
  | Anthropic Messages + Files + CORS/PNA
  v
office_edge 网关（本仓库，127.0.0.1:8790）
  | 标准化后的 Anthropic Messages（模型别名已还原）
  v
CC Switch 内置代理（127.0.0.1:15721）
  | 当前供应商配置 + 真实密钥（网关永远碰不到）
  v
上游模型 API（DeepSeek / Kimi / GLM / ...）
```

好处：切换 CC Switch 供应商无需重配 Office；Office 兼容修复不污染 CC Switch；真实密钥只在 CC Switch 进程里。

## 2. 为何直连 127.0.0.1（而非公网隧道）

WebView2/Chromium 视 loopback 为可信地址，HTTPS 页面可直接请求 `http://127.0.0.1`，条件是网关返回 CORS 头 + `Access-Control-Allow-Private-Network: true`。早期版本用 cloudflared 临时隧道回环，免费隧道域名周期性被云端吊销（30 分钟~2.5 小时断线一次），loopback 直连根治，且不再依赖系统代理。详见 [ADR-0001](adr/0001-loopback-direct-connection.md)。

## 3. 双代 CC Switch 适配（通道自动识别）

网关对 `~/.cc-switch/cc-switch.db` **只读**（`mode=ro`），每请求实时读取，识别逻辑：

| | 通道 A（claude-desktop） | 通道 B（claude） |
| --- | --- | --- |
| 供应商行 | `app_type='claude-desktop'` | `app_type='claude'` |
| 网关令牌 | `settings.claude_desktop_gateway_token` | 无（占位令牌 PROXY_MANAGED） |
| 模型来源 | `meta.claudeDesktopModelRoutes` | `settings_config.env` 的 `ANTHROPIC_*_MODEL` 五槽位 |
| 上游路径前缀 | `/claude-desktop` | 空（`/v1/messages`） |

`CCSWITCH_CHANNEL=auto`（默认）按"当前启用的供应商行存在于哪张类型下"自动判定；也可显式指定。**因为每请求都重读数据库，CC Switch GUI 切换供应商后下一句话立即生效**——这是"热切换"的全部原理。

## 4. 模型别名机制

Office 会过滤不含 `claude` 的模型 ID。网关把当前供应商的槽位模型包装成 `claude-*` 别名展示；用户选择后，网关在转发前把别名还原为 CC Switch 认识的语义模型 id（`--ccswitch--` 分隔符 + 内部映射表）。模型永不写死。

## 5. 兼容策略：透明优先 + 自动降级

1. 默认**全量透传**（保留 thinking、图片、文档、工具、metadata、beta 头，不阉割能力）；
2. 仅做无损归一化：`tools[].custom` 两种外壳解包、`tool_choice={type:tool}` 转 `auto`（部分上游拒绝强制工具）、模型别名还原；
3. 仅当上游返回"字段不支持"类 4xx 时，自动深度清洗（去 thinking/cache_control/citations/metadata、规整 system 与工具）并**重试一次**；
4. 仍失败则把上游错误**如实**带回（同时落盘 `runtime/last-upstream-error.txt`），绝不伪造成功。

## 6. Files API 与安全

- 元数据 SQLite（含 SHA-256），内容以随机 `file_<hex>` 落盘（禁止文件名拼路径、防穿越），临时文件 + `os.replace` 原子写
- 过期自动清理（默认 24h TTL，上限 24h）；归档 415 拒绝；未知二进制 415 明确报错不静默丢；sqlite 连接保证关闭（防句柄泄漏）
- 文本提取：标准库兜底（zipfile+xml、PDF zlib best-effort）；装了 pypdf/python-docx/openpyxl/python-pptx 自动增强

## 7. 进程模型与自愈

```text
计划任务/HKCU Run（用户登录触发，免管理员）
  -> pythonw supervisor.py     单实例锁 + 健康探测
       -> pythonw gateway/office_edge.py   实际网关
```

- 守护每 5 秒真正请求 `/healthz`（不是看端口/进程），连续 3 次失败才重启——识别"端口还在但服务假死"
- `/healthz` 只代表网关自身，不因 CC Switch 暂未启动而误杀网关
- 自启优先用户级计划任务；注册被拒（策略限制）自动回退 HKCU Run 键

## 8. 稳定性优先级

**历史数据不丢 > 请求透明 > 明确错误 > 自动恢复 > 额外功能**

由此派生的铁律：`cc-switch.db` 永远只读；绝不建议用户清 Office WebView/IndexedDB（聊天历史只存在那里）；网关只监听 127.0.0.1；日志默认脱敏且轮转。
