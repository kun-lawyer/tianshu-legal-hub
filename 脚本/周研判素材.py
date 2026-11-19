"""案件周研判素材：以本人勾定的在办案件为准，汇总每案的卷宗动态与案件台账数据，标出要关注的事。只读。

输入：在办案件勾选清单.xlsx 里「在办」为「是」的行（本人可随时改）。
输出：周研判/素材-YYYY-MM-DD.md，交给 Agent 写研判。
"""
import datetime as dt
import importlib.util
import json
import os
import re
import sys
from pathlib import Path

from openpyxl import load_workbook

if sys.stdout:
    sys.stdout.reconfigure(encoding='utf-8')
HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
import cfg  # noqa: E402


def load(name, file):
    spec = importlib.util.spec_from_file_location(name, HERE / file)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


jd = load('judge', '在办判断.py')
ldg = load('ledger', 'ledger.py')
BJ = dt.timezone(dt.timedelta(hours=8))
TODAY = dt.datetime.now(BJ).date()
LIST = ROOT / '在办案件勾选清单.xlsx'
INVENTORY = ROOT / '日志' / '案件目录盘点.json'
OUT_DIR = ROOT / '周研判'
SINGLE = cfg.path('目录.单次案件')
ADVISOR = cfg.path('目录.顾问单位')
EXCLUDE = tuple(cfg.get('不盘点目录', []))
WORK_FILE = re.compile(r'起诉状|答辩|代理词|质证|上诉状|再审申请|证据目录|申请书|意见|发问|提纲')


def active_rows():
    ws = load_workbook(LIST, read_only=True)['案件清单']
    head = [c.value for c in next(ws.iter_rows(max_row=1))]
    rows = [dict(zip(head, r)) for r in ws.iter_rows(min_row=2, values_only=True)]
    return [r for r in rows if r['在办'] == '是'], {r['完整路径'] for r in rows}


def new_folders(known):
    """清单里没有、近 30 天新建的案件目录，提醒本人判断要不要纳入。"""
    out = []
    units = ADVISOR.iterdir() if ADVISOR and ADVISOR.is_dir() else []
    roots = ([SINGLE] if SINGLE and SINGLE.is_dir() else []) + \
            [sub for unit in units if unit.is_dir() and not unit.name.startswith(('_',) + EXCLUDE)
             for sub in unit.iterdir() if sub.is_dir() and jd.re.search(r'诉讼|仲裁|执行|案件|被诉', sub.name)]
    for r in roots:
        for d in r.iterdir():
            if d.is_dir() and str(d) not in known and (TODAY - dt.date.fromtimestamp(d.stat().st_ctime)).days <= 30:
                out.append(str(d))
    return out


def our_last_work(folder):
    best = None
    for root, _, names in os.walk(folder):
        for n in names:
            if WORK_FILE.search(n) and not n.lower().endswith(('.md', '.log')):
                t = dt.date.fromtimestamp(os.path.getmtime(os.path.join(root, n)))
                if not best or t > best[1]:
                    best = (n, t)
    return best


def main():
    if not LIST.exists():
        sys.exit(f'缺少 {LIST.name}：先运行 python 脚本/在办判断.py 生成「在办案件初判（新）.xlsx」，'
                 f'复核「在办」一列后另存为 {LIST.name}')
    rows, known = active_rows()
    cases = ldg.get_all('cases')
    schedules = ldg.get_all('schedules')
    out, flags = [], {k: [] for k in ('开庭临近', '临庭未备', '期限', '裁判后', '停滞', '未入台账')}
    scanned, owner = {}, {}
    for r in rows:
        try:
            scanned[r['完整路径']] = jd.classify(r['完整路径'])
        except Exception as e:  # noqa: BLE001
            scanned[r['完整路径']] = e
            continue
        c, s = jd.candidate(r['完整路径'], scanned[r['完整路径']][1], cases)
        if c and c.get('trial_date'):
            # 台账 没录案号时，同一当事人的几个目录分不清：卷宗里有传票写着这场开庭日期的，才是这个案件
            dates = {d['future'].isoformat() for d in scanned[r['完整路径']][0] if d.get('future')}
            s += 50 if c['trial_date'][:10] in dates else 0
        scanned[r['完整路径']] += (c,)
        # 一个 台账 案件只归得分最高的那个目录
        if c and (c['id'] not in owner or s > owner[c['id']][1]):
            owner[c['id']] = (r['完整路径'], s)
    # 手工登的开庭日程常不关联案件：按标题里的当事人字号对到在办目录，一场都不能漏
    loose, orphan = {}, []
    linked = {c['id'] for c in cases}
    for s in schedules:
        if (s.get('schedule_date') or '') < TODAY.isoformat() or s.get('schedule_type') != '开庭' or (s.get('title') or '').startswith('【已取消】'):
            continue
        if s.get('case_id') in linked and s['case_id'] in owner:
            continue
        g = jd.grams(s.get('title') or '')
        ranked = sorted(((len(g & jd.grams(Path(r['完整路径']).name)), r['完整路径']) for r in rows), reverse=True)
        if ranked and ranked[0][0] >= 2 and (len(ranked) < 2 or ranked[1][0] < ranked[0][0]):
            loose.setdefault(ranked[0][1], []).append((s['schedule_date'], f"开庭 {s.get('schedule_time') or ''}（{s.get('title')}）"))
        elif (dt.date.fromisoformat(s['schedule_date']) - TODAY).days <= 14:
            orphan.append(f"{s['schedule_date']} {s.get('schedule_time') or ''} {s.get('title')}（{s.get('location') or '地点未填'}）")

    for r in rows:
        folder = r['完整路径']
        name = Path(folder).name
        unit = r['单位'] or '单次案件'
        if isinstance(scanned[folder], Exception):
            out.append(f'- {unit}｜{name}｜读取出错：{scanned[folder]}')
            continue
        docs, numbers, last_any, case = scanned[folder]
        if case and owner[case['id']][0] != folder:
            case = None
        court = [d for d in docs if d['kind'] != 'filing']
        latest = max(court, key=lambda d: d['date']) if court else None
        work = our_last_work(folder)
        future = []
        if case:
            for k, label in (('trial_date', '开庭'), ('evidence_deadline', '举证期限'), ('appeal_deadline', '上诉期限')):
                v = (case.get(k) or '')[:10]
                if v and v >= TODAY.isoformat():
                    future.append((v, label + (' ' + case.get('trial_time', '') if k == 'trial_date' else '')))
            for s in schedules:
                if s.get('case_id') == case['id'] and (s.get('schedule_date') or '') >= TODAY.isoformat():
                    future.append((s['schedule_date'], f"{s.get('schedule_type')} {s.get('schedule_time') or ''}"))
        future += loose.get(folder, [])
        # 同一场开庭在案件和日程里各登一次时只算一次
        seen, uniq = set(), []
        for d, w in sorted(set(future)):
            key = (d, re.search(r'\d{1,2}:\d{2}', w).group(0) if re.search(r'\d{1,2}:\d{2}', w) else w)
            if '开庭' in w and key in seen:
                continue
            seen.add(key)
            uniq.append((d, w))
        future = uniq
        idle = (TODAY - last_any).days if last_any else 9999
        tag = f'{unit}｜{name}'
        line = [f'- **{tag}**']
        mediating = bool(case) and '调解中' in (case.get('tags') or '')
        line.append(f"台账：{case['status']}，{case.get('case_procedure') or ''}，{case.get('court_case_no') or '无案号'}"
                    + ('，**调解中**' if mediating else '') if case else '台账：未录入')
        if latest:
            line.append(f"最新法院文书：{latest['kind']}「{latest['name']}」{latest['date']}" + ('（生效）' if latest.get('final') else ''))
        if work:
            line.append(f'我方最近材料：「{work[0]}」{work[1]}')
        line.append(f'卷宗 {idle} 天无改动' if idle < 9999 else '卷宗无原始文件')
        if future:
            line.append('将来：' + '；'.join(f'{d} {w}' for d, w in future[:4]))
        out.append('，'.join(line))

        soon = [(d, w) for d, w in future if (dt.date.fromisoformat(d) - TODAY).days <= 14]
        hearing_soon = [(d, w) for d, w in soon if '开庭' in w]
        if hearing_soon:
            flags['开庭临近'].append(f"{tag}：{hearing_soon[0][0]} {hearing_soon[0][1]}")
            if not mediating and (not work or (TODAY - work[1]).days > 30):
                flags['临庭未备'].append(f"{tag}：{hearing_soon[0][0]} 开庭，近 30 天没有新的答辩、代理词、质证或证据材料")
        for d, w in soon:
            if '期限' in w:
                flags['期限'].append(f'{tag}：{d} {w}')
        if latest and latest.get('decision') and (TODAY - latest['date']).days <= 30:
            flags['裁判后'].append(f"{tag}：{latest['date']} 收到{latest['kind']}" + ('（生效）' if latest.get('final') else '，注意上诉期'))
        if idle > 60 and not future:
            flags['停滞'].append(f'{tag}：{idle} 天无改动，无将来开庭和期限')
        if not case:
            flags['未入台账'].append(tag)

    fresh = new_folders(known)
    OUT_DIR.mkdir(exist_ok=True)
    path = OUT_DIR / f'素材-{TODAY.isoformat()}.md'
    titles = {'开庭临近': '14 天内开庭', '临庭未备': '开庭临近但近 30 天无新材料', '期限': '14 天内期限', '裁判后': '近 30 天收到裁判文书',
              '停滞': '停滞 60 天以上且无将来安排', '未入台账': '在办但案件台账里没有'}
    md = [f'# 案件周研判素材 {TODAY.isoformat()}', '', f'在办案件 {len(rows)} 件（以 在办案件勾选清单.xlsx 为准）。', '']
    for k, items in flags.items():
        md += [f'## {titles[k]}（{len(items)}）'] + [f'- {x}' for x in items] + ['']
    md += [f'## 14 天内开庭但对不上在办案件（{len(orphan)}）'] + [f'- {x}' for x in orphan] + ['']
    md += [f'## 清单外近 30 天新建的案件目录（{len(fresh)}）'] + [f'- {x}' for x in fresh] + ['']
    md += ['## 全部在办案件'] + out
    path.write_text('\n'.join(md) + '\n', encoding='utf-8')
    print(f'素材已写入 {path}：在办 {len(rows)} 件，' + '，'.join(f'{titles[k]} {len(v)}' for k, v in flags.items()) +
          f'，新目录 {len(fresh)}')


if __name__ == '__main__':
    main()
