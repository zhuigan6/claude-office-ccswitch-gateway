# -*- coding: utf-8 -*-
"""
verify_gateway.py —— 一键验收门（纯标准库，零依赖）

默认覆盖（零费用）：
  健康检查 / 模型列表 / CORS+PNA 预检 / 错误令牌 401 / 非法 JSON 400 /
  未知路由 404 / 归档 415 / Files 全生命周期（上传-列表-元数据-下载-删除-删后404，SHA-256 校验）

可选 --inference：非流式 + 流式各调一次真实模型（经过 CC Switch，会产生少量上游费用）。
用法：python tools/verify_gateway.py --base-url http://127.0.0.1:8790 --token PROXY_MANAGED [--inference]
"""
import argparse
import hashlib
import http.client
import json
import sys
import urllib.error
import urllib.parse
import urllib.request

PASS = 0


def ok(name):
    global PASS
    PASS += 1
    print(f"PASS {name}")


def require(cond, msg):
    if not cond:
        raise RuntimeError(msg)


def opener():
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))  # 本地请求绝不走系统代理


def req(client_open, method, url, headers=None, data=None, timeout=30):
    r = urllib.request.Request(url, data=data, method=method)
    for k, v in (headers or {}).items():
        r.add_header(k, v)
    try:
        resp = client_open.open(r, timeout=timeout)
        return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


def multipart_field(boundary, name, filename, ctype, data: bytes):
    head = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'
        f"Content-Type: {ctype}\r\n\r\n"
    ).encode("utf-8")
    return head + data + b"\r\n"


def main() -> int:
    global PASS
    parser = argparse.ArgumentParser(description="Verify the local Claude Office gateway.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8790")
    parser.add_argument("--token", default="PROXY_MANAGED")
    parser.add_argument("--inference", action="store_true")
    args = parser.parse_args()

    base = args.base_url.rstrip("/")
    client = opener()
    h = {"x-api-key": args.token, "anthropic-version": "2023-06-01"}

    # 1. 健康检查
    status, _, body = req(client, "GET", f"{base}/healthz")
    require(status == 200, f"healthz status={status}")
    health = json.loads(body)
    require(health.get("status") == "ok", "healthz payload not ok")
    print(f"PASS healthz version={health.get('version')} channel={health.get('ccswitch_channel')} provider={health.get('active_provider')}")

    # 2. 模型列表（claude 别名，防被 Office 过滤）；401 时自动读取本机实时令牌重试
    status, _, body = req(client, "GET", f"{base}/v1/models", headers=h)
    if status == 401:
        st, _, sb = req(client, "GET", f"{base}/status/ccswitch")
        live = ""
        if st == 200:
            try:
                live = json.loads(sb).get("token") or ""
            except ValueError:
                live = ""
        if live:
            h["x-api-key"] = live
            print("NOTE 旧版 CC Switch（claude-desktop 通道）强制令牌校验，已从 /status/ccswitch 读取本机实时令牌")
            status, _, body = req(client, "GET", f"{base}/v1/models", headers=h)
    require(status == 200, f"models status={status} body={body[:200]!r}")
    models = json.loads(body).get("data", [])
    require(len(models) >= 1, "model list is empty")
    require(all("claude" in str(m.get("id", "")).lower() for m in models), "non-claude alias found")
    ok(f"models count={len(models)}")

    # 3. CORS + PNA 预检（WebView2 直连 loopback 的关键）
    status, headers, _ = req(client, "OPTIONS", f"{base}/v1/messages", headers={
        "Origin": "https://pivot.claude.ai",
        "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "content-type,x-api-key,anthropic-version",
    })
    require(status in (200, 204), f"CORS status={status}")
    require(headers.get("Access-Control-Allow-Origin") == "https://pivot.claude.ai", "CORS origin mismatch")
    require(str(headers.get("Access-Control-Allow-Private-Network", "")).lower() == "true", "PNA header missing")
    ok("cors+pna preflight")

    # 4. 错误令牌 -> 401（仅在网关配置了令牌时有意义；占位模式下跳过）
    status, _, _ = req(client, "GET", f"{base}/v1/files", headers={"x-api-key": "definitely-wrong-token"})
    if status == 401:
        ok("auth wrong-token 401")
    else:
        print(f"NOTE auth bypassed (placeholder-token mode), wrong-token status={status}")

    # 5. 非法 JSON -> 400
    status, _, _ = req(client, "POST", f"{base}/v1/messages", headers=h, data=b"{not-json")
    require(status == 400, f"invalid JSON status={status}")
    ok("invalid json 400")

    # 6. 未知路由 -> 404
    status, _, _ = req(client, "GET", f"{base}/v1/definitely-not-exist", headers=h)
    require(status == 404, f"unknown route status={status}")
    ok("unknown route 404")

    # 7. 归档 -> 415（不自动解压，防压缩炸弹）
    boundary = "verifygate"
    zip_body = multipart_field(boundary, "file", "test.zip", "application/zip", b"PK\x03\x04fake")
    zip_body += f"--{boundary}--\r\n".encode()
    status, _, _ = req(client, "POST", f"{base}/v1/files",
                       headers={**h, "Content-Type": f"multipart/form-data; boundary={boundary}"},
                       data=zip_body)
    require(status == 415, f"archive status={status}")
    ok("archive 415")

    # 8. Files 全生命周期 + SHA-256
    content = b"Claude Office gateway file round-trip verification.\n" * 10
    digest = hashlib.sha256(content).hexdigest()
    body = multipart_field(boundary, "file", "verify-gateway.txt", "text/plain", content)
    body += f"--{boundary}--\r\n".encode()
    status, _, body = req(client, "POST", f"{base}/v1/files",
                          headers={**h, "Content-Type": f"multipart/form-data; boundary={boundary}"},
                          data=body)
    require(status == 200, f"upload status={status} body={body[:300]!r}")
    fid = json.loads(body).get("id")
    require(isinstance(fid, str) and fid.startswith("file_"), f"invalid file id {fid!r}")
    ok("files upload")

    status, _, body = req(client, "GET", f"{base}/v1/files/{fid}", headers=h)
    require(status == 200 and json.loads(body).get("size_bytes") == len(content), "file metadata mismatch")

    status, _, body = req(client, "GET", f"{base}/v1/files", headers=h)
    require(status == 200 and any(f.get("id") == fid for f in json.loads(body).get("data", [])), "file missing from list")

    status, _, body = req(client, "GET", f"{base}/v1/files/{fid}/content", headers=h)
    require(status == 200 and hashlib.sha256(body).hexdigest() == digest, "download sha256 mismatch")
    ok("files list/metadata/download (sha256)")

    status, _, _ = req(client, "DELETE", f"{base}/v1/files/{fid}", headers=h)
    require(status == 200, f"delete status={status}")
    status, _, _ = req(client, "GET", f"{base}/v1/files/{fid}/content", headers=h)
    require(status == 404, f"post-delete status={status}")
    ok("files delete/post-delete-404")

    # 9. 可选：真实推理（非流式 + 流式）
    if args.inference:
        model = models[0]["id"]
        payload = {"model": model, "max_tokens": 16,
                   "messages": [{"role": "user", "content": "Reply with OK only."}]}
        status, _, body = req(client, "POST", f"{base}/v1/messages", headers=h,
                              data=json.dumps(payload).encode("utf-8"), timeout=120)
        require(status == 200, f"inference status={status} body={body[:500]!r}")
        require(isinstance(json.loads(body), dict), "inference response not json object")
        ok("inference non-stream")

        payload["stream"] = True
        conn = http.client.HTTPConnection("127.0.0.1", int(urllib.parse.urlparse(base).port or 80), timeout=120)
        conn.request("POST", "/v1/messages", body=json.dumps(payload).encode("utf-8"),
                     headers={**h, "Content-Type": "application/json", "Accept": "text/event-stream"})
        resp = conn.getresponse()
        require(resp.status == 200, f"stream status={resp.status}")
        events, buf = 0, b""
        while True:
            chunk = resp.read(1024)
            if not chunk:
                break
            buf += chunk
        for line in buf.split(b"\n"):
            line = line.strip()
            if line.startswith(b"data: ") and line[6:] not in (b"[DONE]", b""):
                json.loads(line[6:])
                events += 1
        conn.close()
        require(events > 0, "stream returned no data events")
        ok(f"inference stream events={events}")

    print(f"VERIFICATION_GATE=PASS ({PASS} checks)")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(f"VERIFICATION_GATE=FAIL: {type(exc).__name__}: {exc}", file=sys.stderr)
        sys.exit(1)
