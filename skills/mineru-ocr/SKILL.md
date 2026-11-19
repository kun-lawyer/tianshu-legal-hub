---
name: mineru-ocr
homepage: https://github.com/cat-xierluo/legal-skills
author: 杨卫薪律师（微信ywxlaw）；本地优先改造见 NOTICE.md
version: "2.0.0"
description: 将本地文档转换为 Markdown。默认调用本机 MinerU 4.0 常驻服务解析，文件不出本机；本地不可用时自动回落官方云端 API。本技能应在用户需要 PDF 转 Markdown、OCR、扫描件转换、表格识别、图片转文字时使用。
license: Complete terms in LICENSE.txt
---

# MinerU 文档转 Markdown（本地优先）

## 使用方法

在 PowerShell 中运行（`<skill目录>` 为本技能的实际安装位置）：

```powershell
python <skill目录>/scripts/convert.py "文件路径"               # 本地优先，失败回落云端
python <skill目录>/scripts/convert.py "文件路径" --cloud       # 强制走云端
python <skill目录>/scripts/convert.py "文件路径" --local-only  # 只走本地，本地失败即报错，绝不上传
```

也可设环境变量 `MINERU_LOCAL_ONLY=1` 达到 `--local-only` 的效果，适合处理不允许出本机的材料。

输出 Markdown 到源文件同目录同名 `.md`，归档到 `archive/日期_时间_文件名/`。`archive/` 含原件副本与识别结果，已列入 `.gitignore`，不要提交或分享。

## 两条通道

| | 本地（默认） | 云端（回落） |
| --- | --- | --- |
| 文件去向 | 不出本机 | 上传至 MinerU OSS |
| 引擎 | MinerU 4.0.0，MinerU2.5-Pro-1.2B | 官方 v4 API |
| 单份耗时 | 7 至 20 秒（服务常驻时） | 视上传与排队，通常 1 分钟以上 |
| 页数 / 体积 | 无限制 | 600 页、200 MB |
| Token | 不需要 | 需要，约 90 天过期 |
| 网络 | 不需要 | 需要 |

本地解析失败（服务起不来、模型损坏、解析报错）时自动回落云端，不会中断工作。没装本地环境时同样直接走云端。

## 安装与配置

### 云端通道（必配，作为回落）

1. 到 https://mineru.net/apiManage/token 申请 API Token（有效期约 90 天）。
2. 把 `config/.env.example` 复制为 `config/.env`，填入 `MINERU_API_TOKEN=你的Token`。`config/.env` 已列入 `.gitignore`。
3. Python 依赖：`pip install requests`。

`convert.py` 的云端通道必须有 Token，缺 `config/.env` 或 Token 时会报错并给出申请指引。上游的 Node 版 `scripts/convert.js` 仍保留，不填 Token 时可走官方免登录轻量接口（10 MB、20 页以内），用法见上游说明。

### 本地通道（可选，推荐）

本地通道依赖另装的 MinerU 4.0 服务，不随本技能分发。`scripts/local_backend.py` 固定在以下位置找它，两个文件都存在才启用本地通道：

- `~/.mineru-local/venv/Scripts/python.exe`
- `~/.mineru-local/venv/Scripts/mineru-kit.exe`

参考安装步骤（Windows，需 [uv](https://docs.astral.sh/uv/)；显卡、CUDA 版本以 MinerU 官方说明为准：https://github.com/opendatalab/MinerU ）：

```powershell
uv venv "$HOME\.mineru-local\venv" --python 3.12
$env:VIRTUAL_ENV="$HOME\.mineru-local\venv"
uv pip install -U "mineru[torch]>=4.0,<5"
# 上一步会把 torch 换成 PyPI 的 CPU 版，按显卡装回 CUDA 版（下例为 cu130）
uv pip install --reinstall torch==2.14.0 torchvision==0.29.0 --index-url https://download.pytorch.org/whl/cu130
```

两个坑：装 mineru 时 `uv` 会把 CUDA 版 torch 换成 CPU 版，必须回装；RTX 50 系（Blackwell，sm_120）在 cu128 索引里没有 torch 2.14.0，要走 cu130。装完确认 CUDA 可用：

```powershell
$HOME\.mineru-local\venv\Scripts\python.exe -c "import torch;print(torch.cuda.is_available(),torch.cuda.get_device_capability(0))"
```

模型在首次解析时自动下载（约 4.6 GB，默认走 ModelScope 源），无需手动拉。

### 本地服务运行方式

- 常驻服务：`127.0.0.1:8766`（8765 留给 funasr-transcribe；可用 `MINERU_LOCAL_PORT` 改），由 `local_backend.py` 按需自动拉起
- 服务工作目录：`~/.mineru-local/store`（服务端的文件缓存放这里，不会落进案件目录）
- 首次调用会自动启动并加载模型（约 2 分钟，之后约 7 秒），常驻期间占用内存约 3.3 GB，热态解析约 2 秒

`~/.mineru-local` 在用户目录下，换电脑需重装；装好前技能自动走云端，不影响使用。

手动管理：

```powershell
# 启动
$HOME\.mineru-local\venv\Scripts\mineru-kit.exe api-server --host 127.0.0.1 --port 8766 --tier standard
# 健康检查
Invoke-WebRequest http://127.0.0.1:8766/v1/health -UseBasicParsing
```

## 档位

MinerU 4.0 已取消 3.x 的 `pipeline` / `vlm` 说法，改为四档：

| 档位 | 模型 | 适用 |
| --- | --- | --- |
| `flash` | 纯文本层提取 | 已有文字层的电子版 PDF，最快 |
| `basic` | 轻量模型 | 版面简单的扫描件 |
| `standard`（默认） | MinerU2.5-Pro-1.2B | 法律文书默认档，印刷体实测与云端相当 |
| `advanced` | MinerU2.5-Pro-1.2B | 版面复杂、表格密集 |

改档位：设环境变量 `MINERU_LOCAL_TIER`，或直接调 `local_backend.py "文件" advanced`。

## 法律材料注意事项

**手写体票据、收据、转账凭证的 OCR 结果不得直接采信。** 实测同一份手写收据，本地与云端在账号位数、身份证号归属地、金额数字上互相矛盾。凡要写进质证意见、起诉状、证据目录的账号、身份证号、金额、日期，必须回看原件逐字核对。

印刷体文书（判决书、裁决书、申请书、政府公函）实测本地与云端相似度 99.3%，案号、金额、日期全部一致，可作为阅读底本，但引用原文仍须核对原件。

其他行为差异：

- 本地会把公章识别为 `<details><summary>seal</summary>...</details>` 结构，云端不识别
- 本地输出用半角标点（`原告:`），云端用全角（`原告：`），摘抄进文书时注意。案号括号的半全角情况两边一致
- 内嵌 base64 图片会被剥离，默认移入 `archive/<本次归档>/images/`，源文件目录只留干净的 `.md`，避免在案件目录或同步盘里留下中间文件。实测一份判决书从 174 KB 降到正文 5 KB
- 需要图片留在源文件旁边时，设 `MINERU_KEEP_IMAGES=1`，会生成 `<文件名>_images/` 并在 Markdown 里保留相对引用

## 故障排除

| 问题 | 处理 |
| --- | --- |
| 提示"本地解析失败，回落云端" | 已自动回落，不影响交付；需排查时手动启服务看报错 |
| 提示"未检测到本地 MinerU 环境" | `~/.mineru-local/venv` 未安装，按上文安装，或继续用云端 |
| 服务 300 秒未就绪 | 检查显卡驱动与 `venv\Scripts\mineru-kit.exe` 是否存在 |
| CUDA 不可用（退回 CPU，明显变慢） | `torch` 被覆盖成 CPU 版，重装：`uv pip install --reinstall torch==2.14.0 torchvision==0.29.0 --index-url https://download.pytorch.org/whl/cu130` |
| 云端 401 / Unauthorized | Token 过期，到 https://mineru.net/apiManage/token 换新，更新 `config/.env` |

## 维护

本版对上游有改造（`local_backend.py` 与 `convert.py` 的本地分流），从上游同步更新时不要整目录覆盖，否则会退回纯云端上传。改动要点见 `NOTICE.md`。
