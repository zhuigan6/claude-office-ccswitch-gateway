# macOS 支持状态

**当前状态：计划中，未经验证。** 本仓库代码是跨平台纯标准库 Python，理论上可在 macOS 运行；但整条链路（CC Switch macOS 版数据位置、Office for Mac 加载项形态、WebView 数据槽）尚未在一台真实 Mac 上验证过。**在没有完成下表验证前，请勿宣称 macOS 支持。**

## 已知的待验证差异

| 项 | Windows（已验证） | macOS（待验证） |
| --- | --- | --- |
| cc-switch.db 位置 | `%USERPROFILE%\.cc-switch\cc-switch.db` | 预期 `~/.cc-switch/cc-switch.db`（待确认） |
| CC Switch 代理端口 | 15721 | 待确认是否一致 |
| Office 加载项宿主 | WebView2 | WKWebView（CORS/PNA 行为可能不同，PNA 是 Chromium 特有） |
| 常驻方式 | 计划任务 / HKCU Run | launchd（LaunchAgent plist） |
| 僵死进程清理 | `netstat`/`taskkill` | `lsof`/`kill`（supervisor 已按 `os.name` 跳过 Windows 专属路径） |
| 旁加载方式 | HKCU 注册表 | 未知，需调研 Office for Mac 加载项机制 |

## 验证路线（欢迎贡献）

1. 在 macOS 上运行 `python3 gateway/office_edge.py`，确认 `/healthz`、`/v1/models`（对真实 cc-switch.db）正常
2. 确认 Office for Mac 加载项能否直连 `http://127.0.0.1:8790`（WKWebView 的 loopback 策略）
3. 写 `scripts/install.sh`（launchd LaunchAgent + 等健康）
4. 跑 `python3 tools/verify_gateway.py` 全绿后在三个 Office 应用里实测

完成后更新本文件与 README 环境要求表，并按 SemVer 记入 CHANGELOG。
