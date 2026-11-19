"""微信消息摘取：把上次运行以来客户、同事的微信消息整理成一份按会话排列的文本，交 Agent 提取待办。只读微信库。

范围：送审人对照表里的人、律所同事的私聊；有他们发言或有人提到本人的群。本人在这些会话里的回复一并收，
用来判断事情是否已经回了、办了。输出 日志/微信摘取/<北京时间>.md，打印路径。
"""
import datetime as dt
import hashlib
import html
import json
import re
import sys
import time
from collections import defaultdict
from pathlib import Path

import zstandard

if sys.stdout:
    sys.stdout.reconfigure(encoding='utf-8')
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import cfg  # noqa: E402
import 微信库  # noqa: E402

ROOT = HERE.parent
SENDERS = json.loads((ROOT / '送审人.json').read_text(encoding='utf-8')) if (ROOT / '送审人.json').exists() else {}
STATE = ROOT / '日志' / '微信摘取状态.json'
OUT_DIR = ROOT / '日志' / '微信摘取'
ME = cfg.ME
COLLEAGUES = cfg.COLLEAGUES
EXCLUDE_FILE = ROOT / '微信待办排除.json'
_UNITS_CFG = cfg.units()
# 群名含通用工作词、配置的工作群关键词或顾问单位别名，才当工作群
_GROUP_WORDS = [w for w in [*(cfg.get('微信.工作群关键词', []) or []),
                            *(a for v in _UNITS_CFG.values() for a in v.get('别名', []))] if w]
WORK_GROUP = re.compile('|'.join([r'法|律|案|诉|仲裁|公司|集团|政府|镇|局|委|厅|院|项目|工作|合同|顾问|'
                                  r'协会|商会|联会|执委|专委|党|支部', *map(re.escape, _GROUP_WORDS)]))
BIG_GROUP = 60
UNITS = {k: v['名称'] for k, v in _UNITS_CFG.items()}
BJ = dt.timezone(dt.timedelta(hours=cfg.TZ_HOURS))
FUNASR = (cfg.get('工具.funasr') or 'http://127.0.0.1:8765').rstrip('/')
MAX_BACK = 3 * 86400
ZSTD = zstandard.ZstdDecompressor()


def excluded(name):
    try:
        words = json.loads(EXCLUDE_FILE.read_text(encoding='utf-8')).get('排除', [])
    except (OSError, ValueError):
        words = []
    return any(w and w in (name or '') for w in words)


def text_of(lt, content):
    t, sub = lt & 0xFFFFFFFF, lt >> 32
    if isinstance(content, bytes):
        try:
            content = ZSTD.decompress(content, max_output_size=1 << 22).decode('utf-8', 'ignore')
        except Exception:  # noqa: BLE001
            content = content.decode('utf-8', 'ignore')
    content = re.sub(r'^[\w\-@.]+:\n', '', content or '')
    if t == 1:
        return content.strip()
    if t == 3:
        return '[图片]'
    if t == 34:
        return '[语音]'
    if t == 43:
        return '[视频]'
    if t == 49:
        title = re.search(r'<title>(.*?)</title>', content, re.S)
        title = title.group(1).strip() if title else ''
        if sub in (6, 74):
            return f'[文件] {title}'
        if sub == 57:
            ref = re.search(r'<refermsg>.*?<content>(.*?)</content>', content, re.S)
            quoted = re.sub(r'\s+', ' ', ref.group(1))[:60] if ref else ''
            return f'{title}（引用：{quoted}）' if quoted else title
        if sub == 5:
            return f'[链接] {title}'
        if sub == 19:
            # 转发的聊天记录：交办要求常在记录里（例：张三 11-16 转李四「最好星期三给我们」），只给标题会漏
            des = re.search(r'<des>(.*?)</des>', content, re.S)
            body = html.unescape(des.group(1)).strip().replace('\n', '；') if des else ''
            return f'[{title}] {body}'.strip()
        return f'[{title}]' if title else ''
    return ''


def file_path(store, title, ct):
    """微信 4.x 把收到的文件存在 msg/file/<消息年月>/，重名时加 (1)(2)；取名字对得上、修改时间离消息最近的那份。"""
    if not title:
        return None
    stem, ext = Path(title).stem, Path(title).suffix
    found = []
    for month in {dt.datetime.fromtimestamp(ct, BJ).strftime('%Y-%m'), dt.datetime.fromtimestamp(ct + 86400, BJ).strftime('%Y-%m')}:
        d = store.parent / 'msg' / 'file' / month
        if d.is_dir():
            found += [p for p in d.iterdir() if p.suffix == ext and re.fullmatch(re.escape(stem) + r'(\(\d+\))*', p.stem)]
    return min(found, key=lambda p: abs(p.stat().st_mtime - ct)) if found else None


LOOKBACK = 7
IMG_DIR = OUT_DIR / '图片'


def mat():
    # 放到用时再导入，避免与 微信材料收取.py 互相导入
    import 微信材料收取
    return 微信材料收取


def clean_images():
    IMG_DIR.mkdir(parents=True, exist_ok=True)
    for p in IMG_DIR.iterdir():
        if p.stat().st_mtime < time.time() - 14 * 86400:
            p.unlink()


def image_text(b, name):
    """('img', 会话, 时间, packed, 发送人) → 「[图片]（本机：解出的图）」，供交办时打开看。"""
    _, talker, ct, packed, _ = b
    m = mat()
    src, why, _ = m.locate_image(talker, ct, packed)
    if not src:
        return f'[图片]（{why}）'
    stem = str(IMG_DIR / f"{dt.datetime.fromtimestamp(ct, BJ):%m%d-%H%M%S}_{re.sub(r'[^\w（）()]+', '_', name)}")
    for ext in ('jpg', 'png', 'gif', 'bmp'):
        if Path(f'{stem}.{ext}').exists():
            return f'[图片]（本机：{stem}.{ext}）'
    try:
        data, ext = m.decode_dat(src.read_bytes())
    except Exception as e:  # noqa: BLE001
        return f'[图片]（解不开：{e}）'
    Path(f'{stem}.{ext}').write_bytes(data)
    return f'[图片]（本机：{stem}.{ext}）'


def funasr_ready():
    """语音转写要本机 FunASR（funasr-transcribe skill，地址见 config.json 的 工具.funasr）；没开就拉起来，空闲 10 分钟它自己关。"""
    import subprocess
    import urllib.request

    def up():
        try:
            urllib.request.urlopen(f'{FUNASR}/health', timeout=3)
            return True
        except OSError:
            return False
    if up():
        return True
    server = cfg.path('工具.funasr_server', './skills/funasr-transcribe/scripts/server.py')
    if not server or not server.exists():
        return False
    py = cfg.path('工具.funasr_python')
    subprocess.Popen([str(py) if py and py.exists() else sys.executable, str(server)], cwd=server.parent, creationflags=0x08000000,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(90):
        time.sleep(2)
        if up():
            return True
    return False


def transcribe_voices(voices):
    """voices: {(会话, create_time, local_id): None} → 填入转写文字；失败的留 None。"""
    if not voices:
        return
    import importlib.util
    import tempfile
    silk = cfg.path('微信.silk转写脚本')
    if not silk or not silk.exists():
        return
    spec = importlib.util.spec_from_file_location('ts', silk)
    ts = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ts)
    if not funasr_ready():
        return
    media = 微信库.connect('message/media_0.db')
    chat_id = {u: rid for rid, u in media.execute('SELECT rowid, user_name FROM Name2Id')}
    # silk 解码器是老 C 程序，路径不能有中文：临时目录用纯英文
    with tempfile.TemporaryDirectory(prefix='wxvoice_', dir='C:/Users/Public') as tmp:
        for key in voices:
            talker, ct, lid = key
            if talker not in chat_id:
                continue
            row = media.execute('SELECT voice_data FROM VoiceInfo WHERE chat_name_id=? AND local_id=? AND create_time=?',
                                (chat_id[talker], lid, ct)).fetchone()
            if not row or not row[0]:
                continue
            silk = Path(tmp) / f'{ct}_{lid}.silk'
            silk.write_bytes(row[0])
            try:
                wav = ts.decode_silk(str(silk), tmp)
                res = ts.transcribe(wav) if wav else None
                text = res.get('text') if isinstance(res, dict) else res
                voices[key] = (text or '').strip() or None
            except Exception:  # noqa: BLE001
                pass


def main():
    state = json.loads(STATE.read_text(encoding='utf-8')) if STATE.exists() else {}
    now = int(time.time())
    since = max(state.get('last', now - 86400), now - MAX_BACK)
    contact = 微信库.connect('contact/contact.db')
    names = {u: (r or n or u) for u, r, n in contact.execute('SELECT username, remark, nick_name FROM contact')}
    people = set(SENDERS) | {u for u, n in names.items() if n in COLLEAGUES}
    me_ids = {u for u, n in names.items() if n == ME} | {mat().account()['wxid']}
    store = 微信库.account()[1]
    chats = defaultdict(list)
    early = defaultdict(list)
    voices = {}
    back = since - LOOKBACK * 86400
    for i in range(10):
        rel = f'message/message_{i}.db'
        wal = store / (rel + '-wal')
        if not (store / rel).exists() or max((store / rel).stat().st_mtime, wal.stat().st_mtime if wal.exists() else 0) < back:
            continue
        con = 微信库.connect(rel)
        ids = {rid: u for rid, u in con.execute('SELECT rowid, user_name FROM Name2Id')}
        talker_of = {'Msg_' + hashlib.md5(u.encode()).hexdigest(): u for u in ids.values()}
        for (t,) in con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'Msg_%'").fetchall():
            talker = talker_of.get(t)
            if not talker:
                continue
            # 私聊全收（公众号、服务号、排除名单除外）；群在下面按是否与工作相关再筛
            if talker.startswith('gh_') or talker in ('filehelper', 'weixin', 'notifymessage') or excluded(names.get(talker, talker)):
                continue
            for lid, ct, lt, sid, content, packed in con.execute(
                    f'SELECT local_id, create_time, local_type, real_sender_id, message_content, packed_info_data FROM "{t}" '
                    f'WHERE create_time >= ? ORDER BY create_time', (back,)):
                body = text_of(lt, content)
                if not body:
                    continue
                sender = ids.get(sid, '')
                if body == '[图片]':
                    body = ('img', talker, ct, packed, sender)
                elif body.startswith('[文件] '):
                    p = file_path(store, body[5:], ct)
                    body += f'（本机：{p}）' if p else '（本机未下载）'
                elif lt == (19 << 32) | 49:
                    # 聊天记录里的附件不在 msg/file，不标本机路径会被当成没下载（曾有一条转发记录即如此）
                    head = {'uid': '', 'ct': ct, 'time': '', 'chat': '', 'sender': ''}
                    for it in mat().record_items(talker, ct, lid, mat().xml_of(content), packed, head):
                        body += f"；记录内{it['kind']}：{it['name'] if it['kind'] == '文件' else ''}" + \
                                (f"（本机：{it['src']}）" if it['src'] else f"（{it['reason']}）")
                if ct < since:
                    # 窗口之前只留对方发的材料：客户常先陆续发图片、表格，过几天才说要办什么
                    if sender not in me_ids and (isinstance(body, tuple) or body.startswith(('[文件] ', '[视频]'))
                                                 or '；记录内' in body):
                        early[talker].append((ct, names.get(sender, sender), sender, body))
                    continue
                if body == '[语音]':
                    voices[(talker, ct, lid)] = None
                    body = ('voice', talker, ct, lid)
                chats[talker].append((ct, names.get(sender, sender), sender, body))
    # 群：本人发过言、被 @、客户或同事发过言、或群名像工作群，才收；大群只留本人发言和被 @ 的前后几条
    trimmed = set()
    for talker, msgs in list(chats.items()):
        if not talker.endswith('@chatroom'):
            continue
        mine = [k for k, (_, _, s, b) in enumerate(msgs) if s in me_ids or (ME and isinstance(b, str) and f'@{ME}' in b)]
        known = any(s in people for _, _, s, _ in msgs)
        if not (mine or known or WORK_GROUP.search(names.get(talker, ''))):
            drop = msgs
            del chats[talker]
        elif len(msgs) > BIG_GROUP and not known:
            keep = {j for k in mine for j in range(max(0, k - 3), min(len(msgs), k + 4))}
            drop = [m for j, m in enumerate(msgs) if j not in keep]
            chats[talker] = [m for j, m in enumerate(msgs) if j in keep]
            trimmed.add(talker)
            if not chats[talker]:
                del chats[talker]
        else:
            drop = []
        for _, _, _, b in drop:
            if isinstance(b, tuple) and b[0] == 'voice':
                voices.pop(b[1:], None)
    transcribe_voices(voices)
    for talker, msgs in chats.items():
        chats[talker] = [(ct, n, s, (f"[语音] {voices.get(b[1:])}" if voices.get(b[1:]) else '[语音 未转写]')
                          if isinstance(b, tuple) and b[0] == 'voice' else b) for ct, n, s, b in msgs]
    # 图片解出来给本机路径，交办时能打开看；群里只解客户、同事发的
    clean_images()
    for talker in chats:
        group = talker.endswith('@chatroom')
        for box in (chats, early):
            box[talker] = [(ct, n, s, (image_text(b, n) if not group or s in people else '[图片]') if isinstance(b, tuple) else b)
                           for ct, n, s, b in box.get(talker, [])]
    out, kept = [], 0
    colleague_ids = {u for u, n in names.items() if n in COLLEAGUES}
    for talker, msgs in sorted(chats.items(), key=lambda kv: kv[1][-1][0], reverse=True):
        if talker.endswith('@chatroom'):
            title = f"群：{names.get(talker, talker)}" + ('（大群，只留本人发言和被 @ 的前后几条）' if talker in trimmed else '')
        else:
            who = SENDERS.get(talker)
            tag = (f"（{UNITS.get(who['unit'], who['unit'])}，经办人 {who.get('person') or '未填'}）" if who
                   else '（同事）' if talker in colleague_ids else '')
            title = f"私聊：{names.get(talker, talker)}{tag}"
        out.append(f'### {title}')
        if early.get(talker):
            out.append(f'（此前 {LOOKBACK} 天对方发来的材料，与本会话所提之事相关的都要打开看：）')
            out += [f"  {dt.datetime.fromtimestamp(ct, BJ):%m-%d %H:%M} {name}：{body}" for ct, name, _, body in early[talker]]
            out.append('（以下为本次摘取范围内的消息）')
        for ct, name, _, body in msgs:
            out.append(f"{dt.datetime.fromtimestamp(ct, BJ):%m-%d %H:%M} {name}：{body[:500]}")
            kept += 1
        out.append('')
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / f'{dt.datetime.now(BJ):%Y-%m-%d-%H%M}.md'
    head = [f'# 微信摘取 {dt.datetime.fromtimestamp(since, BJ):%m-%d %H:%M} 至 {dt.datetime.now(BJ):%m-%d %H:%M}', '',
            f'{sum(1 for l in out if l.startswith("### "))} 个会话，{kept} 条消息。', '']
    path.write_text('\n'.join(head + out) + '\n', encoding='utf-8')
    state['last'] = now
    STATE.parent.mkdir(exist_ok=True)
    STATE.write_text(json.dumps(state), encoding='utf-8')
    print(f'已写入 {path}：{sum(1 for l in out if l.startswith("### "))} 个会话，{kept} 条消息')


if __name__ == '__main__':
    if not cfg.enabled('微信'):
        print('微信模块未启用（config.json 的 微信.启用）')
        sys.exit(0)
    main()
