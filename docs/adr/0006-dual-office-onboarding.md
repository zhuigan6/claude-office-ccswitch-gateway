# ADR-0006：Office 接入双轨——Gateway 配置界面 + 旁加载清单

- 状态：已采纳（2026-09）
- 关联：`docs/OFFICE-ONBOARDING.md`；`scripts/New-OfficeManifest.ps1`；`scripts/Install-DeveloperSideload.ps1`

## 背景

让 Office 加载项指向本地网关有两条路，取决于加载项版本：

- **方式 A**：较新版官方加载项在设置里有 Gateway/自定义端点界面，手填 URL/Token/Header/Format 即可；
- **方式 B**：老版加载项没有该界面。实测的死路包括：应用商店转圈、"我的加载项"无上传按钮、共享目录只认 UNC 不认本地盘。最终可行的是 **HKCU 开发者旁加载**（免管理员）：`HKCU\...\WEF\Developer` 下写两条 REG_SZ（名字分别为清单 GUID 与清单完整路径，数据均为清单路径，经实机注册表验证），一份清单声明 Workbook/Document/Presentation 三个 Host，装一次三件套共用。

## 决策

两种方式都保留、都文档化、都提供脚本：

- 方式 A 是首选（零脚本，10 秒填完）；
- 方式 B 由 `scripts\New-OfficeManifest.ps1`（生成清单，可配 `-GatewayUrl`）与 `scripts\Install-DeveloperSideload.ps1`（写/清注册表，`-Remove` 卸载）支撑，注册表结构以实机验证的"两条 REG_SZ"为准，不凭文档猜。

## 被否掉的方案

- **只支持方式 A**：老版加载项用户无路可走；
- **只支持方式 B**：能用界面却逼用户跑脚本，平白增加门槛与出错面；
- **写自定义 .ps1 时用无 BOM 的 UTF-8**：实测 PowerShell 5.x 会按 GBK 解析中文注释直接报错——所有 .ps1 必须 UTF-8 带 BOM（CI 有检查）。

## 后果

- 每种 Office 版本都有明确路径；文档开头就帮用户判断自己该走哪条；
- 脚本必须与本机已验证的注册表结构逐字一致，改动前先 `reg query` 复核；
- 旁加载清单内嵌网关地址，改端口后需重新生成清单并重装。
