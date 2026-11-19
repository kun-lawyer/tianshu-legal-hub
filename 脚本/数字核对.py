"""数字核对：一套文书（作战包、质证意见、代理词、发问清单、答辩状……）之间的金额、面积、户数等数字是否一致。

  python 数字核对.py <目录或 docx ...> [--base <主文书.docx>] [--out <报告.md>]

给目录时取目录下（不含子目录）全部 docx，跳过 ~$ 临时文件和「修改备注」「（带回答）」「（自己看）」版。
不给 --base 时，文件名含「定稿」的作主文书；都没有就不设主文书，只比互相冲突。

报告四部分：
一、同一项目数字不一致：同一个项目名（数字前的几个字，表格里没有时用行名加列名）在不同位置出现了不同的数，须逐条处理。
二、疑似笔误：相差不到 3%、数字串又很像的两个金额或面积（如 48,328,177.28 与 48,238,177.28）。
三、主文书里找不到的金额（无主文书时为：只在一份文件出现的金额），1 万元以上，只作参考。
四、各文件识别到的数字个数（个数为 0 的多半是扫描件或图片，须人工核）。
万元与元互换比较，按写出的小数位允许四舍五入误差。报告只是线索，不改任何文件。
"""
import difflib
import re
import sys
from collections import defaultdict
from pathlib import Path

import docx

if sys.stdout:
    sys.stdout.reconfigure(encoding='utf-8')

NUM = re.compile(r'(?<![\d.])(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)\s*(亿元|万元|元|平方米|平米|㎡|m²|m2|立方米|m³|吨|户|间|套|栋|%|％|个月|天)')
UNIT = {'亿元': ('元', 1e8), '万元': ('元', 1e4), '元': ('元', 1), '平方米': ('㎡', 1), '平米': ('㎡', 1), '㎡': ('㎡', 1),
        'm²': ('㎡', 1), 'm2': ('㎡', 1), '立方米': ('m³', 1), 'm³': ('m³', 1), '％': ('%', 1)}
FILLER = re.compile(r'[为是共计约即达按的了已仅尚还均各计至和与及或等其中合]')
GENERIC = {'', '合计', '小计', '总计', '金额', '数额', '其中', '共计'}
SKIP_NAME = re.compile(r'修改备注|（带回答）|（自己看）|^~\$')


def cjk(s):
    return re.sub(r'[^一-鿿]', '', s)


def label_of(before):
    """数字前的项目名：同一句里最后几个汉字，去掉虚词。"""
    seg = re.split(r'[，。；：:;,、（）()\n]|\s{2,}', before)[-1] if before else ''
    if len(cjk(seg)) < 2:  # 「结论造价：48…」这种项目名在冒号前
        parts = re.split(r'[，。；;,（）()\n]', before)
        seg = parts[-1] if parts else ''
    # 前面紧挨着另一个数时（「由 A 元自改为 B 元」），项目名只取最后一个数之后的字
    seg = re.split(r'\d[\d,.]*\s*(?:亿元|万元|元|㎡|平方米|户|间|套|%)?', seg)[-1]
    key = FILLER.sub('', cjk(seg))[-5:]
    return key


def scan(path):
    """返回 [(项目名, 规范值, 单位, 小数精度, 原文, 位置)]"""
    d = docx.Document(str(path))
    out = []

    def take(text, where, fallback=''):
        for m in NUM.finditer(text):
            raw, unit = m.group(1), m.group(2)
            base_unit, mul = UNIT.get(unit, (unit, 1))
            val = float(raw.replace(',', '')) * mul
            dec = len(raw.split('.')[1]) if '.' in raw else 0
            tol = 0.5 * mul / (10 ** dec) if mul > 1 else 0.005
            # 数字前自带项目名的（如「结论造价48…元」）以自带的为准，否则用表格行名加列名
            own = label_of(text[:m.start()])
            label = own if len(own) >= 3 or not fallback else fallback
            ctx = text[max(0, m.start() - 18):m.end() + 6].replace('\n', ' ')
            out.append((label, val, base_unit, tol, f'{raw}{unit}', ctx, where))

    for i, p in enumerate(d.paragraphs, 1):
        if p.text.strip():
            take(p.text, f'第{i}段')
    for t_no, t in enumerate(d.tables, 1):
        rows = t.rows
        header = [c.text.strip() for c in rows[0].cells] if rows else []
        seen_cells = set()
        for r_no, row in enumerate(rows, 1):
            cells = [c.text.strip() for c in row.cells]
            # 行名保留数字：「19栋5号网点」「19栋6号网点」是两行
            name = re.sub(r'[^\w]', '', cells[0])[-8:] if cells else ''
            for c_no, text in enumerate(cells):
                # 合并单元格在 python-docx 里会重复出现，同一行只取一次
                if not text or (r_no, text) in seen_cells:
                    continue
                seen_cells.add((r_no, text))
                col = cjk(header[c_no])[-4:] if 0 < c_no < len(header) else ''
                fallback = f'{name}/{col}' if c_no and r_no > 1 and name and col else ''
                take(text, f'表{t_no}第{r_no}行', fallback)
    return out


def same(a, b):
    return a[2] == b[2] and abs(a[1] - b[1]) <= max(a[3], b[3])


def fmt(x):
    return f"{x[0]}｜{x[1][4]}（{x[1][6]}：…{x[1][5]}…）"


def main(argv):
    base, out = None, None
    if '--base' in argv:
        i = argv.index('--base')
        base = Path(argv[i + 1])
        del argv[i:i + 2]
    if '--out' in argv:
        i = argv.index('--out')
        out = Path(argv[i + 1])
        del argv[i:i + 2]
    files = []
    for a in argv:
        p = Path(a)
        if p.is_dir():
            files += sorted(f for f in p.glob('*.docx') if not SKIP_NAME.search(f.name))
        elif p.suffix.lower() == '.docx':
            files.append(p)
    if base and base not in files:
        files.insert(0, base)
    if not base:
        base = next((f for f in files if '定稿' in f.name), None)
    if len(files) < 1:
        sys.exit('没有可核对的 docx')

    data = {}
    for f in files:
        try:
            data[f] = scan(f)
        except Exception as e:  # noqa: BLE001
            print(f'读不了 {f.name}：{e}', file=sys.stderr)
            data[f] = []

    # 一、同一项目不同数
    groups = defaultdict(list)
    for f, items in data.items():
        for it in items:
            if len(it[0].replace('/', '')) >= 3 and it[0] not in GENERIC:
                # 只比最后四个字：「甲公司已交付网点」与「已交付网点」是同一项
                key = it[0] if '/' in it[0] else it[0][-4:]
                groups[(key, it[2])].append((f.name, it))
    conflicts = []
    for (label, unit), occ in groups.items():
        # 同一段、同一行里的几个数是在讲不同的事（「由 A 改为 B」「合计 A，其中 B」），不算冲突
        if len({(o[0], o[1][6]) for o in occ}) < 2:
            continue
        distinct = []
        for o in occ:
            if not any(same(o[1], d[1]) for d in distinct):
                distinct.append(o)
        if len(distinct) > 1 and len({(d[0], d[1][6]) for d in distinct}) > 1:
            conflicts.append((label, unit, distinct, occ))

    # 二、疑似笔误：两个金额相差不到 3%，数字串又高度相似（错位、漏位、换位）
    allm = [(f.name, it) for f, items in data.items() for it in items if it[2] in ('元', '㎡') and it[1] >= 1000]
    typos, seen_pair = [], set()
    for i, (fa, a) in enumerate(allm):
        for fb, b in allm[i + 1:]:
            if a[2] != b[2] or same(a, b) or abs(a[1] - b[1]) > 0.03 * max(a[1], b[1]):
                continue
            da, db = re.sub(r'\D', '', f'{a[1]:.2f}'), re.sub(r'\D', '', f'{b[1]:.2f}')
            if difflib.SequenceMatcher(None, da, db).ratio() < 0.8 or a[3] >= 50 or b[3] >= 50:
                continue  # 万元粗略数和精确数之间不比
            key = tuple(sorted((round(a[1], 2), round(b[1], 2))))
            if key not in seen_pair:
                seen_pair.add(key)
                typos.append(((fa, a), (fb, b)))

    # 三、主文书没有的金额（或只在一份文件出现的金额）
    money = {f: [it for it in items if it[2] == '元' and it[1] >= 10000] for f, items in data.items()}
    orphans = defaultdict(list)
    for f, items in money.items():
        if base and f == base:
            continue
        pool = money.get(base, []) if base else [x for g, its in money.items() if g != f for x in its]
        seen = []
        for it in items:
            if not any(same(it, b) for b in pool) and not any(same(it, s) for s in seen):
                orphans[f.name].append(it)
                seen.append(it)

    md = ['# 数字核对报告', '',
          '文件：' + '；'.join(f.name + ('（主文书）' if f == base else '') for f in files), '',
          f'## 一、同一项目数字不一致（{len(conflicts)}）', '']
    if not conflicts:
        md.append('未发现。')
    for label, unit, distinct, occ in sorted(conflicts, key=lambda c: -len(c[3])):
        md.append(f'- **{label}**（{unit}）出现 {len(occ)} 次、{len(distinct)} 种数：')
        md += [f'  - {fmt(d)}' for d in distinct]
    md += ['', f'## 二、疑似笔误：相差不到 3% 的近似数（{len(typos)}）', '']
    if not typos:
        md.append('未发现。')
    for a, b in typos:
        md += [f'- {fmt(a)}', f'  对 {fmt(b)}']
    md += ['', f"## 三、{'主文书里找不到的金额' if base else '只在一份文件出现的金额'}（1 万元以上，只作参考）", '']
    if not orphans:
        md.append('未发现。')
    for name, items in orphans.items():
        md.append(f'- {name}（{len(items)} 处）：')
        md += [f'  - {it[4]}（{it[6]}：…{it[5]}…）' for it in items[:15]]
        if len(items) > 15:
            md.append(f'  - 另有 {len(items) - 15} 处未列')
    md += ['', '## 四、识别到的数字个数', '']
    md += [f"- {f.name}：{len(items)}{'（0 个：可能是扫描件或图片，须人工核）' if not items else ''}" for f, items in data.items()]
    md += ['', '说明：项目名取自数字前的几个字或表格行列名，同名不同事（如两个不同期间的「欠款」）会误报；报告只作核对线索，逐条看过再改。']
    text = '\n'.join(md) + '\n'
    if out:
        out.write_text(text, encoding='utf-8')
        print(f'报告：{out}；项目数字不一致 {len(conflicts)} 项，疑似笔误 {len(typos)} 对')
    else:
        print(text)


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    main(sys.argv[1:])
