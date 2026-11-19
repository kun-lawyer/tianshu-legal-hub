---
name: tianshu-hub
description: 天枢中台：律师日常工作自动化。安装、配置和运维天枢中台时使用：用户说"装一下天枢中台""改中台配置""加一条规则""停掉某条规则""看看中台在跑什么""晨报没出来""面板打不开""飞书收不到""语音转写不了"，或要把开庭期限、传票、法院短信、新案起草、庭后跟进、送审合同交给 Agent 自动处理时触发。不用于起草具体文书本身（那是各处理手册和 skills/ 下各 skill 的事）。
slug: tianshu-legal-hub
displayName: 天枢中台：律师工作自动化
version: 1.0.0
summary: 律师的日常杂事在后台自己转，律师只在要拍板时点一下。定时出晨报晚报，传票和法院短信自动入台账，开庭前出作战包，庭后跟进，期限前起草文书；本机网页面板一屏看全，对外动作一律先问本人。基于 agent-autoloop，支持 Claude Code 和任意命令行 Agent。
license: MIT
---

# 天枢中台

天枢中台跑在 agent-autoloop 规则引擎上：到点或来事件时，确定性的活交给 `脚本/` 下的 Python，要动脑的活交给 Agent 按 `手册/` 处理，对外发送、提交、删除、花钱的事先推卡片等本人批准。本文件教 Agent 怎么帮用户装、配、管、查这套系统。

下文路径都相对本仓库根目录；Python 一律用 `.venv\Scripts\python.exe`（安装后才有）。项目须知见 `AGENTS.md`，先读它。

## 安装

让用户双击 `安装.cmd`，或在 PowerShell 运行 `.\install.ps1`。它先补齐 Python、Node.js、Git，建 `.venv` 装依赖，再走 11 步向导：生成 `config.json`；填案件台账账号并试登录；选 Agent（Claude Code 或任意命令模板）；装 agent-autoloop；装 `skills/`；可选的 FunASR、MinerU、飞书；合并规则；桌面快捷方式；自检。

- 向导会提问，Agent 不要替用户编答案。用户已把答案给全时，写成应答文件再跑：`.\install.ps1 --answers 应答.json`（键名见 `工具/安装.py` 里各 `ask`、`yes`、`secret` 的第一个参数）。
- 装完想改，重跑 `.venv\Scripts\python.exe 工具\安装.py`；已有的配置、凭据、skill、规则会先问再覆盖，覆盖前备份。
- 案件台账后端不是现成服务。用户没有 台账接口的台账时，告诉他台账相关功能不可用，或照 `脚本/ledger.py` 的命令行用法另写适配器。

## 改配置

配置在 `config.json`（模板 `config.example.json`，每个键都有 `_` 开头的说明）。常改的：

| 要做的事 | 改哪里 |
|---|---|
| 本人姓名、同事、本地地名 | `本人`、`同事`、`地名停用词` |
| 案件资料目录 | `资料根目录`、`目录.*` |
| 开关可选模块 | `微信.启用`、`飞书.启用`、`顾问单位.启用` |
| 语音转写、扫描件识别 | `工具.funasr*`、`工具.mineru_convert` |
| 面板端口 | `面板.端口` |

定时脚本每次运行都重读配置，改完即生效；常驻的面板和飞书收消息程序要重启才生效。开关模块后要重跑安装向导，它会按开关增删对应规则。

## 规则

规则在 `~/.agent-autoloop/rules.json`，模板 `rules.example.json` 共 21 条。不要手工整段覆盖用户的 rules.json，用安装向导合并，或只改要改的那条，改完必须校验：

```
agent-autoloop check              校验，几秒内生效；写错了旧规则继续跑
agent-autoloop rules explain      列出生效规则和下次运行时间
agent-autoloop emit <类型> --dry-run   预演一个事件会派给哪条规则
agent-autoloop pause <规则> / resume <规则>   暂停、恢复
agent-autoloop run <规则>         立即跑一次
```

交给 Agent 的规则与手册对应：开庭作战包、新案成稿、微信待办、飞书助理、法院短信、送审收件处理、案件周研判、改稿回流、面板出稿（`手册/出稿.md`）、庭后跟进。其余是脚本规则：每日晨报、每日晚报、传票入库、作战包派单、庭后派单、期限派单、改稿登记、送审收件读取、微信材料收取、知识库刷新、案号与名称整理。

## 看状态

- `agent-autoloop status`：谁在线、等本人决定什么、在做什么、花了多少；`agent-autoloop doctor` 查哪里没配好。
- 面板：`.venv\Scripts\python.exe 面板\server.py`，打开 `http://127.0.0.1:3351/`。中台没启动时状态和运行记录两栏会提示连不上，其余照常。
- 台账：`.venv\Scripts\python.exe 脚本\ledger.py active`（在办案件）、`agenda --days 30`（近期开庭与期限）、`tasks`（待办）；不带参数运行看全部用法。台账只增改不删，改为已结案须本人确认。
- 报告：`晨报/`、`晚报/`、`周研判/` 下按日期的 .md。

## 排错

| 现象 | 先查 |
|---|---|
| 脚本报"缺少配置文件" | 仓库根目录没有 `config.json`：跑安装向导 |
| 晨报写"读取失败：缺少案件管理后端凭据" | `~/.tianshu-ledger/credentials.json` 不存在或账号错：重跑向导第 4 步 |
| 某模块脚本只打印"未启用" | 正常，`config.json` 里该模块关着 |
| 规则不跑 | `agent-autoloop status` 看中台是否在线；`rules explain` 看规则在不在、下次几点 |
| 周研判报缺 `在办案件勾选清单.xlsx` | 先跑 `脚本\在办判断.py`，复核后另存为该文件名 |
| 飞书收不到消息 | 计划任务「天枢中台-飞书收短信」是否在运行；`lark-cli doctor` 看应用与连通；`lark-cli event status` 看监听 |
| 语音转写不了 | `工具.funasr_python` 指向的环境是否存在；`skills/funasr-transcribe/scripts/setup.py --verify` |
| 扫描件识别慢或失败 | 本机 MinerU 用 CPU 跑会很慢；`~/.mineru-local/venv` 里 `torch.cuda.is_available()`；没有本机服务时需要云端 Token |

## 边界

- 对外发消息、向法院或当事人提交材料、删数据、花钱、改权限、换密钥，一律先问本人（rules.json 的 `askFirst`）。
- 案件材料、客户信息只在本机处理。不要把 `config.json`、凭据、`日志/`、报告、台账导出提交或上传。
- 起草具体文书时按对应手册和 `skills/` 下的 skill 做，本文件不管文书内容。
