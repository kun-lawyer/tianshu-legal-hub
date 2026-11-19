"""案件台账（案件管理后端）读写工具，天枢中台各规则统一经它访问。

只有查询、新增、更新，没有删除（取消日程只加标记）。改为已结案或填结案日期必须加 --confirmed（由本人在卡片上确认后才可加）。
每次写入同时记一条案件日志，操作人写「账号名（天枢中台）」，网站上看得出是自动写的。
账号在 凭据目录/credentials.json（凭据目录由配置 台账.凭据目录 指定，默认 ~/.tianshu-ledger），令牌缓存在同目录 token.json。

用法：
  python ledger.py find <关键词或案号>                      按案号、案名、当事人、法院找案件
  python ledger.py show <id>                                 一个案件的全部字段及其日程
  python ledger.py active                                    在办案件（状态不是已结案）
  python ledger.py agenda [--days 30]                        今天起 N 天内的开庭、期限与日程
  python ledger.py update <id> 字段=值 [字段=值 ...] [--confirmed]
  python ledger.py mediate <id> on|off [说明]                标为或取消「调解中」（开庭改出调解准备单）
  python ledger.py add-case <json 文件>                      新建案件（自动编号 前缀-年-序号）
  python ledger.py add-schedule <json 文件>                  新建日程；同一场已有则跳过
  python ledger.py cancel-schedule <日程id> <依据>           取消日程（标题加【已取消】，不删记录）；id 见 agenda
  python ledger.py edit-schedule <日程id> <json 文件>        改期、改时段、补地点，JSON 须带 note 说明依据
  python ledger.py tasks [--all]                             未完成任务（JSON）
  python ledger.py add-task <json 文件>                      新建任务；有相近的未完成任务则跳过。
                                                           JSON 带 case_key（案子简称）的，挂到该案总任务下作为分支
  python ledger.py edit-task <id> <json 文件>                改写待办（判断有误时用），JSON 须带 note 说明依据
  python ledger.py done-task <id>[#分支序号] [说明]          把任务（或总任务的某个分支）标为已完成
  python ledger.py group-task <id> <案子简称>                把一条单独的待办并到案件总任务下
"""
import datetime as dt
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import cfg

if sys.stdout:  # 面板用 pythonw 加载本模块时没有标准输出
    sys.stdout.reconfigure(encoding='utf-8')
HOME = cfg.path('台账.凭据目录', '~/.tianshu-ledger') or Path.home() / '.tianshu-ledger'
_CRED_FILE = HOME / 'credentials.json'
if not _CRED_FILE.exists():
    raise SystemExit(f'缺少案件管理后端凭据 {_CRED_FILE}：请新建该文件，写入 JSON 三个字段 '
                     '{"base": "后端地址，如 https://...", "username": "账号", "password": "密码"}')
CRED = json.loads(_CRED_FILE.read_text(encoding='utf-8'))
BASE = CRED['base'].rstrip('/')
BJ = dt.timezone(dt.timedelta(hours=cfg.TZ_HOURS))
TODAY = dt.datetime.now(BJ).date()
OPERATOR = CRED['username'] + cfg.get('台账.操作人后缀', '（天枢中台）')
STATUSES = ['待接案', '已立案', '庭审阶段', '执行阶段', '已结案']
CASE_FIELDS = {'case_name', 'case_type', 'case_procedure', 'status', 'priority', 'client_name', 'opponent_name',
               'court_name', 'court_case_no', 'lead_lawyer', 'assist_lawyers', 'case_amount', 'filing_date',
               'trial_date', 'trial_time', 'evidence_deadline', 'appeal_deadline', 'close_date', 'description',
               'notes', 'tags', 'cause'}
SCHEDULE_FIELDS = {'title', 'schedule_date', 'schedule_time', 'end_time', 'schedule_type', 'location', 'case_id',
                   'case_name', 'assigned_to', 'assist_to', 'description'}
DATE = re.compile(r'^\d{4}-\d{2}-\d{2}$')


def die(msg):
    print(msg, file=sys.stderr)
    sys.exit(1)


def token(fresh=False):
    cache = HOME / 'token.json'
    if not fresh and cache.exists():
        t = json.loads(cache.read_text(encoding='utf-8'))
        if t.get('expires', 0) > time.time() + 300:
            return t['token']
    body = json.dumps({'username': CRED['username'], 'password': CRED['password']}).encode('utf-8')
    req = urllib.request.Request(BASE + '/api/auth/login', data=body, method='POST',
                                 headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=30) as r:
        res = json.loads(r.read())
    cache.write_text(json.dumps({'token': res['token'], 'expires': time.time() + res.get('expiresIn', 86400)}),
                     encoding='utf-8')
    return res['token']


def call(method, path, body=None, retry=True):
    data = None if body is None else json.dumps(body, ensure_ascii=False).encode('utf-8')
    req = urllib.request.Request(BASE + path, data=data, method=method,
                                 headers={'Authorization': f'Bearer {token()}', 'Content-Type': 'application/json'})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                raw = r.read()
                return json.loads(raw) if raw else None
        except urllib.error.HTTPError as e:
            if e.code == 401 and retry:
                token(fresh=True)
                return call(method, path, body, retry=False)
            if e.code >= 500 and method == 'GET' and attempt < 2:
                time.sleep(1 + attempt * 2)
                continue
            die(f'台账 {method} {path} 失败 {e.code}：{e.read().decode("utf-8", "replace")[:300]}')
        except (urllib.error.URLError, OSError) as e:
            # 网站偶发断连（SSL EOF、连接重置）：读操作重试两次；写操作可能已生效，不重试免得重复写
            if method == 'GET' and attempt < 2:
                time.sleep(1 + attempt * 2)
                continue
            die(f'台账 {method} {path} 连不上：{e}')


def get_all(table):
    rows, offset = [], 0
    while True:
        res = call('GET', f'/api/tables/{table}?limit=1000&offset={offset}')
        rows += res.get('data') or []
        offset += 1000
        if offset >= (res.get('total') or 0):
            return [r for r in rows if not r.get('deleted_at')]


def norm_no(s):
    return re.sub(r'[\s（）()〔〕\[\]【】]', '', s or '')


def brief(c):
    return (f"{c['id']} | {c.get('court_case_no') or '-'} | {c['case_name']} | {c.get('status')} | "
            f"{c.get('case_procedure') or ''} | 开庭 {c.get('trial_date') or '-'} {c.get('trial_time') or ''}")


def log(case, content):
    call('POST', '/api/tables/case_logs', {'case_id': case.get('case_number') or case['id'], 'case_name': case['case_name'],
                                           'action': '其他', 'operator': OPERATOR, 'content': content,
                                           'log_date': TODAY.isoformat()})


def cmd_find(args):
    if not args:
        die('用法：find <关键词或案号>')
    q = ' '.join(args)
    nq = norm_no(q)
    hits = [c for c in get_all('cases')
            if (nq and nq in norm_no(c.get('court_case_no')))
            or any(q in (c.get(k) or '') for k in ('case_name', 'opponent_name', 'client_name', 'court_name', 'case_number'))]
    for c in hits:
        print(brief(c))
    if not hits:
        print('没找到')


def cmd_show(args):
    c = call('GET', f'/api/tables/cases/{args[0]}')
    c = c.get('data', c) if isinstance(c, dict) else c
    print(json.dumps(c, ensure_ascii=False, indent=1))
    for s in get_all('schedules'):
        if s.get('case_id') in (c['id'], c.get('case_number')):
            print('日程：', s['schedule_date'], s.get('schedule_time') or '', s['schedule_type'], s['title'])


def cmd_active(_):
    for c in get_all('cases'):
        if c.get('status') != '已结案':
            print(brief(c))


MEDIATING = '调解中'
MEDIATING_MARK = '【调解中】'


def tag_list(case):
    return [t for t in re.split(r'[，,、\s]+', case.get('tags') or '') if t]


def set_mediating(cid, on, note=''):
    """在案件 tags 里加或去「调解中」。晨报、面板、作战包、周研判都认这个标记。"""
    case = call('GET', f'/api/tables/cases/{cid}')
    case = case.get('data', case) if isinstance(case, dict) else case
    tags = tag_list(case)
    if (MEDIATING in tags) == on:
        return case, False
    tags = tags + [MEDIATING] if on else [t for t in tags if t != MEDIATING]
    call('PATCH', f'/api/tables/cases/{cid}', {'tags': '，'.join(tags)})
    log(case, ('标为调解中' if on else '取消调解中，恢复按开庭准备') + (f'：{note}' if note else ''))
    return case, True


def cmd_mediate(args):
    if len(args) < 2 or args[1] not in ('on', 'off'):
        die('用法：mediate <id> on|off [说明]')
    case, changed = set_mediating(args[0], args[1] == 'on', ' '.join(args[2:]))
    print(('已' if changed else '本来就') + ('标为调解中' if args[1] == 'on' else '不是调解中'), case['case_name'])


def cmd_agenda(args):
    days = int(args[args.index('--days') + 1]) if '--days' in args else 30
    end = TODAY + dt.timedelta(days=days)
    items = []
    cases = get_all('cases')
    # 调解中的案件，开庭标题带标记，晨报、面板、作战包派单都据此区分
    mediating = {k for c in cases if MEDIATING in tag_list(c) for k in (c['id'], c.get('case_number')) if k}
    for c in cases:
        if c.get('status') == '已结案':
            continue
        for key, label in (('trial_date', '开庭'), ('evidence_deadline', '举证期限'), ('appeal_deadline', '上诉期限')):
            v = c.get(key) or ''
            if DATE.match(v[:10]) and TODAY - dt.timedelta(days=3) <= dt.date.fromisoformat(v[:10]) <= end:
                mark = MEDIATING_MARK if key == 'trial_date' and c['id'] in mediating else ''
                items.append((v[:10], c.get('trial_time') if key == 'trial_date' else '', label, c['case_name'] + mark,
                              c.get('court_case_no') or '', c['id'], 'case', c['id'], key))
    for s in get_all('schedules'):
        v = s.get('schedule_date') or ''
        # 本人在面板取消或办完的日程，标题带【已取消】【已完成】，不再出现在晨报、面板和作战包派单里
        # （庭后派单只跳过【已取消】：标完成的庭照样要问结果）
        if (s.get('title') or '').startswith(('【已取消】', '【已完成】')):
            continue
        if DATE.match(v[:10]) and TODAY <= dt.date.fromisoformat(v[:10]) <= end:
            mark = MEDIATING_MARK if s.get('schedule_type') == '开庭' and s.get('case_id') in mediating else ''
            items.append((v[:10], s.get('schedule_time') or '', s.get('schedule_type') or '日程', s['title'] + mark,
                          s.get('location') or '', s.get('case_id') or '', 'schedule', s['id'], '',
                          (s.get('description') or '')[:2000]))
    print(json.dumps(sorted(items), ensure_ascii=False, indent=1))


def cmd_update(args):
    if len(args) < 2:
        die('用法：update <id> 字段=值 ...')
    cid, confirmed = args[0], '--confirmed' in args
    patch = {}
    for a in args[1:]:
        if a == '--confirmed':
            continue
        k, _, v = a.partition('=')
        if k not in CASE_FIELDS:
            die(f'不允许改的字段：{k}')
        if k.endswith(('_date', '_deadline')) and v and not DATE.match(v):
            die(f'{k} 须为 YYYY-MM-DD')
        if k == 'status' and v not in STATUSES:
            die(f'status 只能是 {STATUSES}')
        patch[k] = v
    if (patch.get('status') == '已结案' or patch.get('close_date')) and not confirmed:
        die('改为已结案或填结案日期须本人确认：先交 ask 卡片，本人确认后再加 --confirmed')
    case = call('GET', f'/api/tables/cases/{cid}')
    case = case.get('data', case) if isinstance(case, dict) else case
    before = {k: case.get(k) for k in patch}
    if all((before[k] or '') == v for k, v in patch.items()):
        print('无变化，未写入')
        return
    call('PATCH', f'/api/tables/cases/{cid}', patch)
    changes = '；'.join(f"{k}：{before[k] or '空'} → {v or '空'}" for k, v in patch.items() if (before[k] or '') != v)
    log(case, f'天枢中台更新：{changes}')
    print('已更新', changes)


def cmd_add_case(args):
    created = add_case(json.loads(Path(args[0]).read_text(encoding='utf-8')))
    print('已新建', created['case_number'], created.get('id', ''))


def add_case(data):
    """新建案件，返回含 id、case_number 的记录。"""
    bad = set(data) - CASE_FIELDS
    if bad:
        die(f'不认识的字段：{bad}')
    if not data.get('case_name'):
        die('case_name 必填')
    if data.get('status', '待接案') not in STATUSES or data.get('status') == '已结案':
        die('新建案件状态只能是待接案、已立案、庭审阶段、执行阶段')
    cases = get_all('cases')
    if data.get('court_case_no') and any(norm_no(c.get('court_case_no')) == norm_no(data['court_case_no']) for c in cases):
        die(f"案号 {data['court_case_no']} 已存在，不重复新建；用 find 查到后 update")
    prefix = f"{cfg.get('台账.案件编号前缀', 'AJ')}-{TODAY.year}-"
    used = {c.get('case_number') for c in cases}
    n = max([int(x[len(prefix):]) for x in used if x and x.startswith(prefix) and x[len(prefix):].isdigit()] or [0]) + 1
    while f'{prefix}{n:03d}' in used:
        n += 1
    data = {'case_type': '民商事案件', 'priority': '中', 'status': '待接案', 'lead_lawyer': CRED['username'],
            'case_amount': 0, **data, 'case_number': f'{prefix}{n:03d}'}
    for c in get_all('clients'):
        names = [c['name']] + [a for a in re.split(r'[,，、\s]+', c.get('aliases') or '') if a]
        if data.get('client_name') in names:
            data.update(client_id=c['id'], client_type=c.get('client_type') or '', client_contact=c.get('phone') or '')
            break
    created = call('POST', '/api/tables/cases', data)
    created = created.get('data', created) if isinstance(created, dict) else created
    created = {**data, **(created or {})}
    log(created, f"天枢中台新建案件「{data['case_name']}」")
    return created


STOP = re.compile(r'公司|纠纷|案件|合同|建设|工程|施工|开庭|诉讼|有限|责任|' + cfg.place_pattern() + r'|vs|VS|与|诉|及|\d+')


def grams(s):
    cjk = re.sub(r'[^一-鿿]', '', STOP.sub('', s or ''))
    return {cjk[i:i + 2] for i in range(len(cjk) - 1)}


def existing_schedule(data):
    """同一场开庭常被手工登过：同日同时同类型，且关联同一案件或标题有当事人字号重合，算同一日程。"""
    t = (data.get('schedule_time') or '').lstrip('0')
    for s in get_all('schedules'):
        if s.get('schedule_date') != data['schedule_date'] or s.get('schedule_type') != data['schedule_type'] \
                or (s.get('schedule_time') or '').lstrip('0') != t:
            continue
        if data.get('case_id') and s.get('case_id') == data['case_id']:
            return s
        if grams(s.get('title')) & grams(data.get('title')):
            return s
    return None


def cmd_add_schedule(args):
    data = json.loads(Path(args[0]).read_text(encoding='utf-8'))
    bad = set(data) - SCHEDULE_FIELDS
    if bad:
        die(f'不认识的字段：{bad}')
    if not (data.get('title') and DATE.match(data.get('schedule_date', ''))):
        die('title 与 schedule_date（YYYY-MM-DD）必填')
    data = {'schedule_type': '其他', 'assigned_to': CRED['username'], **data}
    s = existing_schedule(data)
    if s:
        print('已有同一日程，未重复新建：', s['id'], s['title'])
        return
    created = call('POST', '/api/tables/schedules', data)
    created = created.get('data', created) if isinstance(created, dict) else created
    print('已新建日程', (created or {}).get('id', ''), data['schedule_date'], data.get('schedule_time') or '', data['title'])


def get_schedule(sid):
    s = call('GET', f'/api/tables/schedules/{sid}')
    return s.get('data', s) if isinstance(s, dict) else s


def cmd_cancel_schedule(args):
    """与面板「取消这条」同一做法：标题前加【已取消】，不删记录，说明末尾记取消依据。"""
    if len(args) < 2:
        die('用法：cancel-schedule <日程id> <依据>')
    s = get_schedule(args[0])
    if (s.get('title') or '').startswith('【已取消】'):
        print('本来就已取消', s['title'])
        return
    note = ' '.join(args[1:])
    call('PATCH', f"/api/tables/schedules/{s['id']}", {
        'title': '【已取消】' + s['title'],
        'description': (s.get('description') or '') + f'\n[天枢中台 {TODAY}] 取消：{note}'})
    print('已取消日程', s['schedule_date'], s['title'])


def cmd_edit_schedule(args):
    """改期、改时段、补地点。JSON 写要改的日程字段，另写 note 说明依据。"""
    if len(args) != 2:
        die('用法：edit-schedule <日程id> <json 文件>')
    data = json.loads(Path(args[1]).read_text(encoding='utf-8'))
    note = (data.pop('note', '') or '').strip()
    bad = set(data) - {'title', 'schedule_date', 'schedule_time', 'end_time', 'location', 'description'}
    if bad:
        die(f'不允许改的字段：{bad}')
    if not note:
        die('note 必填：写明改了什么、依据什么')
    if 'schedule_date' in data and not DATE.match(data['schedule_date']):
        die('schedule_date 须为 YYYY-MM-DD')
    s = get_schedule(args[0])
    changes = '；'.join(f"{k}：{s.get(k) or '空'} → {v or '空'}" for k, v in data.items() if k != 'description')
    data['description'] = (data.get('description') or s.get('description') or '') + \
        f'\n[天枢中台 {TODAY}] 改动：{changes or "说明"}。{note}'
    call('PATCH', f"/api/tables/schedules/{s['id']}", data)
    print('已改日程', s['title'], changes)


TASK_FIELDS = {'title', 'case_id', 'case_name', 'client_name', 'description', 'assigned_to', 'status', 'priority',
               'task_type', 'due_date'}


# 一个案子只建一条「案件待办」总任务，标题【案子简称】开头，各分支写在描述里：
#   ☐ 截止日｜优先级｜承办人｜事项      未办
#   ☑ 截止日｜优先级｜承办人｜事项      已办
#   　（全角空格开头）该分支的来源、要点、文件路径
# 台账任务表没有父子字段，所以分支只能放在描述里；总任务的截止日取未办分支最早的，优先级取最高的。
GROUP = re.compile(r'^【(.+?)】')
SUB = re.compile(r'^([☐☑]) (\S*)｜(\S*)｜([^｜]*)｜(.*)$')
OPEN = ('待处理', '进行中')
PRI_ORDER = {'高': 0, '中': 1, '低': 2}


def parse_group(desc):
    head, subs, tail = [], [], []
    for line in (desc or '').split('\n'):
        m = SUB.match(line)
        if m:
            subs.append({'done': m[1] == '☑', 'due_date': m[2], 'priority': m[3], 'assigned_to': m[4], 'title': m[5],
                         'detail': []})
        elif line.startswith('　') and subs and not tail:
            subs[-1]['detail'].append(line[1:])
        elif subs:
            tail.append(line)
        else:
            head.append(line)
    return head, subs, tail


def render_group(head, subs, tail):
    lines = list(head)
    for s in subs:
        lines.append(f"{'☑' if s['done'] else '☐'} {s['due_date']}｜{s['priority']}｜{s['assigned_to']}｜{s['title']}")
        lines += ['　' + d for d in s['detail']]
    return '\n'.join(lines + tail)


def group_key(t):
    m = GROUP.match(t.get('title') or '')
    return m[1] if m and (t.get('description') or '').startswith('[案件待办]') else None


def same_key(a, b):
    ga, gb = grams(a), grams(b)
    return a == b or (ga and gb and len(ga & gb) >= max(2, 0.6 * min(len(ga), len(gb))))


def save_group(t, head, subs, tail):
    """按分支重算总任务的标题、截止日、优先级、状态后写回。"""
    key = group_key(t)
    left = [s for s in subs if not s['done']]
    patch = {'description': render_group(head, subs, tail), 'title': f'【{key}】全部办完'}
    if left:
        left.sort(key=lambda s: s['due_date'] or '9999')
        patch['due_date'] = left[0]['due_date'] or t.get('due_date') or ''
        patch['priority'] = min((s['priority'] for s in left), key=lambda p: PRI_ORDER.get(p, 1))
        patch['title'] = f'【{key}】{len(left)} 项未办：{left[0]["title"]}'
        if t.get('status') not in OPEN:
            patch['status'] = '待处理'
    else:
        patch['status'] = '已完成'
        patch['completed_date'] = dt.datetime.now(BJ).strftime('%Y-%m-%d %H:%M')
    call('PATCH', f"/api/tables/tasks/{t['id']}", patch)
    return patch


def add_sub(data, key):
    """把一件事挂到该案子的总任务下；没有总任务就新建。"""
    sub = {'done': False, 'due_date': data.get('due_date') or '', 'priority': data.get('priority') or '中',
           'assigned_to': data.get('assigned_to') or CRED['username'], 'title': data['title'],
           'detail': [l for l in (data.get('description') or '').split('\n') if l.strip()]}
    tasks = get_all('tasks')
    parent = next((t for t in tasks if t.get('status') in OPEN and group_key(t) and same_key(group_key(t), key)), None)
    if not parent:
        created = call('POST', '/api/tables/tasks', {
            'title': f'【{key}】', 'description': f'[案件待办] {key}', 'status': '待处理', 'task_type': '其他',
            'assigned_to': CRED['username'], 'priority': sub['priority'], 'due_date': sub['due_date'],
            **{k: data[k] for k in ('case_id', 'case_name', 'client_name') if data.get(k)}})
        parent = created.get('data', created) if isinstance(created, dict) else created
        parent = {**parent, 'title': f'【{key}】', 'description': f'[案件待办] {key}'}
    head, subs, tail = parse_group(parent.get('description'))
    g = grams(sub['title'])
    for s in subs:
        if not s['done'] and len(g & grams(s['title'])) >= max(3, len(g) // 2):
            print('该案已有相近的未办分支，未重复挂入：', parent['id'], s['title'])
            return
    subs.append(sub)
    save_group(parent, head, subs, tail)
    print(f"已挂到【{group_key(parent)}】下（任务 {parent['id']}，第 {len(subs)} 项）：{sub['title']}")


def sub_done(tid, no, note=''):
    """把总任务的第 no 项分支标为已办；全部办完总任务自动完成。"""
    t = call('GET', f'/api/tables/tasks/{tid}')
    t = t.get('data', t) if isinstance(t, dict) else t
    head, subs, tail = parse_group(t.get('description'))
    if not 1 <= no <= len(subs):
        die(f'没有第 {no} 项分支（共 {len(subs)} 项）')
    s = subs[no - 1]
    if s['done']:
        print('本来就是已办')
        return
    s['done'] = True
    s['detail'].append(f'[{TODAY} 办完] {note}' if note else f'[{TODAY} 办完]')
    patch = save_group(t, head, subs, tail)
    print('已办', s['title'], '；总任务' + ('全部办完' if patch.get('status') == '已完成' else '仍有未办'))


CLOSE_MARK = re.compile(r'^\[(天枢中台|面板) (\d{4}-\d{2}-\d{2})\] ?(.*)$')
SUB_MARK = re.compile(r'^\[(\d{4}-\d{2}-\d{2}) 办完\] ?(.*)$')


def recent_done(days=7):
    """近 days 天办完的事：单条待办与案件总任务里勾掉的分支，各带关闭方与依据。总任务本身不重复列。"""
    since = (TODAY - dt.timedelta(days=days)).isoformat()
    out = []
    for t in get_all('tasks'):
        key = group_key(t)
        if key:
            for s in parse_group(t.get('description'))[1]:
                m = next((SUB_MARK.match(d) for d in reversed(s['detail']) if SUB_MARK.match(d)), None)
                if s['done'] and m and m[1] >= since:
                    out.append({'date': m[1], 'title': s['title'], 'case_key': key, 'assigned_to': s['assigned_to'],
                                'by': '面板' if m[2] == '面板勾选' else '中台', 'note': '' if m[2] == '面板勾选' else m[2],
                                'task_id': t['id']})
        elif t.get('status') == '已完成' and (t.get('completed_date') or '')[:10] >= since:
            m = next((CLOSE_MARK.match(l) for l in reversed((t.get('description') or '').split('\n')) if CLOSE_MARK.match(l)), None)
            out.append({'date': t.get('completed_date') or '', 'title': t['title'], 'case_key': '',
                        'assigned_to': t.get('assigned_to') or '',
                        'by': {'天枢中台': '中台', '面板': '面板'}.get(m[1]) if m else '未记',
                        'note': '' if not m or m[3] == '标为已完成' else m[3],
                        'task_id': t['id']})
    return sorted(out, key=lambda x: x['date'], reverse=True)


def cmd_tasks(args):
    """未完成的任务，JSON 输出。加 --all 含已完成和已取消。案件总任务另给 subtasks（no 为 done-task 用的序号）。"""
    rows = [t for t in get_all('tasks') if '--all' in args or t.get('status') in OPEN]
    out = []
    for t in rows:
        item = {k: t.get(k) for k in ('id', 'title', 'case_name', 'assigned_to', 'status', 'priority', 'due_date',
                                       'description', 'created_at')}
        if group_key(t):
            item['case_key'] = group_key(t)
            item['subtasks'] = [{'no': i, **s, 'detail': '\n'.join(s['detail'])}
                                for i, s in enumerate(parse_group(t.get('description'))[1], 1)]
        out.append(item)
    print(json.dumps(out, ensure_ascii=False, indent=1))


def cmd_add_task(args):
    data = json.loads(Path(args[0]).read_text(encoding='utf-8'))
    key = (data.pop('case_key', '') or '').strip()
    bad = set(data) - TASK_FIELDS
    if bad:
        die(f'不认识的字段：{bad}')
    if not data.get('title'):
        die('title 必填')
    if data.get('due_date') and not DATE.match(data['due_date']):
        die('due_date 须为 YYYY-MM-DD')
    if key:
        add_sub(data, key)
        return
    data = {'status': '待处理', 'priority': '中', 'task_type': '其他', 'assigned_to': CRED['username'], **data}
    if data['status'] not in ('待处理', '进行中'):
        die('新建任务状态只能是待处理或进行中')
    g = grams(data['title'])
    for t in get_all('tasks'):
        if t.get('status') in ('待处理', '进行中') and len(g & grams(t.get('title'))) >= max(3, len(g) // 2):
            print('已有相近的未完成任务，未重复新建：', t['id'], t['title'])
            return
    created = call('POST', '/api/tables/tasks', data)
    created = created.get('data', created) if isinstance(created, dict) else created
    print('已新建任务', (created or {}).get('id', ''), data['title'])


def cmd_group_task(args):
    """把一条单独的待办并到案件总任务下，原任务改为已取消并注明去向。"""
    if len(args) < 2:
        die('用法：group-task <任务id> <案子简称>')
    t = call('GET', f'/api/tables/tasks/{args[0]}')
    t = t.get('data', t) if isinstance(t, dict) else t
    if group_key(t):
        die('这本身就是案件总任务')
    if t.get('status') not in OPEN:
        die(f"这条待办已是「{t.get('status')}」，不用并")
    key = ' '.join(args[1:])
    add_sub({k: t.get(k) for k in ('title', 'due_date', 'priority', 'assigned_to', 'description', 'case_id',
                                   'case_name', 'client_name')}, key)
    call('PATCH', f"/api/tables/tasks/{t['id']}", {
        'status': '已取消', 'description': (t.get('description') or '') + f'\n[天枢中台 {TODAY}] 已并入【{key}】案件待办'})


def cmd_edit_task(args):
    """改写一条待办（判断有误时用）。JSON 可写 title、description、due_date、priority、case_name、client_name，
    另写 note 说明改了什么、依据什么，记进说明末尾。案件总任务的标题由分支重算，不在这里改。"""
    if len(args) != 2:
        die('用法：edit-task <任务id> <json 文件>')
    data = json.loads(Path(args[1]).read_text(encoding='utf-8'))
    note = (data.pop('note', '') or '').strip()
    bad = set(data) - {'title', 'description', 'due_date', 'priority', 'case_name', 'client_name'}
    if bad:
        die(f'不允许改的字段：{bad}')
    if not note:
        die('note 必填：写明改了什么、依据什么')
    if data.get('due_date') and not DATE.match(data['due_date']):
        die('due_date 须为 YYYY-MM-DD')
    t = call('GET', f'/api/tables/tasks/{args[0]}')
    t = t.get('data', t) if isinstance(t, dict) else t
    if group_key(t) and 'title' in data:
        die('案件总任务的标题由分支重算，不能直接改')
    data['description'] = (data.get('description') or t.get('description') or '') + f'\n[天枢中台 {TODAY}] 改写：{note}'
    call('PATCH', f"/api/tables/tasks/{t['id']}", data)
    print('已改写', t.get('title'), '→', data.get('title') or t.get('title'))


def cmd_done_task(args):
    if not args:
        die('用法：done-task <id>[#分支序号] [说明]')
    note = ' '.join(args[1:])
    if '#' in args[0]:
        tid, _, no = args[0].partition('#')
        sub_done(tid, int(no), note)
        return
    t = call('GET', f'/api/tables/tasks/{args[0]}')
    t = t.get('data', t) if isinstance(t, dict) else t
    if t.get('status') == '已完成':
        print('本来就是已完成')
        return
    desc = (t.get('description') or '') + f'\n[天枢中台 {TODAY}] {note}'.rstrip()
    call('PATCH', f'/api/tables/tasks/{args[0]}', {'status': '已完成', 'description': desc,
                                                  'completed_date': dt.datetime.now(BJ).strftime('%Y-%m-%d %H:%M')})
    print('已完成', t.get('title'))


COMMANDS = {'tasks': cmd_tasks, 'add-task': cmd_add_task, 'edit-task': cmd_edit_task, 'done-task': cmd_done_task,
            'group-task': cmd_group_task, 'find': cmd_find, 'show': cmd_show, 'active': cmd_active, 'agenda': cmd_agenda, 'update': cmd_update,
            'add-case': cmd_add_case, 'add-schedule': cmd_add_schedule, 'cancel-schedule': cmd_cancel_schedule,
            'edit-schedule': cmd_edit_schedule, 'mediate': cmd_mediate}
if __name__ == '__main__':
    # 不带参数或 --help 是查用法，正常退出；中台跑的模型常这样看用法，原先退出码 1 被记成工具报错
    if len(sys.argv) < 2 or sys.argv[1] in ('-h', '--help', 'help'):
        print(__doc__)
        sys.exit(0)
    if sys.argv[1] not in COMMANDS:
        print(__doc__)
        sys.exit(1)
    COMMANDS[sys.argv[1]](sys.argv[2:])
