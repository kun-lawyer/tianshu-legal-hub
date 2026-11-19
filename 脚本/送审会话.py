"""送审会话留底：送审件归档后，把送审人这一轮在微信里发的全部文件、图片补进送审文件夹（收件只收合同类文件名，
请示、函件、说明会漏），并把这段对话渲染成「客户要求（M.D）.png」放进同一文件夹，起草时照着它回应客户诉求。

用法: python 送审会话.py --folder 送审文件夹 --since "2026-11-16 15:18" [--sender wxid] [--name 已收文件名] [--hours 12]
  窗口：since 前 2 小时至后 --hours 小时（不超过现在）。认不出 sender 时用 --name 在微信库里按文件名反查。
  可重复运行：已在文件夹里的同名同大小文件不再复制，图片覆盖重画。最后一行输出「送审会话: {JSON}」。
"""
import argparse
import datetime as dt
import hashlib
import json
import re
import shutil
import sys
import time
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

if sys.stdout:
    sys.stdout.reconfigure(encoding='utf-8')
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import cfg  # noqa: E402
import 微信库  # noqa: E402
import 微信材料收取 as 材料  # noqa: E402
from 微信消息摘取 import text_of  # noqa: E402

# 由送审收件归档时调起，那时送审收件还开着 wx_live_inbox 里的库，这里另用一个目录
if __name__ == '__main__':
    微信库.LIVE = 微信库.LIVE.with_name('wx_live_session')
BJ = dt.timezone(dt.timedelta(hours=cfg.TZ_HOURS))
FONT = str(cfg.path('工具.中文字体') or 'C:/Windows/Fonts/msyh.ttc')
BOLD = str(cfg.path('工具.中文粗体') or 'C:/Windows/Fonts/msyhbd.ttc')
ME = cfg.ME


def find_sender(name, around):
    """按文件名在 around 前后一天的文件消息里反查 (发件人, 会话)。"""
    key = re.sub(r'\(\d+\)$', '', Path(name).stem).strip()
    store = 微信库.account()[1]
    hits = []
    for i in range(10):
        rel = f'message/message_{i}.db'
        if not (store / rel).exists():
            continue
        con = 微信库.connect(rel)
        try:
            ids = {rid: u for rid, u in con.execute('SELECT rowid, user_name FROM Name2Id')}
            talker_of = {'Msg_' + hashlib.md5(u.encode()).hexdigest(): u for u in ids.values()}
            for (t,) in con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'Msg_%'").fetchall():
                for ct, lt, sid, mc in con.execute(
                        f'SELECT create_time, local_type, real_sender_id, message_content FROM "{t}" '
                        f'WHERE create_time BETWEEN ? AND ? AND (local_type & 4294967295) = 49',
                        (around - 86400, around + 86400)):
                    if key and key in text_of(lt, mc) and t in talker_of:
                        hits.append((abs(ct - around), ids.get(sid, ''), talker_of[t]))
        finally:
            con.close()
    return min(hits)[1:] if hits else (None, None)


def chat_rows(talker, start, end):
    """该会话窗口内全部消息：(时间戳, 发送人 wxid, 文字)。"""
    table = 'Msg_' + hashlib.md5(talker.encode()).hexdigest()
    rows = []
    store = 微信库.account()[1]
    for i in range(10):
        rel = f'message/message_{i}.db'
        if not (store / rel).exists():
            continue
        con = 微信库.connect(rel)
        try:
            if not con.execute("SELECT 1 FROM sqlite_master WHERE name=?", (table,)).fetchone():
                continue
            ids = {rid: u for rid, u in con.execute('SELECT rowid, user_name FROM Name2Id')}
            for ct, lt, sid, mc in con.execute(
                    f'SELECT create_time, local_type, real_sender_id, message_content FROM "{table}" '
                    f'WHERE create_time BETWEEN ? AND ? ORDER BY create_time', (start, end)):
                t = text_of(lt, mc)
                if t and not (lt >> 32 == 74):
                    rows.append((ct, ids.get(sid, ''), t))
        finally:
            con.close()
    return sorted(set(rows))


def wrap(text, font, width):
    """按像素宽度折行；行首遇到中文标点时并回上一行。"""
    lines, cur = [], ''
    for ch in text:
        if font.getlength(cur + ch) > width and cur and ch not in '，。、；：！？）》”':
            lines.append(cur)
            cur = ch
        else:
            cur += ch
    return lines + [cur]


def render(rows, title, out, me):
    f, fb, fs = ImageFont.truetype(FONT, 26), ImageFont.truetype(BOLD, 30), ImageFont.truetype(FONT, 20)
    width, pad = 1000, 40
    blocks = []
    for ct, sender, text in rows:
        mine = sender in me
        name = (ME or '我') if mine else 材料.display(sender) if sender else ''
        lines = [l for para in text.splitlines() for l in wrap(para, f, 760)]
        blocks.append((f"{name}  {dt.datetime.fromtimestamp(ct, BJ):%Y-%m-%d %H:%M:%S}", lines, mine))
    h = pad * 2 + 70 + sum(30 + len(b[1]) * 38 + 40 for b in blocks)
    img = Image.new('RGB', (width, h), '#f5f5f5')
    d = ImageDraw.Draw(img)
    d.text((pad, pad), title, font=fb, fill='#222')
    y = pad + 70
    for head, lines, mine in blocks:
        d.text((pad, y), head, font=fs, fill='#888')
        y += 30
        bw = max(d.textlength(l, font=f) for l in lines) + 40
        bh = len(lines) * 38 + 20
        d.rounded_rectangle((pad, y, pad + bw, y + bh), 10, fill='#d7f0c8' if mine else '#ffffff', outline='#ddd')
        for k, l in enumerate(lines):
            d.text((pad + 20, y + 10 + k * 38), l, font=f, fill='#111')
        y += bh + 20
    d.text((pad, h - 34), f'据本机微信数据库生成，{dt.datetime.now(BJ):%Y-%m-%d %H:%M}', font=fs, fill='#aaa')
    img.save(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--folder', required=True)
    ap.add_argument('--since', required=True)
    ap.add_argument('--sender')
    ap.add_argument('--talker')
    ap.add_argument('--name')
    ap.add_argument('--hours', type=float, default=12)
    a = ap.parse_args()
    folder = Path(a.folder)
    t0 = dt.datetime.strptime(a.since, '%Y-%m-%d %H:%M').replace(tzinfo=BJ).timestamp()
    sender, talker = a.sender, a.talker
    if not sender and a.name:
        sender, talker = find_sender(a.name, t0)
    if not sender:
        print('送审会话: ' + json.dumps({'error': '认不出发件人，未留底'}, ensure_ascii=False))
        return
    talker = talker or sender
    start, end = int(t0 - 7200), int(min(time.time(), t0 + a.hours * 3600))

    added, missing = [], []
    have = {(p.name, p.stat().st_size) for p in folder.rglob('*') if p.is_file()}
    have_base = {re.sub(r'\(\d+\)$', '', p.stem) + p.suffix.lower() for p in folder.rglob('*') if p.is_file()}
    for it in 材料.session(材料.messages)([talker], start):
        if it['ct'] > end or (talker.endswith('@chatroom') and it['sender'] != 材料.display(sender)):
            continue
        if not it['src']:
            missing.append(f"{it['name']}（{it['reason']}）")
            continue
        src = Path(it['src'])
        if it['kind'] == '文件':
            stem = Path(it['name'])
            if (it['name'], src.stat().st_size) in have or stem.stem + stem.suffix.lower() in have_base:
                continue
            out = folder / it['name']
            shutil.copy2(src, out)
        elif it['kind'] == '图片':
            data, ext = 材料.decode_dat(src.read_bytes())
            out = folder / (Path(it['name']).stem + '.' + ext)
            if out.exists():
                continue
            out.write_bytes(data)
        else:
            out = folder / it['name']
            if out.exists():
                continue
            shutil.copy2(src, out)
        added.append(out.name)

    acct = 材料.session(材料.account)()
    me = {u for u, r, n in 材料.session(材料.contacts)() if ME and ME in (r, n)} | {acct['wxid']}
    rows = [r for r in chat_rows(talker, start, end)
            if not talker.endswith('@chatroom') or r[1] == sender or r[1] in me]
    day = dt.datetime.fromtimestamp(t0, BJ)
    img = folder / f'客户要求（{day.month}.{day.day}）.png'
    who = 材料.session(材料.display)(sender)
    render(rows, f'微信会话：{who}  {day:%Y-%m-%d}', img, me)
    texts = [t for _, s, t in rows if s not in me and not t.startswith('[')]
    print('送审会话: ' + json.dumps({'image': str(img), 'added': added, 'missing': missing,
                                    'client_text': texts}, ensure_ascii=False))


if __name__ == '__main__':
    if not cfg.enabled('微信'):
        print('微信模块未启用（config.json 的 微信.启用）')
        sys.exit(0)
    main()
