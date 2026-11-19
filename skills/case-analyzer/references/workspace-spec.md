# 案件工作区规范

> 工作区根目录 `<MATTERS_ROOT>`：默认 `~/legal-profile/matters/`，设环境变量 `MATTERS_ROOT` 可改到别处。
> case-analyzer 是工作区的唯一创建者与数据层维护者（建骨架用 `scripts/init_matter.py`）。你如有其他读写案件进度的 skill 或脚本，可读写这里，但不负责创建。

使用约定：

- case.our_side、case.stage 未经核实时留空，deadlines 为空并保留核实待办。
- 每条期限都要能回溯起算日、依据和算式；脚本预填的期限须对照原文核实。
- `matter.yaml` 为状态源，matter.md 为阅读视图。不默认复制整卷材料，`材料/` 只放确需引用的件或 OCR 结果。

## 目录结构

```
<MATTERS_ROOT>/<slug>/
├── matter.yaml              ← 机读数据层（唯一数据契约）
├── matter.md                ← 人读看板
├── history.md               ← 大事记，一行一条
├── 材料/                     ← 原始材料（OCR 后的 .md 与原件并存）
└── 产物/                     ← 各模式输出，YYMMDD 前缀
```

## slug 命名

格式：`<年月><序号> <我方简称> <案由>`，全部小写英文或中文均可，不含空格以外的特殊字符。

```
✅ 260726 甲某 民间借贷
✅ 260315 乙公司 建设工程合同
❌ [2026]X01民初1号        （含方括号，跨平台路径易出问题）
❌ 甲某案                        （无日期，无法排序）
```

案号写进 `matter.yaml` 的 `case_no` 字段，不进目录名。一个案件可能有一审二审多个案号，目录名要稳定。

## matter.yaml schema

字段标记含义：
- `🔧 建档时`：setup 模式创建工作区时填入，来自原始材料
- `⏳ 后续`：留空，由后续模式回填，注明由哪个模式填

```yaml
# 案件数据层 v1.0
# 由 case-analyzer 维护。手工改动请同步 matter.md，否则两边会漂移。

# ── 🔧 建档时 ──────────────────────────────
meta:
  slug: "260726 甲某 民间借贷"
  created: 2026-07-26              # 系统时间，禁止估算
  last_updated: 2026-07-26
  created_by: case-analyzer/setup

case:
  name: "甲某诉乙某民间借贷纠纷案"
  case_no: "（2026）X01民初1号"   # 无案号时留空，不编造
  cause: "民间借贷纠纷"
  type: 民事                         # 民事 / 刑事 / 行政 / 仲裁
  stage: 一审                        # 一审 / 二审 / 再审 / 执行 / 仲裁
  court: "XX县人民法院"
  judge: ""                         # 已知才填
  amount: 500000                    # 标的额，单位元，无则 null
  our_side: 被告                     # 我方立场，必填
  filed_date: 2026-07-20            # 立案日或收案日

parties:
  plaintiff:
    - name: "甲某"
      type: 自然人                   # 自然人 / 法人 / 其他组织
      id: ""                        # 身份证号或统一社会信用代码
      credit_code: ""               # 法人填，用企业信息查询渠道核验
  defendant:
    - name: "乙某"
      type: 自然人
      id: ""
  third_party: []

client:
  name: "乙某"
  slug: ""                          # 对应你的客户档案名（如有）
  contact: ""

# ── 🔧 建档时（法定期限按 time-discipline 算式推算）──
deadlines:
  - item: "答辩期限"
    basis: "收到起诉状副本次日起 15 日"
    start: 2026-07-21
    due: 2026-08-04
    remaining: 9                    # 相对 last_updated 计算，每次更新重算
    level: 🟠
    status: 未完成                   # 未完成 / 已完成 / 已逾期
  - item: "举证期限"
    basis: "举证通知书载明"
    start: 2026-07-21
    due: 2026-08-19
    remaining: 24
    level: 🟢
    status: 未完成

hearing:
  date: null                        # 开庭日期，YYYY-MM-DD HH:MM
  location: ""

# ── ⏳ 后续：analyze 模式填 ──────────────────
issues: []
# - id: 1
#   title: "借款合意是否成立"
#   our_view: ""
#   their_view: ""
#   legal_risk: 🔴                   # 双轴严重度（守则 A22）
#   business_friction: 🟡
#   status: 未决                     # 我方有利 / 我方不利 / 未决

legal_relations: []                 # analyze 模式填

# ── ⏳ 后续：analyze / evidence-organizer 填 ──
evidence_gaps: []
# - item: "转账凭证原件"
#   why: "证明实际交付，缺则借贷关系不成立"
#   level: 🔴
#   action: "向银行调取流水"

# ── ⏳ 后续：strategy 模式填 ─────────────────
strategy:
  outlook: ""                       # 定性预判，禁百分比
  outlook_reason: ""                # 定性后必须给理由
  primary_goal: ""
  fallback: ""
  bottom_line: ""

# ── ⏳ 后续：doc 模式填（判决书分析后）──────────
judgment:
  result: ""                        # 胜 / 败 / 部分支持
  key_holding: ""                   # 裁判要旨
  appeal_window_due: null
  appeal_recommendation: ""

# ── ⏳ 后续：每次动作后追加 ───────────────────
status:
  current: 待答辩
  next_action: "起草答辩状"
  next_action_due: 2026-08-04
  blocked_on: ""                    # 卡在什么上，用于找停滞案件
  last_activity: 2026-07-26         # 停滞检测用
```

## 字段使用约定

### 禁止编造

`case_no`、`id`、`credit_code`、`judge`、`amount` 这类事实字段，材料里没有就**留空**，不要填"待确认"以外的任何猜测值。留空是信息缺失，填错是事实错误，后者严重得多。

法人主体的 `credit_code` 用你可用的企业信息查询渠道（天眼查、企查查、国家企业信用信息公示系统等）核验，查询时用完整登记名或 18 位统一社会信用代码，禁止用简称直查。

### remaining 字段每次重算

`deadlines[].remaining` 是相对 `meta.last_updated` 的值，不是静态数字。任何模式读取工作区时都要用当前系统时间重算一遍，发现与存档值不符就更新 yaml。

读到 `remaining` 为负数说明已逾期，改 `status: 已逾期`、`level: 🔴`，并在报告里单独成段提示。

### 与 matter.md 的同步

两份文件覆盖同样的事实，`matter.yaml` 是权威源。改了 yaml 就要同步 md，反之亦然。发现两边不一致时以 yaml 为准，并在 `history.md` 记一行漂移修正。

## matter.md 骨架

```markdown
# [案件名称]

> 数据层：`matter.yaml` | 大事记：`history.md`
> 最后更新：YYYY-MM-DD（系统时间）

## 一、基本信息

| 项 | 内容 |
|---|---|
| 案号 | |
| 案由 | |
| 我方立场 | |
| 当前阶段 | |
| 承办法院 | |
| 标的额 | |

## 二、当事人

原告：
被告：

## 三、期限（🔴≤3天 🟠4-7天 🟡8-15天 🟢>15天）

| 事项 | 算式 | 届满 | 剩余 | 状态 |
|---|---|---|---|---|

## 四、争议焦点

| # | 焦点 | 我方观点 | 对方观点 | 法律风险 | 商业摩擦 | 态势 |
|---|---|---|---|---|---|---|

## 五、证据缺口

## 六、当前状态与下一步

- 现状：
- 下一步：
- 卡在：

## 七、待复核事项

标 `[review]` 的项，等主办律师拍板。
```

## history.md 格式

一行一条，倒序或正序统一即可，建议正序（新的追加在末尾）：

```
2026-07-26 14:30 | 建档 | case-analyzer/setup 创建工作区，归档材料 10 份 | 产物/260726 建档记录.md
2026-07-26 15:10 | 案情分析 | 识别争议焦点 3 项，证据缺口 2 处 | 产物/260726 争议焦点分析.md
2026-08-01 09:00 | 答辩状 | 答辩状起草完成 | 产物/260801 答辩状.docx
```

字段：`时间 | 动作类型 | 摘要 | 产物路径`

每个模式结束时**自动追加**一行，不问用户。

## 产物目录

一次性产物带 `YYMMDD ` 前缀放 `产物/`。同名产物改一轮就新出一份，不覆盖：

```
产物/260726 争议焦点分析.md
产物/260801 争议焦点分析.md      ← 第二轮，与上一份并存
```

需要知道哪份最新时看日期前缀，不需要额外的版本号或"最终版"字样。
