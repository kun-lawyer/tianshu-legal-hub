#!/usr/bin/env python3
"""建立案件工作区骨架：目录 + matter.yaml + matter.md + history.md

只做确定性工作（建目录、算期限、写骨架），要素提取与判断仍由模型完成。
所有日期取系统时间，不接受调用方传入"今天是几号"。
工作区根目录默认 ~/legal-profile/matters，可用环境变量 MATTERS_ROOT 指定。
"""
import argparse
import os
import sys
from datetime import date, timedelta
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

MATTERS_ROOT = Path(os.environ.get("MATTERS_ROOT") or Path.home() / "legal-profile" / "matters").expanduser()

# 期限模板：名称 -> (依据, 天数)。起算日为基准日次日（期间开始的日不计入）
DEADLINE_PRESETS = {
    "答辩期限": ("收到起诉状副本次日起 15 日", 15),
    "举证期限": ("举证通知书载明，暂按 30 日预设，收到通知书后据实修正", 30),
    "上诉期限": ("判决书送达次日起 15 日", 15),
    "裁定上诉期限": ("裁定书送达次日起 10 日", 10),
}


def level_of(remaining):
    if remaining < 0:
        return "🔴"
    if remaining <= 3:
        return "🔴"
    if remaining <= 7:
        return "🟠"
    if remaining <= 15:
        return "🟡"
    return "🟢"


def build_deadlines(items, served, today):
    """items 为 [(名称, 依据, 天数)]，起算日 = served + 1 天"""
    out = []
    for name, basis, days in items:
        start = served + timedelta(days=1)
        due = start + timedelta(days=days - 1)
        remaining = (due - today).days
        out.append(
            {
                "item": name,
                "basis": basis,
                "start": start,
                "due": due,
                "remaining": remaining,
                "level": level_of(remaining),
                "status": "已逾期" if remaining < 0 else "未完成",
            }
        )
    return out


def yaml_block(d, today, args, deadlines):
    lines = [
        "# 案件数据层 v1.0",
        "# 由 case-analyzer 维护。手工改动请同步 matter.md，否则两边会漂移。",
        "",
        "meta:",
        f'  slug: "{args.slug}"',
        f"  created: {today}",
        f"  last_updated: {today}",
        "  created_by: case-analyzer/setup",
        "",
        "case:",
        f'  name: "{args.name}"',
        f'  case_no: "{args.case_no}"',
        f'  cause: "{args.cause}"',
        f"  type: {args.case_type}",
        f"  stage: {args.stage}",
        f'  court: "{args.court}"',
        '  judge: ""',
        f"  amount: {args.amount if args.amount is not None else 'null'}",
        f"  our_side: {args.our_side}",
        f"  filed_date: {args.served if args.served else 'null'}",
        "",
        "parties:",
        "  plaintiff: []      # 🔧 建档时按材料填，禁编造证件号",
        "  defendant: []",
        "  third_party: []",
        "",
        "client:",
        f'  name: "{args.client}"',
        '  slug: ""           # 对应你的客户档案名（如有）',
        '  contact: ""',
        "",
        "deadlines:",
    ]
    if deadlines:
        for x in deadlines:
            lines += [
                f'  - item: "{x["item"]}"',
                f'    basis: "{x["basis"]}"',
                f"    start: {x['start']}",
                f"    due: {x['due']}",
                f"    remaining: {x['remaining']}",
                f'    level: "{x["level"]}"',
                f"    status: {x['status']}",
            ]
    else:
        lines.append("  []                 # ⚠️ 未提供送达日，期限待人工补齐")
    lines += [
        "",
        "hearing:",
        "  date: null",
        '  location: ""',
        "",
        "issues: []           # ⏳ analyze 模式填",
        "legal_relations: []  # ⏳ analyze 模式填",
        "evidence_gaps: []    # ⏳ analyze 模式填",
        "",
        "strategy:            # ⏳ strategy 模式填",
        '  outlook: ""        # 定性，禁百分比',
        '  outlook_reason: ""',
        '  primary_goal: ""',
        '  fallback: ""',
        '  bottom_line: ""',
        "",
        "judgment:            # ⏳ doc 模式填",
        '  result: ""',
        '  key_holding: ""',
        "  appeal_window_due: null",
        '  appeal_recommendation: ""',
        "",
        "status:",
        "  current: 已建档",
        '  next_action: "待补齐案件要素并跑 analyze"',
        "  next_action_due: null",
        '  blocked_on: ""',
        f"  last_activity: {today}",
        "",
    ]
    return "\n".join(lines)


def md_block(args, today, deadlines):
    rows = "\n".join(
        f"| {x['item']} | {x['start']} 起算 + {(x['due'] - x['start']).days + 1} 日 = {x['due']} "
        f"| {x['due']} | {x['remaining']} 天 | {x['level']} {x['status']} |"
        for x in deadlines
    ) or "| （待补：未提供送达日） | | | | |"

    return f"""# {args.name}

> 数据层：`matter.yaml` ｜ 大事记：`history.md`
> 最后更新：{today}

## 一、基本信息

| 项 | 内容 |
|---|---|
| 案号 | {args.case_no or "（待补）"} |
| 案由 | {args.cause or "（待补）"} |
| 我方立场 | {args.our_side} |
| 当前阶段 | {args.stage} |
| 承办法院 | {args.court or "（待补）"} |
| 标的额 | {args.amount if args.amount is not None else "（待补）"} |

## 二、当事人

原告：（待补，禁编造证件号）
被告：（待补）

## 三、期限（🔴≤3天 🟠4-7天 🟡8-15天 🟢>15天）

| 事项 | 算式 | 届满 | 剩余 | 状态 |
|---|---|---|---|---|
{rows}

## 四、争议焦点

（待 analyze 模式填）

## 五、证据缺口

（待 analyze 模式填）

## 六、当前状态与下一步

- 现状：已建档
- 下一步：补齐案件要素，跑 analyze
- 卡在：

## 七、待复核事项

标 `[review]` 的项，等主办律师拍板。
"""


def main():
    p = argparse.ArgumentParser(description="建立案件工作区骨架")
    p.add_argument("--slug", required=True, help='目录名，如 "260726 甲某 民间借贷"')
    p.add_argument("--name", required=True, help="案件全称")
    p.add_argument("--our-side", required=True, help="我方立场：原告/被告/上诉人/被上诉人等")
    p.add_argument("--served", help="送达日或收案日 YYYY-MM-DD，用于推算期限")
    p.add_argument("--deadlines", default="答辩期限,举证期限",
                   help="逗号分隔，可选：" + "/".join(DEADLINE_PRESETS))
    p.add_argument("--case-no", default="", help="案号，没有就留空，禁编造")
    p.add_argument("--cause", default="", help="案由")
    p.add_argument("--case-type", default="民事")
    p.add_argument("--stage", default="一审")
    p.add_argument("--court", default="")
    p.add_argument("--client", default="")
    p.add_argument("--amount", type=float, default=None)
    p.add_argument("--force", action="store_true", help="目录已存在时覆盖骨架文件")
    args = p.parse_args()

    today = date.today()
    root = (MATTERS_ROOT / args.slug).resolve()

    # slug 直接来自用户输入，含 ../ 时会写到 matters 之外
    if root.parent != MATTERS_ROOT.resolve() or not args.slug.strip():
        print(f"❌ slug 非法：{args.slug!r}")
        print(f"   工作区必须是 {MATTERS_ROOT} 的直接子目录，不得含路径分隔符或 ..")
        return 1

    if root.exists() and not args.force:
        print(f"❌ 工作区已存在：{root}")
        print("   要重建请加 --force（会覆盖 matter.yaml / matter.md，history.md 保留）")
        return 1

    served = None
    if args.served:
        try:
            served = date.fromisoformat(args.served)
        except ValueError:
            print(f"❌ --served 格式应为 YYYY-MM-DD，收到：{args.served}")
            return 1
        if served > today:
            print(f"❌ 送达日 {served} 晚于系统当前日期 {today}，已完成事件不得使用未来日期")
            return 1

    deadlines = []
    if served:
        wanted = [x.strip() for x in args.deadlines.split(",") if x.strip()]
        unknown = [w for w in wanted if w not in DEADLINE_PRESETS]
        if unknown:
            print(f"❌ 未知期限类型：{unknown}，可选：{list(DEADLINE_PRESETS)}")
            return 1
        deadlines = build_deadlines(
            [(w, *DEADLINE_PRESETS[w]) for w in wanted], served, today
        )

    for sub in ("材料", "产物"):
        (root / sub).mkdir(parents=True, exist_ok=True)

    (root / "matter.yaml").write_text(
        yaml_block(root, today, args, deadlines), encoding="utf-8"
    )
    (root / "matter.md").write_text(md_block(args, today, deadlines), encoding="utf-8")

    hist = root / "history.md"
    entry = (f"{today} | 建档 | case-analyzer/setup 创建工作区"
             f"（我方 {args.our_side}）| matter.yaml\n")
    if hist.exists():
        with hist.open("a", encoding="utf-8") as f:
            f.write(entry)
    else:
        hist.write_text(f"# {args.name} 大事记\n\n{entry}", encoding="utf-8")

    print(f"✅ 工作区已建立：{root}")
    print(f"   基准日：{today}（系统时间）")
    if deadlines:
        print("   期限：")
        for x in deadlines:
            print(f"     {x['level']} {x['item']}：{x['start']} 起算 → {x['due']} 届满，"
                  f"剩余 {x['remaining']} 天（{x['status']}）")
    else:
        print("   ⚠️ 未提供 --served，期限段留空待人工补齐")
    print("   下一步：补齐当事人与案件要素，然后跑 analyze 模式")
    return 0


if __name__ == "__main__":
    sys.exit(main())
