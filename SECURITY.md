# 安全政策

## 支持的版本

| 版本 | 支持状态 |
| --- | --- |
| latest（见 [Releases](https://github.com/zhuigan6/claude-office-ccswitch-gateway/releases)） | ✅ 接收安全修复 |
| 更早版本 | ❌ 请先升级 |

## 如何报告漏洞

**请不要用公开 Issue 报告安全漏洞。**

使用 GitHub 的私密漏洞报告：本仓库 **Security → Advisories → Report a vulnerability**，或通过 Issue 私下联系维护者（先只说"发现安全问题"，细节走私密渠道）。

报告时请包含：影响版本、复现步骤、影响评估。**不要**包含任何真实 API Key 或令牌样本。

## 范围界定

属于本项目的攻击面：

- 网关 HTTP 服务（默认只监听 `127.0.0.1`）——未授权访问、请求走私、路径穿越（Files API）、附件解析（docx/xlsx/pptx/pdf 解压炸弹等）
- 令牌校验逻辑（占位令牌模式 / 固定令牌模式）
- 对 `cc-switch.db` 的访问是否可能越出只读边界

不属于本项目（请报告给对应上游）：

- 上游模型供应商（DeepSeek/Kimi/GLM 等）自身的安全问题
- CC Switch 本体
- Microsoft Office / WebView2
- 用户在对话中让模型生成的任何内容

## 安全设计基线（供审计参考）

- 网关对 `cc-switch.db` 以 `mode=ro` 只读打开，代码路径无写入
- 真实供应商密钥只存在于 CC Switch 进程内，网关不落盘、不转发存储
- 附件以随机 ID 落盘（文件名不参与拼路径）、临时文件原子替换、归档类型直接拒绝、总额配额限制
- 日志默认脱敏且轮转；`/status/ccswitch` 会返回 CC Switch 网关令牌（仅 loopback 可达）——共机环境请设置 `EDGE_TOKEN` 并了解风险
