# ADR-0001：loopback 直连取代公网隧道

- 状态：已采纳（2026-09，实机验证）
- 关联：`docs/ARCHITECTURE.md` §2

## 背景

早期版本用 cloudflared 免费临时隧道把本机网关暴露成公网 HTTPS 域名，让 Office 加载项（HTTPS 页面）访问。实际运行发现：免费 quick tunnel 域名会被云端周期性吊销（30 分钟~2.5 小时一次，报 `Unauthorized: Tunnel not found`），进程还活着但隧道已死；且域名每次变化，已打开的加载项还停在旧地址。旧守护只看进程死活，误判为健康。

## 决策

网关只监听 `127.0.0.1`，Office 加载项直连 `http://127.0.0.1:<port>`。WebView2/Chromium 视 loopback 为可信地址，HTTPS 页面可以直接请求，条件是网关返回 CORS 头 + `Access-Control-Allow-Private-Network: true`（预检时原样回显请求头）。

## 被否掉的方案

- **继续用隧道 + 更激进的看门狗**：治标不治本，域名吊销无法本地预防；
- **命名隧道（付费/登录）**：引入账号依赖与配置门槛，违背"低门槛"目标；
- **让 Office 直连 CC Switch 15721**：CC Switch 不提供模型发现/CORS/PNA/Files 等兼容面，且会混淆两层职责。

## 后果

- 断线问题根治；不再依赖 cloudflared 与系统代理；
- 新增义务：预检必须回 PNA 头与原样回显请求头（漏一个就是"OPTIONS 过了 POST 被拦"）；
- 多设备访问场景（手机/平板）不受支持——如真有需求未来另立 ADR。
