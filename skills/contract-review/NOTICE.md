# NOTICE

本目录（contract-review）部分内容源自第三方开源项目，按其许可证使用和修改。

## 来源

- 上游项目：legal-assistant-skills（企查查 MCP 集成版），https://github.com/duhu2000/legal-assistant-skills
- 该仓库在 GitHub 上标注为 fork 自 https://github.com/zh-xx/legal-assistant-skills
- 版权声明（见 `SKILL_EN.md` 末尾）：Copyright (c) 2026 JiCheng
- 许可证：Apache License 2.0（SPDX-License-Identifier: Apache-2.0），全文见本目录 `LICENSE-APACHE-2.0.txt`

## 源自上游的文件

- `SKILL_EN.md`、`README_QCC_MCP.md`、`requirements.txt`
- `references/` 下全部文件
- `scripts/` 下除 `add_comments.py` 以外的全部文件（含 `ooxml/`）

## 本项目所做的修改

- `SKILL.md` 由本项目作者重写为精简的中文批注流程（v3.x），默认流程不再调用上游的 `workflow.py` 等脚本
- `scripts/add_comments.py` 为本项目作者新增
- 上游文档中写死的本机配置路径改为通用说法
- 文字中的破折号按本项目文风统一替换

本目录内上游文件继续适用 Apache License 2.0；本仓库其余部分的许可证见仓库根目录 `LICENSE`。
