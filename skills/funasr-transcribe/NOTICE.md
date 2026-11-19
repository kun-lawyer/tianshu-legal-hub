# 来源与改动说明

本技能改自杨卫薪律师及贡献者的 [cat-xierluo/legal-skills](https://github.com/cat-xierluo/legal-skills) 中的 `skills/local-asr`（MIT 许可，原文见 `LICENSE.txt`，未改动），本版沿用 `funasr-transcribe` 这一名称。感谢原作者的开放分享。

本版改动要点：

- 运行说明由 macOS 写法改写为 Windows + PowerShell：命令一律用 `python`（不用 `python3`），去掉 `export PATH=...` 一类 macOS 修复方式，端口占用排查改用 `Get-NetTCPConnection` / `Stop-Process`，临时文件写到 `$env:TEMP`。
- `scripts/init_env.py` 在 Windows 上取当前 Python 解释器路径；它生成的 `assets/skill-env.json` 含本机路径，不随仓库分发。
- 服务默认端口 `127.0.0.1:8765` 不变；同机部署 mineru-ocr 时其本地服务使用 8766，互不冲突。
- 开源整理：`archive/`（转写稿）与 `assets/skill-env.json` 列入 `.gitignore`；补充本地服务安装要点。
- 修正 `scripts/setup.py` 在 Windows 上的内存检测：原用 `GlobalMemoryStatus` 的 32 位字段，内存超过 4 GB 时回绕（64 GB 机器读成 1.6 GB 而拒绝安装），改用 `GlobalMemoryStatusEx`。
