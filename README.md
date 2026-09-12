# Claude Office × CC Switch 本地网关

**中文** | [English](README.en.md)

---

## 这是什么

让 Word / Excel / PowerPoint 里的 **Claude 官方加载项**，通过本机一个轻量网关，使用 **CC Switch 当前选中的供应商和模型**（DeepSeek / Kimi / GLM 等 Anthropic 兼容端点均可）。网关检查数据库及 WAL 的变化，后续请求使用更新后的路由；Office 模型菜单可能需要重新打开。

**本机适配层**：默认只监听 `127.0.0.1`，向 CC Switch 转发请求，由 CC Switch 连接供应商。网关只读其配置，不持久化供应商密钥。

```text
Word / Excel / PPT 的 Claude 加载项 (https://pivot.claude.ai)
      ↓  本机 HTTP，CORS/PNA 放行
office_edge 网关  http://127.0.0.1:8790   （纯 Python 标准库，无窗常驻）
      ↓  每请求实时读取 cc-switch.db（只读）拿"当前供应商/模型/令牌"
CC Switch 内置代理  http://127.0.0.1:15721
      ↓
CC Switch 当前供应商（GUI 一键切换，按请求热生效）
```

## 功能

- **Anthropic Messages 透传**：保留 thinking、原生工具、强制工具选择和扩展参数；仅解包 Office custom 工具外壳。上游明确以 400/422 拒绝 metadata/service_tier 时，移除对应字段重试一次。
- **有边界的兼容记忆**：按通道、供应商、模型与配置隔离，五分钟过期；不会自动删除思考、图片、工具或系统提示。详见 [3.4 升级说明](docs/UPGRADE-3.4.md)。
- **报错看得懂**：网关自身错误带机器可读 `code` + 可行动的 `suggestion`；上游错误归一为官方错误形状
- **动态模型列表**：`/v1/models` 按当前供应商实时合成 `claude-*` 别名（避免被 Office 过滤），切换供应商后重开侧栏即更新
- **Files API**：上传/列表/下载/删除（默认 24h 有效、总额配额、后台自动清理），消息引用 `file_id` 自动内联为图片或文本（TXT/MD/CSV/JSON/XML/PDF/DOCX/XLSX/PPTX），归档明确拒绝不解压（防压缩炸弹）
- **自愈常驻**：守护进程每 5 秒健康探测，假死自动重启，开机自启
- **双代 CC Switch 适配**：自动识别新旧两代的数据库结构与上游路径（见 [ADR-0003](docs/adr/0003-upstream-channel-auto-detect.md)），结构再变时给出明确诊断
- **一键安装 / 一键验收**：`install.ps1`（或双击 `install.bat`）装好即用；`verify.ps1` 验收、`diagnose.ps1` 一键脱敏诊断
- **可选增强**：`install.ps1 -WithExtras` 安装 pypdf/python-docx/openpyxl/python-pptx，PDF/Office 文本提取质量更好（不装也能跑，自动退回内置解析）

## 环境要求

| 需要 | 说明 |
| --- | --- |
| Windows 10/11（macOS 见[说明](docs/MACOS-NOTES.md)） | 网关为跨平台纯标准库，macOS 适配进行中 |
| Python 运行时 | **下载 Release 包则不需要**——包内自带官方嵌入式运行时；用 git 安装才需要 Python 3.9+ |
| [CC Switch](https://github.com/farion1231/cc-switch) | 已配置至少一个 **Claude 分类**供应商并启用 |
| Microsoft Office（Word/Excel/PPT） | 已安装官方 Claude 加载项（打开侧栏能用网页版即可） |

## 三步上手

**方式一（推荐，零 Python 安装）**：

1. 到 [Releases](../../releases) 下载最新的 `ClaudeOfficeGateway-vX.Y.Z-windows-x64.zip`，解压到固定目录（如 `C:\Tools\ClaudeOfficeGateway`；不要放 OneDrive/临时/下载目录）
2. 双击 `install.bat`
3. Office 侧配置（见下）→ `verify.ps1` 验收

**方式二（git/开发者）**：

```powershell
# 1. 克隆仓库到固定目录，进入目录，一键安装（无 Python 时自动下载嵌入式运行时）
powershell -ExecutionPolicy Bypass -File .\install.ps1

# 2. Office 侧二选一：
#    A（新版加载项，有 Gateway 配置界面）：
#       URL: http://127.0.0.1:8790  Token: PROXY_MANAGED  Header: x-api-key  Format: Anthropic Messages
#    B（老版加载项无配置界面）：生成旁加载清单并写入注册表
powershell -ExecutionPolicy Bypass -File .\scripts\New-OfficeManifest.ps1
powershell -ExecutionPolicy Bypass -File .\scripts\Install-DeveloperSideload.ps1 -Manifest .\sideload\claude-office-ccswitch-gateway.xml

# 3. 验收（零费用）
powershell -ExecutionPolicy Bypass -File .\verify.ps1
```

然后打开 Word/Excel/PPT 的 Claude 侧栏，正常聊天即为成功。详细图文步骤见 [docs/DEPLOYMENT-WINDOWS.md](docs/DEPLOYMENT-WINDOWS.md)。

**让 AI 代理替你装**：把 [SKILL.md](SKILL.md) 喂给 Claude Code / Cursor 等代理（或直接让它读仓库），它会按手册完成安装、配置、验收和排障。

## 日常使用

- **切换供应商/模型**：直接在 CC Switch GUI 里切；建议切换后关开一次 Office 侧栏以刷新模型列表
- **升级网关**：停掉网关进程 → 覆盖 `gateway/office_edge.py`、`supervisor.py` → 重启（`.env` 与 `runtime\` 数据自动保留，详见[升级](docs/DEPLOYMENT-WINDOWS.md#升级)）
- **卸载**：`powershell -ExecutionPolicy Bypass -File .\uninstall.ps1`（只移除自启与进程，数据保留）

## 常见问题

| 现象 | 处理 |
| --- | --- |
| 加载项显示 `Could not reach gateway` / `Failed to fetch` | 跑 `.\diagnose.ps1` 一键诊断（自动脱敏可直接贴 Issue）；若 8790 端口在但健康失败属假死，守护会在约 20 秒内自动恢复 |
| 一定要安装 Python 吗？ | **不需要**。Release 包自带官方嵌入式运行时（见 [ADR-0007](docs/adr/0007-bundled-embeddable-runtime.md)）；git 方式安装且没有 Python 时，install.ps1 也会自动下载嵌入式运行时 |
| `502 inference gateway` | 网关已收到请求但连不上 15721 或上游：确认 CC Switch 正在运行且当前供应商可用；不要动 Office 配置 |
| `Something went wrong` | 多为工具定义兼容问题，网关已自动转换/重试；仍失败请到 Issues 反馈（附 `runtime\last-upstream-error.txt`，**先删掉其中的密钥**） |
| 模型列表只有一个/不更新 | 在 CC Switch 确认当前供应商已配置 Claude 模型槽位映射；切换后重开侧栏 |
| 聊天历史消失/要求重新登录 | ⚠️ **绝不要清 Office 缓存/WebView 数据**——历史存在本地 IndexedDB，清了就没了。参照 [docs/OFFICE-ONBOARDING.md](docs/OFFICE-ONBOARDING.md) 排查 |

## 能力边界（如实说明）

- 网关只保证**协议完整转发**；视觉/工具调用/长上下文等能力取决于当前供应商的模型本身，网关无法让不支持的模型凭空获得能力
- PDF 扫描件、复杂排版可能提取不全；归档（zip/rar/7z 等）只提示先解压，不自动解压
- 本项目与 Anthropic、Microsoft、CC Switch 均无隶属关系；相关商标归各自所有者

## 文档

- [架构与数据流](docs/ARCHITECTURE.md) · [Windows 部署](docs/DEPLOYMENT-WINDOWS.md) · [Office 接入两方式](docs/OFFICE-ONBOARDING.md) · [验收与排障](docs/VERIFICATION.md) · [macOS 状态](docs/MACOS-NOTES.md)
- [决策记录 ADR](docs/adr/) —— 每个重要设计为什么这样做

## 参与贡献

欢迎 Issue 和 PR，见 [CONTRIBUTING.md](CONTRIBUTING.md)。改代码前请先跑 `python -m unittest discover -s tests` 与 `.\verify.ps1`，绿了再提交。

## 许可证

[MIT](LICENSE)
