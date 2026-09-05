# ADR-0002：标准库内核 + 可选依赖渐进增强

- 状态：已采纳（2026-09）
- 关联：`gateway/office_edge.py`

## 背景

两条实现路线并存：纯 Python 标准库（零依赖，`http.server` 手写，PDF/Office 提取靠 zlib/zipfile best-effort）与 FastAPI 全家桶（uvicorn+httpx+pypdf 等，解析质量高，但要 venv+pip+锁定版本）。项目目标用户是"有电脑 + CC Switch + Office"的普通人，安装门槛是第一优先级。

## 决策

主干采用**纯标准库**实现：`pip` 依赖为零，装个 Python 就能跑。同时提供**可选渐进增强**：检测到 `pypdf` / `python-docx` / `openpyxl` / `python-pptx` 时自动用高质量解析，未安装则静默退回内置解析（`install.ps1 -WithExtras` 一键安装增强包）。

## 被否掉的方案

- **全面 FastAPI 化**：解析质量好但把"装 Python"变成"装 Python+venv+十来个锁定依赖"，升级时依赖漂移风险高，与低门槛目标冲突；
- **纯标准库到底，不做增强**：PDF/复杂 Office 提取质量明显差距，用户会误以为"网关坏了"。

## 后果

- 网关核心任何机器上 `python office_edge.py` 即起；CI 无需锁依赖；
- 增强路径必须做好异常兜底（增强失败自动退回标准库路径，已有测试覆盖）；
- 验收工具 `tools/verify_gateway.py` 同样保持零依赖。
