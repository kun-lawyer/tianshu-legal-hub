# MinerU PDF -> Markdown Converter（Python 跨平台版）
# 通过 MinerU API 将 PDF/图片等文档转换为 Markdown 格式

from __future__ import annotations

import json
import os
import random
import shutil
import string
import sys
import tempfile
import time
import zipfile
from datetime import datetime
from pathlib import Path

import requests


SKILL_ROOT = Path(__file__).resolve().parent.parent


def _print(msg: str) -> None:
    sys.stdout.buffer.write((msg + "\n").encode("utf-8", errors="replace"))
    sys.stdout.flush()


def load_config() -> dict:
    env_path = SKILL_ROOT / "config" / ".env"
    if not env_path.is_file():
        raise RuntimeError(
            "\n===============================================\n"
            "MinerU 配置文件不存在\n"
            "===============================================\n"
            "请按以下步骤配置：\n\n"
            "1. 访问申请 Token：\n"
            "   https://mineru.net/apiManage/token\n\n"
            "2. 配置 Token：\n"
            "   告诉 AI：\"帮我配置 MinerU，Token 是：xxx\"\n\n"
            "   或手动配置：\n"
            f"   cp {SKILL_ROOT}/config/.env.example {SKILL_ROOT}/config/.env\n"
            f"   编辑 {SKILL_ROOT}/config/.env\n\n"
            "Token 失效时请在官方控制台更新\n"
        )

    config: dict[str, str] = {}
    text = env_path.read_text(encoding="utf-8", errors="replace")
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if (value.startswith('"') and value.endswith('"')) or (
            value.startswith("'") and value.endswith("'")
        ):
            value = value[1:-1]
        config[key] = value

    token = config.get("MINERU_API_TOKEN", "")
    if not token or token == "your_token_here" or "example" in token:
        raise RuntimeError(
            "\n===============================================\n"
            "MinerU API Token 未配置\n"
            "===============================================\n"
            "请按以下步骤配置：\n\n"
            "1. 访问申请 Token：\n"
            "   https://mineru.net/apiManage/token\n\n"
            "2. 配置 Token：\n"
            "   告诉 AI：\"帮我配置 MinerU，Token 是：xxx\"\n\n"
            f"   或手动编辑：{SKILL_ROOT}/config/.env\n"
            "   设置：MINERU_API_TOKEN=你的Token\n\n"
            "Token 失效时请在官方控制台更新\n"
        )

    return config


def _truthy(v: str | None) -> bool:
    return str(v).lower() == "true"


def _gen_data_id() -> str:
    suffix = "".join(random.choices(string.ascii_lowercase + string.digits, k=9))
    return f"convert_{int(time.time() * 1000)}_{suffix}"


def convert(file_path: str) -> dict:
    if not file_path:
        raise RuntimeError("未提供文件路径")

    src = Path(file_path).expanduser()
    if not src.is_file():
        raise RuntimeError(f"文件不存在: {file_path}")

    config = load_config()

    api_base = config.get("MINERU_API_BASE") or "https://mineru.net/api/v4"
    api_token = config.get("MINERU_API_TOKEN", "")
    user_token = config.get("MINERU_USER_TOKEN", "")
    enable_ocr = _truthy(config.get("MINERU_ENABLE_OCR", "true"))
    enable_table = _truthy(config.get("MINERU_ENABLE_TABLE", "true"))
    enable_formula = _truthy(config.get("MINERU_ENABLE_FORMULA", "false"))
    language_code = config.get("MINERU_LANGUAGE_CODE") or "ch"
    allowed_exts_raw = config.get("MINERU_ALLOWED_EXTS") or "pdf,doc,docx,ppt,pptx,png,jpg,jpeg"
    allowed_exts = [e.strip().lower() for e in allowed_exts_raw.split(",") if e.strip()]
    try:
        poll_max = int(config.get("MINERU_POLL_MAX") or 60)
    except ValueError:
        poll_max = 60
    try:
        poll_sleep = int(config.get("MINERU_POLL_SLEEP") or 10)
    except ValueError:
        poll_sleep = 10
    log_level = (config.get("MINERU_LOG_LEVEL") or "low").lower()
    level_map = {"low": 1, "medium": 2, "high": 3}
    cur_level = level_map.get(log_level, 1)

    def log(msg: str, level: int = 1) -> None:
        if cur_level >= level:
            _print(msg)

    ext = src.suffix.lstrip(".").lower()
    if ext not in allowed_exts:
        raise RuntimeError(f"不支持的文件类型: {ext}")

    file_name = src.name
    log(f"开始转换: {file_name}", 2)

    work_dir = Path(tempfile.mkdtemp(prefix="mineru_"))
    input_file = work_dir / f"input.{ext}"

    try:
        shutil.copyfile(src, input_file)

        # 1. 申请上传地址
        data_id = _gen_data_id()
        req1 = {
            "enable_formula": bool(enable_formula),
            "language": language_code,
            "enable_table": bool(enable_table),
            "files": [
                {"name": file_name, "is_ocr": bool(enable_ocr), "data_id": data_id}
            ],
        }
        headers1 = {
            "Authorization": f"Bearer {api_token}",
            "Content-Type": "application/json",
        }
        if user_token:
            headers1["token"] = user_token

        r1 = requests.post(
            f"{api_base}/file-urls/batch",
            headers=headers1,
            json=req1,
            timeout=60,
        )
        (work_dir / "resp1.json").write_text(r1.text, encoding="utf-8")

        if r1.status_code not in (200, 201):
            body = r1.text
            if r1.status_code in (401, 403) or "Unauthorized" in body or "invalid" in body.lower():
                raise RuntimeError(
                    "\n===============================================\n"
                    "MinerU API Token 无效或已过期\n"
                    "===============================================\n"
                    f"HTTP 状态: {r1.status_code}\n\n"
                    "Token 失效时请在官方控制台更新\n\n"
                    "解决方法：\n"
                    "1. 访问重新申请 Token：\n"
                    "   https://mineru.net/apiManage/token\n\n"
                    "2. 告诉 AI：\"我的 MinerU Token 过期了，新的 Token 是：xxx\"\n\n"
                    f"   或手动更新：{SKILL_ROOT}/config/.env\n"
                )
            raise RuntimeError(f"申请上传地址失败 (HTTP {r1.status_code}): {body}")

        o = r1.json()
        data = o.get("data") if isinstance(o.get("data"), dict) else {}
        batch_id = o.get("batch_id") or data.get("batch_id") or ""
        file_urls = o.get("file_urls") or data.get("file_urls") or []
        oss_headers = o.get("headers") or data.get("headers") or []

        if not batch_id or not file_urls:
            raise RuntimeError(f"API 响应缺少 batch_id 或 file_urls: {r1.text}")

        upload_url = file_urls[0]
        if isinstance(upload_url, str):
            try:
                parsed = json.loads(upload_url)
                if isinstance(parsed, str):
                    upload_url = parsed
            except (ValueError, TypeError):
                pass
        upload_url = str(upload_url).replace("\n", " ").replace("\r", " ").replace("\t", " ").strip()

        log(f"batchId: {batch_id}", 3)
        log(f"uploadURL: {upload_url}", 3)

        # 2. 上传文件到 OSS
        put_headers: dict[str, str] = {}
        if isinstance(oss_headers, list):
            for h in oss_headers:
                if isinstance(h, dict):
                    for k, v in h.items():
                        if k:
                            put_headers[str(k)] = str(v)

        with input_file.open("rb") as fp:
            r_up = requests.put(upload_url, headers=put_headers, data=fp, timeout=300)

        if r_up.status_code not in (200, 201):
            raise RuntimeError(f"文件上传失败 (HTTP {r_up.status_code}): {r_up.text}")

        log("上传成功", 2)

        # 3. 轮询转换结果
        poll_url = f"{api_base}/extract-results/batch/{batch_id}"
        poll_headers = {"Authorization": f"Bearer {api_token}"}
        if user_token:
            poll_headers["token"] = user_token

        log("开始轮询结果...", 2)
        result_url = ""
        poll_count = 0
        last_poll_path = work_dir / "poll_last.json"

        while poll_count < poll_max and not result_url:
            time.sleep(poll_sleep)
            poll_count += 1

            rp = requests.get(poll_url, headers=poll_headers, timeout=60)
            last_poll_path.write_text(rp.text, encoding="utf-8")
            try:
                st = rp.json()
            except ValueError:
                continue
            arr = (st.get("data") or {}).get("extract_result") or []
            if not isinstance(arr, list) or not arr:
                continue

            done_item = next((x for x in arr if isinstance(x, dict) and x.get("state") == "done"), None)
            fail_item = next((x for x in arr if isinstance(x, dict) and x.get("state") == "failed"), None)
            first = arr[0] if isinstance(arr[0], dict) else {}

            if done_item and done_item.get("full_zip_url"):
                result_url = done_item["full_zip_url"]
                log("处理完成！", 2)
            elif fail_item:
                raise RuntimeError(f"MinerU 处理失败: {fail_item.get('err_msg') or '未知错误'}")
            elif poll_count % 10 == 0 or cur_level >= 3:
                log(f"处理状态: {first.get('state', 'unknown')} ({poll_count}/{poll_max})", 2)

        if not result_url:
            raise RuntimeError(f"处理超时，已尝试 {poll_count} 次")

        # 4. 下载结果
        result_zip = work_dir / "result.zip"
        with requests.get(result_url, stream=True, timeout=300) as r_dl:
            r_dl.raise_for_status()
            with result_zip.open("wb") as out:
                for chunk in r_dl.iter_content(chunk_size=65536):
                    if chunk:
                        out.write(chunk)

        extract_dir = work_dir / "unzipped"
        extract_dir.mkdir(exist_ok=True)
        with zipfile.ZipFile(result_zip, "r") as zf:
            zf.extractall(extract_dir)

        md_files = sorted(extract_dir.rglob("*.md"))
        if not md_files:
            raise RuntimeError("未找到 Markdown 文件")
        md_file = md_files[0]

        # 5. 保存 Markdown 到源文件目录
        base_name_no_ext = src.stem
        output_md_path = src.parent / f"{base_name_no_ext}.md"
        shutil.copyfile(md_file, output_md_path)
        log(f"已保存: {output_md_path}", 2)

        # 6. 归档完整结果到 Skill 内部
        archive_root = SKILL_ROOT / "archive"
        archive_root.mkdir(exist_ok=True)
        now = datetime.now()
        date_str = now.strftime("%Y%m%d")
        time_str = now.strftime("%H%M%S")
        archive_sub = archive_root / f"{date_str}_{time_str}_{base_name_no_ext}"
        archive_sub.mkdir(parents=True, exist_ok=True)
        for item in work_dir.iterdir():
            dest = archive_sub / item.name
            if item.is_dir():
                shutil.copytree(item, dest, dirs_exist_ok=True)
            else:
                shutil.copyfile(item, dest)
        log(f"已归档到: {archive_sub}", 2)

        return {
            "success": True,
            "outputPath": str(output_md_path),
            "archivePath": str(archive_sub),
            "message": f"成功转换 {file_name} -> {base_name_no_ext}.md",
        }

    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


def _archive_local(src: Path, result: dict) -> str:
    """本地解析结果归档，与云端保持同一套 archive 目录结构。"""
    archive_root = SKILL_ROOT / "archive"
    now = datetime.now()
    archive_sub = archive_root / f"{now:%Y%m%d}_{now:%H%M%S}_{src.stem}"
    archive_sub.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, archive_sub / f"input{src.suffix}")
    out = Path(result["outputPath"])
    if out.is_file():
        shutil.copyfile(out, archive_sub / out.name)
    # 剥离出的图片移入归档，案件目录只留 .md（源数据区不留中间文件）
    img_dir = result.get("imageDir")
    if img_dir and not result.get("imagesInPlace"):
        src_dir = Path(img_dir)
        if src_dir.is_dir():
            shutil.copytree(src_dir, archive_sub / "images", dirs_exist_ok=True)
    (archive_sub / "meta.json").write_text(
        json.dumps({
            "backend": "local",
            "source": str(src),
            "elapsed_seconds": round(result.get("elapsed", 0), 2),
            "status": result.get("status"),
            "images_extracted": result.get("images", 0),
            "converted_at": now.isoformat(timespec="seconds"),
        }, ensure_ascii=False, indent=1),
        encoding="utf-8",
    )
    return str(archive_sub)


def main(argv: list[str]) -> int:
    try:
        # Windows 控制台默认 GBK，强制 UTF-8 输出
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass

        allowed = {"--cloud", "--local-only"}
        unknown = [a for a in argv if a.startswith("--") and a not in allowed]
        if unknown:
            raise RuntimeError("未知选项: " + ", ".join(unknown))
        args = [a for a in argv if a not in allowed]
        force_cloud = "--cloud" in argv
        local_only = "--local-only" in argv or os.environ.get("MINERU_LOCAL_ONLY", "").lower() in {"1", "true", "yes"}
        if local_only and force_cloud:
            raise RuntimeError("本地限定模式不可同时指定 --cloud")
        if len(args) != 1:
            _print("用法: python convert.py <本地文件路径> [--cloud | --local-only]")
            return 1
        src = Path(args[0]).expanduser().resolve()
        if not src.is_file():
            raise RuntimeError("文件不存在或输入不是本地文件")
        if not force_cloud:
            try:
                sys.path.insert(0, str(Path(__file__).resolve().parent))
                import local_backend
                if not local_backend.available():
                    raise RuntimeError("未检测到本地 MinerU 环境")
                result = local_backend.convert_local(str(src), log=_print)
                result["archivePath"] = _archive_local(src, result)
                _print(result["message"])
                return 0
            except Exception as e:
                if local_only:
                    raise RuntimeError(f"本地解析失败，禁止上传: {e}") from e
                _print(f"本地解析失败，回落官方云端 API: {e}")
        result = convert(str(src))
        _print(result["message"])
        return 0
    except Exception as e:
        _print(f"转换失败: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
