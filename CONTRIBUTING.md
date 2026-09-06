# 参与贡献

**中文** | [English](CONTRIBUTING.en.md)

感谢关注！本项目追求**低门槛、长期稳定**，贡献前请花两分钟读完这份约定。

## 动手前

1. 先翻一遍 [docs/adr/](docs/adr/)——重要设计都有一票否决级的理由（比如"为什么不用隧道"），避免重复讨论已否定的路线；
2. 大改动（新接口、换依赖、改协议行为）先开 Issue 对齐，避免做完合不进来；
3. Bug 修复欢迎直接提 PR，附复现步骤。

## 开发环境

零依赖：装好 Python 3.9+ 即可。

```powershell
python -m unittest discover -s tests -v   # 回归测试（单元 + 端到端，必须全绿）
powershell -ExecutionPolicy Bypass -File .\verify.ps1   # 实机验收门（需网关在跑）
```

改 `.ps1` 脚本：**必须保存为 UTF-8 带 BOM**（PowerShell 5.x 对无 BOM 的中文注释按 GBK 解析会报错）。改完可用以下命令自检语法：

```powershell
$e=$null; [System.Management.Automation.Language.Parser]::ParseFile("<文件>", [ref]$null, [ref]$e); $e
```

## 提交约定

- 一个 PR 只做一件事；提交信息用祈使句，如 `fix: 附件过期后返回 410 而非 404`
- 修 bug 请先在 `tests/` 加一个会失败的测试，再修到绿（防复发）
- 涉及用户可见行为的变化必须同步更新 `CHANGELOG.md` 与相关文档

## 铁律（违反任何一条都会被拒）

1. `cc-switch.db` 只读——网关与工具绝不写入
2. 不建议用户清 Office WebView/IndexedDB（聊天历史只存在那里）
3. 不把密钥、`.env`、`runtime/`、任何用户数据提交进仓库
4. 不伪造上游行为（如伪造 count_tokens 精确值、吞掉上游错误）
5. 网关保持只监听 127.0.0.1
6. 稳定性优先级：历史数据不丢 > 请求透明 > 明确错误 > 自动恢复 > 新功能

## 报告安全问题

**不要**用公开 Issue 报安全漏洞。请在 Issue 里只写"发现安全问题"，细节用私有渠道联系维护者。
