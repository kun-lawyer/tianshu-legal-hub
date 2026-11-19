"""改稿回流：中台出的稿，本人改过以后，把改了什么摘出来，供每周总结成写作规则。纯脚本。

  python 改稿回流.py register <文件...> [--rule 规则名]   出稿后立即登记，存一份底稿文字
  python 改稿回流.py snap                                 补登：扫中台运行记录里出过的稿（定时跑，防手册漏登）
  python 改稿回流.py diff [--days 14]                     对比底稿与本人改后的版本，写 改稿回流/素材-<日期>.md
  python 改稿回流.py done                                 本轮素材已总结，标为已看（下次只报新改动）

本人改后的版本：同一文件被改写，或同目录下登记之后出现的、名字相近的 docx（如「（修改）」「（定稿）」「v2」）。
只读卷宗，不改任何卷宗文件。底稿与登记放 日志/改稿回流/。
"""
import datetime as dt
import difflib
import hashlib
import json
import re
import sqlite3
import sys
from pathlib import Path

import docx

if sys.stdout:
    sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, str(Path(__file__).resolve().parent))
import cfg  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
HOME = ROOT / '日志' / '改稿回流'
REG = HOME / '登记.json'
SNAPS = HOME / '底稿'
OUT = ROOT / '改稿回流'
HUB_DB = cfg.path('中台.数据库', '~/.agent-autoloop/hub.db')
BJ = dt.timezone(dt.timedelta(hours=8))
EXT = ('.docx', '.md')
SKIP = re.compile(r'^~\$|（带回答）|（自己看）')
PATH = re.compile(r'[A-Za-z]:\\[^`\'"<>|\n*?：:，。；、》《]+')
NAME = re.compile(r'《([^《》\n]+?\.(?:docx|md))》|([^\s`《》\\/：:，,。；]+?\.(?:docx|md))')


def now():
    return dt.datetime.now(BJ)


def load():
    return json.loads(REG.read_text(encoding='utf-8')) if REG.exists() else {'files': {}, 'last_run': 0}


def save(reg):
    HOME.mkdir(parents=True, exist_ok=True)
    tmp = REG.with_suffix('.tmp')
    tmp.write_text(json.dumps(reg, ensure_ascii=False, indent=1), encoding='utf-8')
    tmp.replace(REG)


def digest(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def text_of(p):
    p = Path(p)
    if p.suffix.lower() == '.md':
        return [l.strip() for l in p.read_text(encoding='utf-8', errors='replace').splitlines() if l.strip()]
    d = docx.Document(str(p))
    lines = [x.text.strip() for x in d.paragraphs if x.text.strip()]
    for t in d.tables:
        for row in t.rows:
            cells = []
            for c in row.cells:
                if c.text.strip() and c.text.strip() not in cells:
                    cells.append(c.text.strip())
            if cells:
                lines.append('｜'.join(cells))
    return lines


def register(paths, rule=''):
    reg, n = load(), 0
    for p in paths:
        p = Path(p).resolve()
        if not p.exists() or p.suffix.lower() not in EXT or SKIP.search(p.name):
            continue
        # 中台自己的晨报、晚报、日志不是交给本人改的稿
        if ROOT in p.parents and (ROOT / '助理产出') not in p.parents:
            continue
        key = str(p)
        if key in reg['files']:
            continue  # 已登记的不覆盖：此后的变化可能就是本人改的
        try:
            lines = text_of(p)
        except Exception as e:  # noqa: BLE001
            print(f'读不了 {p.name}：{e}')
            continue
        snap = SNAPS / (hashlib.sha1(key.encode('utf-8')).hexdigest()[:16] + '.txt')
        SNAPS.mkdir(parents=True, exist_ok=True)
        snap.write_text('\n'.join(lines), encoding='utf-8')
        reg['files'][key] = {'hash': digest(p), 'snap': snap.name, 'at': now().isoformat(timespec='minutes'),
                             'mtime': p.stat().st_mtime, 'rule': rule, 'reviewed': ''}
        n += 1
    save(reg)
    return n


def cmd_register(args):
    rule = ''
    if '--rule' in args:
        i = args.index('--rule')
        rule = args[i + 1]
        del args[i:i + 2]
    print(f'已登记 {register(args, rule)} 份')


def ts(v):
    if isinstance(v, (int, float)):
        return v / 1000 if v > 1e11 else v
    return dt.datetime.fromisoformat(str(v).replace('Z', '+00:00')).timestamp() if v else 0


def cmd_snap(_):
    """从中台运行记录补登：回执里提到的目录和文件，运行期间新写出的稿都登记。"""
    reg = load()
    con = sqlite3.connect(f'file:{HUB_DB}?mode=ro', uri=True)
    rows = [json.loads(d) for (d,) in con.execute("select data from projections where collection='runs'")]
    rows = [r for r in rows if r.get('id', 0) > reg.get('last_run', 0) and r.get('state') == 'done' and r.get('finished_at')]
    n = 0
    for r in sorted(rows, key=lambda r: r['id']):
        text = f"{r.get('summary') or ''}\n{r.get('last_result') or ''}"
        start, end = ts(r['created_at']) - 60, ts(r['finished_at']) + 300
        dirs, files = set(), set()
        for m in PATH.finditer(text):
            p = Path(m.group(0).strip().rstrip('\\'))
            if p.suffix.lower() in EXT:
                files.add(p)
                dirs.add(p.parent)
            elif p.is_dir():
                dirs.add(p)
        names = {a or b for a, b in NAME.findall(text)}
        for d in dirs:
            if not d.is_dir():
                continue
            for f in d.iterdir():
                if f.suffix.lower() in EXT and (f.name in names or start <= f.stat().st_mtime <= end):
                    files.add(f)
        # 运行结束后又被改过的，已不是原稿，没有底稿可比，不登记
        n += register([f for f in files if f.exists() and f.stat().st_mtime <= end], r.get('rule') or '')
        reg = load()
        reg['last_run'] = max(reg.get('last_run', 0), r['id'])
        save(reg)
    print(f'没事可做：没有新出的稿' if not n else f'补登 {n} 份')


def versions(key, info, reg):
    """本人改后的版本：原文件被改写，或同目录登记之后出现的相近文件。"""
    p, out = Path(key), []
    if p.exists() and digest(p) != info['hash']:
        out.append(p)
    stem = re.sub(r'（初稿）|（定稿）|\(\d\)|v\d+$', '', p.stem)
    if p.parent.is_dir():
        for f in p.parent.iterdir():
            if f == p or f.suffix.lower() not in EXT or SKIP.search(f.name) or str(f) in reg['files']:
                continue
            if f.stat().st_mtime > info['mtime'] + 60 and difflib.SequenceMatcher(None, stem, f.stem).ratio() >= 0.6:
                out.append(f)
    return out


def inline(a, b):
    sm = difflib.SequenceMatcher(None, a, b)
    out = []
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op == 'equal':
            seg = a[i1:i2]
            out.append(seg if len(seg) <= 24 else seg[:10] + '…' + seg[-10:])
        if op in ('delete', 'replace'):
            out.append(f'[-{a[i1:i2]}-]')
        if op in ('insert', 'replace'):
            out.append(f'{{+{b[j1:j2]}+}}')
    return ''.join(out)


def compare(old, new):
    sm = difflib.SequenceMatcher(None, old, new, autojunk=False)
    dels, adds, mods = [], [], []
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op == 'delete':
            dels += old[i1:i2]
        elif op == 'insert':
            adds += new[j1:j2]
        elif op == 'replace':
            a, b = old[i1:i2], new[j1:j2]
            # 一一对得上的算改写，其余算删、增
            for x in a:
                best = max(b, key=lambda y: difflib.SequenceMatcher(None, x, y).ratio(), default=None)
                if best is not None and difflib.SequenceMatcher(None, x, best).ratio() >= 0.5:
                    mods.append((x, best))
                    b = [y for y in b if y is not best]
                else:
                    dels.append(x)
            adds += b
    return dels, adds, mods


def cmd_diff(args):
    days = int(args[args.index('--days') + 1]) if '--days' in args else 14
    reg = load()
    since = now() - dt.timedelta(days=days)
    md, hits = [], 0
    for key, info in reg['files'].items():
        if dt.datetime.fromisoformat(info['at']) < since - dt.timedelta(days=30):
            continue
        for v in versions(key, info, reg):
            try:
                if dt.datetime.fromtimestamp(v.stat().st_mtime, BJ) < since:
                    continue
                h = digest(v)
                if h in (info.get('reviewed') or '').split(','):
                    continue
                old = (SNAPS / info['snap']).read_text(encoding='utf-8').splitlines()
                new = text_of(v)
            except Exception as e:  # noqa: BLE001
                md.append(f'- 读不了 {v}：{e}')
                continue
            dels, adds, mods = compare(old, new)
            if not (dels or adds or mods):
                continue
            hits += 1
            total = sum(map(len, old)) or 1
            changed = sum(len(x) for x in dels) + sum(len(x) for x in adds) + sum(abs(len(a) - len(b)) + 10 for a, b in mods)
            md += [f'## {hits}. {Path(key).name} → {v.name}', '',
                   f'- 位置：{v.parent}', f'- 出稿规则：{info.get("rule") or "未记"}；登记 {info["at"]}；本人改于 {dt.datetime.fromtimestamp(v.stat().st_mtime, BJ):%m-%d %H:%M}',
                   f'- 篇幅：{total} 字 → {sum(map(len, new))} 字；删段 {len(dels)}，增段 {len(adds)}，改写 {len(mods)}，改动约占 {min(100, changed * 100 // total)}%', '']
            if mods:
                md.append('**改写**（[-删-]{+增+}）：')
                md += [f'- {inline(a, b)[:400]}' for a, b in mods[:40]]
                md.append('')
            if dels:
                md.append('**删掉的段**：')
                md += [f'- {x[:200]}' for x in dels[:25]]
                md.append('')
            if adds:
                md.append('**新加的段**：')
                md += [f'- {x[:200]}' for x in adds[:25]]
                md.append('')
            info.setdefault('pending', [])
            if h not in info['pending']:
                info['pending'].append(h)
    save(reg)
    if not hits:
        print('没事可做：近期没有本人改过的中台稿')
        return
    OUT.mkdir(exist_ok=True)
    out = OUT / f'素材-{now():%Y-%m-%d}.md'
    out.write_text(f'# 改稿回流素材 {now():%Y-%m-%d}\n\n共 {hits} 份稿被本人改过。\n\n' + '\n'.join(md) + '\n', encoding='utf-8')
    print(f'{hits} 份稿被本人改过，素材：{out}')


def cmd_done(_):
    reg = load()
    n = 0
    for info in reg['files'].values():
        if info.get('pending'):
            info['reviewed'] = ','.join(filter(None, [info.get('reviewed') or ''] + info.pop('pending')))
            n += 1
    save(reg)
    print(f'已标 {n} 份为已总结')


CMDS = {'register': cmd_register, 'snap': cmd_snap, 'diff': cmd_diff, 'done': cmd_done}
if __name__ == '__main__':
    if len(sys.argv) < 2 or sys.argv[1] not in CMDS:
        print(__doc__)
        sys.exit(1)
    CMDS[sys.argv[1]](sys.argv[2:])
