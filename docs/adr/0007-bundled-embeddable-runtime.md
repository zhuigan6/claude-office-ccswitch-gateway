# ADR-0007：发行自带嵌入式 Python 运行时（"零 Python 安装"）

- 状态：已采纳（2026-09，v3.1）
- 关联：`.github/workflows/release.yml`；`install.ps1`；`install.bat`

## 背景

网关为纯标准库 Python，无需 pip 依赖，但仍要求用户机器上有 Python 3.9+ 运行时——这是目标用户（有电脑 + CC Switch + Office 的普通人）安装路上最大的一道坎：去 python.org 装解释器、勾选 Add to PATH。

## 决策

三层运行时策略：

1. **Release 附件自带运行时**（主路径）：打 tag 时 CI 自动把"仓库 + Python 官方嵌入式包（embeddable，约 11MB，PSF 许可证允许再分发）"打成 zip 挂到 Release。用户下载 → 解压 → 双击 `install.bat`，全程不感知 Python。
2. **系统 Python 优先级回退**：git clone 用户或高级用户没带 `_python\` 时，install.ps1 自动找系统 Python（py / python，要求 ≥3.9）。
3. **自动下载兜底**：两者都没有时，install.ps1 现场下载嵌入式包解压到 `_python\`（不写系统、免管理员），失败则给出明确指引。

## 被否掉的方案

- **PyInstaller 打包单 exe**：真正的单文件，但未签名 exe 对新开源仓库几乎必触发 Defender/SmartScreen 误报（"Windows 已保护你的电脑"），用户要点"仍要运行"；代码签名证书要年费。误报吓退的用户可能比"装 Python"劝退的还多——且本项目历史已记录过启动文件夹 VBS/BAT 被 Defender 拦截的教训；
- **Go/Rust 重写**：单二进制是终极形态，但等于丢弃刚全链路验证的 v3.0 安全网重新踩坑，违背"拒绝大爆炸重写"纪律。若将来用户量证明值得，正确姿势是并行实现 + 共用语言无关的验收门逐项切换；
- **只提供系统 Python 安装指引**：现状，门槛不减。

## 后果

- 嵌入式运行时无 pip：`-WithExtras`（PDF/Office 增强解析）在自带运行时下不可用，install.ps1 会给出明确警告并退回标准库解析（增强解析本就是可选增强）；
- `_python\` 目录被 gitignore，不入库；发布包由 CI 从 python.org 固定版本（3.11.9）拉取，可复现；
- 发布包仅含 Windows x64 嵌入式运行时；系统 Python 路径仍对 ARM/macOS 开放；
- 升级语义：替换目录后重跑 `install.bat` 即可，`.env` 与 `runtime\` 数据保留。
