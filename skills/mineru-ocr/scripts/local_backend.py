# MinerU 本地后端：常驻 api-server + 解析 + base64 图片剥离
# 与 convert.py 的云端流程并存，本地失败时由 convert.py 回落云端

from __future__ import annotations

import base64
import hashlib
import json
import mimetypes
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

LOCAL_ROOT = Path.home() / ".mineru-local"
VENV_PY = LOCAL_ROOT / "venv" / "Scripts" / "python.exe"
KIT_EXE = LOCAL_ROOT / "venv" / "Scripts" / "mineru-kit.exe"
# 服务端 FileStore 把 blobs 缓存建在自己的工作目录下，不传 cwd 就会继承调用方，
# 于是案件目录里冒出 blobs\（内含原件与识别结果的完整副本，还会被同步盘上传）
STORE_DIR = LOCAL_ROOT / "store"
HOST = "127.0.0.1"
# 8765 已被 funasr-transcribe 占用
PORT = int(os.environ.get("MINERU_LOCAL_PORT", "8766"))
BASE = f"http://{HOST}:{PORT}/v1"

# 模型首次加载约需 2 分钟，服务常驻后单份文档 7 至 20 秒
STARTUP_TIMEOUT = 300
DEFAULT_TIER = os.environ.get("MINERU_LOCAL_TIER", "standard")


def available() -> bool:
    return VENV_PY.is_file() and KIT_EXE.is_file()


def _req(method: str, path: str, body=None, raw: bytes | None = None,
         ctype: str = "application/json", timeout: int = 1800):
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    req = urllib.request.Request(BASE + path, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", ctype)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = resp.read()
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:500]
        raise RuntimeError(f"本地服务 HTTP {e.code} on {path}: {detail}")
    try:
        return json.loads(payload)
    except ValueError:
        return payload


def _alive(timeout: int = 3) -> bool:
    try:
        urllib.request.urlopen(f"http://{HOST}:{PORT}/v1/health", timeout=timeout).read()
        return True
    except Exception:
        return False


def ensure_server(log=lambda m: None) -> None:
    if _alive():
        return
    log("本地 MinerU 服务未运行，正在启动（首次加载模型约 2 分钟）...")
    env = dict(os.environ)
    env.setdefault("MINERU_MODEL_SOURCE", "modelscope")
    # DETACHED_PROCESS 与 CREATE_NO_WINDOW 互斥，同时传会让进程留在调用方的
    # 作业对象里、父进程一退出就被连带杀掉；单传 DETACHED_PROCESS 则会弹出一个
    # 空的控制台窗口。CREATE_NO_WINDOW 配新进程组既隐藏窗口又能存活。
    cmd = [str(KIT_EXE), "api-server", "--host", HOST, "--port", str(PORT), "--tier", DEFAULT_TIER]
    STORE_DIR.mkdir(parents=True, exist_ok=True)
    kw = dict(env=env, cwd=str(STORE_DIR), stdin=subprocess.DEVNULL,
              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True)
    hidden = (getattr(subprocess, "CREATE_NO_WINDOW", 0)
              | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
    try:
        subprocess.Popen(cmd, creationflags=hidden, **kw)
    except OSError:
        subprocess.Popen(cmd, creationflags=getattr(subprocess, "DETACHED_PROCESS", 0), **kw)
    deadline = time.time() + STARTUP_TIMEOUT
    while time.time() < deadline:
        if _alive():
            log("本地服务已就绪")
            return
        time.sleep(3)
    raise RuntimeError(f"本地服务 {STARTUP_TIMEOUT} 秒内未就绪")


_DATA_URI = re.compile(r"!\[([^\]]*)\]\(data:image/(\w+);base64,([A-Za-z0-9+/=\s]+)\)")


def strip_inline_images(md: str, img_dir: Path, stem: str,
                        keep_in_place: bool) -> tuple[str, list[Path]]:
    """剥离内嵌 base64 图片，避免 Markdown 体积膨胀。

    默认落到临时目录、由调用方移入 archive，源目录只留干净的 .md，
    不在案件目录或同步盘里留中间文件。
    """
    saved: list[Path] = []

    def _repl(m):
        alt, ext, b64 = m.group(1), m.group(2), m.group(3)
        try:
            blob = base64.b64decode(re.sub(r"\s+", "", b64))
        except Exception:
            return f"![{alt}](图片解码失败)"
        img_dir.mkdir(parents=True, exist_ok=True)
        name = f"{stem}_img{len(saved) + 1}.{'jpg' if ext == 'jpeg' else ext}"
        path = img_dir / name
        path.write_bytes(blob)
        saved.append(path)
        if keep_in_place:
            return f"![{alt}]({img_dir.name}/{name})"
        return f"![{alt or '图'}]（原图 {name} 见 mineru-ocr 归档）"

    return _DATA_URI.sub(_repl, md), saved


def _upload(p: Path) -> str:
    raw = p.read_bytes()
    up = _req("POST", "/uploads", {
        "filename": p.name,
        "bytes": len(raw),
        "mime_type": mimetypes.guess_type(p.name)[0] or "application/octet-stream",
        "purpose": "parse",
        "sha256sum": hashlib.sha256(raw).hexdigest(),
    })
    # 服务端按 sha256 去重，命中已完成上传时直接复用
    fid = (up.get("file") or {}).get("id") or up.get("file_id")
    if up.get("status") == "completed" and fid:
        return fid
    _req("PUT", f"/uploads/{up['id']}/content", raw=raw, ctype="application/octet-stream")
    done = _req("POST", f"/uploads/{up['id']}/complete", {})
    fid = (done.get("file") or {}).get("id") or done.get("file_id") or done.get("id")
    if not fid:
        raise RuntimeError(f"上传未返回 file_id: {json.dumps(done, ensure_ascii=False)[:300]}")
    return fid


def convert_local(file_path: str, tier: str = DEFAULT_TIER,
                  ocr_mode: str = "auto", log=lambda m: None) -> dict:
    src = Path(file_path).expanduser().resolve()
    if not src.is_file():
        raise RuntimeError(f"文件不存在: {file_path}")

    ensure_server(log)
    t0 = time.time()

    file_id = _upload(src)
    # 本地服务只对 PDF 接受 page_range，图片带上会报 page_range_invalid，随后回落云端
    item = {"source": {"type": "file_id", "file_id": file_id}}
    if src.suffix.lower() == ".pdf":
        item["page_range"] = "all"
    job = _req("POST", "/parse/jobs", {
        "files": [item],
        "tier": tier,
        "ocr_mode": ocr_mode,
        "output_formats": ["markdown"],
    })
    jid = job["job_id"]
    log(f"本地解析中 (job {jid}, 档位 {tier})...")

    while True:
        time.sleep(2)
        st = _req("GET", f"/parse/jobs/{jid}")
        if st["status"] in ("completed", "partial", "failed", "canceled"):
            break

    if st["status"] not in ("completed", "partial"):
        err = json.dumps(st.get("files", [{}])[0].get("error") or {}, ensure_ascii=False)
        raise RuntimeError(f"本地解析 {st['status']}: {err[:300]}")

    md = ""
    for f in st.get("files", []):
        outs = f.get("output_files") or {}
        fid = outs.get("markdown") or outs.get("md")
        if isinstance(fid, dict):
            fid = fid.get("file_id") or fid.get("id")
        if fid:
            c = _req("GET", f"/files/{fid}/content")
            md = c if isinstance(c, str) else (
                c.decode("utf-8", "replace") if isinstance(c, bytes)
                else json.dumps(c, ensure_ascii=False))
    if not md.strip():
        raise RuntimeError("本地解析未返回 Markdown 内容")

    raw_len = len(md)
    keep = os.environ.get("MINERU_KEEP_IMAGES", "").lower() in ("1", "true", "yes")
    img_dir = (src.parent / f"{src.stem}_images") if keep else \
        Path(tempfile.mkdtemp(prefix="mineru_img_"))
    md, images = strip_inline_images(md, img_dir, src.stem, keep_in_place=keep)

    out_path = src.parent / f"{src.stem}.md"
    out_path.write_text(md, encoding="utf-8")
    elapsed = time.time() - t0

    n_img = len(images)
    return {
        "success": True,
        "backend": "local",
        "outputPath": str(out_path),
        "elapsed": elapsed,
        "images": n_img,
        "imageDir": str(img_dir) if n_img else "",
        "imagesInPlace": keep,
        "status": st["status"],
        "message": (f"成功转换 {src.name} -> {out_path.name}"
                    f"（本地 {tier} 档，{elapsed:.1f} 秒，正文 {len(md)} 字"
                    + (f"，剥离内嵌图 {n_img} 张"
                       + ("留在源目录" if keep else "已移入归档")
                       + f"，原始 {raw_len} 字" if n_img else "") + "）"),
    }


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    if len(sys.argv) < 2:
        print("用法: python local_backend.py <文件路径> [档位]")
        sys.exit(1)
    t = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_TIER
    r = convert_local(sys.argv[1], tier=t, log=print)
    print(r["message"])
