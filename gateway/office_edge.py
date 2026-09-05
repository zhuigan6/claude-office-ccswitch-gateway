# -*- coding: utf-8 -*-
"""
office_edge.py —— Office 加载项 ⇄ CC Switch 边缘网关 v3.0（统一版）
纯 Python 标准库即可运行（PDF/Office 文本提取装了 pypdf 等可选依赖会自动增强）。

链路：
  Word/Excel/PPT 的 Claude 加载项 (https://pivot.claude.ai)
    -> http://127.0.0.1:{EDGE_PORT}   （本机直连，WebView2 视 127.0.0.1 为可信地址）
    -> CC Switch 内置代理 127.0.0.1:15721
    -> CC Switch“当前启用”的供应商（GUI 切换即按请求热切换）

适配两代 CC Switch（自动识别，见 docs/adr/0003）：
  通道 A（claude-desktop）：providers.app_type='claude-desktop'，
        网关令牌存于 settings.claude_desktop_gateway_token，
        模型路由存于 meta.claudeDesktopModelRoutes，上游路径前缀 /claude-desktop。
  通道 B（claude）：providers.app_type='claude'，
        模型槽位存于 settings_config.env 的 ANTHROPIC_MODEL /
        ANTHROPIC_DEFAULT_{HAIKU,SONNET,OPUS,FABLE}_MODEL，上游路径前缀为空。
  可用 CCSWITCH_CHANNEL=auto|claude-desktop|claude 覆盖。

职责（不保存任何供应商密钥）：
  1) CORS / Private-Network-Access 预检放行；
  2) 访问令牌校验（EDGE_TOKEN 未设置时接受占位令牌 PROXY_MANAGED，凭据由 CC Switch 托管）；
  3) 协议适配：Anthropic Messages 透传、OpenAI Chat <-> Anthropic 转换；
  4) 兼容自适应：默认“透明转发”，仅当上游返回“字段不支持”的 4xx 时自动深度清洗重试一次；
  5) Files API：上传落盘 + SQLite 元数据（含 SHA-256），引用 file_id 时内联为图片/文本块；
  6) /v1/models 把当前供应商槽位模型包装成 claude-* 别名，避免被 Office 过滤。

设计原则：历史不丢 > 请求透明 > 明确错误 > 自动恢复 > 额外功能。
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import re
import secrets
import sqlite3
import sys
import threading
import time
import urllib.parse
import zipfile
import zlib
from contextlib import contextmanager
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def _load_env_file(path=None):
    """极简 .env 加载（KEY=VALUE，# 注释；不覆盖已存在的环境变量）。"""
    p = path or os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
    try:
        with open(p, "r", encoding="utf-8-sig") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                k, v = k.strip(), v.strip().strip('"').strip("'")
                if k and k not in os.environ:
                    os.environ[k] = v
    except OSError:
        pass


_load_env_file()

# --------------------------------------------------------------------------- #
# 配置
# --------------------------------------------------------------------------- #
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DATA_DIR = os.path.abspath(os.getenv("EDGE_DATA_DIR") or os.path.join(ROOT, "runtime"))
FILES_DIR = os.path.join(DATA_DIR, "files", "objects")
FILES_DB = os.path.join(DATA_DIR, "files", "files.db")
os.makedirs(FILES_DIR, exist_ok=True)

EDGE_HOST = os.getenv("EDGE_HOST", "127.0.0.1")
EDGE_PORT = int(os.getenv("EDGE_PORT", "8790"))
CCSWITCH_BASE = os.getenv("CCSWITCH_BASE", "http://127.0.0.1:15721").rstrip("/")
CCSWITCH_CHANNEL = os.getenv("CCSWITCH_CHANNEL", "auto").strip().lower()
CCSWITCH_DB = os.getenv(
    "CCSWITCH_DB", os.path.join(os.path.expanduser("~"), ".cc-switch", "cc-switch.db")
)
DEFAULT_ANTHROPIC_MODEL = os.getenv("DEFAULT_ANTHROPIC_MODEL", "claude-sonnet-4-6")
LOG_REDACT = os.getenv("EDGE_LOG_REDACT", "1") != "0"
LOG_SHAPE = os.getenv("EDGE_LOG_SHAPE", "0") == "1"
MAX_BODY = int(os.getenv("EDGE_MAX_BODY_BYTES", str(32 * 1024 * 1024)))
MAX_FILE_BYTES = int(os.getenv("EDGE_MAX_FILE_BYTES", str(32 * 1024 * 1024)))
MAX_EXTRACT_CHARS = int(os.getenv("EDGE_MAX_EXTRACT_CHARS", "60000"))
ACCESS_LOG = os.path.join(DATA_DIR, "edge-access.log")
ACCESS_LOG_MAX = int(os.getenv("EDGE_ACCESS_LOG_MAX", str(2 * 1024 * 1024)))
VERSION = "3.1"

# 到 127.0.0.1 的转发绝不走系统代理（http.client 本身不读环境代理，这里再兜底，防止 502 no body）
for _k in ("NO_PROXY", "no_proxy"):
    os.environ[_k] = "127.0.0.1,localhost"

_db_lock = threading.Lock()
_db_mtime = 0.0
_state_cache: dict = {}
_file_lock = threading.Lock()
_MODEL_MAP: dict = {}  # picker 别名 -> 语义模型 id，用于转发前还原

ARCHIVE_EXT = {".zip", ".rar", ".7z", ".tar", ".gz", ".bz2", ".xz"}
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
TEXT_EXT = {".txt", ".md", ".markdown", ".csv", ".json", ".xml", ".log", ".yaml", ".yml", ".ini", ".html", ".htm"}
OFFICE_EXT = {".docx": "word", ".xlsx": "excel", ".pptx": "ppt"}


def _log(*a):
    print("[edge]", time.strftime("%H:%M:%S"), *a, flush=True)


# --------------------------------------------------------------------------- #
# CC Switch 只读数据库（通道识别 + 实时令牌 + 模型槽位；真正转发实时打 15721）
# --------------------------------------------------------------------------- #
def _slot_entries_from_env(env: dict):
    """通道 B：把 ANTHROPIC_* 槽位映射为 (picker 别名, 语义模型, 真实模型)。"""
    table = (
        ("ANTHROPIC_MODEL", "claude-ccswitch-default", "claude"),
        ("ANTHROPIC_DEFAULT_HAIKU_MODEL", "claude-haiku-4-5", "claude-haiku-4-5"),
        ("ANTHROPIC_DEFAULT_SONNET_MODEL", "claude-sonnet-4-6", "claude-sonnet-4-6"),
        ("ANTHROPIC_DEFAULT_OPUS_MODEL", "claude-opus-4-8", "claude-opus-4-8"),
        ("ANTHROPIC_DEFAULT_FABLE_MODEL", "claude-fable-4-8", "claude-fable-4-8"),
    )
    out = []
    for key, picker, semantic in table:
        real = (env.get(key) or "").strip()
        if real:
            out.append((picker, semantic, real))
    return out


def read_ccswitch_state(force: bool = False) -> dict:
    global _db_mtime, _state_cache, _MODEL_MAP
    try:
        mtime = os.path.getmtime(CCSWITCH_DB)
    except OSError:
        return {"error": f"db not found: {CCSWITCH_DB}"}
    with _db_lock:
        if not force and _state_cache and abs(mtime - _db_mtime) < 0.3:
            return _state_cache
        state = {"token": "", "channel": None, "prefix": "", "active": None,
                 "proxy_base": CCSWITCH_BASE}
        try:
            uri = "file:%s?mode=ro" % urllib.parse.quote(CCSWITCH_DB.replace("\\", "/"), safe=":/")
            con = sqlite3.connect(uri, uri=True, timeout=2.0)
            cur = con.cursor()

            def _load(app_type: str) -> dict | None:
                row = cur.execute(
                    "SELECT id,name,settings_config,meta FROM providers "
                    "WHERE app_type=? AND is_current=1 LIMIT 1", (app_type,)
                ).fetchone()
                if not row:
                    return None
                pid, name, cfg, meta = row
                env, routes, api_format = {}, {}, "anthropic"
                try:
                    env = (json.loads(cfg) or {}).get("env", {}) or {}
                except Exception:
                    pass
                try:
                    m = json.loads(meta or "{}") or {}
                    api_format = m.get("apiFormat", "anthropic")
                    for _k, v in (m.get("claudeDesktopModelRoutes") or {}).items():
                        if isinstance(v, dict) and v.get("model"):
                            routes[_k] = v["model"]
                except Exception:
                    pass
                return {"id": pid, "name": name, "env": env, "api_format": api_format,
                        "routes": routes}

            active = None
            channel, prefix = None, ""
            order = {"claude-desktop": ("claude-desktop", "/claude-desktop"),
                     "claude": ("claude", "")}
            if CCSWITCH_CHANNEL in order:
                app_type, prefix = order[CCSWITCH_CHANNEL]
                active = _load(app_type)
                channel = app_type if active else None
            else:  # auto：优先识别 claude-desktop（旧版特指 Claude Desktop 分类），
                   # 再识别 claude（新版合并后的通用分类）。顺序不可颠倒：
                   # 旧版库里 'claude' 是 Claude Code（编辑器）分类，先试会抓错。
                for app_type, pfx in (("claude-desktop", "/claude-desktop"), ("claude", "")):
                    active = _load(app_type)
                    if active:
                        channel, prefix = app_type, pfx
                        break

            if channel == "claude-desktop" and active:
                try:
                    r = cur.execute(
                        "SELECT value FROM settings WHERE key='claude_desktop_gateway_token'"
                    ).fetchone()
                    if r:
                        state["token"] = r[0]
                except sqlite3.Error:
                    pass

            entries = []
            if active:
                if channel == "claude-desktop":
                    slot_order = ["claude-opus-5", "claude-sonnet-5", "claude-haiku-4-5", "claude-fable-5"]
                    keys = [k for k in slot_order if k in active["routes"]] + \
                           [k for k in active["routes"] if k not in slot_order]
                    for k in keys:
                        entries.append((k, k, active["routes"][k]))
                else:
                    entries = _slot_entries_from_env(active["env"])
            state.update({"channel": channel, "prefix": prefix, "active": active,
                          "model_entries": entries})
            _MODEL_MAP = {picker: semantic for picker, semantic, _ in entries}
            con.close()
        except Exception as exc:
            state["error"] = f"{type(exc).__name__}: {exc}"
        _state_cache, _db_mtime = state, mtime
        return state


def expected_tokens() -> set:
    """允许的访问令牌：cc-switch 实时网关令牌 + 显式配置的 EDGE_TOKEN。
    两者都为空时表示“占位令牌模式”（Office 里填任意值如 PROXY_MANAGED 均可）。"""
    toks = set()
    live = read_ccswitch_state().get("token", "")
    if live:
        toks.add(live)
    fixed = os.getenv("EDGE_TOKEN", "").strip()
    if fixed:
        toks.add(fixed)
    return toks


def expected_token() -> str:
    return read_ccswitch_state().get("token", "") or os.getenv("EDGE_TOKEN", "").strip()


def _split_base(base: str):
    u = urllib.parse.urlparse(base)
    return u.hostname, u.port or 80, u.scheme == "https"


def upstream_prefix() -> str:
    return read_ccswitch_state().get("prefix", "") or ""


# --------------------------------------------------------------------------- #
# 模型别名：Office 会过滤不含 claude 的模型 ID；包装为 claude 槽位别名，转发前还原
# --------------------------------------------------------------------------- #
SLOT_LABEL = {
    "claude-opus-5": "Opus", "claude-sonnet-5": "Sonnet",
    "claude-haiku-4-5": "Haiku", "claude-fable-5": "Fable",
    "claude-ccswitch-default": "Default",
}


def canonical_model(model: str):
    """claude-opus-5--ccswitch--deepseek-v4-pro[1m] -> claude-opus-5（经 _MODEL_MAP 还原语义 id）"""
    if not isinstance(model, str):
        return model
    base = model.split("--ccswitch--")[0]
    return _MODEL_MAP.get(base, base)


def build_models_list(state: dict):
    entries = state.get("model_entries") or []
    prov = (state.get("active") or {}).get("name") or "ccswitch"
    data, now = [], int(time.time())
    for picker, _semantic, real in entries:
        data.append({
            "id": picker,
            "type": "model",
            "display_name": f"{SLOT_LABEL.get(picker, picker)} · {real} ({prov})",
            "created_at": now,
        })
    return {"data": data, "has_more": False,
            "first_id": data[0]["id"] if data else None,
            "last_id": data[-1]["id"] if data else None}


# --------------------------------------------------------------------------- #
# 报文兼容：默认透明；仅在上游报“不支持字段/variant”时做一次深度清洗
# --------------------------------------------------------------------------- #
_ANTHROPIC_TOP_KEEP = {
    "model", "messages", "system", "max_tokens", "temperature", "top_p", "top_k",
    "stop_sequences", "stream", "tools", "tool_choice",
}
# 上游 4xx 中出现这些字样时，认为是“字段不兼容”，值得清洗后重试一次
_RETRYABLE_4XX = re.compile(
    r"unknown variant|unknown field|unexpected (field|key|token)|deserialize|invalid type|"
    r"thinking|service_tier|metadata|cache_control|tool_choice|input_schema|extra field|"
    r"not (support|allow)|expected ", re.IGNORECASE)


def normalize_tools(tools):
    """无损标准化工具定义，兼容两种 custom 外壳：
       A) {"type":"custom","name":..,"description":..,"input_schema":..}
       B) {"type":"custom","custom":{"name":..,"description":..,"input_schema":..}}
       -> 标准 {"name","description","input_schema"}（Claude/DeepSeek 都接受）"""
    if not isinstance(tools, list):
        return tools
    out = []
    for t in tools:
        if not isinstance(t, dict):
            continue
        src = t.get("custom", t) if (t.get("type") == "custom" and isinstance(t.get("custom"), dict)) else t
        clean = {k: src[k] for k in ("name", "description", "input_schema") if k in src}
        if clean.get("name"):
            out.append(clean)
    return out


def normalize_payload(p: dict) -> dict:
    """每次都做的“无损”归一化：工具外壳、模型别名还原；不删任何内容块/字段。"""
    if not isinstance(p, dict):
        return p
    if "model" in p:
        p["model"] = canonical_model(p.get("model"))
    if isinstance(p.get("tools"), list):
        p["tools"] = normalize_tools(p["tools"])
    # DeepSeek 类可能拒绝强制 tool_choice={type:tool,...}，自动选择更稳
    tc = p.get("tool_choice")
    if isinstance(tc, dict) and tc.get("type") == "tool":
        p["tool_choice"] = {"type": "auto"}
    return p


def _strip_mapping(d):
    return {k: v for k, v in d.items() if k not in ("cache_control", "citations")} if isinstance(d, dict) else d


def _strip_block(b):
    if not isinstance(b, dict):
        return b
    if b.get("type") in ("thinking", "redacted_thinking"):
        return None
    out = {}
    for k, v in b.items():
        if k in ("cache_control", "citations"):
            continue
        if k == "content" and isinstance(v, list):
            v = [x for x in (_strip_block(y) for y in v) if x is not None]
        elif k == "input" and isinstance(v, dict):
            v = _strip_mapping(v)
        out[k] = v
    return out


def deep_sanitize(p: dict) -> dict:
    """深度清洗（仅在透明转发被上游拒绝后使用一次）。"""
    if not isinstance(p, dict):
        return p
    if isinstance(p.get("tools"), list):
        kept = [t for t in normalize_tools(p["tools"]) if t.get("name")]
        if kept:
            p["tools"] = kept
        else:
            p.pop("tools", None)
    if "tools" not in p:
        p.pop("tool_choice", None)
    sy = p.get("system")
    if isinstance(sy, list):
        parts = [b.get("text", "") if isinstance(b, dict) else str(b) for b in sy]
        sy = "\n\n".join(x for x in parts if x)
    if sy:
        p["system"] = sy
    else:
        p.pop("system", None)
    for m in (p.get("messages") or []):
        if isinstance(m, dict) and isinstance(m.get("content"), list):
            m["content"] = [x for x in (_strip_block(b) for b in m["content"]) if x is not None]
    p = {k: v for k, v in p.items() if k in _ANTHROPIC_TOP_KEEP}
    if not p.get("max_tokens"):
        p["max_tokens"] = 4096
    return p


def _shape_summary(payload: dict) -> str:
    """请求形状摘要（不含正文，不泄漏内容）：模型/流式/角色序列/块类型计数。"""
    msgs = payload.get("messages") if isinstance(payload.get("messages"), list) else []
    roles, blocks = [], {}
    for m in msgs:
        if not isinstance(m, dict):
            continue
        roles.append(str(m.get("role")))
        content = m.get("content")
        if isinstance(content, list):
            for b in content:
                t = b.get("type") if isinstance(b, dict) else type(b).__name__
                blocks[t] = blocks.get(t, 0) + 1
    return (f"model={payload.get('model')} stream={payload.get('stream')} "
            f"max_tokens={payload.get('max_tokens')} tools={len(payload.get('tools') or [])} "
            f"messages={len(msgs)} roles={roles} blocks={blocks}")


# --------------------------------------------------------------------------- #
# Files API：SQLite 元数据（含 SHA-256）+ 落盘内容（随机 ID，禁止文件名拼路径）
# --------------------------------------------------------------------------- #
def files_db():
    con = sqlite3.connect(FILES_DB, timeout=5.0)
    con.execute(
        "CREATE TABLE IF NOT EXISTS files ("
        "id TEXT PRIMARY KEY, filename TEXT, content_type TEXT, size INTEGER,"
        "created_at INTEGER, expires_at INTEGER, purpose TEXT, sha256 TEXT)")
    try:  # 兼容旧库结构
        con.execute("SELECT sha256 FROM files LIMIT 1")
    except sqlite3.OperationalError:
        con.execute("ALTER TABLE files ADD COLUMN sha256 TEXT")
    return con


@contextmanager
def files_conn():
    """保证提交并真正关闭连接（sqlite3 的 with 只提交不关闭，长期运行会泄漏句柄）。"""
    con = files_db()
    try:
        with _file_lock:
            yield con
        con.commit()
    finally:
        con.close()


def prune_expired():
    """删除过期记录与其落盘对象，避免长期堆积。"""
    now = int(time.time())
    try:
        with files_conn() as con:
            rows = con.execute("SELECT id FROM files WHERE expires_at < ?", (now,)).fetchall()
            con.execute("DELETE FROM files WHERE expires_at < ?", (now,))
        for (fid,) in rows:
            try:
                os.remove(os.path.join(FILES_DIR, fid))
            except OSError:
                pass
    except Exception:
        pass


class FileInlineError(Exception):
    def __init__(self, code, message):
        super().__init__(message); self.code = code; self.message = message


def _ext(name: str) -> str:
    return os.path.splitext(name or "")[1].lower()


def files_create(filename, content_type, data: bytes, purpose, expires_in_seconds):
    prune_expired()
    if len(data) > MAX_FILE_BYTES:
        raise FileInlineError(413, f"file too large, max {MAX_FILE_BYTES} bytes")
    ext = _ext(filename)
    if ext in ARCHIVE_EXT:
        raise FileInlineError(415, "archives are not auto-extracted; please unpack first")
    fid = "file_" + secrets.token_hex(16)
    tmp = os.path.join(FILES_DIR, fid + ".tmp")
    dst = os.path.join(FILES_DIR, fid)
    with open(tmp, "wb") as fh:
        fh.write(data)
    os.replace(tmp, dst)  # 原子替换
    now = int(time.time())
    ttl = expires_in_seconds if isinstance(expires_in_seconds, int) and 0 < expires_in_seconds <= 86400 else 3600
    digest = hashlib.sha256(data).hexdigest()
    with files_conn() as con:
        con.execute("INSERT INTO files VALUES (?,?,?,?,?,?,?,?)",
                    (fid, filename, content_type or "application/octet-stream", len(data),
                     now, now + ttl, purpose or "user_message", digest))
    return files_meta(fid)


def files_meta(fid):
    with files_conn() as con:
        r = con.execute("SELECT id,filename,content_type,size,created_at,expires_at,purpose FROM files WHERE id=?", (fid,)).fetchone()
    if not r or int(time.time()) > r[5]:  # 过期视为不存在
        return None
    return {"id": r[0], "type": "file", "filename": r[1], "content_type": r[2], "size_bytes": r[3],
            "created_at": r[4], "expires_at": r[5], "purpose": r[6]}


def files_list():
    prune_expired()
    with files_conn() as con:
        rows = con.execute("SELECT id,filename,content_type,size,created_at,expires_at,purpose FROM files ORDER BY created_at DESC").fetchall()
    data = []
    for r in rows:
        data.append({"id": r[0], "type": "file", "filename": r[1], "content_type": r[2], "size_bytes": r[3],
                     "created_at": r[4], "expires_at": r[5], "purpose": r[6]})
    return {"data": data, "has_more": False}


def files_blob(fid):
    meta = files_meta(fid)
    if not meta:
        return None, None
    dst = os.path.join(FILES_DIR, fid)
    if not os.path.exists(dst):
        return meta, None
    with open(dst, "rb") as fh:
        return meta, fh.read()


def files_delete(fid):
    meta = files_meta(fid)
    with files_conn() as con:
        con.execute("DELETE FROM files WHERE id=?", (fid,))
    dst = os.path.join(FILES_DIR, fid)
    try:
        os.remove(dst)
    except OSError:
        pass
    return meta


def parse_multipart(body: bytes, content_type: str):
    """极简 multipart/form-data 解析，返回 {field_name: (filename, ctype, data)}"""
    m = re.search(r'boundary=(?:"([^"]+)"|([^;]+))', content_type or "")
    if not m:
        return {}
    boundary = (m.group(1) or m.group(2)).strip().encode()
    out = {}
    for part in body.split(b"--" + boundary):
        if not part or part in (b"--", b"--\r\n", b"\r\n"):
            continue
        if part.startswith(b"\r\n"):
            part = part[2:]
        if part.endswith(b"\r\n"):
            part = part[:-2]
        head, sep, content = part.partition(b"\r\n\r\n")
        if not sep:
            continue
        name = filename = ctype = None
        for line in head.decode("utf-8", "ignore").split("\r\n"):
            mm = re.search(r'name="([^"]*)"', line)
            if mm and name is None:
                name = mm.group(1)
            fm = re.search(r'filename="([^"]*)"', line)
            if fm:
                filename = fm.group(1)
            if line.lower().startswith("content-type:"):
                ctype = line.split(":", 1)[1].strip()
        if name is not None:
            out[name] = (filename, ctype, content)
    return out


# --------------------------------------------------------------------------- #
# 文本提取：标准库 best-effort 兜底；装了 pypdf/python-docx/openpyxl/python-pptx 自动增强
# --------------------------------------------------------------------------- #
def _xml_to_text(xml_bytes: bytes) -> str:
    txt = xml_bytes.decode("utf-8", "ignore")
    txt = re.sub(r"<[^>]+>", " ", txt)
    txt = (txt.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
           .replace("&quot;", '"').replace("&#39;", "'").replace("&nbsp;", " "))
    return re.sub(r"\s+", " ", txt).strip()


def extract_office(data: bytes) -> str:
    # 增强路径：可选依赖质量更好（python-docx/openpyxl/python-pptx）
    ext_done = False
    try:
        from docx import Document  # noqa
        from openpyxl import load_workbook  # noqa
        from pptx import Presentation  # noqa
        ext_done = True
    except Exception:
        ext_done = False
    if ext_done:
        try:
            return _extract_office_enhanced(data)
        except Exception as exc:
            _log("enhanced office extract failed, fallback stdlib:", type(exc).__name__)
    parts = []
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        names = z.namelist()
        priority = [n for n in names if re.search(r"(document|sharedStrings|sheet\d+|slide\d+)\.xml$", n)]
        for n in (priority or names):
            if n.endswith(".xml"):
                try:
                    parts.append(_xml_to_text(z.read(n)))
                except Exception:
                    pass
    return "\n".join(x for x in parts if x)[:MAX_EXTRACT_CHARS]


def _extract_office_enhanced(data: bytes) -> str:
    chunks = []
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        names = z.namelist()
        if any(n.endswith(".docx") or n == "word/document.xml" for n in names) or "word/document.xml" in names:
            from docx import Document
            doc = Document(io.BytesIO(data))
            chunks.append("\n".join(p.text for p in doc.paragraphs if p.text))
        if "xl/workbook.xml" in names:
            from openpyxl import load_workbook
            wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
            try:
                for ws in wb.worksheets:
                    chunks.append(f"[Sheet: {ws.title}]")
                    for row in ws.iter_rows(values_only=True):
                        line = "\t".join("" if v is None else str(v) for v in row).rstrip()
                        if line:
                            chunks.append(line)
            finally:
                wb.close()
        if "ppt/presentation.xml" in names:
            from pptx import Presentation
            prs = Presentation(io.BytesIO(data))
            for i, slide in enumerate(prs.slides, 1):
                chunks.append(f"[Slide {i}]")
                for shape in slide.shapes:
                    t = getattr(shape, "text", "")
                    if t:
                        chunks.append(t)
    if not chunks:  # 不是可识别的 Office 结构，交给标准库兜底
        raise ValueError("not a recognized OOXML package")
    return "\n".join(c for c in chunks if c)[:MAX_EXTRACT_CHARS]


def extract_pdf(data: bytes) -> str:
    if MAX_EXTRACT_CHARS > 0:
        try:
            from pypdf import PdfReader
            reader = PdfReader(io.BytesIO(data))
            text = "\n\n".join((page.extract_text() or "") for page in reader.pages)
            text = re.sub(r"\s+", " ", text).strip()
            if text:
                return text[:MAX_EXTRACT_CHARS]
        except ImportError:
            pass
        except Exception as exc:
            _log("pypdf extract failed, fallback stdlib:", type(exc).__name__)
    texts = []
    for sm in re.finditer(rb"stream\r?\n?(.*?)endstream", data, re.S):
        raw = sm.group(1).strip(b"\r\n")
        try:
            dec = zlib.decompress(raw)
        except Exception:
            continue
        s = dec.decode("utf-8", "ignore")
        for tm in re.finditer(r"\((.*?)\)\s*Tj", s, re.S):
            texts.append(tm.group(1))
        for arr in re.finditer(r"\[(.*?)\]\s*TJ", s, re.S):
            for tm in re.finditer(r"\((.*?)\)", arr.group(1), re.S):
                texts.append(tm.group(1))
    out = " ".join(texts)
    out = re.sub(r"\\[()\\]", " ", out)
    return re.sub(r"\s+", " ", out).strip()[:MAX_EXTRACT_CHARS]


def _text_block(name, text):
    return {"type": "text", "text": f"[附件 {name} 的内容]\n{text}"}


IMAGE_MIME = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
              ".gif": "image/gif", ".webp": "image/webp"}


def inline_file_block(block, walk_source=False):
    """把含 file_id 的块内联成上游可直接消费的 image/text 块。"""
    fid = block.get("file_id")
    src = block.get("source") if isinstance(block.get("source"), dict) else None
    if not fid and isinstance(src, dict):
        fid = src.get("file_id")
    if not fid:
        return block
    meta, data = files_blob(fid)
    if not meta:
        raise FileInlineError(404, f"file_id not found: {fid}")
    if data is None:
        raise FileInlineError(410, f"file content missing: {fid}")
    name, ctype = meta["filename"], (meta["content_type"] or "").lower()
    ext = _ext(name)
    if ctype.startswith("image/") or ext in IMAGE_EXT:
        media = ctype if ctype.startswith("image/") else IMAGE_MIME.get(ext, "image/png")
        return {"type": "image",
                "source": {"type": "base64", "media_type": media,
                           "data": base64.b64encode(data).decode("ascii")}}
    if ext in ARCHIVE_EXT:
        raise FileInlineError(415, f"archive not supported inline: {name}")
    text = None
    try:
        if ext in TEXT_EXT or ctype.startswith("text/") or ctype in ("application/json", "application/xml"):
            text = data.decode("utf-8", "ignore")[:MAX_EXTRACT_CHARS]
        elif ext in OFFICE_EXT:
            text = extract_office(data)
        elif ext == ".pdf" or ctype == "application/pdf":
            text = extract_pdf(data)
    except Exception as exc:
        text = None
        _log("extract failed", name, type(exc).__name__)
    if text:
        return _text_block(name, text)
    # 未知二进制：明确报错，不静默丢弃
    raise FileInlineError(415, f"unsupported or unextractable file type for inline: {name} ({ctype or ext})")


def expand_file_refs(payload: dict):
    def walk_blocks(blocks):
        out = []
        for b in blocks:
            if isinstance(b, dict) and (b.get("file_id") or (isinstance(b.get("source"), dict) and b["source"].get("file_id"))):
                out.append(inline_file_block(b))
            else:
                out.append(b)
        return out
    for m in (payload.get("messages") or []):
        if isinstance(m, dict) and isinstance(m.get("content"), list):
            m["content"] = walk_blocks(m["content"])
    sy = payload.get("system")
    if isinstance(sy, list):
        payload["system"] = walk_blocks(sy)
    return payload


# --------------------------------------------------------------------------- #
# OpenAI <-> Anthropic 转换
# --------------------------------------------------------------------------- #
def openai_to_anthropic(payload: dict) -> dict:
    system_parts, out_msgs = [], []
    for m in payload.get("messages") or []:
        role = m.get("role", "user")
        content = m.get("content")
        if isinstance(content, list):
            text = " ".join(c.get("text", "") for c in content if isinstance(c, dict) and c.get("type") == "text").strip()
        else:
            text = str(content or "")
        if role == "system":
            if text:
                system_parts.append(text)
            continue
        if role not in ("user", "assistant"):
            role = "user"
        if text:
            out_msgs.append({"role": role, "content": text})
    if not out_msgs:
        raise ValueError("messages is empty")
    ant = {"model": payload.get("model") or DEFAULT_ANTHROPIC_MODEL,
           "max_tokens": int(payload.get("max_tokens") or payload.get("max_completion_tokens") or 4096),
           "messages": out_msgs, "stream": bool(payload.get("stream", False))}
    if not ant["model"].startswith("claude"):
        ant["model"] = DEFAULT_ANTHROPIC_MODEL
    if system_parts:
        ant["system"] = "\n\n".join(system_parts)
    if "temperature" in payload:
        ant["temperature"] = payload["temperature"]
    return ant


def anthropic_to_openai(ant: dict, req_model: str) -> dict:
    text = "".join(b.get("text", "") for b in ant.get("content", []) if isinstance(b, dict) and b.get("type") == "text")
    usage = ant.get("usage", {})
    pt, ct = usage.get("input_tokens", 0), usage.get("output_tokens", 0)
    stop = ant.get("stop_reason") or "stop"
    finish = {"end_turn": "stop", "max_tokens": "length", "tool_use": "tool_calls"}.get(stop, "stop")
    return {"id": "chatcmpl-" + str(ant.get("id", "")), "object": "chat.completion",
            "created": int(time.time()), "model": req_model,
            "choices": [{"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": finish}],
            "usage": {"prompt_tokens": pt, "completion_tokens": ct, "total_tokens": pt + ct}}


# --------------------------------------------------------------------------- #
# 上游往返（透明优先，4xx 可兼容则深度清洗重试一次；支持 SSE）
# --------------------------------------------------------------------------- #
def _upstream_headers(handler):
    token = expected_token()
    if not token and handler is not None:
        auth = handler.headers.get("Authorization", "")
        token = (auth[7:].strip() if auth.lower().startswith("bearer ")
                 else handler.headers.get("x-api-key", "").strip())
    h = {"Authorization": f"Bearer {token}", "x-api-key": token,
         "anthropic-version": handler.headers.get("anthropic-version", "2023-06-01"),
         "Content-Type": "application/json"}
    for hh in ("anthropic-beta", "x-stainless-helper"):  # 透传压缩/测试版能力头
        v = handler.headers.get(hh)
        if v:
            h[hh] = v
    return h


def _checked_sse_line(line: bytes) -> bytes:
    """校验一行 SSE：data: 载荷必须是合法 JSON（[DONE] 除外）；
    畸形则替换为明确的 error 事件并丢弃原始行。其余行原样放行。"""
    s = line.strip()
    if not s.startswith(b"data: "):
        return line + b"\n"
    payload = s[6:]
    if not payload or payload == b"[DONE]":
        return line + b"\n"
    try:
        json.loads(payload)
    except Exception as exc:
        _log("malformed upstream sse data dropped:",
             type(exc).__name__, payload[:120])
        safe = json.dumps({"type": "error",
                           "error": {"type": "gateway_bad_event",
                                     "message": "Dropped malformed upstream data event"}},
                          ensure_ascii=False)
        return ("event: error\ndata: %s\n\n" % safe).encode("utf-8")
    return line + b"\n"


def upstream_roundtrip(handler, upstream_path, payload, stream, sanitized=False):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    host, port, _ = _split_base(CCSWITCH_BASE)
    conn = HTTPConnection(host, port, timeout=600 if stream else 300)
    try:
        conn.request("POST", upstream_path, body=body, headers=_upstream_headers(handler))
        resp = conn.getresponse()
        if resp.status >= 400:
            err = resp.read()
            conn.close()
            text = err.decode("utf-8", "ignore")
            if (not sanitized) and _RETRYABLE_4XX.search(text):
                _log("upstream 4xx -> deep-sanitize retry:", text[:160].replace("\n", " "))
                retry = deep_sanitize(json.loads(json.dumps(payload)))  # 深拷贝
                return upstream_roundtrip(handler, upstream_path, retry, stream, sanitized=True)
            try:
                with open(os.path.join(DATA_DIR, "last-upstream-error.txt"), "wb") as fh:
                    fh.write(("HTTP %s\n" % resp.status).encode() + err)
            except Exception:
                pass
            ct = "application/json"
            return handler.proxy_response(resp.status, err, [("Content-Type", ct)])
        if stream:
            handler.begin_sse(resp.status)
            # 逐行校验 data: 载荷后再转发：畸形事件替换为明确的 gateway_bad_event，
            # 避免让 Office 前端的 JSON 解析崩溃（对齐另一台实测机器的健壮性设计）。
            buf = b""
            try:
                while True:
                    chunk = resp.read(2048)
                    if not chunk:
                        break
                    buf += chunk
                    *lines, buf = buf.split(b"\n")
                    for line in lines:
                        handler.write_chunk(_checked_sse_line(line))
                if buf.strip():  # 上游断流时的残尾行
                    handler.write_chunk(_checked_sse_line(buf))
            finally:
                handler.end_sse(); conn.close()
        else:
            data = resp.read(); conn.close()
            handler.proxy_response(resp.status, data, [("Content-Type", resp.getheader("Content-Type") or "application/json")])
    except (ConnectionError, OSError, BrokenPipeError) as exc:
        try:
            conn.close()
        except Exception:
            pass
        _log("UPSTREAM UNREACHABLE:", type(exc).__name__, exc)
        try:
            handler._json(502, {"error": {"type": "api_error", "message": f"cc-switch/upstream unreachable: {exc}"}})
        except Exception:
            pass


def openai_stream_from_anthropic(handler, upstream_path, payload, req_model):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    host, port, _ = _split_base(CCSWITCH_BASE)
    conn = HTTPConnection(host, port, timeout=600)
    conn.request("POST", upstream_path, body=body, headers=_upstream_headers(handler))
    resp = conn.getresponse()
    if resp.status >= 400:
        err = resp.read(); conn.close()
        return handler.proxy_response(resp.status, err, [("Content-Type", "application/json")])
    handler.begin_sse(200, openai_mode=True)
    cid = "chatcmpl-" + str(int(time.time())); created = int(time.time())

    def chunk(d):
        return ("data: " + json.dumps(d, ensure_ascii=False) + "\n\n").encode("utf-8")
    handler.write_chunk(chunk({"id": cid, "object": "chat.completion.chunk", "created": created, "model": req_model,
                               "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}]}))
    buf = b""
    try:
        while True:
            raw = resp.read(2048)
            if not raw:
                break
            buf += raw
            *lines, buf = buf.split(b"\n")
            for line in lines:
                line = line.strip()
                if not line.startswith(b"data:"):
                    continue
                pl = line[5:].strip()
                if pl in (b"[DONE]", b""):
                    continue
                try:
                    ev = json.loads(pl)
                except Exception:
                    continue
                if ev.get("type") == "content_block_delta":
                    delta = (ev.get("delta") or {}).get("text", "")
                    if delta:
                        handler.write_chunk(chunk({"id": cid, "object": "chat.completion.chunk", "created": created,
                                                  "model": req_model,
                                                  "choices": [{"index": 0, "delta": {"content": delta}, "finish_reason": None}]}))
                elif ev.get("type") == "message_stop":
                    handler.write_chunk(chunk({"id": cid, "object": "chat.completion.chunk", "created": created,
                                              "model": req_model,
                                              "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}))
                    handler.write_chunk(b"data: [DONE]\n\n")
    finally:
        handler.end_sse(); conn.close()


# --------------------------------------------------------------------------- #
# HTTP 服务
# --------------------------------------------------------------------------- #
class EdgeHandler(BaseHTTPRequestHandler):
    server_version = f"OfficeEdge/{VERSION}"
    protocol_version = "HTTP/1.1"

    def _origin(self):
        return self.headers.get("Origin", "*")

    def _cors(self, preflight=False):
        self.send_header("Access-Control-Allow-Origin", self._origin())
        self.send_header("Access-Control-Allow-Credentials", "true")
        self.send_header("Vary", "Origin")
        if preflight:
            self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS,PUT,DELETE")
            req_headers = self.headers.get("Access-Control-Request-Headers", "")
            if req_headers:
                self.send_header("Access-Control-Allow-Headers", req_headers)
            else:
                self.send_header("Access-Control-Allow-Headers",
                                 "authorization,content-type,x-api-key,anthropic-version,anthropic-beta,"
                                 "x-stainless-helper,anthropic-dangerous-direct-browser-access,*")
            self.send_header("Access-Control-Allow-Private-Network", "true")
            self.send_header("Access-Control-Max-Age", "86400")

    def _json(self, status, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self._cors(); self.end_headers()
        try:
            self.wfile.write(body)
        except BrokenPipeError:
            pass

    def _presented_token(self, query):
        auth = self.headers.get("Authorization", "")
        if auth.lower().startswith("bearer "):
            return auth[7:].strip()
        if self.headers.get("x-api-key", "").strip():
            return self.headers["x-api-key"].strip()
        def q(n):
            v = query.get(n)
            return (v[0] if v else "") if isinstance(v, list) else (v or "")
        return q("gateway_token") or q("access_token")

    def _check_auth(self, query):
        want = expected_tokens()
        if not want:
            _log("WARN: no gateway token available, auth bypassed")
            return True
        return self._presented_token(query) in want

    def proxy_response(self, status, data, headers, conn=None):
        self.send_response(status)
        has_ct = False
        for k, v in headers:
            self.send_header(k, v)
            if k.lower() == "content-type":
                has_ct = True
        if not has_ct:
            self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data))); self._cors(); self.end_headers()
        try:
            self.wfile.write(data)
        except BrokenPipeError:
            pass
        if conn:
            conn.close()

    def begin_sse(self, status, openai_mode=False):
        self.send_response(status)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.close_connection = True
        self.send_header("Connection", "close"); self._cors(); self.end_headers()

    def write_chunk(self, b):
        self.wfile.write(b); self.wfile.flush()

    def end_sse(self):
        try:
            self.wfile.flush()
        except Exception:
            pass

    def log_message(self, *a):
        if not LOG_REDACT:
            _log(self.address_string(), *a)

    def _access(self, method):
        try:
            if os.path.exists(ACCESS_LOG) and os.path.getsize(ACCESS_LOG) > ACCESS_LOG_MAX:
                os.replace(ACCESS_LOG, ACCESS_LOG + ".1")  # 简单轮转，防止长期运行无限增长
            line = "%s %s %s | origin=%s auth=%s acrm=%s clen=%s\n" % (
                time.strftime("%H:%M:%S"), method, self.path, self.headers.get("Origin", "-"),
                "yes" if (self.headers.get("Authorization") or self.headers.get("x-api-key")) else "no",
                self.headers.get("Access-Control-Request-Method", "-"), self.headers.get("Content-Length", "0"))
            with open(ACCESS_LOG, "a", encoding="utf-8") as fh:
                fh.write(line)
        except Exception:
            pass

    # ---------------- OPTIONS ---------------- #
    def do_OPTIONS(self):
        self._access("OPTIONS")
        self.send_response(204); self._cors(preflight=True)
        self.send_header("Content-Length", "0"); self.end_headers()

    # ---------------- GET ---------------- #
    def do_GET(self):
        self._access("GET")
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        query = urllib.parse.parse_qs(parsed.query)
        if path in ("/healthz", "/health"):
            state = read_ccswitch_state(force=True); active = state.get("active") or {}
            return self._json(200, {"status": "ok", "edge": "office_edge", "version": VERSION,
                                    "ccswitch_channel": state.get("channel"),
                                    "ccswitch_proxy": CCSWITCH_BASE + state.get("prefix", ""),
                                    "active_provider": active.get("name"),
                                    "upstream_base_url": active.get("env", {}).get("ANTHROPIC_BASE_URL"),
                                    "api_format": active.get("api_format"),
                                    "model_routes": active.get("routes")})
        if path == "/status/ccswitch":
            return self._json(200, read_ccswitch_state(force=True))
        if not self._check_auth(query):
            return self._json(401, {"error": {"type": "authentication_error", "message": "invalid gateway token"}})
        if path in ("/v1/models", "/models"):
            state = read_ccswitch_state(force=True)
            if state.get("model_entries"):
                return self._json(200, build_models_list(state))
            return self._simple_proxy_get(upstream_prefix() + "/v1/models")
        m = re.fullmatch(r"/v1/files/([A-Za-z0-9_]+)/content", path)
        if m:
            return self._file_content(m.group(1))
        m = re.fullmatch(r"/v1/files/([A-Za-z0-9_]+)", path)
        if m:
            meta = files_meta(m.group(1))
            return self._json(200, meta) if meta else self._json(404, {"error": {"message": "file not found"}})
        if path == "/v1/files":
            return self._json(200, files_list())
        self._json(404, {"error": {"message": "not found", "path": path}})

    def _simple_proxy_get(self, upstream_path):
        token = expected_token()
        host, port, _ = _split_base(CCSWITCH_BASE)
        conn = HTTPConnection(host, port, timeout=60)
        conn.request("GET", upstream_path, headers={"Authorization": f"Bearer {token}", "x-api-key": token,
                                                    "anthropic-version": "2023-06-01", "Accept": "application/json"})
        resp = conn.getresponse(); data = resp.read(); conn.close()
        self.proxy_response(resp.status, data, [("Content-Type", resp.getheader("Content-Type") or "application/json")])

    def _file_content(self, fid):
        meta, data = files_blob(fid)
        if not meta:
            return self._json(404, {"error": {"message": "file not found"}})
        if data is None:
            return self._json(410, {"error": {"message": "file content missing"}})
        self.send_response(200)
        self.send_header("Content-Type", meta.get("content_type") or "application/octet-stream")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Content-Disposition", f'attachment; filename="{meta.get("filename", fid)}"')
        self._cors(); self.end_headers()
        try:
            self.wfile.write(data)
        except BrokenPipeError:
            pass

    # ---------------- DELETE ---------------- #
    def do_DELETE(self):
        self._access("DELETE")
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        query = urllib.parse.parse_qs(parsed.query)
        if not self._check_auth(query):
            return self._json(401, {"error": {"type": "authentication_error", "message": "invalid gateway token"}})
        m = re.fullmatch(r"/v1/files/([A-Za-z0-9_]+)", path)
        if m:
            meta = files_delete(m.group(1))
            if not meta:
                return self._json(404, {"error": {"message": "file not found"}})
            return self._json(200, {"id": m.group(1), "deleted": True, "type": "file"})
        self._json(404, {"error": {"message": "not found", "path": path}})

    # ---------------- POST ---------------- #
    def do_POST(self):
        self._access("POST")
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        query = urllib.parse.parse_qs(parsed.query)
        if not self._check_auth(query):
            return self._json(401, {"error": {"type": "authentication_error", "message": "invalid gateway token"}})
        length = int(self.headers.get("Content-Length", 0) or 0)
        if length > MAX_BODY:
            return self._json(413, {"error": {"message": "body too large"}})
        raw = self.rfile.read(length) if length else b""

        # Files 上传：multipart/form-data
        if path in ("/v1/files", "/files"):
            return self._files_upload(raw)
        # count_tokens：原样透传，上游不支持就把 404 如实带回（不伪造精确 token）
        if path.endswith("/v1/messages/count_tokens"):
            return self._raw_proxy_post(upstream_prefix() + "/v1/messages/count_tokens", raw)

        try:
            payload = json.loads(raw.decode("utf-8")) if raw else {}
        except Exception:
            return self._json(400, {"error": {"message": "invalid JSON body"}})
        state = read_ccswitch_state()
        provider = (state.get("active") or {}).get("name")

        # Anthropic Messages
        messages_paths = {"/v1/messages", "/messages", upstream_prefix() + "/v1/messages"}
        if path in messages_paths:
            try:
                payload = normalize_payload(payload)
                payload = expand_file_refs(payload)
            except FileInlineError as e:
                return self._json(e.code, {"error": {"type": "invalid_request_error", "message": e.message}})
            if LOG_SHAPE:
                _log("request shape:", _shape_summary(payload))
            _log(f"POST /v1/messages provider={provider} model={payload.get('model')} stream={payload.get('stream')}")
            return upstream_roundtrip(self, upstream_prefix() + "/v1/messages", payload, bool(payload.get("stream")))

        # OpenAI Chat
        if path in ("/v1/chat/completions", "/chat/completions"):
            try:
                ant = openai_to_anthropic(payload)
                ant = normalize_payload(ant)
            except Exception as exc:
                return self._json(400, {"error": {"message": f"bad openai request: {exc}"}})
            req_model = payload.get("model", ant["model"])
            _log(f"POST /v1/chat/completions[oai->ant] provider={provider} stream={ant['stream']}")
            if ant["stream"]:
                return openai_stream_from_anthropic(self, upstream_prefix() + "/v1/messages", ant, req_model)
            return self._openai_nonstream(ant, req_model)
        self._json(404, {"error": {"message": "not found", "path": path}})

    def _raw_proxy_post(self, upstream_path, raw: bytes):
        token = expected_token()
        host, port, _ = _split_base(CCSWITCH_BASE)
        conn = HTTPConnection(host, port, timeout=120)
        h = {"Authorization": f"Bearer {token}", "x-api-key": token,
             "anthropic-version": self.headers.get("anthropic-version", "2023-06-01"),
             "Content-Type": self.headers.get("Content-Type", "application/json")}
        if self.headers.get("anthropic-beta"):
            h["anthropic-beta"] = self.headers["anthropic-beta"]
        conn.request("POST", upstream_path, body=raw, headers=h)
        resp = conn.getresponse(); data = resp.read(); conn.close()
        self.proxy_response(resp.status, data, [("Content-Type", resp.getheader("Content-Type") or "application/json")])

    def _files_upload(self, raw: bytes):
        ctype = self.headers.get("Content-Type", "")
        if "multipart/form-data" not in ctype:
            return self._json(400, {"error": {"message": "expected multipart/form-data"}})
        parts = parse_multipart(raw, ctype)
        file_part = parts.get("file") or parts.get("")
        if not file_part:
            return self._json(400, {"error": {"message": "missing 'file' field"}})
        filename, fct, data = file_part
        purpose = (parts.get("purpose") or (None, None, b"user_message"))[2].decode("utf-8", "ignore")
        exp = None
        if "expires_in_seconds" in parts:
            try:
                exp = int(parts["expires_in_seconds"][2].decode())
            except Exception:
                exp = None
        try:
            meta = files_create(filename or "upload", fct, data, purpose, exp)
        except FileInlineError as e:
            return self._json(e.code, {"error": {"type": "invalid_request_error", "message": e.message}})
        self._json(200, meta)

    def _openai_nonstream(self, ant, req_model):
        body = json.dumps(ant, ensure_ascii=False).encode("utf-8")
        host, port, _ = _split_base(CCSWITCH_BASE)
        conn = HTTPConnection(host, port, timeout=300)
        conn.request("POST", upstream_prefix() + "/v1/messages", body=body, headers=_upstream_headers(self))
        resp = conn.getresponse(); data = resp.read(); conn.close()
        if resp.status >= 400:
            return self.proxy_response(resp.status, data, [("Content-Type", "application/json")])
        try:
            self._json(200, anthropic_to_openai(json.loads(data.decode("utf-8")), req_model))
        except Exception:
            self._json(502, {"error": {"message": "bad upstream response", "raw": data[:500].decode("utf-8", "ignore")}})


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def main():
    files_db().close()
    state = read_ccswitch_state(force=True)
    if state.get("error"):
        _log("WARN read cc-switch db:", state["error"])
    _log("channel =", state.get("channel") or "(none)", "| CC Switch proxy =", CCSWITCH_BASE + state.get("prefix", ""))
    _log("active provider =", (state.get("active") or {}).get("name"))
    srv = Server((EDGE_HOST, EDGE_PORT), EdgeHandler)
    _log(f"Office edge v{VERSION} listening on http://{EDGE_HOST}:{EDGE_PORT}")
    _log("endpoints: /healthz /status/ccswitch /v1/models /v1/messages /v1/chat/completions "
         "/v1/files[GET,POST] /v1/files/{id}[/content] DELETE /v1/files/{id} /v1/messages/count_tokens")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        srv.shutdown()


if __name__ == "__main__":
    sys.exit(main())
