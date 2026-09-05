# 更新日志

本文件遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.0.0/) 格式，
版本号遵循[语义化版本](https://semver.org/lang/zh-CN/)。给人看，不放 git log。

## [3.1.1] - 2026-09-06

### Fixed
- `install.ps1` 旧进程清理限定在**本安装目录**（含已知旧版路径迁移）：此前会误杀本机其他副本的网关进程（干净机测试时发现并复现修复）

## [3.1.0] - 2026-09-06

主题：零 Python 安装 + 端到端测试安全网（ADR-0007）。

### Added
- **Release 自带嵌入式 Python 运行时**：打 tag 自动把"仓库 + 官方嵌入式 Python"打成 zip 发布——用户下载解压双击 `install.bat` 即装，无需安装 Python（见 ADR-0007）
- `install.bat` 双击安装入口（参数原样透传给 install.ps1）
- **端到端测试**：CI 内起真实网关子进程 + mock CC Switch 上游，覆盖消息转发、4xx 自动深清洗重试、流式转发、文件内联全链路（tests/test_e2e.py）
- `install.ps1` 安装前自动迁移：停掉旧版网关/守护进程、移除旧版自启键、检测端口占用并明确报错（不再抢端口）
- `diagnose.ps1` 一键诊断：健康/通道/进程/自启/日志尾部一键收集，令牌密钥自动打码，可直接贴 Issue

### Changed
- 流式转发增加 **SSE 畸形事件防护**：`data:` 载荷逐行校验，坏事件替换为明确的 `gateway_bad_event` 而非让 Office 前端解析崩溃
- `install.ps1` 运行时优先级：自带 `_python\` → 系统 Python → 自动下载官方嵌入式运行时（兜底）

## [3.0.0] - 2026-09-06

统一两台实测机器的两套实现，首个准备公开发布的版本。

### Added
- 双代 CC Switch 通道自动识别（`claude-desktop` / `claude`，`CCSWITCH_CHANNEL=auto`，见 ADR-0003）
- 一键安装 `install.ps1`（Python 自检、.env 生成、自启注册、健康等待）与 `uninstall.ps1`
- 一键验收门 `verify.ps1` + `tools/verify_gateway.py`：健康/模型/CORS+PNA/401/400/404/415/Files 全生命周期（SHA-256 比对），可选 `-RunInference` 真实推理
- 守护进程 `supervisor.py`：单实例锁、每 5 秒应用层健康探测、连续 3 次失败重启、僵死监听清理
- 回归测试套件 `tests/`（21 项，纯标准库）与 GitHub Actions CI（Windows/macOS/Linux 矩阵）
- Files 元数据增加 SHA-256；请求形状脱敏日志（`EDGE_LOG_SHAPE=1`）
- Office 接入双轨文档与旁加载脚本（`scripts/`，注册表结构经实机验证）
- 决策记录 docs/adr/（6 篇）

### Changed
- 网关由本机 v2.0 升级为统一版 v3.0：上游路径/令牌/模型来源不再硬编码（ADR-0003/0004）
- 自启标准化为"用户级计划任务优先、HKCU Run 回退"（ADR-0005）
- 文本提取渐进增强：装有 pypdf/python-docx/openpyxl/python-pptx 时自动启用，未装退回标准库（ADR-0002）

### Security
- `cc-switch.db` 全程只读（`mode=ro`）；网关永不接触真实上游密钥
- 默认只监听 127.0.0.1；日志脱敏且 2MB 轮转；归档附件明确拒绝不自动解压

## [2.0.0] - 2026-09-01（本机历史版本）
- loopback 直连取代 cloudflared 隧道（ADR-0001）
- 透明优先 + 4xx 自动深清洗重试一次；Files API（原子写、过期清理、句柄泄漏修复）
- 访问日志轮转；CORS/PNA 预检修复

## [1.x] - 2026-08（本机历史版本）
- 初版隧道方案与 Office 旁加载打通（细节见原机交接档案）
