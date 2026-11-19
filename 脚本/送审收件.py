"""送审收件：微信收件 → 读本机微信库认发件人 → 已确认的送审人自动归档 → 交 Agent 出评审表初稿。

纯脚本，不调模型。由中台每半小时运行，最后一段输出即回执：
- 已确认送审人发来的件：按该单位命名规则归档，发 inbox.filed 事件（交 Agent 出批注版和评审表初稿）。
- 认不出发件人、对照表里没有、单位没配送审目录的：发 inbox.pending 事件（交 Agent 推卡片问）。
- 已确认送审人发来但微信还没下载的文件：推一张通知，同一条消息只提醒一次。
送审人对照在 送审人.json（由本人确认后的 送审人对照表.xlsx 生成）；没有这个文件就不自动归档。
"""
import datetime as dt
import hashlib
import json
import re
import subprocess
import sys
import time
from pathlib import Path

import zstandard

sys.stdout.reconfigure(encoding='utf-8')
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import cfg  # noqa: E402

if not cfg.enabled('顾问单位'):
    print('顾问单位模块未启用（config.json 的 顾问单位.启用）')
    sys.exit(0)

import 微信库  # noqa: E402

INBOX_SCRIPT = cfg.path('顾问单位.收件脚本')
sys.path.insert(0, str(INBOX_SCRIPT.parent))
from wechat_inbox import wechat_file_dirs  # noqa: E402

# 12:00、17:30 与微信待办的摘取同时跑，各用各的解密目录，否则 Windows 不让覆盖被对方打开的库
微信库.LIVE = 微信库.LIVE.with_name('wx_live_inbox')
UNITS = cfg.inbox().get('单位', {})
UNIT_DIRS = {k: v['目录'] for k, v in cfg.units().items()}
ME = cfg.ME
COLLEAGUES = cfg.COLLEAGUES
ADVISOR = cfg.path('目录.顾问单位')
MANIFEST = cfg.path('目录.送审收件清单')
SENDERS = HERE.parent / '送审人.json'
STATE = HERE.parent / '日志' / '送审收件状态.json'
DOC = re.compile(r'\.(docx?|pdf|wps|zip|rar|7z)$', re.I)
BJ = dt.timezone(dt.timedelta(hours=8))
LOOKBACK = 3 * 86400
# 启用前的积压只在晨报列出，不自动处理
START = cfg.get('顾问单位.送审收件起始日', '') or ''


def run(args):
    return subprocess.run([sys.executable, str(INBOX_SCRIPT), *args], capture_output=True, text=True,
                          encoding='utf-8', errors='replace')


def base(name):
    """微信重名文件会被存成「名字(1).docx」，比对时去掉这个序号。"""
    p = Path(name)
    return re.sub(r'\(\d+\)$', '', p.stem).strip() + p.suffix.lower()


def file_messages():
    contact = 微信库.connect('contact/contact.db')
    names = {u: (r or n or u) for u, r, n in contact.execute('SELECT username, remark, nick_name FROM contact')}
    since = int(time.time()) - LOOKBACK
    store = 微信库.account()[1]
    out = []
    for i in range(10):
        rel = f'message/message_{i}.db'
        if not (store / rel).exists() or (store / rel).stat().st_mtime < since:
            continue
        con = 微信库.connect(rel)
        ids = {rid: u for rid, u in con.execute('SELECT rowid, user_name FROM Name2Id')}
        talker_of = {'Msg_' + hashlib.md5(u.encode()).hexdigest(): u for u in ids.values()}
        for (t,) in con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'Msg_%'").fetchall():
            talker = talker_of.get(t)
            for lid, ct, lt, sid, content in con.execute(
                    f'SELECT local_id, create_time, local_type, real_sender_id, message_content FROM "{t}" '
                    f'WHERE create_time >= ? AND (local_type & 4294967295) = 49', (since,)):
                if lt >> 32 not in (6, 74):
                    continue
                if isinstance(content, bytes):
                    try:
                        content = zstandard.ZstdDecompressor().decompress(content, max_output_size=1 << 22).decode('utf-8', 'ignore')
                    except Exception:  # noqa: BLE001
                        continue
                m = re.search(r'<title>(.*?)</title>', content or '', re.S)
                title = (m.group(1).strip() if m else '')
                if not DOC.search(title):
                    continue
                sender = ids.get(sid, '')
                out.append({'key': f'{t}:{lid}', 'time': ct, 'title': title, 'sender': sender, 'talker': talker,
                            'sender_name': names.get(sender, sender),
                            'chat': names.get(talker, talker) if talker and talker.endswith('@chatroom') else '私聊'})
    return out


def sha1(path):
    h = hashlib.sha1()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(1 << 20), b''):
            h.update(block)
    return h.hexdigest()


def handled(item, msgs, senders):
    """本人已经处理过的件返回依据，否则返回空串。

    本人常直接在微信里审完回评审表、手工归档，收件清单不知道，会一直当成待归档。两条依据任一成立即算已处理：
    收件后本人向同一会话发过评审表；同一份文件已在该单位送审目录里。
    """
    got = dt.datetime.strptime(item['received'], '%Y-%m-%d %H:%M').replace(tzinfo=BJ).timestamp()
    origin = [m for m in msgs if m.get('sender') == item.get('sender') and base(m['title']) == base(item['name'])]
    talkers = {m['talker'] for m in origin}
    stem = re.sub(r'\(\d+\)$', '', Path(item['name']).stem).strip()
    for m in msgs:
        # 本人或同事在同一会话回了评审表，或回了这份文件的审核版、修订版
        if (m['sender_name'] == ME or m['sender_name'] in COLLEAGUES) and m['time'] >= got - 3600 and \
                (m['talker'] in talkers or m['talker'] == item.get('sender')) and \
                ('评审表' in m['title'] or (len(stem) >= 4 and stem in m['title'])):
            return f"{m['sender_name']} 已于 {dt.datetime.fromtimestamp(m['time'], BJ):%m-%d %H:%M} 回过「{m['title']}」"
    unit = item.get('unit') or (senders.get(item.get('sender') or '') or {}).get('unit')
    dest = UNITS.get(unit or '', {}).get('送审目录')
    staged = Path(item.get('staged') or '')
    if dest and staged.exists():
        root = ADVISOR / UNIT_DIRS.get(unit, '') / dest
        size, digest = staged.stat().st_size, item.get('sha1')
        for p in root.rglob('*') if root.exists() else []:
            if p.is_file() and p.stat().st_size == size and sha1(p) == digest:
                return f'同一份文件已在 {p.parent}'
    return ''


def main():
    col = run(['collect', '--days', '2'])
    if col.returncode != 0:
        print(f'没跑成：微信收件出错（退出码 {col.returncode}）{col.stderr.strip()[-300:]}')
        sys.exit(1)
    senders = json.loads(SENDERS.read_text(encoding='utf-8')) if SENDERS.exists() else {}
    state = json.loads(STATE.read_text(encoding='utf-8')) if STATE.exists() else {'warned': []}
    try:
        msgs = file_messages()
        wx_error = ''
    except Exception as e:  # noqa: BLE001
        msgs, wx_error = [], f'读微信库失败：{e}'

    # 已登记送审人发来、清单里还没有的件按文件名点名收，不看关键词
    # （2026-11-18 某顾问单位的《业务约定书》不含"合同""协议"被筛掉，却报成"还没下载"）
    items = json.loads(MANIFEST.read_text(encoding='utf-8')) if MANIFEST.exists() else []
    have = {base(i['name']) for i in items}
    stems = {re.sub(r'\(\d+\)$', '', Path(m['title']).stem).strip() for m in msgs
             if (senders.get(m['sender']) or {}).get('unit') and base(m['title']) not in have}
    if stems:
        args = ['collect', '--days', '3', '--any']
        for s in sorted(stems):
            args += ['--match', s]
        run(args)

    items = json.loads(MANIFEST.read_text(encoding='utf-8')) if MANIFEST.exists() else []
    changed = False
    for it in items:
        if it.get('status') != '待归档' or it.get('sender') or str(it.get('received', '')) < START:
            continue
        got = dt.datetime.strptime(it['received'], '%Y-%m-%d %H:%M').replace(tzinfo=BJ).timestamp()
        near = [m for m in msgs if base(m['title']) == base(it['name']) and abs(m['time'] - got) < 86400]
        if near:
            m = min(near, key=lambda m: abs(m['time'] - got))
            it.update(sender=m['sender'], sender_name=m['sender_name'], chat=m['chat'])
            changed = True
    if changed:
        MANIFEST.write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding='utf-8')

    done_lines = []
    for it in items:
        if it.get('status') == '待归档' and str(it.get('received', '')) >= START:
            why = handled(it, msgs, senders)
            if why:
                it.update(status='已人工处理', note=why)
                done_lines.append(f"{it['name']}：{why}")
                changed = True
    if changed:
        MANIFEST.write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding='utf-8')

    events, notices, filed_lines = [], [], []
    pending = [i for i in items if i.get('status') == '待归档' and str(i.get('received', '')) >= START]
    # 同一发件人两小时内的件算一批
    batches = []
    for it in sorted(pending, key=lambda i: (i.get('sender') or '', i['received'])):
        last = batches[-1] if batches else None
        if last and last[0].get('sender') == it.get('sender') and it.get('sender') and \
                (dt.datetime.strptime(it['received'], '%Y-%m-%d %H:%M') -
                 dt.datetime.strptime(last[-1]['received'], '%Y-%m-%d %H:%M')) <= dt.timedelta(hours=2):
            last.append(it)
        else:
            batches.append([it])
    for batch in batches:
        who = senders.get(batch[0].get('sender') or '')
        ids = ','.join(str(i['id']) for i in batch)
        unit = who and who.get('unit')
        if who and unit and who.get('person') and UNITS.get(unit, {}).get('送审目录') \
                and not any(i.get('loose') for i in batch):
            res = run(['file', '--ids', ids, '--person', who['person'], '--unit', unit])
            target = next((l.split('目标文件夹: ', 1)[1].split('（')[0] for l in res.stdout.splitlines()
                           if l.startswith('目标文件夹: ')), '')
            if res.returncode == 0 and target:
                filed_lines.append(f"{who['person']}（{unit}）{len(batch)} 件 → {target}")
                sess = next((l.split(': ', 1)[1] for l in res.stdout.splitlines() if l.startswith('送审会话: ')), '{}')
                events.append({'id': f'filed-{ids}', 'type': 'inbox.filed', 'subject': 'inbox',
                               'summary': f"{who['person']} 送审 {len(batch)} 件"[:200],
                               'detail': json.dumps({'ids': [i['id'] for i in batch], 'unit': unit, 'person': who['person'],
                                                     'folder': target, 'files': [i['name'] for i in batch],
                                                     '会话留底': json.loads(sess)},
                                                    ensure_ascii=False)[:4000]})
                continue
        for i in batch:
            events.append({'id': f"inbox-{i['id']}", 'type': 'inbox.pending', 'subject': 'inbox',
                           'summary': str(i.get('name', ''))[:200], 'detail': json.dumps(i, ensure_ascii=False)[:4000]})

    staged = {base(i['name']) for i in items}
    # 本地已下载的（含已手工归档、收件跳过的）不算"还没下载"
    downloaded = {base(p.name) for d in wechat_file_dirs() for p in Path(d).glob('*/*')}
    for m in msgs:
        who = senders.get(m['sender'])
        if who and who.get('unit') and base(m['title']) not in staged and base(m['title']) not in downloaded \
                and m['key'] not in state['warned'] and time.time() - m['time'] > 1800:
            notices.append(f"{m['sender_name']}：{m['title']}")
            state['warned'].append(m['key'])
    state['warned'] = state['warned'][-500:]
    STATE.parent.mkdir(exist_ok=True)
    STATE.write_text(json.dumps(state, ensure_ascii=False), encoding='utf-8')

    receipt = {}
    if events:
        receipt['events'] = events
    if notices:
        receipt['notice'] = [{'title': f'微信里有 {len(notices)} 份送审文件还没下载',
                              'text': '请在微信里点开下载，下一轮会自动收进来：\n' + '\n'.join(notices[:15]),
                              'key': f"missing-{dt.datetime.now(BJ):%Y%m%d%H%M}"}]
    if wx_error:
        receipt.setdefault('notice', []).append({'title': '送审收件读不了微信库', 'text': wx_error +
                                                 '\n微信重新登录或升级后密钥可能变了，需要用 WeChatDataAnalysis 重新取一次密钥。',
                                                 'key': 'wx-db-error'})
    if not receipt:
        print('没事可做：没有新的送审件')
        return
    head = []
    if filed_lines:
        head.append('自动归档 ' + '；'.join(filed_lines))
    if any(e['type'] == 'inbox.pending' for e in events):
        head.append(f"{sum(e['type'] == 'inbox.pending' for e in events)} 件待确认")
    print('，'.join(head) or '有送审收件通知')
    print()
    print('```agent-autoloop')
    print(json.dumps(receipt, ensure_ascii=False))
    print('```')


if __name__ == '__main__':
    main()
