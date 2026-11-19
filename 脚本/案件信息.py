"""案件信息：给面板的案件卡找卷宗、读案号、取最近沟通和开庭前待核事项；并定时给台账里缺案号的在办案件补案号。

  python 案件信息.py fill      给近期有开庭、期限而台账没有案号的案件补案号（卷宗文书和传票里读得出唯一案号才写，写入记案件日志）
  python 案件信息.py names     把近期有节点的案件名称整理成统一格式，存 日志/案件名称.json（fill 之后也会跑）
  python 案件信息.py no <目录> 只看某个卷宗里读出的案号

案号来源依次为：卷宗里的文件名和文书正文（docx、有文字层的 pdf、md、txt），传票扫描件（按开庭日期和时间对上文件名后本机 OCR）。
只读卷宗；写台账只写 court_case_no 一项。
"""
import datetime as dt
import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections import defaultdict
from pathlib import Path

if sys.stdout:
    sys.stdout.reconfigure(encoding='utf-8')
HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
import cfg  # noqa: E402

SUMMONS = cfg.path('目录.传票扫描')
FOLDERS = ROOT / '日志' / '案件卷宗.json'
CHECKED = ROOT / '日志' / '面板待核.json'
CASE_NO = re.compile(r'[（(〔\[]\s*(20\d{2})\s*[）)〕\]]\s*([一-鿿]{1,2}\d{0,4})\s*([一-鿿]{1,5})\s*(\d{1,6})\s*号')
STOP = re.compile(r'有限责任公司|股份有限公司|有限公司|公司|集团|纠纷|一案|案件|合同|建设工程|施工|房屋买卖|确认|效力|开庭|仲裁')


def load(name, file=None):
    spec = importlib.util.spec_from_file_location(name, HERE / (file or name + '.py'))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


finder = load('找案件目录')


def read_json(p):
    try:
        return json.loads(p.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}


def write_json(p, data):
    p.parent.mkdir(exist_ok=True)
    tmp = p.with_suffix('.tmp')
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding='utf-8')
    os.replace(tmp, p)


def words_of(name):
    """案件名拆成当事人字号：「甲方置业诉乙方投资房屋买卖合同纠纷案」→ 甲方置业、乙方投资。"""
    parts = re.split(r'诉|与|和|、|vs|VS|Vs|[-\u2014－]|（|\(', re.sub(r'^\d{1,2}[:：]\d{2}[-－]?', '', name or ''))
    out = []
    for p in parts:
        p = STOP.sub('', p).strip('）) 　')
        p = re.sub(r'(房产|确权|买卖|租赁|借款|劳动|建设|工程|项目).*$', '', p)
        if 2 <= len(p) <= 8:
            out.append(p)
    return out[:4]


def folder_of(key, name='', no='', hint=''):
    """案件对应的卷宗目录。先用绑定或记过的，再按案号、当事人字号找；两个字号都对上或案号对上才认。"""
    saved = read_json(FOLDERS)
    for cand in (hint, saved.get(key)):
        if cand and Path(cand).is_dir():
            if saved.get(key) != cand:
                saved[key] = cand
                write_json(FOLDERS, saved)
            return cand
    names = words_of(name)
    # 微信材料收取里绑定过卷宗的案子，直接用那个目录
    for b in read_json(ROOT / '日志' / '待办材料.json').get('bind', {}).values():
        ws = b.get('words') or []
        if ws and all(w in (name or '') for w in ws) and Path(b.get('folder') or '').is_dir():
            saved[key] = b['folder']
            write_json(FOLDERS, saved)
            return b['folder']
    # 先按案号找；案号对不上目录和文件名的，再按当事人字号找
    # 目录名常用字号的前两个字（甲乙电力→甲乙），一并拿去比
    names = list(dict.fromkeys(w for n in names for w in (short(n), short(n)[:2] if len(short(n)) >= 3 else '') if len(w) >= 2))
    for words, need in (([no] + names, 80), (names, 20 if len(names) >= 2 else 10)):
        if not words or (need == 80 and not no):
            continue
        hits = finder.search(words)
        if hits and hits[0][0] >= need and not (len(hits) > 1 and hits[1][0] == hits[0][0]):
            saved[key] = hits[0][1]
            write_json(FOLDERS, saved)
            return hits[0][1]
    return ''


def norm_no(m):
    return f'（{m[1]}）{m[2]}{m[3]}{m[4]}号'


def text_of_file(p):
    ext = p.suffix.lower()
    try:
        if ext in ('.md', '.txt'):
            return p.read_text(encoding='utf-8', errors='ignore')[:20000]
        if ext == '.docx':
            import docx
            d = docx.Document(str(p))
            return '\n'.join(x.text for x in d.paragraphs[:60])
        if ext == '.pdf':
            import pymupdf as fitz
            with fitz.open(p) as d:
                return '\n'.join(d[i].get_text() for i in range(min(2, len(d))))
    except Exception:  # noqa: BLE001
        return ''
    return ''


def numbers_in(folder, limit=80):
    """卷宗里出现过的案号 → {案号: [权重, 最近出现的文件时间, 出处]}。文件名里的权重 3，正文里的 1。"""
    found = defaultdict(lambda: [0, 0.0, ''])
    files = [p for p in Path(folder).rglob('*') if p.is_file() and not p.name.startswith('~$')]
    files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    read = 0
    for p in files:
        mt = p.stat().st_mtime
        for m in CASE_NO.finditer(p.name):
            f = found[norm_no(m)]
            f[0] += 3
            if mt > f[1]:
                f[1], f[2] = mt, p.name
        # 我方起草的文书会引用类案的案号，只读法院来文和对方材料
        if read < limit and p.suffix.lower() in ('.md', '.txt', '.docx', '.pdf') and re.search(r'传票|通知|判决|裁定|调解书|起诉状|上诉状|申请书|笔录|受理|举证|应诉|法院|仲裁委', str(p.relative_to(folder))) \
                and not re.search(r'类案|检索|研究', str(p.relative_to(folder))):
            read += 1
            head = text_of_file(p)[:1500]
            for m in CASE_NO.finditer(head):
                f = found[norm_no(m)]
                f[0] += 1
                if mt > f[1]:
                    f[1], f[2] = mt, p.name
    return found


RANK = [('再', 4), ('终', 3), ('初', 2), ('仲', 2), ('执', 1), ('保', 0)]


def pick_no(found):
    """取本案现在的案号：出现两次以上的里面，审级最高、文件最新的那个；分不出就不取。"""
    strong = {k: v for k, v in found.items() if v[0] >= 2}
    if not strong:
        return '', sorted(found, key=lambda k: -found[k][0])[:4]
    level = lambda k: next((r for w, r in RANK if w in k), 0)  # noqa: E731
    order = sorted(strong, key=lambda k: (level(k), strong[k][1], strong[k][0]), reverse=True)
    if len(order) > 1 and level(order[0]) == level(order[1]) and abs(strong[order[0]][1] - strong[order[1]][1]) < 86400 and strong[order[0]][0] == strong[order[1]][0]:
        return '', order[:4]
    return order[0], order[:4]


def summons_no(date, time, name):
    """传票扫描件：文件名是「月.日-时：分（当事人）」，按开庭日期、时间和当事人字号对上后本机 OCR 读案号。"""
    if not SUMMONS or not SUMMONS.is_dir() or not date:
        return '', ''
    d = dt.date.fromisoformat(date)
    words = words_of(name)
    cands = []
    for p in SUMMONS.iterdir():
        m = re.match(r'(\d{1,2})\.(\d{1,2})', p.name)
        if not m or (int(m[1]), int(m[2])) != (d.month, d.day) or p.suffix.lower() not in ('.jpg', '.jpeg', '.png', '.pdf'):
            continue
        if abs(p.stat().st_mtime - dt.datetime.combine(d, dt.time()).timestamp()) > 200 * 86400:
            continue  # 往年同月同日的
        hh = (time or '').split(':')[0].lstrip('0')
        score = (2 if hh and re.search(rf'-\s*0?{hh}\D', p.name) else 0) + sum(2 for w in words if w in p.name or w[:2] in p.name)
        cands.append((score, p))
    cands.sort(key=lambda x: -x[0])
    if not cands or cands[0][0] < 2 or (len(cands) > 1 and cands[1][0] == cands[0][0]):
        return '', ''
    src = cands[0][1]
    conv = cfg.path('工具.mineru_convert')
    if not conv:
        return '', src.name
    with tempfile.TemporaryDirectory(prefix='summons_') as tmp:
        work = Path(tmp) / ('summons' + src.suffix.lower())
        shutil.copy(src, work)
        try:
            subprocess.run([sys.executable, str(conv), str(work)], capture_output=True, timeout=240, creationflags=0x08000000)
        except (OSError, subprocess.TimeoutExpired):
            return '', src.name
        md = work.with_suffix('.md')
        text = md.read_text(encoding='utf-8', errors='ignore') if md.exists() else ''
    nums = list(dict.fromkeys(norm_no(m) for m in CASE_NO.finditer(text)))
    return (nums[0] if len(nums) == 1 else ''), src.name


# ---------- 给面板 ----------

def pack_for(folder, date):
    """这场开庭的作战包或调解准备单：文件名带开庭日期，同一天有几版的取最新改过的。"""
    if not date:
        return None
    packs = [p for p in Path(folder).rglob('*.docx') if not p.name.startswith('~$')
             and re.match(r'(庭前作战包|调解准备单)（' + re.escape(date), p.name)]
    return max(packs, key=lambda p: p.stat().st_mtime) if packs else None


def checks(folder, key, date=''):
    """开庭前待核，只取这场开庭的作战包（或调解准备单）里本来就列给开庭前办的三块：
    「庭前待办」表里的 ☐ 项（带时限、负责人），「携带原件」清单，附录「未核事项」。
    工作记录不再取：那里混着内部备注和已办完的事。没有这场开庭的作战包就不列。"""
    pack = pack_for(folder, date)
    if not pack:
        return {'pack': '', 'items': []}
    import docx
    from docx.table import Table
    from docx.text.paragraph import Paragraph
    d = docx.Document(str(pack))
    out, part = [], ''
    for el in d.element.body.iterchildren():
        tag = el.tag.rsplit('}', 1)[-1]
        if tag == 'p':
            t = Paragraph(el, d).text.strip()
            if not t:
                continue
            if re.match(r'^[一二三四五六七八九十]+、|^附[：:]', t):
                part = 'todo' if '庭前待办' in t else 'gap' if '未核' in t else ''
                continue
            if part == 'todo' and re.match(r'^携带', t):
                part = 'carry'
                continue
            body = re.sub(r'^[▪•·☐\-\s]+', '', t)
            if part == 'carry':
                continue
            elif part == 'gap' and re.match(r'^(还)?未核[：:]', body):
                # 附录里另有「数字和事实来源」「法条沿用」等说明，只取明写「未核：」的那一段
                for x in re.split(r'[；;]', re.sub(r'^(还)?未核[：:]\s*', '', body)):
                    x = x.strip(' 。')
                    if x:
                        out.append(('未核事项', x))
        elif tag == 'tbl' and part == 'todo':
            for row in Table(el, d).rows:
                cells = list(dict.fromkeys(c.text.strip() for c in row.cells if c.text.strip()))
                if cells and cells[0] in ('待办', '事项'):
                    continue
                if cells and (cells[0].startswith('☐') or len(cells) >= 2):
                    main = cells[0].lstrip('☐ ').strip() or (cells[1] if len(cells) > 1 else '')
                    extra = [c for c in cells[1:] if c != main]
                    out.append(('庭前待办', main + (f"（{' · '.join(extra)}）" if extra else '')))
    done = read_json(CHECKED)
    res, seen = [], set()
    for group, text in out:
        if not text or text in seen:
            continue
        seen.add(text)
        k = hashlib.sha1(f'{key}|{text}'.encode('utf-8')).hexdigest()[:12]
        res.append({'key': k, 'group': group, 'text': text[:200], 'done': bool(done.get(k))})
    return {'pack': pack.name, 'items': res}


def check_set(key, on):
    done = read_json(CHECKED)
    if on:
        done[key] = dt.date.today().isoformat()
    else:
        done.pop(key, None)
    write_json(CHECKED, done)


def talk(mat, chats, n=3):
    """绑定微信会话的最近几句。chats: [{'id':..., 'name':...}]。须在 mat.session 里调用。"""
    import 微信消息摘取 as pick
    wx = mat.微信库
    store, me = wx.account()[1], mat.account()['wxid']
    rows = []
    for i in range(10):
        rel = f'message/message_{i}.db'
        if not (store / rel).exists():
            continue
        con = mat.conn(rel)
        ids = {rid: u for rid, u in con.execute('SELECT rowid, user_name FROM Name2Id')}
        for c in chats:
            t = 'Msg_' + hashlib.md5(c['id'].encode()).hexdigest()
            if not con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (t,)).fetchone():
                continue
            for ct, lt, sid, content in con.execute(f'SELECT create_time, local_type, real_sender_id, message_content FROM "{t}" ORDER BY create_time DESC LIMIT 8'):
                body = pick.text_of(lt, content)
                if body:
                    sender = ids.get(sid, '')
                    rows.append((ct, c['name'], (cfg.ME or '我') if sender == me else (mat.display(sender) if sender else c['name']), re.sub(r'\s+', ' ', body)[:90]))
    rows.sort(reverse=True)
    bj = dt.timezone(dt.timedelta(hours=cfg.TZ_HOURS))
    return [{'time': dt.datetime.fromtimestamp(ct, bj).strftime('%m-%d %H:%M'), 'chat': chat, 'who': who, 'text': text}
            for ct, chat, who, text in rows[:n]][::-1]


# ---------- 定时补案号 ----------

def cmd_fill():
    ldg = load('ledger')
    today = ldg.TODAY.isoformat()
    cases = [c for c in ldg.get_all('cases') if c.get('status') != '已结案' and not (c.get('court_case_no') or '').strip()]
    sched = defaultdict(list)
    for s in ldg.get_all('schedules'):
        if (s.get('schedule_date') or '') >= today and s.get('case_id'):
            sched[s['case_id']].append(s)
    done, unsure = [], []
    for c in cases:
        future = [(c.get(k) or '')[:10] for k in ('trial_date', 'evidence_deadline', 'appeal_deadline') if (c.get(k) or '')[:10] >= today]
        if not future and not sched.get(c['id']) and not sched.get(c.get('case_number')):
            continue
        folder = folder_of(c['id'], c['case_name'])
        no, cands, src = '', [], ''
        if folder:
            found = numbers_in(folder)
            no, cands = pick_no(found)
            src = f'卷宗 {Path(folder).name}（{found[no][2]}）' if no else ''
        if not no and (c.get('trial_date') or '')[:10] >= today:
            no, name = summons_no(c['trial_date'][:10], c.get('trial_time') or '', c['case_name'])
            src = f'传票 {name}' if no else src
        if no:
            ldg.call('PATCH', f"/api/tables/cases/{c['id']}", {'court_case_no': no})
            ldg.log(c, f'天枢中台补案号：{no}，读自{src}')
            done.append(f"{c['case_name']}：{no}（{src}）")
        else:
            unsure.append(f"{c['case_name']}：" + ('卷宗里有几个案号分不出：' + '、'.join(cands) if cands else '没找到卷宗' if not folder else '卷宗和传票里都没读到案号'))
    if not done and not unsure:
        print('没事可做：近期有节点的在办案件都有案号')
        return
    print(f'补案号 {len(done)} 件，读不出 {len(unsure)} 件')
    for line in done + unsure:
        print('- ' + line)
    if unsure:
        key = 'caseno-' + hashlib.sha1('|'.join(unsure).encode('utf-8')).hexdigest()[:10]
        print('\n```agent-autoloop')
        print(json.dumps({'notice': [{'title': f'{len(unsure)} 件案件读不出案号，请在台账里手填', 'text': '\n'.join(unsure), 'key': key, 'fyi': True}]}, ensure_ascii=False))
        print('```')


# ---------- 案件统一名称 ----------
# 格式（本人 2026-11-16 定）：我方当事人简称与对方当事人简称XX纠纷（项目名称，没有不写）。本人在面板改过的名称不再自动改。
NAMES = ROOT / '日志' / '案件名称.json'
CAUSES = ['建设工程施工合同', '建设工程', '建工纠纷', '房屋买卖合同', '商品房', '买卖合同', '租赁合同', '借款合同', '民间借贷', '金融借款', '确认合同效力', '确认协议无效',
          '确认合同无效', '房产确权', '所有权确认', '劳动争议', '劳动合同', '股权转让', '保管合同', '承揽合同', '服务合同', '追偿权', '担保', '侵权', '合同']
PARTY_END = re.compile(r'.*(有限责任公司|股份有限公司|有限公司|公司|工程局|集团|委员会|村委会|政府|管理人|管委会|医院|学校|银行)')
BUILD = re.compile(r'建设工程|施工|工程款|总承包|分包')


def short(p):
    p = re.sub(r'^(' + cfg.place_pattern() + r')(?=.{2,})', '', p.strip('、 '))
    p = re.sub(r'(有限责任公司|股份有限公司|有限公司|公司)$', '', p)
    return re.sub(r'(有限责任公司|股份有限公司|有限公司)', '', p)


def project_of(folder, parties):
    """建工案件的项目名称：先看卷宗目录名去掉当事人后剩下的，再看卷宗里合同文件名里的「××项目／工程」。"""
    if not folder:
        return ''
    name = re.sub(r'^[\d.、\s]+', '', Path(folder).name)
    for w in sorted({w for p in parties for w in (p, short(p), short(p)[:2])}, key=len, reverse=True):
        if len(w) >= 2:
            name = name.replace(w, '')
    name = re.sub(r'诉|与|和|vs|VS|案件|纠纷|一案|公司|建设工程|施工合同|[、，\u2014－\-（）()·\s]', '', name)
    if 2 <= len(name) <= 14 and re.search(r'项目|标段|路|道|桥|楼|园|厂|期|小区|下穿|管网|广场|工程$', name) and not re.search(r'建筑工程|诉', name):
        return name
    from collections import Counter
    hits = Counter()
    for p in Path(folder).rglob('*'):
        if p.is_file():
            m = re.search(r'([一-鿿A-Za-z0-9#]{2,18}?(?:项目|工程|标段|厂房|大楼|小区|道路))[一-鿿（）()]{0,8}(?:施工|总承包|分包|承包)?(?:合同|协议)', p.stem)
            if m:
                hits[re.sub(r'^(关于|附件\d*|.*?《)', '', m[1])] += 1
    return hits.most_common(1)[0][0] if hits else ''


def client_of(client, folder):
    """我方可能的叫法：台账的委托人，加卷宗所在的顾问单位目录名。"""
    out = [short(client)] if client else []
    if finder.ADVISOR:  # 没配顾问单位目录时跳过
        try:
            rel = Path(folder).relative_to(finder.ADVISOR)
            out.append(short(re.sub(r'^[\d.、\s]+', '', rel.parts[0])))
        except (ValueError, IndexError):
            pass
    return [x for x in out if len(x) >= 2]


def unified(raw, cause='', folder='', client=''):
    """把台账里写法各异的案件名整理成统一格式；整理不了的原样返回。"""
    s = re.sub(r'^\d{1,2}[:：]\d{2}\s*[-－]?\s*', '', raw or '').strip()
    s = re.sub(r'（[^（）]*）$|\([^()]*\)$', '', s)
    s = re.sub(r'(一案|案件|案|开庭)$', '', s)
    m = re.split(r'(诉|vs|VS|Vs|与)', s, maxsplit=1)
    if len(m) < 3:
        return s or raw
    a, rest = short(m[0]), m[2]
    idx = min((rest.find(c) for c in CAUSES if rest.find(c) > 0), default=-1)
    b, tail = (rest[:idx], rest[idx:]) if idx > 0 else (rest, '')
    tail = re.sub(r'纠纷$', '', tail)
    tail = '建设工程施工合同' if tail == '建工' else tail
    b = re.sub(r'及第三人|及', '、', b)
    cause = re.sub(r'纠纷$', '', cause or '') or tail
    proj = ''
    pm = PARTY_END.match(b)
    if pm and len(b) - pm.end() >= 2:
        b, proj = b[:pm.end()], b[pm.end():]
    else:
        # 「甲方工程乙方药业一期项目」：字号后面直接跟项目名
        pj = re.match(r'(.{2,6}?(?:工程|建设|置业|地产|实业|园林|电力|药业|集团))(.{2,}(?:项目|工程|标段|厂房|大楼))$', b)
        if pj:
            b, proj = pj[1], pj[2]
    bs = '、'.join(short(x) for x in re.split(r'[、，,]', b) if x.strip())
    build = bool(BUILD.search(raw + cause))
    if build:
        proj = re.sub(r'(建设工程|施工)$', '', proj) or project_of(folder, [m[0]] + re.split(r'[、，,]', b))
    else:
        bs, proj = bs + short(proj) if proj and not pm else bs, ''
    if not a or not bs:
        return raw
    if not proj:
        proj = project_of(folder, [m[0]] + re.split(r'[、，,]', b)) if build else ''
    # 我方排前面
    sides, mine = [a] + bs.split('、'), client_of(client, folder)
    def sub(p, w):  # 「交投」是「交通投资」的缩写：各字按顺序都在里面
        it = iter(w)
        return 2 <= len(p) <= 4 and all(ch in it for ch in p)
    ours = [p for p in sides if any(p in w or w in p or sub(p, w) or any(w[i:i + 2] in p for i in range(len(w) - 1)) for w in mine)]
    if ours and len(ours) < len(sides):
        sides = ours + [p for p in sides if p not in ours]
        head = '、'.join(ours) + '与' + '、'.join(sides[len(ours):])
    else:
        head = a + '与' + bs
    return head + (f"{cause}纠纷" if cause else '') + (f"（{proj}）" if proj else '')


def cmd_names():
    ldg = load('ledger')
    today = ldg.TODAY.isoformat()
    names = read_json(NAMES)
    cases = {c['id']: c for c in ldg.get_all('cases') if c.get('status') != '已结案'}
    by_no = {c.get('case_number'): c for c in cases.values() if c.get('case_number')}
    want = {}
    for c in cases.values():
        if any((c.get(k) or '')[:10] >= today for k in ('trial_date', 'evidence_deadline', 'appeal_deadline')):
            want[c['id']] = c['case_name'], c.get('cause') or '', c['id']
    for s in ldg.get_all('schedules'):
        if (s.get('schedule_date') or '') < today or s.get('schedule_type') != '开庭' or (s.get('title') or '').startswith('【已取消】'):
            continue
        c = cases.get(s.get('case_id')) or by_no.get(s.get('case_id'))
        if c:
            want[c['id']] = c['case_name'], c.get('cause') or '', c['id']
        else:
            want['n:' + s['title']] = s['title'], '', ''
    for t in ldg.get_all('tasks'):
        if t.get('status') in ('待处理', '进行中') and (t.get('description') or '').startswith('[案件待办]'):
            c = next((c for c in cases.values() if c['case_name'] == t.get('case_name')), None)
            if c:
                want[c['id']] = c['case_name'], c.get('cause') or '', c['id']
    changed = 0
    for key, (raw, cause, cid) in want.items():
        if names.get(key, {}).get('manual'):
            continue
        folder = folder_of(key, raw, (cases.get(cid) or {}).get('court_case_no') or '')
        # 台账里已是标准格式的（核实后填的），照用，不再改写
        std = re.fullmatch(r'.{2,30}与.{2,30}纠纷(（[^（）]+）)?', raw)
        title = raw if std else unified(raw, cause, folder, (cases.get(cid) or {}).get('client_name') or '')
        if names.get(key, {}).get('title') != title:
            names[key] = {'title': title, 'raw': raw}
            changed += 1
    write_json(NAMES, names)
    print(f'没事可做：案件名称无变化' if not changed else f'整理案件名称 {changed} 件')
    for key in want:
        print('-', names[key]['raw'], '→', names[key]['title'], '（手动）' if names[key].get('manual') else '', '｜卷宗：' + (Path(read_json(FOLDERS).get(key, '')).name or '未找到'))


if __name__ == '__main__':
    if len(sys.argv) >= 2 and sys.argv[1] == 'fill':
        cmd_fill()
        cmd_names()
    elif len(sys.argv) >= 2 and sys.argv[1] == 'names':
        cmd_names()
    elif len(sys.argv) == 3 and sys.argv[1] == 'no':
        f = numbers_in(sys.argv[2])
        print('取：', pick_no(f)[0] or '（分不出）')
        for k, v in sorted(f.items(), key=lambda x: -x[1][0]):
            print(v[0], k, v[2])
    else:
        print(__doc__)
