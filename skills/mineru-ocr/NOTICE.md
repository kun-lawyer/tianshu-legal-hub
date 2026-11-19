# 来源与改动说明

本技能改自杨卫薪律师及贡献者的 [cat-xierluo/legal-skills](https://github.com/cat-xierluo/legal-skills) 中的 `skills/legal-ocr`（MIT 许可，原文见 `LICENSE.txt`，未改动）。感谢原作者的开放分享。

本版改动要点：

- 本地优先：新增 `scripts/local_backend.py`，默认调用本机 MinerU 4.0 常驻服务（`127.0.0.1:8766`）解析，文件不出本机；本地不可用或解析失败时由 `scripts/convert.py` 自动回落官方云端 API，另提供 `--cloud`（强制云端）与 `--local-only`（禁止上传）。
- 入口为 Python 版 `scripts/convert.py`（本地分流在此实现），在 Windows + PowerShell 下运行，命令一律用 `python`；Node 版 `scripts/convert.js` 保留未改。
- 本地解析会剥离 Markdown 内嵌的 base64 图片并移入 `archive/`，源文件目录只留干净的 `.md`。
- 开源整理：真实 `config/.env`（含 Token）与 `archive/` 不分发并列入 `.gitignore`；说明文字改为工具中立写法。
