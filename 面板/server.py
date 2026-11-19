"""天枢中台面板：本机网页。读中台状态与案件台账，可回答卡片、管规则、改待办和日程、处理送审件、派活给助理。

只监听 127.0.0.1。中台本人密钥、台账 账号只留在本进程里，不下发给网页。写操作要求自定义请求头并校验 Host。
台账 写入只开放白名单字段，不删除记录（待办只能取消），每次改动记案件日志或任务说明。
"""
import datetime
import importlib.util
import json
import os
import socket
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
# 下面加载的脚本内部 import cfg，先把脚本目录放进搜索路径
sys.path.insert(0, str(ROOT / '脚本'))
import cfg  # noqa: E402

PORT = cfg.get('面板.端口', 3351)
MANIFEST = cfg.path('目录.送审收件清单')
OPEN_ROOTS = cfg.paths('资料根目录') + [ROOT]


def load(name, file):
    spec = importlib.util.spec_from_file_location(name, ROOT / '脚本' / file)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


morning = load('morning', '晨报.py')
# 面板日历展开要看到下月底；只改本进程里的这份，晨报本身仍看 30 天
morning.HORIZON = 62
ldg = load('ledger', 'ledger.py')
mat = load('materials', '微信材料收取.py')
info = load('caseinfo', '案件信息.py')
finder = load('finddir', '找案件目录.py')
CONFIG = cfg.hub_config()
HUB = CONFIG['url'].rstrip('/')
OWNER = CONFIG['owner']
ALLOWED_HOSTS = {f'127.0.0.1:{PORT}', f'localhost:{PORT}'}
TASK_STATUSES = ('待处理', '进行中', '已完成', '已取消')
SCHEDULE_EDIT = {'title', 'schedule_date', 'schedule_time', 'schedule_type', 'location', 'description'}
CASE_DATE_FIELDS = {'trial_date', 'trial_time', 'evidence_deadline', 'appeal_deadline'}


class Fail(Exception):
    pass


def _die(msg):
    raise Fail(msg)


# ledger.py 出错时本来打印后退出进程；在面板里改成抛出，原因带回网页
ldg.die = _die
mat.ldg.die = _die
mat.微信库.LIVE = mat.微信库.LIVE.with_name('wx_live_mat_panel')


def j(obj):
    return json.dumps(obj, ensure_ascii=False).encode('utf-8')


# ===== 日程、待办的常驻缓存 =====
# 读案件台账一次要三五秒。后台线程每分钟读一遍存着，网页来取立即给；面板写过东西后立刻重读，
# 网页带 wait=1 来取时等这次重读完成，保证看到的是改后的结果。
_live = {'agenda': None, 'good': None, 'tasks': None, 'list': [], 'gen': 1, 'done': 0}
_cond = threading.Condition()


def _read_agenda():
    rows, error = morning.agenda()
    if error:
        good = _live['good']
        # 读失败不清空：接着显示上次读到的，并注明是几点的数据
        return j(dict(good, error=f"台账暂时读不到，显示的是 {good['at']} 的数据（{error[-60:]}）") if good else {'error': error, 'items': []})
    today = datetime.datetime.now(BJ).date()
    nxt = (today.replace(day=1) + datetime.timedelta(days=32)).replace(day=1)
    month_end = (nxt + datetime.timedelta(days=32)).replace(day=1)
    rows = [{'left': r[0], 'date': r[1].isoformat(), 'what': r[2], 'name': r[3],
             'refs': r[4] if len(r) > 4 else []} for r in (rows or [])]
    try:
        # 案件卡的案号：开庭不在两周内、只挂了待办或日程的案子，原先拿不到案号，一律显示「案号待补」
        cases = [{'id': c['id'], 'name': c.get('case_name') or '', 'no': c.get('court_case_no') or '',
                  'client': c.get('client_name') or '', 'opp': c.get('opponent_name') or ''}
                 for c in ldg.get_all('cases') if c.get('status') != '已结案']
    except Exception:  # noqa: BLE001
        cases = (_live['good'] or {}).get('cases', [])
    body = {'error': None, 'at': datetime.datetime.now(BJ).strftime('%H:%M'),
            'items': [r for r in rows if r['left'] <= 14],
            'cal': [r for r in rows if r['date'] < month_end.isoformat()], 'cases': cases}
    _live['good'] = body
    return j(body)


def _read_tasks():
    tasks = morning.open_tasks()
    try:
        materials = mat.status(tasks or [])
    except Exception:  # noqa: BLE001
        materials = {}
    if tasks is not None:
        _live['list'] = tasks
    return j({'items': tasks or [], 'error': tasks is None, 'materials': materials})


def _refresher():
    while True:
        with _cond:
            _cond.wait_for(lambda: _live['gen'] > _live['done'], timeout=60)
            gen = _live['gen']
        try:
            agenda, tasks = _read_agenda(), _read_tasks()
        except BaseException as e:  # noqa: BLE001
            agenda = tasks = None
            err = str(e)
        with _cond:
            if agenda is not None:
                _live.update(agenda=agenda, tasks=tasks)
            elif _live['agenda'] is None:
                _live.update(agenda=j({'error': err, 'items': []}), tasks=j({'items': [], 'error': True, 'materials': {}}))
            _live['done'] = gen
            _cond.notify_all()


def kick():
    with _cond:
        _live['gen'] += 1
        _cond.notify_all()


def live(what, wait):
    with _cond:
        gen = _live['gen']
        if _live[what] is None or wait:
            _cond.wait_for(lambda: _live['done'] >= gen and _live[what] is not None, timeout=40)
        return _live[what] or j({'error': '还在读案件台账，稍等', 'items': []})


def inbox_json():
    items = json.loads(MANIFEST.read_text(encoding='utf-8')) if MANIFEST and MANIFEST.exists() else []
    since = (datetime.date.today() - datetime.timedelta(days=3)).isoformat()
    recent = [{k: i.get(k) for k in ('id', 'received', 'name', 'status', 'sender_name', 'unit', 'note', 'target', 'staged')}
              for i in items if str(i.get('received', '')) >= since or i.get('status') == '待归档']
    recent.sort(key=lambda i: i['received'] or '', reverse=True)
    return j({'items': recent, 'pending': sum(1 for i in items if i.get('status') == '待归档')})


def report_json(kind):
    folder = ROOT / {'evening': '晚报', 'weekly': '周研判'}.get(kind, '晨报')
    files = sorted(p for p in folder.glob('*.md') if not p.stem.startswith('素材')) if folder.exists() else []
    return j({'name': files[-1].stem, 'path': str(files[-1]), 'text': files[-1].read_text(encoding='utf-8')} if files else {})


BJ = datetime.timezone(datetime.timedelta(hours=8))


def worklog_json(day):
    """工作日志：某天（北京时间）中台跑过的每一轮，取自中台运行记录；/v1/state 一次给最近 60 轮，用 before 往前翻。"""
    day = datetime.date.fromisoformat(day) if day else datetime.datetime.now(BJ).date()
    start = datetime.datetime.combine(day, datetime.time(), BJ)
    end = start + datetime.timedelta(days=1)
    at = lambda s: datetime.datetime.fromisoformat(s.replace('Z', '+00:00'))  # noqa: E731
    runs, before = {}, None
    for _ in range(30):
        code, body = hub('GET', '/v1/state' + (f'?before={before}' if before else ''))
        if code != 200:
            raise Fail(f'读不到中台运行记录：{body.decode("utf-8", "replace")[:120]}')
        s = json.loads(body)
        batch = s.get('runs') or []
        runs.update((r['id'], r) for r in batch)
        if not s.get('hasMore') or not batch or min(at(r['createdAt']) for r in batch) < start:
            break
        before = s.get('nextBefore')
    items = []
    for r in sorted(runs.values(), key=lambda r: r['id']):
        t = at(r['createdAt'])
        if not start <= t < end:
            continue
        ev = (r.get('events') or [{}])[0]
        items.append({'id': r['id'], 'rule': r['rule'], 'state': r['state'], 'manual': r.get('manual'),
                      'time': t.astimezone(BJ).strftime('%H:%M'),
                      'end': at(r['finishedAt']).astimezone(BJ).strftime('%H:%M') if r.get('finishedAt') else '',
                      'summary': r.get('summary') or '', 'error': r.get('error') or '',
                      'trigger': '' if ev.get('source') == 'schedule' else (ev.get('summary') or '')})
    return j({'day': day.isoformat(), 'items': items, 'today': datetime.datetime.now(BJ).date().isoformat()})


def hub(method, path, body=None, key=None):
    data = None if body is None else json.dumps(body, ensure_ascii=False).encode('utf-8')
    req = urllib.request.Request(HUB + path, data=data, method=method,
                                 headers={'Authorization': f'Bearer {key or OWNER}', 'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except OSError as e:
        return 502, j({'error': f'连不上中台：{e}'})


def state_json():
    """中台状态，另带本人称呼和星钟用的本地经度（网页不写死）。"""
    code, body = hub('GET', '/v1/state')
    try:
        s = json.loads(body)
    except ValueError:
        return code, body
    if isinstance(s, dict):
        s.update(me=cfg.ME, lon=cfg.get('面板.经度', 120))
        body = j(s)
    return code, body


def record(table, rid):
    r = ldg.call('GET', f'/api/tables/{table}/{rid}')
    return r.get('data', r) if isinstance(r, dict) else r


# ===== 写操作 =====

def task_add(b):
    title = (b.get('title') or '').strip()
    if not title:
        raise Fail('事项不能为空')
    data = {'title': title, 'status': '待处理', 'priority': b.get('priority') or '中', 'task_type': '其他',
            'assigned_to': b.get('assigned_to') or ldg.CRED['username'], 'due_date': b.get('due_date') or '',
            'case_name': b.get('case_name') or '', 'description': '[面板新建]' + (f"\n{b['description']}" if b.get('description') else '')}
    if data['due_date'] and not ldg.DATE.match(data['due_date']):
        raise Fail('截止日须为 YYYY-MM-DD')
    ldg.call('POST', '/api/tables/tasks', data)
    return {'ok': True}


def task_update(tid, b):
    t = record('tasks', tid)
    patch = {k: (b[k] or '') for k in ('title', 'due_date', 'assigned_to', 'priority', 'status', 'case_name') if k in b}
    if patch.get('status') and patch['status'] not in TASK_STATUSES:
        raise Fail('状态不对')
    if patch.get('due_date') and not ldg.DATE.match(patch['due_date']):
        raise Fail('截止日须为 YYYY-MM-DD')
    finishing = patch.get('status') == '已完成' and t.get('status') != '已完成'
    if finishing:
        patch['completed_date'] = datetime.datetime.now(ldg.BJ).strftime('%Y-%m-%d %H:%M')
    if b.get('note') or finishing:
        today = datetime.datetime.now(ldg.BJ).date()
        patch['description'] = (t.get('description') or '') + f"\n[面板 {today}] {b.get('note') or '标为已完成'}"
    if patch:
        ldg.call('PATCH', f'/api/tables/tasks/{tid}', patch)
    return {'ok': True}


def schedule_add(b):
    data = {k: (b.get(k) or '') for k in SCHEDULE_EDIT}
    data['schedule_type'] = data['schedule_type'] or '开庭'
    if not data['title'] or not ldg.DATE.match(data['schedule_date']):
        raise Fail('标题和日期（YYYY-MM-DD）必填')
    if ldg.existing_schedule(data):
        raise Fail('同一时间已有这场日程')
    ldg.call('POST', '/api/tables/schedules', {'assigned_to': ldg.CRED['username'], **data})
    return {'ok': True}


def schedule_update(sid, b):
    patch = {k: (b[k] or '') for k in SCHEDULE_EDIT if k in b}
    if 'schedule_date' in patch and not ldg.DATE.match(patch['schedule_date']):
        raise Fail('日期须为 YYYY-MM-DD')
    if patch:
        ldg.call('PATCH', f'/api/tables/schedules/{sid}', patch)
    return {'ok': True}


def case_dates(cid, b):
    patch = {k: (b[k] or '') for k in CASE_DATE_FIELDS if k in b}
    for k, v in patch.items():
        if k != 'trial_time' and v and not ldg.DATE.match(v):
            raise Fail(f'{k} 须为 YYYY-MM-DD')
    case = record('cases', cid)
    changes = '；'.join(f"{k}：{case.get(k) or '空'} → {v or '空'}" for k, v in patch.items() if (case.get(k) or '') != v)
    if changes:
        ldg.call('PATCH', f'/api/tables/cases/{cid}', patch)
        ldg.log(case, f'面板修改：{changes}')
    return {'ok': True, 'changes': changes}


def mediate(cid, b):
    """开庭转调解或取消：改案件 tags 里的「调解中」。日程上挂的可能是 台账编号，先换成案件 id。"""
    if cid.startswith(f"{cfg.get('台账.案件编号前缀', 'AJ')}-"):
        cid = next((c['id'] for c in ldg.get_all('cases') if c.get('case_number') == cid), '') or _die('找不到这个案件')
    on = bool(b.get('on'))
    case, changed = ldg.set_mediating(cid, on, (b.get('note') or '').strip() or '面板操作')
    return {'ok': True, 'changed': changed, 'case': case['case_name']}


MARKS = ROOT / '日志' / '面板轻重.json'


def marks():
    return json.loads(MARKS.read_text(encoding='utf-8')) if MARKS.exists() else {}


def mark_set(b):
    """本人手动定的轻重缓急，按案件或事务的键存；level 为空即交回自动判断。"""
    key, level = (b.get('key') or '').strip(), b.get('level') or ''
    if not key or level not in ('', '急', '常', '缓'):
        raise Fail('参数不对')
    data = marks()
    if level:
        data[key] = level
    else:
        data.pop(key, None)
    tmp = MARKS.with_suffix('.tmp')
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding='utf-8')
    os.replace(tmp, MARKS)
    return {'ok': True}


_info_cache = {}


@mat.session
def _talk(chats):
    return info.talk(mat, chats)


def caseinfo(b):
    """案件卡的补充信息：卷宗目录、绑定微信会话的最近几句、开庭前待核。读微信和卷宗较慢，按案件缓存十分钟。"""
    key = (b.get('key') or '').strip()
    if not key:
        raise Fail('缺案件')
    hit = _info_cache.get(key)
    if hit and time.time() - hit[0] < 600 and not b.get('fresh'):
        return hit[1]
    ids = set(b.get('tasks') or [])
    state, bind = mat.load_state(), None
    for t in _live['list']:
        if t['id'] in ids:
            bind = bind or mat.find_bind(state, t)[1]
    folder = info.folder_of(key, b.get('name') or '', b.get('no') or '', (bind or {}).get('folder') or '')
    out = {'folder': folder, 'talk': [], 'checks': [], 'pack': '', 'talk_error': ''}
    if bind and bind.get('chats'):
        try:
            out['talk'] = _talk(bind['chats'])
        except BaseException as e:  # noqa: BLE001
            out['talk_error'] = str(e)[:80]
    if folder and b.get('hearing'):
        r = info.checks(folder, key, str(b['hearing']))
        out['checks'], out['pack'] = r['items'], r['pack']
    _info_cache[key] = (time.time(), out)
    return out


def check_set(b):
    if not b.get('key'):
        raise Fail('缺事项')
    info.check_set(b['key'], bool(b.get('done')))
    for _, v in _info_cache.values():
        for c in v['checks']:
            if c['key'] == b['key']:
                c['done'] = bool(b.get('done'))
    return {'ok': True}


def name_set(b):
    """本人改案件显示名称；title 为空则交回自动整理。"""
    key, title = (b.get('key') or '').strip(), (b.get('title') or '').strip()
    if not key:
        raise Fail('缺案件')
    names = info.read_json(info.NAMES)
    if title:
        names[key] = dict(names.get(key, {}), title=title, manual=True)
    else:
        names.pop(key, None)
    info.write_json(info.NAMES, names)
    return {'ok': True}


def inbox_status(iid, b):
    status = b.get('status')
    if status not in ('已人工处理', '已忽略', '待归档'):
        raise Fail('状态不对')
    if not MANIFEST or not MANIFEST.exists():
        raise Fail('未配置送审收件清单（config.json 的 目录.送审收件清单）')
    items = json.loads(MANIFEST.read_text(encoding='utf-8'))
    hit = next((i for i in items if str(i.get('id')) == str(iid)), None)
    if not hit:
        raise Fail('没有这一件')
    hit['status'] = status
    hit['note'] = b.get('note') or {'已人工处理': '面板标记已处理', '已忽略': '面板标记不是送审件'}.get(status, '')
    tmp = MANIFEST.with_suffix('.tmp')
    tmp.write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding='utf-8')
    os.replace(tmp, MANIFEST)
    return {'ok': True}


SESSION = ROOT / '日志' / '飞书会话.json'


def chat_subject(text=''):
    """随身助理的会话对象：距上一句不到两小时接着同一个会话，超过就另起（省掉每句话都带全部历史的开销）。
    开头说「新话题」立即另起，说「接着」则不管隔多久都接上一个。"""
    now = time.time()
    try:
        st = json.loads(SESSION.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        st = {}
    head = text.lstrip()[:4]
    if not st.get('subject') or head.startswith('新话题') or (now - st.get('last', 0) > 7200 and not head.startswith('接着')):
        st['subject'] = f'feishu-chat-{int(now)}'
    st['last'] = now
    SESSION.parent.mkdir(exist_ok=True)
    SESSION.write_text(json.dumps(st), encoding='utf-8')
    return st['subject']


def assist(b):
    text = (b.get('text') or '').strip()
    if not text:
        raise Fail('要办的事不能为空')
    event = {'id': f'panel-{int(time.time() * 1000)}', 'type': 'assistant.task', 'subject': chat_subject(text),
             'summary': text.splitlines()[0][:200], 'detail': text[:4000]}
    code, body = hub('POST', '/v1/events', event, key=CONFIG['source']['key'])
    if code != 200:
        raise Fail(body.decode('utf-8', 'replace')[:200])
    return {'ok': True}


def prep(b):
    """面板「出作战包」：直接派给开庭作战包规则（只存卷宗，不发飞书），不走飞书助理。"""
    if not ldg.DATE.match(b.get('date') or '') or not (b.get('hearing') or '').strip():
        raise Fail('缺开庭日期或案件名')
    detail = {k: (b.get(k) or '').strip() for k in ('date', 'time', 'hearing')}
    # 与定时派单共用一条规则，手册凭这个标记不查重
    detail['by'] = '面板'
    if ldg.MEDIATING_MARK in detail['hearing']:
        detail['mode'] = '调解'
    event = {'id': f'panel-prep-{int(time.time() * 1000)}', 'type': 'hearing.prep', 'subject': f"hearing-{detail['date']}",
             'summary': f"{detail['date'][5:]} {detail['time']} {detail['hearing']}"[:200],
             'detail': json.dumps(detail, ensure_ascii=False)}
    code, body = hub('POST', '/v1/events', event, key=CONFIG['source']['key'])
    if code != 200:
        raise Fail(body.decode('utf-8', 'replace')[:200])
    return {'ok': True}


DRAFT_KEYS = ('from', 'title', 'case_name', 'detail', 'task_id', 'sub_no', 'date', 'time', 'kind', 'location', 'case_id',
              'want', 'folder')


def draft(b):
    """面板「出稿」：派给面板出稿规则，稿直接出在卷宗文件夹，拿不准的出卡片问本人。"""
    if not (b.get('title') or '').strip():
        raise Fail('缺事项')
    detail = {k: b[k] for k in DRAFT_KEYS if b.get(k) not in (None, '')}
    detail['by'] = '面板'
    if detail.get('folder') and not Path(detail['folder']).is_dir():
        raise Fail('指定的文件夹不存在')
    what = detail.get('want') or detail['title']
    event = {'id': f'panel-draft-{int(time.time() * 1000)}', 'type': 'draft.request',
             'subject': f"draft-{detail.get('task_id') or detail.get('case_id') or detail.get('date') or 'misc'}",
             'summary': f"出稿：{what}"[:200], 'detail': json.dumps(detail, ensure_ascii=False)[:4000]}
    code, body = hub('POST', '/v1/events', event, key=CONFIG['source']['key'])
    if code != 200:
        raise Fail(body.decode('utf-8', 'replace')[:200])
    if detail.get('task_id'):
        task_update(detail['task_id'], {'note': f'已派出稿：{what[:60]}'})
    return {'ok': True}


def open_path(b):
    p = Path(b.get('path') or '')
    if not p.exists():
        raise Fail('文件或文件夹不存在')
    if not any(root in p.resolve().parents or p.resolve() == root for root in OPEN_ROOTS):
        raise Fail('只能打开资料根目录和天枢中台里的文件')
    os.startfile(str(p))
    return {'ok': True}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def send(self, code, body, ctype='application/json; charset=utf-8'):
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(body)

    def guard(self):
        # 挡 DNS 重绑定；写操作另要自定义请求头，别的网站跨域发不了带这个头的请求
        if self.headers.get('Host') not in ALLOWED_HOSTS:
            self.send(403, b'{"error":"host"}')
            return False
        return True

    def do_GET(self):
        if not self.guard():
            return
        url = urllib.parse.urlparse(self.path)
        try:
            if url.path == '/':
                self.send(200, (HERE / 'index.html').read_bytes(), 'text/html; charset=utf-8')
            elif url.path == '/api/state':
                self.send(*state_json())
            elif url.path == '/api/report':
                self.send(200, report_json(urllib.parse.parse_qs(url.query).get('kind', ['morning'])[0]))
            elif url.path == '/api/worklog':
                self.send(200, worklog_json(urllib.parse.parse_qs(url.query).get('day', [''])[0]))
            elif url.path == '/api/agenda':
                self.send(200, live('agenda', 'wait=1' in url.query))
            elif url.path == '/api/names':
                self.send(200, j(info.read_json(info.NAMES)))
            elif url.path == '/api/marks':
                self.send(200, j(marks()))
            elif url.path == '/api/inbox':
                self.send(200, inbox_json())
            elif url.path == '/api/tasks':
                self.send(200, live('tasks', 'wait=1' in url.query))
            elif url.path == '/api/tasks/done':
                self.send(200, j({'items': ldg.recent_done(7)}))
            else:
                self.send(404, b'{"error":"not found"}')
        except BaseException as e:  # noqa: BLE001
            self.send(500, j({'error': str(e) or type(e).__name__}))

    def do_POST(self):
        if not self.guard():
            return
        if self.headers.get('X-Panel') != '1':
            self.send(403, b'{"error":"header"}')
            return
        length = int(self.headers.get('Content-Length') or 0)
        try:
            body = json.loads(self.rfile.read(length) or b'{}')
        except ValueError:
            self.send(400, b'{"error":"json"}')
            return
        p = urllib.parse.urlparse(self.path).path.strip('/').split('/')
        try:
            if len(p) == 4 and p[:2] == ['api', 'cards'] and p[3] in ('answer', 'resolve') and p[2].isdigit():
                self.send(*hub('POST', f'/v1/cards/{p[2]}/{p[3]}', body))
            elif len(p) == 4 and p[:2] == ['api', 'rules'] and p[3] in ('run', 'pause', 'resume'):
                name = urllib.parse.quote(urllib.parse.unquote(p[2]), safe='')
                self.send(*hub('POST', f'/v1/rules/{name}/{p[3]}', body))
            elif len(p) == 4 and p[:2] == ['api', 'runs'] and p[3] == 'cancel' and p[2].isdigit():
                self.send(*hub('POST', f'/v1/runs/{p[2]}/cancel', {}))
            elif p == ['api', 'tasks', 'add']:
                self.send(200, j(task_add(body)))
            elif len(p) == 4 and p[:2] == ['api', 'tasks'] and p[3] == 'update':
                self.send(200, j(task_update(p[2], body)))
            elif len(p) == 4 and p[:2] == ['api', 'tasks'] and p[3] == 'sub-done':
                ldg.sub_done(p[2], int(body.get('no') or 0), '面板勾选')
                self.send(200, j({'ok': True}))
            elif len(p) == 4 and p[:2] == ['api', 'materials'] and p[3] in ('scan', 'collect'):
                try:
                    res = mat.scan(p[2], body) if p[3] == 'scan' else mat.collect_manual(p[2], body)
                except ValueError as e:
                    raise Fail(str(e))
                self.send(200, j(res))
            elif p == ['api', 'schedules', 'add']:
                self.send(200, j(schedule_add(body)))
            elif len(p) == 4 and p[:2] == ['api', 'schedules'] and p[3] == 'update':
                self.send(200, j(schedule_update(p[2], body)))
            elif len(p) == 4 and p[:2] == ['api', 'cases'] and p[3] == 'dates':
                self.send(200, j(case_dates(p[2], body)))
            elif len(p) == 4 and p[:2] == ['api', 'cases'] and p[3] == 'mediate':
                self.send(200, j(mediate(p[2], body)))
            elif len(p) == 4 and p[:2] == ['api', 'inbox'] and p[3] == 'status':
                self.send(200, j(inbox_status(p[2], body)))
            elif p == ['api', 'assist']:
                self.send(200, j(assist(body)))
            elif p == ['api', 'caseinfo']:
                self.send(200, j(caseinfo(body)))
            elif p == ['api', 'check']:
                self.send(200, j(check_set(body)))
            elif p == ['api', 'names']:
                self.send(200, j(name_set(body)))
            elif p == ['api', 'marks']:
                self.send(200, j(mark_set(body)))
            elif p == ['api', 'draft']:
                self.send(200, j(draft(body)))
            elif p == ['api', 'prep']:
                self.send(200, j(prep(body)))
            elif p == ['api', 'open']:
                self.send(200, j(open_path(body)))
            elif p == ['api', 'folder']:
                # 非诉、行政事务的「打开文件夹」：说明里写了卷宗的直接用，否则按客户、联系人、事项词找
                self.send(200, j(finder.loose(body.get('title') or '', body.get('desc') or '',
                                              body.get('client') or '', body.get('cat') or '非诉')))
            else:
                self.send(404, b'{"error":"not found"}')
            if p[1:2] not in (['caseinfo'], ['check'], ['open'], ['folder'], ['marks'], ['names'], ['materials']) or p[-1:] == ['collect']:
                kick()
        except Fail as e:
            self.send(400, j({'error': str(e)}))
        except BaseException as e:  # noqa: BLE001
            # ledger.py 出错时会 sys.exit，这里接住，把原因交给网页
            self.send(500, j({'error': str(e) or type(e).__name__}))


def main():
    url = f'http://127.0.0.1:{PORT}/'
    with socket.socket() as s:
        busy = s.connect_ex(('127.0.0.1', PORT)) == 0
    if busy:
        webbrowser.open(url)
        return
    threading.Thread(target=_refresher, daemon=True).start()
    server = ThreadingHTTPServer(('127.0.0.1', PORT), Handler)
    if '--no-browser' not in sys.argv:
        webbrowser.open(url)
    server.serve_forever()


if __name__ == '__main__':
    main()
