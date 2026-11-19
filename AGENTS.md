# 天枢中台：Agent 工作约定

本文件写给 agent-autoloop 拉起的命令行 Agent（Claude Code、opencode、Kimi CLI、Gemini CLI 等）。手册里写"按 AGENTS.md"的，指本文件。

## 一、无人值守：命令只写简单的单条

中台规则在本目录无人值守运行，**需要批准的命令没人批，直接被拒**。试运行期间工具报错几乎都是下面这些写法被拦后换写法重试，白耗时间和费用。照下面写，一次过。

容易被拦的写法（按日志里出现次数排）：

1. 字符串里嵌变量或表达式：`"$d\7.二审"`、`"{0:yyyy-MM-dd}" -f ...`
2. `cd`、`Set-Location`：一律用绝对路径，例如 `python <仓库目录>\脚本\ledger.py find <当事人简称>`
3. 子表达式 `$(...)`
4. 脚本块 `{ ... }`：`Where-Object { }`、`ForEach-Object { }`、`if { }`
5. 一行里夹带 `New-Item`、修改环境变量、重定向 `>`、`>>`
6. `Select-String`、`Get-ChildItem` 接管道或复杂路径参数
7. Bash 里的 `find ... | sort` 这类组合

一般放行的：`python <脚本> <参数>`、`pandoc`，以及 Agent 自带的读文件、写文件、改文件、找文件、搜内容功能。具体放行哪些，以所用 Agent 的权限配置为准。

## 二、稍复杂的处理写成 .py 再跑

要筛选、循环、拼路径、存中间结果、批量读文件的，用写文件功能把脚本写到 `%TMP%\claude_work\<规则名>\xxx.py`（`%TMP%` 指系统临时目录，写成展开后的绝对路径；不要另跑命令建目录），再 `python 该文件 参数`。脚本里打印中文前加 `sys.stdout.reconfigure(encoding='utf-8')`。中间结果也让脚本写文件，不用 `>` 重定向。

## 三、其他

- 找文件、搜内容用 Agent 自带的查找功能，不用 `Get-ChildItem -Recurse | Select-String`。
- 改已有文件前先读一遍，有的 Agent 不读就不让改。
- 台账读写一律经 `脚本\ledger.py`，不带参数运行即打印全部用法。
- 法规、案例数据库取条超时或报错的，同一条最多再试一次，仍失败换另一个可用来源或标"核验失败"，不要连续重试。

## 四、skill 怎么找

手册里写"用 xxx skill"的：所用 Agent 有 skill 机制的照常调用；没有的，直接读仓库 `skills\xxx\SKILL.md` 照做，其中的脚本路径以该 skill 目录为基准。

随仓库提供：court-sms、mineru-ocr、funasr-transcribe、claim-chart、judgment-analyzer、gongwen-format、evidence-organizer、case-analyzer、contract-review、case-intake-kit。

以下三个不随仓库提供，有就用；没有时按右栏办：

| skill | 手册里用来做什么 | 没有时 |
|---|---|---|
| litigation-doc-generator | 诉讼文书母版直出（`--type answer` 等）、`dump_docx` 抽正文 | 用 python-docx 按法定格式出 docx，再用 gongwen-format 排版；抽正文用 `pandoc <文件> -t plain -o <txt>` |
| law-opinion-writer | 法律意见书 | 正文出 docx 后按 gongwen-format 排版 |
| guwen-review | 顾问单位批注版和评审表、微信送审收件脚本 | 批注版用 contract-review；评审表由本人在 `示例/` 格式基础上自备模板填写；送审收件规则不启用 |

## 五、通用工作约定

手册引用的通用规则以这里为准。

- **法条现查**：引用的法条（条号、款项、所属法律、施行日期）必须用可用的法规数据库现查核对，不得凭记忆。检索结果只是候选，取到全条原文才算核过。每条落一个核验状态：已核现行、已核历史、仅有候选、核验失败、不适用；后三档不得作为确定依据写进对外稿，稿里标【待核】。
- **对外先确认**：给法院、客户、对方的文书只出稿放进文件夹，先给本人确认，不直接发出或提交。
- **中文**：所有生成内容默认中文，包括文件名和注释。
- **不用破折号**（U+2014，单个或两个连写都算）：改用冒号、逗号、分号或括号，交付前自检。
- **精炼**：结论先行；法条只写条号，不抄原文；不写过渡套话。
- **工作记录**：一轮结束仍有未完成、待确认事项的，在该任务文件夹写或更新当天的 `工作记录（YYYY-MM-DD）.md`；全部完成的不写。
