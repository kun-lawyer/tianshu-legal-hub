"""每日晨报：汇总期限、开庭、送审待归档、知识库待处理，写入 晨报/YYYY-MM-DD.md，并给中台交一张仅告知的通知。

纯脚本，不调模型。由 agent-autoloop 的 command Agent 定时运行，最后一段输出即回执。
送审一栏只在启用顾问单位模块且配置了 目录.送审收件清单 时出；知识库一栏只在配置了 目录.知识库待处理 时出。
"""
import datetime as dt
import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path

import cfg

if sys.stdout:  # pythonw 下没有标准输出
    sys.stdout.reconfigure(encoding='utf-8')

# 本机系统时区可能与执业地不同，剩余天数一律按配置的时区（默认北京时间）算
BJ = dt.timezone(dt.timedelta(hours=cfg.TZ_HOURS))
TODAY = dt.datetime.now(BJ).date()
WEEK = '一二三四五六日'

JWXT = Path(__file__).resolve().parent / 'ledger.py'
INBOX_MANIFEST = cfg.path('目录.送审收件清单') if cfg.enabled('顾问单位') else None
KB_TODO = cfg.path('目录.知识库待处理')
OUT_DIR = Path(__file__).resolve().parent.parent / '晨报'
HORIZON = 30


def agenda():
    """从案件台账取开庭、期限与日程。同一场开庭常在案件和日程里各登一次，同日同时的开庭只留一条。"""
    run = subprocess.run([sys.executable, str(JWXT), 'agenda', '--days', str(HORIZON)], capture_output=True,
                         text=True, encoding='utf-8', errors='replace', timeout=120)
    if run.returncode != 0:
        return None, f'案件台账读取失败：{run.stderr.strip()[-200:]}'
    items = json.loads(run.stdout)
    # 案件上的开庭排在同日程前面，日程才能并进去
    items.sort(key=lambda x: (x[0], x[1], x[2] != '开庭' or not RE_CASE_NO.search(x[4] or '')))
    rows, hearings = [], []
    for day, time, label, title, extra, ref, *more in items:
        d = dt.date.fromisoformat(day)
        # 第 5 项：这一行背后的 台账 记录（面板编辑用），合并的开庭会有多条
        rec = {'kind': more[0], 'id': more[1], 'field': more[2], 'date': day, 'time': time, 'title': title,
               'location': extra, 'case_id': ref,
               'desc': more[3] if len(more) > 3 else ''} if more else None
        row = [(d - TODAY).days, d, f"{label}{(' ' + time) if time else ''}", title + (f'（{extra}）' if extra else ''),
               [rec] if rec else []]
        if label == '开庭':
            same = next((h for h in hearings if h['key'] == (day, time) and
                         (ref and ref == h['ref'] or grams(title) & grams(h['title']))), None)
            if same:
                if extra and extra not in same['row'][3]:
                    same['row'][3] += f'；{extra}'
                if rec:
                    same['row'][4].append(rec)
                continue
            hearings.append({'key': (day, time), 'ref': ref, 'title': title, 'row': row})
        rows.append(row)
    return sorted(rows, key=lambda r: (r[0], r[1], r[2], r[3])), None


RE_CASE_NO = re.compile(r'\d{4}.*号')
STOP = re.compile(r'公司|纠纷|案件|合同|建设|工程|开庭|诉讼')


def grams(s):
    cjk = re.sub(r'[^一-鿿]', '', STOP.sub('', s or ''))
    return {cjk[i:i + 3] for i in range(len(cjk) - 2)}


def open_tasks():
    """案件台账里未完成的任务，按截止日排；没有截止日的排最后。"""
    run = subprocess.run([sys.executable, str(JWXT), 'tasks'], capture_output=True, text=True, encoding='utf-8',
                         errors='replace', timeout=120)
    if run.returncode != 0:
        return None
    tasks = json.loads(run.stdout)
    return sorted(tasks, key=lambda t: (t.get('due_date') or '9999', t.get('created_at') or ''))


def mark(left):
    if left < 0:
        return f'🔴 已逾期 {-left} 天'
    if left <= 3:
        return f'🔴 剩 {left} 天'
    if left <= 7:
        return f'🟠 剩 {left} 天'
    return f'🟢 剩 {left} 天'


def agenda_mark(left, what):
    """日程行的标注（2026-11-17 定）：红橙绿只给开庭和期限；开过的庭写「已开庭，待记结果」而不是「已逾期」；
    聚餐、会议这类普通日程不上色。"""
    if what.startswith('开庭'):
        return f'⚪ 已开庭 {-left} 天，待记结果' if left < 0 else mark(left)
    if '期限' in what:
        return f'🔴 期限已过 {-left} 天' if left < 0 else mark(left)
    return '⚪ 今天' if left == 0 else f'⚪ 剩 {left} 天'


def inbox_pending():
    if not INBOX_MANIFEST.exists():
        return None
    items = json.loads(INBOX_MANIFEST.read_text(encoding='utf-8'))
    return [i for i in items if i.get('status') == '待归档']


def kb_todo_lines():
    if not KB_TODO or not KB_TODO.exists():
        return []
    lines = [l.strip() for l in KB_TODO.read_text(encoding='utf-8').splitlines()]
    return [l for l in lines if l.startswith(('- ', '* ')) or re.match(r'^\d+\.', l)]


def main():
    rows, error = agenda()
    rows = rows or []
    pending = inbox_pending() if INBOX_MANIFEST else None
    todo = kb_todo_lines()

    md = [f'# 晨报 {TODAY.isoformat()}（周{WEEK[TODAY.weekday()]}）', '']
    md.append(f'## 开庭、期限与日程（{HORIZON} 天内，来自案件台账）')
    if error:
        md.append(f'⚠️ {error}')
    elif not rows:
        md.append(f'{HORIZON} 天内无开庭、期限和日程。')
    for left, day, what, name, *_ in rows:
        md.append(f'- {agenda_mark(left, what)}｜{day.isoformat()}（周{WEEK[day.weekday()]}）｜{what}｜{name}')
    # 先把各文件夹工作记录里有明确期限的未完成事项转成待办，当天到期的早上就能看到（2026-11-17 定）
    try:
        yl = importlib.util.spec_from_file_location('leftover', JWXT.parent / '遗留事项.py')
        ylm = importlib.util.module_from_spec(yl)
        yl.loader.exec_module(ylm)
        created = ylm.scan()['created']
    except Exception as e:  # noqa: BLE001
        created = [f'⚠️ 遗留事项没扫成：{str(e)[:120]}']
    if created:
        md += ['', '## 从工作记录转成的待办'] + [f'- {x}' for x in created]
    tasks = open_tasks()
    md += ['', '## 待办']
    if tasks is None:
        md.append('⚠️ 待办读取失败。')
    elif not tasks:
        md.append('没有未完成的待办。')
    for t in tasks or []:
        due = t.get('due_date') or ''
        left = (dt.date.fromisoformat(due) - TODAY).days if due else None
        flag = mark(left) if left is not None else '⚪ 无截止日'
        who = t.get('assigned_to') or ''
        md.append(f"- {flag}｜{due or '未定'}｜{t['title']}{('｜' + who) if who and who != cfg.ME else ''}")
        for s in t.get('subtasks') or []:
            if not s['done']:
                sd = s.get('due_date') or ''
                sw = s.get('assigned_to') or ''
                md.append(f"  - {mark((dt.date.fromisoformat(sd) - TODAY).days) if sd else '⚪'}｜{sd or '未定'}｜"
                          f"{s['title']}{('｜' + sw) if sw and sw != cfg.ME else ''}")
    if INBOX_MANIFEST:
        md += ['', '## 顾问单位送审']
        if pending is None:
            md.append('收件清单不存在，送审收件可能没在跑。')
        elif not pending:
            md.append('没有待归档的送审文件。')
        else:
            md.append(f'{len(pending)} 件待归档：')
            md += [f"- {i.get('received', '')} {i.get('name', '')}" for i in pending]
    if KB_TODO:
        md += ['', '## 知识库待处理']
        md += todo[:15] or ['无。']
        if len(todo) > 15:
            md.append(f'（另有 {len(todo) - 15} 项，见 {KB_TODO}）')

    OUT_DIR.mkdir(exist_ok=True)
    out = OUT_DIR / f'{TODAY.isoformat()}.md'
    out.write_text('\n'.join(md) + '\n', encoding='utf-8')


    # 飞书发详细版（按手机排版）；卡片只留摘要，规则里已设只投桌面通知，不在飞书重复
    try:
        fs = importlib.util.spec_from_file_location('feishu_brief', Path(__file__).resolve().parent / '飞书简报.py')
        fsm = importlib.util.module_from_spec(fs)
        fs.loader.exec_module(fsm)
        sent = fsm.send(fsm.morning_md(rows or [], tasks, fsm.hub_state(), error or ''))
    except Exception as e:  # noqa: BLE001
        sent = str(e)[:200]
    urgent = [r for r in rows if r[0] <= 7]
    head = f'7 天内 {len(urgent)} 项' if urgent else '7 天内无到期事项'
    lines = [f'{agenda_mark(l, w)}｜{d.isoformat()}（周{WEEK[d.weekday()]}）｜{w}｜{n}' for l, d, w, n, *_ in urgent[:12]]
    due = [t for t in tasks or [] if t.get('due_date') and t['due_date'] <= TODAY.isoformat()]
    if due:
        lines.append(f'待办今天到期或已逾期 {len(due)} 件：' + '；'.join(t['title'] for t in due[:5]))
    elif tasks:
        lines.append(f'未完成待办 {len(tasks)} 件')
    if pending:
        lines.append(f'送审待归档 {len(pending)} 件')
    if error:
        lines.append(f'⚠️ {error}')
    lines.append(f'全文：{out}')
    if cfg.enabled('飞书'):
        lines.append('飞书已发详细版' if not sent else f'⚠️ 飞书详细版没发出：{sent}')
    receipt = {'notice': [{'title': f'晨报 {TODAY.isoformat()}：{head}', 'text': '\n'.join(lines),
                           'key': f'brief-{TODAY.isoformat()}', 'fyi': True}]}
    print(f'晨报已写入 {out}，{head}')
    print()
    print('```agent-autoloop')
    print(json.dumps(receipt, ensure_ascii=False))
    print('```')


if __name__ == '__main__':
    main()
