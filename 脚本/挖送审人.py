"""从近一年微信记录挖「发件人 → 顾问单位、经办人」对照，生成待本人确认的 送审人对照表.xlsx。只读微信库。

依据三类信号：备注括号里的单位简称或关联主体；所在群名；我发出的「法律评审表…-经办人」文件及其收件人。
"""
import datetime as dt
import hashlib
import re
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

import zstandard
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill

sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, str(Path(__file__).resolve().parent))
import cfg  # noqa: E402

if not cfg.enabled('顾问单位'):
    print('顾问单位模块未启用（config.json 的 顾问单位.启用）')
    sys.exit(0)

import 微信库  # noqa: E402

UNITS = cfg.units()
INBOX_CFG = cfg.inbox().get('单位', {})
OUT = Path(__file__).resolve().parent.parent / '送审人对照表.xlsx'
ME = cfg.ME
COLLEAGUES = cfg.COLLEAGUES
DOC = re.compile(r'\.(docx?|pdf|wps|zip|rar|7z)$', re.I)
FORM = re.compile(r'法律评审表[\d.\-]*-([一-鿿]{2,4})')
since = int(time.time()) - 365 * 86400


def unit_aliases():
    """每个单位的可识别名称：名称、别名、目录名去编号、全称、关联主体，以及它们去掉地名和公司后缀的简称。"""
    out = []
    for key, u in UNITS.items():
        names = [u.get('名称', ''), re.sub(r'^\d+\.', '', u.get('目录', ''))] + u.get('别名', [])
        names += INBOX_CFG.get(key, {}).get('全称', []) + INBOX_CFG.get(key, {}).get('关联主体', [])
        for n in names:
            for v in {n, re.sub(rf'^({cfg.place_pattern()})|(集团)?(股份)?有限(责任)?公司$|公司$', '', n)}:
                if len(v) >= 2:
                    out.append((v, key))
    return sorted(set(out), key=lambda x: -len(x[0]))


ALIASES = unit_aliases()


def guess_unit(text):
    if not text:
        return None, ''
    for alias, key in ALIASES:
        if alias in text:
            return key, alias
    # 备注里的简称可能比登记名短，例如「植物园」对「某市某某植物园有限公司」
    for part in re.findall(r'[（(]([^）)]{2,10})[）)]', text):
        for alias, key in ALIASES:
            if part in alias:
                return key, part
    return None, ''


def person_name(display):
    name = re.sub(r'[（(].*?[）)]', '', display or '')
    name = re.sub(r'[^一-鿿]', '', name)
    for alias, _ in ALIASES:
        name = name.replace(alias, '')
    name = re.sub(r'集团|公司|财务|融资部|法务|办公室|经理|主任|部', '', name)
    return name if 2 <= len(name) <= 4 else ''


def main():
    contact = 微信库.connect('contact/contact.db')
    names = {u: (r or n or u) for u, r, n in contact.execute('SELECT username, remark, nick_name FROM contact')}
    me = next((u for u, n in names.items() if n == ME), None)
    senders = defaultdict(lambda: {'files': [], 'chats': Counter(), 'forms': Counter()})
    store = 微信库.account()[1]
    for i in range(10):
        rel = f'message/message_{i}.db'
        if not (store / rel).exists() or (store / rel).stat().st_mtime < since:
            continue
        con = 微信库.connect(rel)
        ids = {rid: u for rid, u in con.execute('SELECT rowid, user_name FROM Name2Id')}
        talker_of = {'Msg_' + hashlib.md5(u.encode()).hexdigest(): u for u in ids.values()}
        for (t,) in con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'Msg_%'").fetchall():
            talker = talker_of.get(t)
            if not talker:
                continue
            for ct, lt, sid, content in con.execute(
                    f'SELECT create_time, local_type, real_sender_id, message_content FROM "{t}" '
                    f'WHERE create_time >= ? AND (local_type & 4294967295) = 49', (since,)):
                if lt >> 32 not in (6, 74):
                    continue
                if isinstance(content, bytes):
                    try:
                        content = zstandard.ZstdDecompressor().decompress(content, max_output_size=1 << 22).decode('utf-8', 'ignore')
                    except Exception:  # noqa: BLE001
                        continue
                m = re.search(r'<title>(.*?)</title>', content or '', re.S)
                title = m.group(1).strip() if m else ''
                sender = ids.get(sid, '')
                if sender == me or names.get(sender) == ME:
                    f = FORM.search(title)
                    if f and not talker.endswith('@chatroom'):
                        senders[talker]['forms'][f.group(1)] += 1
                    continue
                if not DOC.search(title) or sender in ('filehelper', '') or names.get(sender, '') in COLLEAGUES:
                    continue
                s = senders[sender]
                s['files'].append((ct, title))
                s['chats'][names.get(talker, talker) if talker.endswith('@chatroom') else '私聊'] += 1

    rows = []
    for wxid, s in senders.items():
        display = names.get(wxid, wxid)
        if display in COLLEAGUES or display == ME or not (s['files'] or s['forms']):
            continue
        unit, why = guess_unit(display)
        basis = f'备注含「{why}」' if unit else ''
        if not unit:
            for chat, _ in s['chats'].most_common():
                if chat != '私聊':
                    unit, why = guess_unit(chat)
                    if unit:
                        basis = f'群「{chat}」含「{why}」'
                        break
        person = s['forms'].most_common(1)[0][0] if s['forms'] else person_name(display)
        if s['forms']:
            basis += ('；' if basis else '') + f"我发给他的评审表署名「{person}」"
        sample = '、'.join(t for _, t in sorted(s['files'], reverse=True)[:3])
        last = dt.datetime.fromtimestamp(max((c for c, _ in s['files']), default=0)).strftime('%Y-%m-%d') if s['files'] else ''
        rows.append([display, UNITS[unit]['名称'] if unit else '', person, basis, len(s['files']), last, sample,
                     '、'.join(c for c, _ in s['chats'].most_common(3)), wxid, unit or ''])
    # 有单位线索或收过评审表的排前面，其余按发来文件数
    rows.sort(key=lambda r: (not r[1], not r[3], -r[4]))

    wb = Workbook()
    ws = wb.active
    ws.title = '送审人'
    head = ['微信名', '推断单位', '推断经办人', '依据', '近一年发来文件数', '最近一次', '示例文件', '来自', '微信号', '单位键',
            '确认单位', '确认经办人']
    ws.append(head)
    for r in rows:
        ws.append(r + [r[1], r[2]])
    for c in ws[1]:
        c.font = Font(bold=True)
    for row in ws.iter_rows(min_row=2, min_col=11, max_col=12):
        for c in row:
            c.fill = PatternFill('solid', fgColor='FCE9D9')
    for col, w in zip('ABCDEFGHIJKL', (22, 16, 10, 40, 10, 11, 60, 30, 26, 14, 16, 12)):
        ws.column_dimensions[col].width = w
    ws.freeze_panes = 'B2'
    ws.auto_filter.ref = f'A1:L{len(rows) + 1}'
    note = wb.create_sheet('说明')
    for line in ['橙色两列是最终生效的：「确认单位」写单位名称（与推断单位同一写法），不是送审人就清空；「确认经办人」写评审表上署的名字。',
                 '只有确认单位不为空的人，发来的文件才会自动归档；其余照旧推卡片问你。',
                 f"单位名称可选：{'、'.join(u['名称'] for u in UNITS.values())}",
                 '改完保存，告诉 Agent「对照表好了」。不要改「微信号」列。']:
        note.append([line])
    note.column_dimensions['A'].width = 120
    wb.save(OUT)
    print(f'共 {len(rows)} 人，推出单位 {sum(1 for r in rows if r[1])} 人，有评审表署名 {sum(1 for r in rows if "评审表" in r[3])} 人；已写 {OUT}')


if __name__ == '__main__':
    main()
