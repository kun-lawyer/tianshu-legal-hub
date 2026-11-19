"""晨报、晚报的飞书详细版：按手机上看排版，开庭带案号、法院、作战包和待核情况，待办带分支，另列中台办了什么。

由 晨报.py、晚报.py 调用：morning_md(rows, tasks) / evening_md(rows, tasks, state)，send(md) 发到本人飞书私聊。
只读台账、卷宗和中台状态，不改任何东西。
"""
import datetime as dt
import importlib.util
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import cfg  # noqa: E402

BJ = dt.timezone(dt.timedelta(hours=8))
TODAY = dt.datetime.now(BJ).date()
WEEK = '一二三四五六日'
MANIFEST = cfg.path('目录.送审收件清单')
QUIET = {'传票入库', '送审收件读取', '微信材料收取', '改稿登记', '每日晨报', '每日晚报', '作战包派单', '庭后派单', '期限派单',
         '知识库刷新', '案号与名称整理'}


def load(name, file):
    spec = importlib.util.spec_from_file_location(name, HERE / file)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def day_label(d):
    left = (d - TODAY).days
    rel = {0: '今天', 1: '明天', 2: '后天'}.get(left, f'{left} 天后' if left > 0 else f'已过 {-left} 天')
    return f'{d.month}月{d.day}日 周{WEEK[d.weekday()]}（{rel}）'


def clean(name):
    return re.sub(r'^\d{1,2}[:：]\d{2}\s*[-－]?\s*', '', name)


def prep_note(info, row):
    """开庭的准备情况：作战包或调解准备单出了没有，待核还剩几项。"""
    _, d, what, name, refs = row
    cid = next((r.get('case_id') for r in refs if r.get('case_id')), '')
    if not cid:
        return '台账未关联案件，未查卷宗'
    no = next((r.get('location') for r in refs if r.get('kind') == 'case' and re.search(r'\d{4}.*号', r.get('location') or '')), '')
    try:
        folder = info.folder_of(cid, clean(refs[0].get('title') or name), no)
        if not folder:
            return '没找到卷宗'
        pack = info.pack_for(folder, d.isoformat())
        if not pack:
            return '**还没有这场开庭的作战包**'
        left = [k for k in info.checks(folder, cid, d.isoformat())['items'] if not k['done']]
        return f"已有《{pack.stem}》" + (f"，待核 {len(left)} 项未勾" if left else '，待核都已勾')
    except Exception as e:  # noqa: BLE001
        return f'卷宗读取出错：{str(e)[:40]}'


def agenda_lines(rows, lo, hi, info=None):
    out, last = [], None
    for row in rows:
        left, d, what, name, *_ = row
        if not lo <= left <= hi:
            continue
        if d != last:
            out.append(f'\n**{day_label(d)}**')
            last = d
        out.append(f'- {what}｜{clean(name)}')
        if info and what.startswith('开庭') and len(row) > 4:
            out.append(f'  准备：{prep_note(info, row)}')
    return out


def task_lines(tasks, horizon=3):
    """逾期、今天和 horizon 天内到期的逐条列（案件总任务列到分支），其余只计数。"""
    out, rest = [], 0
    for t in tasks or []:
        subs = [s for s in (t.get('subtasks') or []) if not s['done']]
        items = [(s.get('due_date') or '', s['title'], s.get('assigned_to') or '', s.get('priority') or '') for s in subs] if subs \
            else [(t.get('due_date') or '', t['title'], t.get('assigned_to') or '', t.get('priority') or '')]
        head = t.get('case_key') or (t.get('case_name') if subs else '')
        for due, title, who, pri in items:
            left = (dt.date.fromisoformat(due) - TODAY).days if due else None
            if left is None or left > horizon:
                rest += 1
                continue
            tag = f'已逾期 {-left} 天' if left < 0 else '今天到期' if left == 0 else f'{due[5:]} 到期'
            out.append(f"- {'🔴' if left <= 0 else '🟠'} {tag}｜{'【' + head + '】' if head else ''}{title}"
                       f"{'｜' + who if who and who != cfg.ME else ''}{'｜急' if pri == '高' else ''}")
    return out, rest


def inbox():
    return json.loads(MANIFEST.read_text(encoding='utf-8')) if MANIFEST and MANIFEST.exists() else []


def runs_since(state, since):
    at = lambda s: dt.datetime.fromisoformat(s.replace('Z', '+00:00')) if s else None  # noqa: E731
    return [r for r in state.get('runs', []) if r.get('createdAt') and at(r['createdAt']) >= since]


ZERO = re.compile(r'(?:收入|写入|补登|归档|新增)\s*0\s*(?:件|场|份|条)|没事要处理|没事可做')
SELF = {'每日晨报', '每日晚报', '送审收件读取'}


def summary_of(r):
    """脚本输出的第一行常以规则名开头（「微信材料收取：…」），列表里已写规则名，去掉重复的。"""
    s = (r.get('summary') or '').strip()
    while s.startswith(r['rule'] + '：'):
        s = s[len(r['rule']) + 1:].strip()
    return s


def useful(r):
    """办成了事的轮次：状态为 done，且不是晨晚报和送审读取本身，摘要也不是「收入 0 件」这类空结果（2026-11-17 定）。"""
    return r.get('state') == 'done' and r['rule'] not in SELF and not ZERO.search(r.get('summary') or '')


def run_lines(runs):
    out = []
    for r in sorted(runs, key=lambda r: r['id']):
        if useful(r) and r['rule'] not in QUIET:
            when = dt.datetime.fromisoformat(r['createdAt'].replace('Z', '+00:00')).astimezone(BJ).strftime('%H:%M')
            out.append(f"- {when} {r['rule']}：{summary_of(r)[:160]}")
        elif r.get('error') and r.get('state') != 'cancelled':
            out.append(f"- ⚠️ {r['rule']} 第 {r['id']} 轮没跑成：{r['error'][:100]}")
    return out


def morning_md(rows, tasks, state=None, error=''):
    info = load('caseinfo', '案件信息.py')
    md = [f'# 晨报 {TODAY.month}月{TODAY.day}日 周{WEEK[TODAY.weekday()]}']
    if error:
        md.append(f'⚠️ {error}')
    hearings = [r for r in rows if 0 <= r[0] <= 7 and r[2].startswith('开庭')]
    deadlines = [r for r in rows if 0 <= r[0] <= 7 and '期限' in r[2]]
    md.append(f"七天内 {len(hearings)} 场开庭、{len(deadlines)} 个期限")
    md += ['\n## 今明两天'] + (agenda_lines(rows, 0, 1, info) or ['无安排。'])
    md += ['\n## 后天至七天内'] + (agenda_lines(rows, 2, 7, info) or ['无安排。'])
    lines, rest = task_lines(tasks)
    md += ['\n## 待办（逾期及三天内到期）'] + (lines or ['无。'])
    if rest:
        md.append(f'另有 {rest} 项期限在三天以后或未定，见面板。')
    if state:
        cards = [c for c in state.get('cards', []) if c.get('state') == 'open']
        md += [f'\n## 等你决定（{len(cards)}）'] + ([f"- {c['title']}" for c in cards] or ['无。'])
        since = dt.datetime.combine(TODAY - dt.timedelta(days=1), dt.time(18, 5), BJ)
        done = run_lines(runs_since(state, since))
        if done:
            md += ['\n## 昨晚以来中台办的事'] + done
    pending = [i for i in inbox() if i.get('status') == '待归档']
    if pending:
        md += [f'\n## 送审待归档（{len(pending)}）'] + [f"- {i.get('received', '')[5:16]}｜{i.get('sender_name') or '发件人未识别'}｜{i['name']}" for i in pending]
    later = [r for r in rows if 8 <= r[0] <= 30 and (r[2].startswith('开庭') or '期限' in r[2])]
    if later:
        md += ['\n## 八至三十天的开庭与期限'] + [f"- {d.month}-{d.day} 周{WEEK[d.weekday()]}｜{w}｜{clean(n)}" for _, d, w, n, *_ in later]
    return '\n'.join(md)


def evening_md(rows, tasks, state=None, error=''):
    info = load('caseinfo', '案件信息.py')
    ldg = load('ledger', 'ledger.py')
    md = [f'# 晚报 {TODAY.month}月{TODAY.day}日 周{WEEK[TODAY.weekday()]}']
    if error:
        md.append(f'⚠️ {error}')
    if state:
        start = dt.datetime.combine(TODAY, dt.time(), BJ)
        runs = runs_since(state, start)
        done = run_lines(runs)
        md += ['\n## 中台今天办的事'] + (done or ['今天没有实际办成的事。'])
        cards = [c for c in state.get('cards', []) if c.get('state') == 'open']
        md += [f'\n## 等你决定（{len(cards)}）'] + ([f"- {c['title']}" for c in cards] or ['无。'])
    try:
        closed = [x for x in ldg.recent_done(1) if x['date'][:10] == TODAY.isoformat()]
    except BaseException:  # noqa: BLE001
        closed = []
    new = [t for t in tasks or [] if (t.get('created_at') or '')[:10] == TODAY.isoformat()]
    if new or closed:
        md.append('\n## 今天的待办变化')
        md += [f"- 新增：{'【' + t['case_key'] + '】' if t.get('case_key') else ''}{t['title'] if not t.get('subtasks') else '、'.join(s['title'] for s in t['subtasks'] if not s['done'])}" for t in new]
        md += [f"- 办完：{'【' + x['case_key'] + '】' if x['case_key'] else ''}{x['title']}（{'中台关' if x['by'] == '中台' else '你点的'}）" for x in closed]
    today_in = [i for i in inbox() if str(i.get('received', '')).startswith(TODAY.isoformat())]
    if today_in:
        md += [f'\n## 今日送审（{len(today_in)}）'] + [f"- {i['received'][11:16]}｜{i.get('sender_name') or '发件人未识别'}｜{i['name']}｜{i.get('status')}" for i in today_in]
    md += ['\n## 明后两天'] + (agenda_lines(rows, 1, 2, info) or ['无安排。'])
    lines, _ = task_lines(tasks, horizon=1)
    md += ['\n## 明天前要办完的待办'] + (lines or ['无。'])
    week = [r for r in rows if 3 <= r[0] <= 7 and (r[2].startswith('开庭') or '期限' in r[2])]
    if week:
        md += ['\n## 本周后几天的开庭与期限'] + [f"- {d.month}-{d.day} 周{WEEK[d.weekday()]}｜{w}｜{clean(n)}" for _, d, w, n, *_ in week]
    return '\n'.join(md)


def hub_state():
    import urllib.request
    hub = cfg.hub_config()
    req = urllib.request.Request(hub['url'].rstrip('/') + '/v1/state', headers={'Authorization': f"Bearer {hub['owner']}"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read())
    except (OSError, ValueError):
        return None


def send(md):
    """发到本人飞书私聊；发不出去不影响晨报、晚报本身，返回出错说明。"""
    if not cfg.enabled('飞书'):
        return '飞书模块未启用（config.json 的 飞书.启用）'
    reply = load('feishu_reply', '飞书回复.py')
    import subprocess
    out = subprocess.run(reply.LARK + ['im', '+messages-send', '--user-id', reply.OWNER, '--as', 'bot', '--markdown', md],
                         capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=180)
    try:
        ok = json.loads(out.stdout).get('ok', True) and out.returncode == 0
    except ValueError:
        ok = False
    return '' if ok else (out.stdout + out.stderr)[-200:]


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    m = load('morning', '晨报.py')
    rows, err = m.agenda()
    print(evening_md(rows or [], m.open_tasks(), None, err or '') if 'evening' in sys.argv else morning_md(rows or [], m.open_tasks(), None, err or ''))
