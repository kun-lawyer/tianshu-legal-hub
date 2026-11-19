"""遗留事项：把各文件夹工作记录里「未完成及待确认」的事项收口（本人 2026-11-17 定）。

由 晚报.py 每天调用一次，也可单跑：
- 近 14 天写的工作记录里，事项写了明确日期且紧跟「交、前、截止、到期」（如「11-18 周三交」「11月20日前」）、
  日期未过的，建成待办，截止日取原文，同一事项只建一次（例：11-16 记录里「某项目三方协议 11-18 交」一直没进待办）；
- 其余按文件夹汇总进晚报「遗留未核」：每个文件夹一行，取该文件夹最新一份记录，写记录日期、几项、第一项，最多 15 个。
  近 30 天 97 个文件夹共 470 项，多是文书层面的待核，逐条列会淹没晚报，所以只列到文件夹；
- 本人说某个文件夹的事办完了：python 遗留事项.py done <编号或文件夹路径> [说明]，划掉；
  该文件夹以后写了新记录或改了原记录，会重新出现。

用法：
  python 遗留事项.py              扫描、建待办、打印清单
  python 遗留事项.py list         只打印清单，不建待办
  python 遗留事项.py done <编号|路径> [说明]
"""
import datetime as dt
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

if sys.stdout:
    sys.stdout.reconfigure(encoding='utf-8')
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import cfg  # noqa: E402

ROOT = HERE.parent
STATE = ROOT / '日志' / '遗留事项.json'
ROOTS = [ROOT] + cfg.paths('资料根目录') + cfg.paths('目录.额外扫描目录')
BJ = dt.timezone(dt.timedelta(hours=8))
TODAY = dt.datetime.now(BJ).date()
DAYS = 14
SHOW = 15
HEAD = re.compile(r'^##\s*(未完成|待确认|待办|遗留)')
# 日期后最多隔一个钟点或星期，紧跟交、前、截止、到期；「8.3898 万元交」「13.1(c)签约前」这类不算
DUE = re.compile(r'(?<![\d.])(?:(20\d\d)[-年])?(\d{1,2})(?:[-月](\d{1,2})日?)(?:\s*\d{1,2}[时:：]\d{0,2}分?)?'
                 r'(?:\s*周[一二三四五六日])?\s*(?:交|前|截止|到期|之前|以前)'
                 # 「12-31前征收决定未公告的，……」是条件不是期限
                 r'(?![一-鿿]{0,8}未)')
AHEAD = 21
NONLIT = re.compile('|'.join([re.escape(str(p)) for p in (cfg.path('目录.顾问单位'), cfg.path('目录.单次案件')) if p]
                             + ['非诉', '咨询', '诉讼案件']))


def load():
    try:
        return json.loads(STATE.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {'tasks': {}, 'done': {}, 'last': []}


def save(s):
    STATE.parent.mkdir(exist_ok=True)
    STATE.write_text(json.dumps(s, ensure_ascii=False, indent=1), encoding='utf-8')


def records():
    cut = time.time() - DAYS * 86400
    out = []
    for root in ROOTS:
        for d, dirs, files in os.walk(root):
            dirs[:] = [x for x in dirs if not x.startswith(('.', '_归档'))]
            out += [Path(d) / f for f in files if f.startswith('工作记录') and f.endswith('.md')
                    and os.path.getmtime(os.path.join(d, f)) >= cut]
    return out


def items_of(p):
    out, on = [], False
    for line in p.read_text(encoding='utf-8', errors='ignore').splitlines():
        if line.startswith('## '):
            on = bool(HEAD.match(line))
            continue
        if not on:
            continue
        m = re.match(r'^(?:[-*]|\d+\.)\s+(.*)', line)
        if m:
            out.append(m[1].strip())
        elif line.strip() and out and line.startswith((' ', '\t')):
            out[-1] += ' ' + line.strip()
    return [re.sub(r'\*\*', '', i) for i in out if not i.startswith('~~')]


def due_of(text):
    for m in DUE.finditer(text):
        try:
            d = dt.date(int(m[1] or TODAY.year), int(m[2]), int(m[3]))
        except ValueError:
            continue
        if 0 <= (d - TODAY).days <= AHEAD:
            return d
    return None


def short(folder):
    for r in ROOTS:
        if folder == r:
            return folder.name
        if r in folder.parents:
            return '\\'.join(folder.relative_to(r).parts[-2:])
    return folder.name


def grams(s):
    s = re.sub(r'[^一-鿿]', ' ', s or '')
    return {w[i:i + 2] for w in s.split() for i in range(len(w) - 1)}


def open_tasks():
    r = subprocess.run([sys.executable, str(HERE / 'ledger.py'), 'tasks'], capture_output=True, text=True,
                       encoding='utf-8', errors='replace', timeout=120)
    out = []
    for t in json.loads(r.stdout or '[]'):
        out.append((t.get('due_date') or '', t.get('title', '') + (t.get('description') or '')))
        out += [(x.get('due_date') or '', x.get('title', '') + (x.get('detail') or '')) for x in t.get('subtasks') or [] if not x.get('done')]
    return out


def already(item, due, rec, tasks):
    """同一截止日已有待办、且与事项或文件夹名有 3 个以上二字词相同的，算已有（例：某投标项目 11-18 已在待办里，标题写法不同）。"""
    g = grams(item[:80]) | grams(rec.parent.name) | grams(rec.parent.parent.name)
    return any(d == due.isoformat() and len(g & grams(text)) >= 3 for d, text in tasks)


def add_task(item, due, rec):
    folder = rec.parent
    head = re.split(r'[。；]', item)[0]
    title = (head if folder == ROOT else f'{re.sub(r"^[0-9.]+", "", folder.name)}：{head}')[:48]
    cat = '非诉' if NONLIT.search(str(folder)) else '行政'
    body = {'title': title, 'due_date': due.isoformat(), 'priority': '中',
            'description': f'[遗留事项]\n来源：{rec}\n原文：{item}\n截止日取自工作记录原文，请核。\n类别：{cat}\n卷宗：{folder}'}
    with tempfile.NamedTemporaryFile('w', suffix='.json', delete=False, encoding='utf-8') as f:
        json.dump(body, f, ensure_ascii=False)
    try:
        r = subprocess.run([sys.executable, str(HERE / 'ledger.py'), 'add-task', f.name], capture_output=True, text=True,
                           encoding='utf-8', errors='replace', timeout=120)
    finally:
        os.unlink(f.name)
    return (r.stdout or r.stderr).strip()


def scan(make_tasks=True, show=SHOW):
    s = load()
    recs = records()
    created = []
    if make_tasks:
        tasks = None
        for rec in sorted(recs):
            for it in items_of(rec):
                due = due_of(it)
                key = hashlib.md5(re.sub(r'\s+', '', it).encode()).hexdigest()
                if not due or key in s['tasks']:
                    continue
                tasks = open_tasks() if tasks is None else tasks
                res = '已有同日相近待办，未建' if already(it, due, rec, tasks) else add_task(it, due, rec)
                s['tasks'][key] = {'title': it[:60], 'due': due.isoformat(), 'from': str(rec), 'result': res[:120],
                                   'at': dt.datetime.now(BJ).strftime('%Y-%m-%d %H:%M')}
                created.append(f"{due.month}-{due.day}｜{it[:50]}（{short(rec.parent)}）｜{res[:40]}")
    latest = {}
    for rec in recs:
        if rec.parent not in latest or rec.name > latest[rec.parent].name:
            latest[rec.parent] = rec
    rows = []
    for folder, rec in latest.items():
        its = items_of(rec)
        sig = f'{rec.name}:{int(rec.stat().st_mtime)}'
        if not its or s['done'].get(str(folder), {}).get('sig') == sig:
            continue
        rows.append((rec.name, folder, rec, its))
    rows.sort(key=lambda x: x[0], reverse=True)
    s['last'] = [str(f) for _, f, _, _ in rows]
    save(s)
    lines = []
    for n, (_, folder, rec, its) in enumerate(rows[:show], 1):
        day = (re.search(r'\d{4}-(\d{2}-\d{2})', rec.name) or [None, ''])[1]
        lines.append(f"[{n}] {short(folder)}（{day}）{len(its)} 项｜{its[0][:40]}")
    if len(rows) > show:
        lines.append(f'另有 {len(rows) - show} 个文件夹，运行 python 脚本/遗留事项.py list 看全部')
    return {'folders': len(rows), 'items': sum(len(x[3]) for x in rows), 'lines': lines, 'created': created}


def done(arg, note=''):
    s = load()
    folder = s['last'][int(arg) - 1] if arg.isdigit() and 0 < int(arg) <= len(s['last']) else arg
    recs = sorted(Path(folder).glob('工作记录*.md'))
    if not recs:
        print(f'没找到 {folder} 下的工作记录')
        return
    rec = recs[-1]
    s['done'][folder] = {'sig': f'{rec.name}:{int(rec.stat().st_mtime)}', 'note': note,
                         'at': dt.datetime.now(BJ).strftime('%Y-%m-%d %H:%M')}
    save(s)
    print(f'已划掉：{folder}（{rec.name}）')


if __name__ == '__main__':
    cmd = sys.argv[1] if len(sys.argv) > 1 else ''
    if cmd == 'done' and len(sys.argv) >= 3:
        done(sys.argv[2], ' '.join(sys.argv[3:]))
    elif cmd in ('', 'list'):
        r = scan(make_tasks=cmd == '', show=SHOW if cmd == '' else 10 ** 6)
        print(f"遗留未核：{r['folders']} 个文件夹 {r['items']} 项")
        print('\n'.join(r['lines']))
        if r['created']:
            print('新建待办：\n' + '\n'.join(r['created']))
    else:
        print(__doc__)
