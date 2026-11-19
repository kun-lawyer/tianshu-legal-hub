"""待办的微信材料收取：把客户在微信里发来的文件、图片、视频收进该待办对应的案件卷宗。

只读微信库，只复制（图片解密转成 jpg/png 后写入），不改微信原文件，不覆盖卷宗里已有的文件。
只在一种情况下新建目录：待办是新诉讼案件（说明里有案号）而资料根目录里没有卷宗，按当事人里认出的顾问单位
（认不出唯一一家再看以往案子所在目录，再看发件人单位）建「序号.案子简称案由」。收进诉讼卷宗的 PDF、图片里有传票的，传票那页另存图片到
传票扫描目录（config.json 的 目录.传票扫描），交 传票入库.py 写开庭日程。
每条待办绑定「微信会话 + 卷宗目录 + 起始日」，记在 日志/待办材料.json；案件总任务按案子简称绑定，
总任务办完后同一案子再建总任务时沿用。只收对方发的，本人发出的不收。
放哪：文件按卷宗里名字相近的已有文件放到同级目录（与 微信文件入卷.py 同一套比对），对不上的和图片、视频放卷宗根目录；
同名但内容不同的加「（微信月日-时分）」后缀另存。
图片只有缩略图（或微信标了有高清原图而本机没有）的，收取时调 微信点开原图.py 在微信里自动点开下载，定时任务要求电脑空闲 2 分钟。
推测出的绑定除了名字对得上的文件，还收待办说明里点名时间（如「11-17 09:04至09:05」）前后 10 分钟内发的图片、视频、文件。

用法：
  python 微信材料收取.py auto                 定时跑：未绑定的待办按来源会话和案件名推测，能唯一确定才自动绑定；
                                              再给已绑定的待办收新材料。推测出的绑定只收名字对得上本案的文件，其余记为待确认；
                                              本人在面板确认过的绑定，该会话对方发的全收。同一会话绑在两条以上待办上的不自动收
  python 微信材料收取.py scan <任务id>        该待办的绑定（没有就给推测）和会话里可收的材料，JSON
  python 微信材料收取.py collect <任务id> <json 文件>
                                              JSON 写 chats（会话名列表）、folder、since（YYYY-MM-DD）、uids（要收的材料），
                                              保存为该待办的绑定并收取，JSON 输出结果
  python 微信材料收取.py status               各绑定与已收件数，JSON
"""
import datetime as dt
import hashlib
import html
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import threading
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path

import zstandard
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

if sys.stdout:
    sys.stdout.reconfigure(encoding='utf-8')
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import cfg  # noqa: E402
import ledger as ldg  # noqa: E402
import 微信库  # noqa: E402
import 微信文件入卷 as 入卷  # noqa: E402
import 找案件目录  # noqa: E402

ROOT = HERE.parent
STATE = ROOT / '日志' / '待办材料.json'
CASE_ROOTS = cfg.paths('资料根目录')
ME = cfg.ME
COLLEAGUES = cfg.COLLEAGUES
BJ = dt.timezone(dt.timedelta(hours=cfg.TZ_HOURS))
ZSTD = zstandard.ZstdDecompressor()
V1, V2 = b'\x07\x08V1\x08\x07', b'\x07\x08V2\x08\x07'
MAGIC = [(b'\xff\xd8\xff', 'jpg'), (b'\x89PNG', 'png'), (b'GIF8', 'gif'), (b'BM', 'bmp')]
NO_WINDOW = 0x08000000
BAD_CHARS = re.compile(r'[\\/:*?"<>|\s]+')


def in_roots(p):
    rp = Path(p).resolve()
    return any(r.resolve() in rp.parents for r in CASE_ROOTS)


# ===== 绑定记录 =====

def load_state():
    try:
        s = json.loads(STATE.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        s = {}
    return {'bind': s.get('bind', {}), 'got': s.get('got', {}), 'pending': s.get('pending', {}), 'clickfail': s.get('clickfail', {})}


def save_state(s):
    STATE.parent.mkdir(exist_ok=True)
    tmp = STATE.with_suffix('.tmp')
    tmp.write_text(json.dumps(s, ensure_ascii=False, indent=1), encoding='utf-8')
    os.replace(tmp, STATE)


def key_of(t):
    g = ldg.group_key(t)
    return f'案:{g}' if g else f"任务:{t['id']}"


def find_bind(state, t):
    k = key_of(t)
    if k in state['bind']:
        return k, state['bind'][k]
    g = ldg.group_key(t)
    if g:
        for bk, b in state['bind'].items():
            if bk.startswith('案:') and ldg.same_key(bk[2:], g):
                return bk, b
    return k, None


def get_task(tid):
    t = ldg.call('GET', f'/api/tables/tasks/{tid}')
    return t.get('data', t) if isinstance(t, dict) else t


def open_tasks():
    return [t for t in ldg.get_all('tasks') if t.get('status') in ldg.OPEN]


# ===== 微信库 =====

# 解密出的临时库在被打开时 Windows 不让覆盖：定时任务在这里换目录，面板在 server.py 里换，与微信摘取的 wx_live 分开
if __name__ == '__main__':
    微信库.LIVE = 微信库.LIVE.with_name('wx_live_mat_auto')
_lock = threading.Lock()
_cache = {}


def C():
    return _cache


def session(fn):
    """入口函数独占读库，开头清缓存（面板常驻，留着就看不到新消息），结束关掉解密库连接。"""
    def run(*args, **kw):
        with _lock:
            _cache.clear()
            try:
                return fn(*args, **kw)
            finally:
                for v in _cache.values():
                    if hasattr(v, 'close'):
                        v.close()
                _cache.clear()
    return run


def account():
    if 'acct' not in C():
        raw, store = 微信库.account()
        info = next((v for v in json.loads(微信库.KEYS.read_text(encoding='utf-8')).values()
                     if Path(v.get('db_key_source_db_storage_path') or '') == store), {})
        xk = info.get('image_xor_key')
        C()['acct'] = {'store': store, 'root': store.parent,
                          'aes': str(info.get('image_aes_key') or '').encode()[:16],
                          'xor': int(xk, 16) if isinstance(xk, str) else xk,
                          'wxid': (re.match(r'(wxid_[^_]+)', store.parent.name) or [None, ''])[1]}
    return C()['acct']


def contacts():
    if 'contacts' not in C():
        con = 微信库.connect('contact/contact.db')
        try:
            C()['contacts'] = [(u, r or '', n or '') for u, r, n in con.execute('SELECT username, remark, nick_name FROM contact')
                                  if not u.startswith('gh_')]
        finally:
            con.close()
    return C()['contacts']


def display(u):
    if 'names' not in C():
        C()['names'] = {u: (r or n or u) for u, r, n in contacts()}
    return C()['names'].get(u, u)


def resolve(name):
    """会话名（备注、昵称或群名）→ [(username, 显示名)]：先全等，没有再按包含找。"""
    name = name.strip()
    exact = [(u, r or n or u) for u, r, n in contacts() if name in (u, r, n)]
    if exact:
        return exact
    return [(u, r or n or u) for u, r, n in contacts() if name in r or name in n]


def resolve_all(names):
    chats, bad = [], []
    for n in names:
        hits = resolve(n)
        if len(hits) == 1:
            chats.append({'id': hits[0][0], 'name': hits[0][1]})
        else:
            bad.append(f"「{n}」{'找不到' if not hits else '对上多个：' + '、'.join(h[1] for h in hits[:6])}")
    if bad:
        raise ValueError('会话名要写准：' + '；'.join(bad))
    return chats


def conn(rel):
    if rel not in C():
        C()[rel] = 微信库.connect(rel)
    return C()[rel]


def xml_of(content):
    if isinstance(content, bytes):
        try:
            content = ZSTD.decompress(content, max_output_size=1 << 22).decode('utf-8', 'ignore')
        except Exception:  # noqa: BLE001
            content = content.decode('utf-8', 'ignore')
    return content or ''


def ym(ts):
    return dt.datetime.fromtimestamp(ts, BJ).strftime('%Y-%m')


def locate_file(title, size, ct):
    stem, ext = Path(title).stem, Path(title).suffix
    found = []
    for month in {ym(ct), ym(ct + 86400)}:
        d = account()['root'] / 'msg' / 'file' / month
        if d.is_dir():
            found += [p for p in d.iterdir() if p.suffix == ext and re.fullmatch(re.escape(stem) + r'(\(\d+\))*', p.stem)]
    if size:
        found = [p for p in found if p.stat().st_size == size] or []
    if not found:
        return None, '本机未下载：先在微信里点开下载，再收'
    return min(found, key=lambda p: abs(p.stat().st_mtime - ct)), ''


def locate_image(talker, ct, packed, xml=''):
    """返回 (原图路径, 原因, 点开任务)。只有缩略图，或微信标了有高清原图而本机只有普通图的，
    给出点开任务，收取时由 微信点开原图.py 在微信里自动点开下载。"""
    m = re.search(rb'([0-9a-f]{32})', packed or b'')
    if not m:
        return None, '图片记录里没有文件名', None
    n = m[1].decode()
    d = account()['root'] / 'msg' / 'attach' / hashlib.md5(talker.encode()).hexdigest() / ym(ct) / 'Img'
    hd = re.search(r'hdlength="(\d+)"', xml)
    job = {'chat': display(talker), 'dir': str(d), 'md5': n, 'hd': bool(hd and int(hd[1]))}
    if (d / f'{n}_h.dat').exists():
        return d / f'{n}_h.dat', '', None
    if (d / f'{n}.dat').exists():
        return d / f'{n}.dat', '', job if job['hd'] and (d / f'{n}_t.dat').exists() else None
    if (d / f'{n}_t.dat').exists():
        return None, '只有缩略图：收取时自动在微信里点开原图', job
    return None, '本机没有这张图', None


def locate_video(ct, xml):
    m = re.search(r'\slength="(\d+)"', xml)
    d = account()['root'] / 'msg' / 'video' / ym(ct)
    if m and d.is_dir():
        hits = [p for p in d.iterdir() if p.suffix == '.mp4' and p.stat().st_size == int(m[1])]
        if len(hits) == 1:
            return hits[0], ''
    return None, '视频未下载：先在微信里点开播放，再收'


def record_items(talker, ct, lid, xml, packed, head):
    """转发的聊天记录（49/19）里的文件和图片。附件存在 attach/<会话md5>/<月>/Rec/<目录>/F/<序号>/<文件名> 与 Img/<序号>，
    目录名在 packed_info_data 里，序号是该条在记录里的位置（例：张三 11-16 转来李四的一份备忘录）。"""
    inner = re.search(r'<recorditem>(.*?)</recorditem>', xml, re.S)
    rec = re.search(rb'([0-9a-f]{16})', packed or b'')
    if not inner:
        return []
    body = inner[1].strip()
    # 有的版本包在 CDATA 里，有的整段转义成 &lt;recordinfo&gt;
    body = body[9:-3] if body.startswith('<![CDATA[') else html.unescape(body)
    try:
        data = ET.fromstring(body).findall('./datalist/dataitem')
    except ET.ParseError:
        return []
    base = None
    if rec:
        for month in (ym(ct), ym(ct + 86400)):
            d = account()['root'] / 'msg' / 'attach' / hashlib.md5(talker.encode()).hexdigest() / month / 'Rec' / rec[1].decode()
            if d.is_dir():
                base = d
                break
    out = []
    for idx, di in enumerate(data):
        dtype = di.get('datatype')
        if dtype not in ('2', '4', '8'):
            continue
        item = {**head, 'uid': f'{talker}|{ct}|{lid}|{idx}'}
        stamp = f"{dt.datetime.fromtimestamp(ct, BJ):%Y%m%d_%H%M%S}_{idx}"
        src, why = None, '本机未下载：先在微信里打开这条聊天记录，点开该项下载，再收'
        if dtype == '8':
            name = (di.findtext('datatitle') or '').strip()
            item.update(kind='文件', name=name)
            p = base / 'F' / str(idx) / name if base and name else None
            if p and p.exists():
                src, why = p, ''
        elif dtype == '2':
            item.update(kind='图片', name=BAD_CHARS.sub('_', f"微信图片_{item['sender']}_{stamp}.jpg"))
            if base and (base / 'Img' / str(idx)).exists():
                src, why = base / 'Img' / str(idx), ''
            elif base and (base / 'Img' / f'{idx}_t').exists():
                why = '只有缩略图：先在微信里打开这条聊天记录看原图，再收'
        else:
            item.update(kind='视频', name=BAD_CHARS.sub('_', f"微信视频_{item['sender']}_{stamp}.mp4"))
            why = '聊天记录里的视频暂不支持自动收，请在微信里另存'
        item.update(src=str(src) if src else '', reason=why)
        out.append(item)
    return out


def messages(chat_ids, since):
    """这些会话里 since（时间戳）以来对方发的文件、图片、视频。"""
    acct = account()
    me = {u for u, r, n in contacts() if ME and ME in (r, n)} | {acct['wxid']}
    want = {'Msg_' + hashlib.md5(u.encode()).hexdigest(): u for u in chat_ids}
    items = []
    for i in range(10):
        rel = f'message/message_{i}.db'
        p = acct['store'] / rel
        wal = p.with_name(p.name + '-wal')
        if not p.exists() or max(p.stat().st_mtime, wal.stat().st_mtime if wal.exists() else 0) < since:
            continue
        con = conn(rel)
        tables = {n for (n,) in con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'Msg_%'")}
        if not tables & want.keys():
            continue
        ids = {rid: u for rid, u in con.execute('SELECT rowid, user_name FROM Name2Id')}
        for t in tables & want.keys():
            talker = want[t]
            for lid, ct, lt, sid, mc, pk in con.execute(
                    f'SELECT local_id, create_time, local_type, real_sender_id, message_content, packed_info_data FROM "{t}" '
                    f'WHERE create_time >= ? AND (local_type & 4294967295) IN (3, 43, 49) ORDER BY create_time', (since,)):
                sender = ids.get(sid, '')
                if sender in me:
                    continue
                kind, sub, xml = lt & 0xFFFFFFFF, lt >> 32, xml_of(mc)
                item = {'uid': f'{talker}|{ct}|{lid}', 'ct': ct, 'time': dt.datetime.fromtimestamp(ct, BJ).strftime('%m-%d %H:%M'),
                        'chat': display(talker), 'sender': display(sender) if sender else display(talker)}
                if kind == 49 and sub == 19:
                    items += record_items(talker, ct, lid, xml, pk, item)
                    continue
                if kind == 49:
                    if sub not in (6, 74):
                        continue
                    title = re.search(r'<title>(.*?)</title>', xml, re.S)
                    title = title[1].strip() if title else ''
                    size = re.search(r'<totallen>(\d+)</totallen>', xml)
                    src, why = locate_file(title, int(size[1]) if size else 0, ct)
                    item.update(kind='文件', name=title, sub=sub)
                elif kind == 3:
                    src, why, job = locate_image(talker, ct, pk, xml)
                    if job:
                        item['img'] = job
                    item.update(kind='图片', name=f"微信图片_{item['sender']}_{dt.datetime.fromtimestamp(ct, BJ):%Y%m%d_%H%M%S}.jpg")
                else:
                    src, why = locate_video(ct, xml)
                    item.update(kind='视频', name=f"微信视频_{item['sender']}_{dt.datetime.fromtimestamp(ct, BJ):%Y%m%d_%H%M%S}.mp4")
                item['name'] = item['name'] if item['kind'] == '文件' else BAD_CHARS.sub('_', item['name'])
                item.update(src=str(src) if src else '', reason=why)
                items.append(item)
    # 发文件时微信先记一条「正在发送」（74），发完再记一条（6），只留一条
    sent = {(i['chat'], i['name']) for i in items if i.get('sub') == 6}
    items = [i for i in items if not (i.get('sub') == 74 and (i['chat'], i['name']) in sent)]
    return sorted(items, key=lambda i: i['ct'])


# ===== 图片解密 =====

def decode_dat(data):
    if data[:6] in (V1, V2):
        acct = account()
        key = acct['aes'] if data[:6] == V2 else b'cfcd208495d565ef'
        aes_size, xor_size = struct.unpack('<II', data[6:14])
        al = aes_size + 16 - aes_size % 16
        body = data[15:]
        dec = Cipher(algorithms.AES(key), modes.ECB()).decryptor()
        head = dec.update(body[:al]) + dec.finalize()
        head = head[:-head[-1]]
        tail = bytes(b ^ acct['xor'] for b in body[len(body) - xor_size:]) if xor_size else b''
        out = head + body[al:len(body) - xor_size] + tail
    else:
        out = b''
        for magic, _ in MAGIC:
            x = data[0] ^ magic[0]
            if data[1] ^ x == magic[1]:
                out = bytes(b ^ x for b in data)
                break
    if out[:4] == b'wxgf':
        out = wxgf_to_jpg(out)
    ext = next((e for magic, e in MAGIC if out.startswith(magic)), None)
    if not ext:
        raise ValueError('图片解不开')
    return out, ext


def ffmpeg():
    exe = shutil.which('ffmpeg')
    if exe:
        return exe
    hits = sorted((Path.home() / 'AppData/Local/Microsoft/WinGet/Packages').glob('Gyan.FFmpeg*/*/bin/ffmpeg.exe'))
    if not hits:
        raise ValueError('原图是微信 wxgf 格式，要 ffmpeg 才能转，本机没找到')
    return str(hits[-1])


def wxgf_to_jpg(data):
    """wxgf 是微信自己的格式，里面是 HEVC 码流；从第一个起始码起交给 ffmpeg 解第一帧。"""
    i = data.find(b'\x00\x00\x00\x01')
    r = subprocess.run([ffmpeg(), '-loglevel', 'error', '-f', 'hevc', '-i', '-', '-frames:v', '1', '-q:v', '2',
                        '-f', 'image2pipe', '-c:v', 'mjpeg', '-'], input=data[i:], capture_output=True,
                       timeout=60, creationflags=NO_WINDOW)
    if r.returncode or not r.stdout:
        raise ValueError('wxgf 转 jpg 失败：' + r.stderr.decode('utf-8', 'replace')[-120:])
    return r.stdout


# ===== 放进卷宗 =====

def plan_dir(item, folder, files):
    if item['kind'] == '文件':
        ranked = 入卷.rank(item['name'], folder, files)
        if ranked and ranked[0][1][0] >= 0.5:
            return ranked[0][0]
    return folder


def place(item, folder, files):
    """收一件材料，返回 (状态, 卷宗里的路径)。"""
    src = Path(item['src'])
    if item['kind'] == '图片':
        data, ext = decode_dat(src.read_bytes())
        name = Path(item['name']).with_suffix('.' + ext).name
    else:
        data, name = None, item['name']
    dst = plan_dir(item, folder, files) / name
    same = (lambda p: p.read_bytes() == data) if data is not None else \
        (lambda p: p.stat().st_size == src.stat().st_size and p.read_bytes() == src.read_bytes())
    if dst.exists():
        if same(dst):
            return '已在卷宗', dst
        mark = dt.datetime.fromtimestamp(item['ct'], BJ).strftime('%m%d-%H%M')
        dst = dst.with_name(f'{dst.stem}（微信{mark}）{dst.suffix}')
        if dst.exists():
            return '已在卷宗', dst
    if data is not None:
        dst.write_bytes(data)
    else:
        shutil.copy2(src, dst)
    return '收入', dst


def check_folder(folder):
    p = Path(folder or '')
    if not folder or not p.is_dir():
        raise ValueError(f'卷宗目录不存在（不新建目录）：{folder}')
    if not in_roots(p):
        raise ValueError(f"卷宗目录须在资料根目录（{'、'.join(map(str, CASE_ROOTS)) or '未配置'}）下")
    return p


# 自动点开原图前要求电脑空闲的秒数：面板上手点收取时为 0，定时任务里设成 120，免得抢正在打字的焦点
CLICK_IDLE = 0


def fetch_originals(items, state):
    """定时任务里同一张图点开失败两次就不再自动试（免得每半小时抢一次焦点），面板手点收取不受此限。"""
    fails = state.setdefault('clickfail', {})
    todo = [it for it in items if it.get('img') and it['uid'] not in state['got']
            and (not CLICK_IDLE or fails.get(it['uid'], 0) < 2)]
    if not todo:
        return
    import 微信点开原图
    res = 微信点开原图.fetch([it['img'] for it in todo], decode_dat, CLICK_IDLE)
    for it in todo:
        p, why = res.get(it['img']['md5'], (None, '没有点开'))
        if p:
            it.update(src=str(p), reason='')
            fails.pop(it['uid'], None)
            continue
        if '电脑正在使用' not in why:
            fails[it['uid']] = fails.get(it['uid'], 0) + 1
        # 定时任务里有高清原图却没点开的，这轮先不收普通图，等下轮
        if CLICK_IDLE or not it['src']:
            it.update(src='', reason=why)


def collect(state, key, bind, items):
    folder = check_folder(bind['folder'])
    files = 入卷.case_files(folder)
    fetch_originals(items, state)
    out = []
    for it in items:
        if it['uid'] in state['got']:
            out.append({**brief(it), 'status': '已收过', 'dst': state['got'][it['uid']]['dst']})
            continue
        if not it['src']:
            out.append({**brief(it), 'status': '不可收', 'reason': it['reason']})
            continue
        try:
            status, dst = place(it, folder, files)
        except Exception as e:  # noqa: BLE001
            out.append({**brief(it), 'status': '不可收', 'reason': str(e)})
            continue
        state['got'][it['uid']] = {'dst': str(dst), 'at': dt.datetime.now(BJ).strftime('%Y-%m-%d %H:%M'), 'key': key}
        if status == '收入':
            files.append(dst)
        out.append({**brief(it), 'status': status, 'dst': str(dst)})
    return out


def brief(it):
    return {k: it[k] for k in ('uid', 'time', 'chat', 'sender', 'kind', 'name')}


# ===== 推测绑定 =====

SRC = re.compile(r'(?:私聊|群[：:]?)\s*([^（(，,。；;\n]+?)\s*(?=[（(，,。；;\n]|\d{1,4}[-年]|$)')
CUT = re.compile(r'(房屋|买卖|合同|纠纷|建设|工程|施工|租赁|借款|借贷|劳动|劳务|侵权|保险|物业|案件|一案|二审|一审|再审|执行|案$).*$')


def parties(s):
    return [p for p in (CUT.sub('', x).strip() for x in re.split(r'诉|与|vs|VS|、|和|及', s or '')) if len(p) >= 2]


LOOKBACK = 7


def since_of(t):
    """默认从建待办前 7 天起：客户提要求前往往已陆续发了图片、表格（例：某客户 11-10 发明细账和截图，11-13 才说发函）。"""
    try:
        c = dt.datetime.fromisoformat(str(t.get('created_at')).replace('Z', '+00:00'))
        c = c if c.tzinfo else c.replace(tzinfo=BJ)
        day = c.astimezone(BJ).date()
    except ValueError:
        day = dt.datetime.now(BJ).date()
    return (day - dt.timedelta(days=LOOKBACK)).isoformat()


def guess(t):
    """按待办说明里的来源会话、案子简称或案件名推测会话和卷宗。同事的私聊不推测（同事经手的案子多，会收错）。"""
    names = []
    for line in (t.get('description') or '').split('\n'):
        if '来源' in line:
            names += [n for n in SRC.findall(line) if n.strip() not in COLLEAGUES and n.strip() != ME]
    chats, unsure = [], []
    for n in dict.fromkeys(n.strip() for n in names):
        hits = resolve(n)
        if len(hits) == 1:
            chats.append({'id': hits[0][0], 'name': hits[0][1]})
        else:
            unsure.append(n)
    words = parties(ldg.group_key(t) or t.get('case_name') or '')
    # 微信待办在描述里写明的卷宗优先：非诉待办没有案件名，按案件名找不到
    named = re.search(r'(?:^|\]\s*)卷宗[：:]\s*(.+?)\s*$', t.get('description') or '', re.M)
    if named and Path(named[1]).is_dir() and in_roots(named[1]):
        return {'chats': chats, 'unsure': unsure, 'folder': named[1], 'candidates': [named[1]], 'since': since_of(t)}
    found = 找案件目录.search(words) if words else []
    top = found[0][0] if found else 0
    sure = bool(found) and (top >= 80 or top >= 10 * len(words) >= 20) and (len(found) == 1 or found[1][0] < top)
    return {'chats': chats, 'unsure': unsure, 'folder': found[0][1] if sure else '',
            'candidates': [f[1] for f in found[:8]], 'since': since_of(t)}


CASE_NO = re.compile(r'[（(〔]\s*20\d\d\s*[)）〕]\s*[\u4e00-\u9fff]{1,2}\s*\d{0,4}\s*[\u4e00-\u9fff]{1,4}\s*\d+\s*号')
CAUSE = re.compile(r'[，,、\s]([\u4e00-\u9fff]{2,10}纠纷)')
LIT_ROOT = re.compile(r'^\d+\.\s*诉讼')
UNITS_CFG = cfg.units()
UNIT_DIRS = {k: v['目录'] for k, v in UNITS_CFG.items()}


def lit_roots_of(unit_dir):
    return [s for s in unit_dir.iterdir() if s.is_dir() and LIT_ROOT.match(s.name)] if unit_dir.is_dir() else []


def client_units(words):
    """案子简称里的当事人对得上哪些顾问单位。别名与简称完全相同才算，三字以上的别名、单位名可包含在简称里
    （两字的短别名常出现在对方公司名里）；一方对上多个单位取别名最长的（「甲投资贸易」不算「甲投资」）。"""
    hits = set()
    for w in words:
        best = max(((len(a), k) for k, v in UNITS_CFG.items() for a in [v['名称'], *v.get('别名', [])]
                    if w == a or (len(a) >= 3 and a in w)), default=None)
        if best:
            hits.add(best[1])
    return hits


def unit_lit_root(unit):
    """该单位的诉讼目录，没有就建「下一个序号.诉讼案件」（例：某顾问单位原先只有合同审查、法律意见书、法律咨询）。"""
    udir = 找案件目录.ADVISOR / UNIT_DIRS[unit]
    subs = lit_roots_of(udir)
    if len(subs) == 1 or not udir.is_dir():
        return subs[0] if subs else None
    if subs:
        return None
    nums = [int(m[1]) for d in udir.iterdir() if d.is_dir() and (m := re.match(r'(\d+)\.', d.name))]
    root = udir / f'{max(nums, default=0) + 1}.诉讼案件'
    root.mkdir()
    return root


def new_case_folder(t, g):
    """新诉讼案件（说明里有案号）在资料根目录里找不到卷宗时新建，返回 (目录, 依据)；不是新诉讼案件返回 ('', '')，
    是新案件但定不了放哪返回 ('', 原因)。放哪按识别：
    1. 当事人里只认出一个顾问单位的，放该单位的诉讼目录。子公司即使旧案放在母公司下，
       只有子公司一家是当事人的案子也放子公司自己的目录；
    2. 认出多个单位或一个都没有的，看同一当事人以往的案子在哪个诉讼目录；
    3. 没有先例再按发件人在送审人对照里的单位。
    目录名「下一个序号.案子简称+案由」。"""
    desc = t.get('description') or ''
    no = CASE_NO.search(desc)
    key = ldg.group_key(t) or t.get('case_name') or ''
    if not no or not key or 找案件目录.search([no[0]]):
        return '', ''
    words = parties(key)
    if any(all(w in Path(c).name for w in words) for c in g['candidates']):
        return '', ''
    seen = Counter(d.parent for d in 找案件目录.case_dirs()
                   if LIT_ROOT.match(d.parent.name) and any(w in d.name for w in words))
    top = seen.most_common(2)
    root, why = None, ''
    clients = client_units(words)
    if len(clients) == 1 and (root := unit_lit_root(next(iter(clients)))):
        why = f"当事人中只有{UNITS_CFG[next(iter(clients))]['名称']}是顾问单位"
    elif top and (len(top) == 1 or top[1][1] < top[0][1]):
        root, why = top[0][0], f'「{key}」的当事人以往 {top[0][1]} 件案子放在这里'
    else:
        senders = json.loads((ROOT / '送审人.json').read_text(encoding='utf-8')) if (ROOT / '送审人.json').exists() else {}
        units = {senders[c['id']]['unit'] for c in g['chats'] if c['id'] in senders}
        subs = lit_roots_of(找案件目录.ADVISOR / UNIT_DIRS.get(next(iter(units)), '_')) if len(units) == 1 else []
        if len(subs) == 1:
            root, why = subs[0], '按发件人所在单位'
    if not root:
        return '', '定不了放哪个单位的诉讼目录（以往没有同一当事人的案子，发件人单位也定不下来）'
    nums = [int(m[1]) for d in root.iterdir() if d.is_dir() and (m := re.match(r'(\d+)', d.name))]
    cause = CAUSE.search(desc)
    name = BAD_CHARS.sub('', key + (cause[1] if cause and '纠纷' not in key else ''))
    folder = root / f'{max(nums, default=0) + 1}.{name}'
    folder.mkdir()
    return str(folder), why


SUMMONS_DIR = cfg.path('目录.传票扫描')
WHEN = re.compile(r'应到时间\D{0,40}?(20\d\d)\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日\D{0,10}?(\d{1,2})\s*[:：时点]\s*(\d{1,2})?')


def doc_text(doc):
    """有文字层直接取，扫描件走本机 OCR（与 传票入库.py 同一通道）。"""
    import tempfile
    import 传票入库
    text = ''.join(p.get_text() for p in doc)
    if len(text.strip()) >= 20 * len(doc):
        return text
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / 'doc.pdf'
        doc.save(p)
        return 传票入库.ocr_text(p)


def summons(path, b):
    """收进诉讼卷宗的 PDF 或图片里有传票的，把传票那页另存一张图片到传票扫描文件夹，由 传票入库.py 写开庭日程。
    只看前 10 页，已过的开庭和传票文件夹里已有的同一场不存。"""
    if SUMMONS_DIR is None or path.suffix.lower() not in ('.pdf', '.jpg', '.jpeg', '.png'):
        return ''
    import pymupdf as fitz
    src = fitz.open(path)
    doc = src if src.is_pdf else fitz.open('pdf', src.convert_to_pdf())
    head = fitz.open()
    head.insert_pdf(doc, to_page=min(len(doc), 10) - 1)
    if len(head) > 1 and not (re.search(r'传\s*票', t := doc_text(head)) and '应到' in t):
        return ''
    for i in range(len(head)):
        one = fitz.open()
        one.insert_pdf(head, from_page=i, to_page=i)
        text = doc_text(one)
        m = re.search(r'传\s*票', text) and WHEN.search(text)
        if not m:
            continue
        y, mo, d, h, mi = int(m[1]), int(m[2]), int(m[3]), int(m[4]), int(m[5] or 0)
        if dt.date(y, mo, d) < dt.datetime.now(BJ).date():
            return f'传票 {path.name} 第 {i + 1} 页：{mo}月{d}日的开庭已过，不另存'
        key = b.get('case_key') or re.sub(r'^\d+\.?', '', Path(b['folder']).name)
        prefix = f'{mo}.{d}-{h}：{mi:02d}'
        words = parties(key)
        dup = [p for p in SUMMONS_DIR.rglob(prefix + '*') if not words or any(w in p.name for w in words)]
        if dup:
            return f'传票 {path.name} 第 {i + 1} 页：传票文件夹里已有 {dup[0].name}'
        dst = SUMMONS_DIR / f"{prefix}（{key.replace('诉', 'VS')}）.jpg"
        head[i].get_pixmap(dpi=200).save(dst, jpg_quality=90)
        return f'传票 {path.name} 第 {i + 1} 页 → {dst}'
    return ''


def ts_of(day):
    return int(dt.datetime.fromisoformat(day).replace(tzinfo=BJ).timestamp())


# ===== 对外入口 =====

@session
def scan(tid, override=None):
    override = override or {}
    t = get_task(tid)
    state = load_state()
    key, b = find_bind(state, t)
    g = guess(t)
    chats = resolve_all(override['chats']) if override.get('chats') else (b or g)['chats']
    folder = override.get('folder') or (b or {}).get('folder') or g['folder']
    since = override.get('since') or (b or {}).get('since') or g['since']
    items = messages([c['id'] for c in chats], ts_of(since)) if chats else []
    files = 入卷.case_files(Path(folder)) if folder and Path(folder).is_dir() else None
    for it in items:
        got = state['got'].get(it['uid'])
        it['status'] = '已收' if got else ('可收' if it['src'] or it.get('img') else '不可收')
        it['dst'] = got['dst'] if got else (str(plan_dir(it, Path(folder), files) / it['name']) if files is not None else '')
        if not got and not it['src'] and it.get('img'):
            it['dst'] += '（收取时自动在微信里点开原图）'
        for k in ('src', 'sub', 'ct', 'img'):
            it.pop(k, None)
    return {'key': key, 'bound': bool(b), 'auto': bool(b and b.get('auto')), 'chats': [c['name'] for c in chats],
            'unsure': g['unsure'], 'folder': folder, 'candidates': g['candidates'], 'since': since, 'items': items}


@session
def collect_manual(tid, body):
    t = get_task(tid)
    chats = resolve_all(body.get('chats') or [])
    if not chats:
        raise ValueError('至少写一个微信会话')
    folder = str(check_folder(body.get('folder')))
    since = body.get('since') or since_of(t)
    if not ldg.DATE.match(since):
        raise ValueError('起始日须为 YYYY-MM-DD')
    state = load_state()
    key, _ = find_bind(state, t)
    bind = state['bind'][key] = {'chats': chats, 'folder': folder, 'since': since, 'auto': False, 'title': t.get('title'),
                                 'task_id': t['id'], 'at': dt.datetime.now(BJ).strftime('%Y-%m-%d %H:%M')}
    uids = set(body.get('uids') or [])
    items = [i for i in messages([c['id'] for c in chats], ts_of(since)) if i['uid'] in uids]
    out = collect(state, key, bind, items)
    state['pending'].pop(key, None)
    save_state(state)
    return {'ok': True, 'items': out}


def status(tasks):
    """给面板：每条待办的绑定与已收件数。tasks 是 台账 tasks 的输出。"""
    state = load_state()
    n = Counter(g['key'] for g in state['got'].values())
    last = {}
    for g in state['got'].values():
        last[g['key']] = max(last.get(g['key'], ''), g['at'])
    out = {}
    for t in tasks:
        k, b = find_bind(state, t)
        if b:
            out[t['id']] = {'auto': b.get('auto', False), 'folder': b['folder'], 'chats': [c['name'] for c in b['chats']],
                            'n': n.get(k, 0), 'last': last.get(k, ''),
                            'pending': state['pending'].get(k, 0) if b.get('auto') else 0}
    return out


STAMP = re.compile(r'(\d{1,2})-(\d{1,2})\s*(\d{1,2}):(\d{2})((?:\s*[至到、和,，]\s*\d{1,2}:\d{2})*)')
CITED_SLACK = 600


def cited_spans(t):
    """微信待办在说明里写明的消息时间（如「11-17 09:04至09:05」「11-17 09:04、09:05」），前后各放宽 10 分钟。
    待办点名的图片、视频据此收取（例：某客户 11-17 发土地证照片，按旧规则只收名字对得上的文件，图片一直待确认）。"""
    try:
        year = dt.datetime.fromisoformat(str(t.get('created_at')).replace('Z', '+00:00')).astimezone(BJ).year
    except ValueError:
        year = dt.datetime.now(BJ).year
    spans = []
    for mo, d, h, mi, more in STAMP.findall(t.get('description') or ''):
        try:
            day = dt.datetime(year, int(mo), int(d), tzinfo=BJ)
        except ValueError:
            continue
        hm = [(int(h), int(mi))] + [tuple(map(int, x.split(':'))) for x in re.findall(r'\d{1,2}:\d{2}', more)]
        ts = [int(day.replace(hour=a, minute=b).timestamp()) for a, b in hm if a < 24 and b < 60]
        if ts:
            spans.append((min(ts) - CITED_SLACK, max(ts) + 60 + CITED_SLACK))
    return spans


def matches_case(it, words, folder, files, spans=()):
    """推测出来的绑定只自动收能对上本案的材料：待办说明里点名时间发的，名字含当事人简称的文件，或与卷宗里已有文件相近的文件。"""
    if any(a <= it['ct'] <= b for a, b in spans):
        return True
    if it['kind'] != '文件':
        return False
    if any(w in it['name'] for w in words):
        return True
    ranked = 入卷.rank(it['name'], folder, files)
    return bool(ranked) and ranked[0][1][0] >= 0.5


REVIEW_MANIFEST = cfg.path('目录.送审收件清单')
REVIEW_WINDOW = 2 * 3600


def review_pass(state):
    """送审件归档后，同一发件人前后两小时内发的图片、视频和其他文件一并收进该送审文件夹。
    送审收件只收文书，配套的截图（例：某送审人 11-16 发一份证明书时附的工资表截图）原先落不进卷宗。"""
    if REVIEW_MANIFEST is None:
        return 0, []
    try:
        items = json.loads(REVIEW_MANIFEST.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return 0, []
    cutoff = dt.datetime.now(BJ) - dt.timedelta(days=3)
    got, lines = 0, []
    for it in items:
        if it.get('status') != '已归档' or not it.get('sender') or not it.get('target'):
            continue
        rt = dt.datetime.strptime(it['received'], '%Y-%m-%d %H:%M').replace(tzinfo=BJ)
        if rt < cutoff:
            continue
        chat = it['sender'] if it.get('chat') in (None, '', '私聊') else next(iter(resolve(it['chat'])), (None,))[0]
        if not chat:
            continue
        t0 = int(rt.timestamp())
        who = display(it['sender'])
        near = [m for m in messages([chat], t0 - REVIEW_WINDOW)
                if m['ct'] <= t0 + REVIEW_WINDOW and m['sender'] == who and m['uid'] not in state['got']]
        if not near:
            continue
        try:
            res = collect(state, f"送审:{it['id']}", {'folder': str(Path(it['target']).parent)}, near)
        except ValueError as e:
            lines.append(f"送审 {it['name']}：{e}")
            continue
        for r in res:
            if r['status'] == '收入':
                got += 1
                lines.append(f"送审 {it['name']}：收入 {r['time']} {r['sender']} 的{r['kind']} → {r['dst']}")
    return got, lines


@session
def auto():
    state = load_state()
    state.setdefault('pending', {})
    active, lines = [], []
    review_got, review_lines = review_pass(state)
    lines += review_lines
    for t in open_tasks():
        k, b = find_bind(state, t)
        if not b:
            g = guess(t)
            nofolder = state.setdefault('nofolder', {})
            if g['chats'] and not g['folder'] and t['id'] not in nofolder:
                made, why = new_case_folder(t, g)
                if made:
                    g['folder'] = made
                    lines.append(f"新建卷宗 {made}（{why}）")
                elif why:
                    nofolder[t['id']] = why
                    lines.append(f"{t.get('title')}：没建卷宗，{why}，请在面板手动指定")
            if not (g['chats'] and g['folder']):
                continue
            b = state['bind'][k] = {'chats': g['chats'], 'folder': g['folder'], 'since': g['since'], 'auto': True,
                                    'case_key': ldg.group_key(t) or '',
                                    'words': parties(ldg.group_key(t) or t.get('case_name') or ''),
                                    'title': t.get('title'), 'task_id': t['id'],
                                    'at': dt.datetime.now(BJ).strftime('%Y-%m-%d %H:%M')}
            lines.append(f"自动绑定 {t.get('title')}：{'、'.join(c['name'] for c in g['chats'])} → {g['folder']}")
        elif b.get('auto'):
            b['since'] = min(b['since'], since_of(t))
        hit = next((a for a in active if a[0] == k), None)
        if hit:
            hit[2].extend(cited_spans(t))
        else:
            active.append((k, b, cited_spans(t)))
    owners = Counter(c['id'] for _, b, _ in active for c in b['chats'])
    got = review_got
    for k, b, spans in active:
        ids = [c['id'] for c in b['chats'] if owners[c['id']] == 1]
        shared = [c['name'] for c in b['chats'] if owners[c['id']] > 1]
        if shared:
            lines.append(f"{k}：会话 {'、'.join(shared)} 同时绑在别的待办上，不自动收，请在面板手动收")
        if not ids:
            continue
        try:
            items = messages(ids, ts_of(b['since']))
            if b.get('auto'):
                folder = check_folder(b['folder'])
                files = 入卷.case_files(folder)
                fits = [i for i in items if matches_case(i, b.get('words') or [], folder, files, spans)]
                state['pending'][k] = sum(1 for i in items if i not in fits and i['uid'] not in state['got'])
                items = fits
            res = collect(state, k, b, items)
        except ValueError as e:
            lines.append(f'{k}：{e}')
            continue
        lit = LIT_ROOT.match(Path(b['folder']).parent.name) or Path(b['folder']).parent == 找案件目录.SINGLE
        for r in res:
            if r['status'] == '收入':
                got += 1
                lines.append(f"{k}：收入 {r['time']} {r['sender']} 的{r['kind']} → {r['dst']}")
                if lit:
                    try:
                        line = summons(Path(r['dst']), b)
                    except Exception as e:  # noqa: BLE001
                        line = f"传票 {Path(r['dst']).name}：没抽成，{e}"
                    if line:
                        lines.append(line)
    save_state(state)
    # 第一行以“没事可做：”开头，中台记为巡查无事，不算一件办成的事
    print(f'微信材料收取：{len(active)} 条待办已绑定，本次收入 {got} 件' if got or lines
          else f'没事可做：{len(active)} 条已绑定的待办都没有新材料')
    for line in lines:
        print(line)


if __name__ == '__main__':
    if not cfg.enabled('微信'):
        print('微信模块未启用（config.json 的 微信.启用）')
        sys.exit(0)
    cmd = sys.argv[1] if len(sys.argv) > 1 else ''
    try:
        if cmd == 'auto':
            CLICK_IDLE = 120
            auto()
        elif cmd == 'scan' and len(sys.argv) == 3:
            print(json.dumps(scan(sys.argv[2]), ensure_ascii=False, indent=1))
        elif cmd == 'collect' and len(sys.argv) == 4:
            body = json.loads(Path(sys.argv[3]).read_text(encoding='utf-8'))
            print(json.dumps(collect_manual(sys.argv[2], body), ensure_ascii=False, indent=1))
        elif cmd == 'status':
            print(json.dumps(status(open_tasks()), ensure_ascii=False, indent=1))
        else:
            sys.exit(__doc__)
    except ValueError as e:
        sys.exit(str(e))
