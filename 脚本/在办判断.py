"""在办初判第三版：先对 台账，再读法院文书正文（含 OCR 副本）判断。重建勾选清单。只读案件目录。

用法：python 在办判断.py
盘点 目录.单次案件 下的子目录和各顾问单位诉讼类子目录下的案件目录，输出 在办案件初判（新）.xlsx；
本人复核「在办」一列后另存为 在办案件勾选清单.xlsx，周研判素材.py 以它为准。

- 排除单位整体不列。
- 台账 对上且状态为待接案、已立案、庭审阶段，或近期有开庭：判在办；台账 标已结案：判不在办。执行阶段多为批量终本，不据此判断。
- 文书类型取正文标题，不看文件名；裁判文书日期取「书记员」之前的落款日期。
"""
import datetime as dt
import os
import re
import sys
from pathlib import Path

import docx
import pymupdf as fitz
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.worksheet.datavalidation import DataValidation

sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, str(Path(__file__).resolve().parent))
import cfg  # noqa: E402

fitz.TOOLS.mupdf_display_errors(False)
# 本人勾过的清单不能被覆盖：重跑全量初判时另存一份新文件
OUT = cfg.ROOT / '在办案件初判（新）.xlsx'
TODAY = dt.date.today()
EXCLUDE = tuple(cfg.get('不盘点目录', []))
DERIVED = {'.md', '.log', '.txt', '.json'}
SKIP = {'.lnk', '.tmp', '.ini', '.db'}
TEXT_EXT = {'.pdf', '.docx', '.md', '.jpg', '.jpeg', '.png'}

TITLE = re.compile(r'(民事|刑事|行政|执行|仲裁)?(判决书|裁定书|调解书|裁决书|决定书|传票|举证通知书|应诉通知书|受理案件通知书|'
                   r'受理通知书|出庭通知书|开庭通知书|缴费通知书|执行通知书|报告财产令|结案通知书|撤诉|调解协议)')
COURT_HEAD = re.compile(r'人民法院|仲裁委员会')
FINAL = re.compile(r'终审判决|终审裁定|准许.{0,8}撤回|准予.{0,8}撤|准许.{0,6}撤诉|自愿达成.{0,10}协议|调解书|'
                   r'终结本次执行|驳回.{0,20}再审申请|终局裁决|裁决为终局|执行完毕|本案执行终结|结案通知')
APPEALABLE = re.compile(r'送达之日起(十五|十|15|10)日内.{0,40}上诉')
PARTY_FILING = re.compile(r'上诉状|再审申请|申请执行|执行申请|强制执行|恢复执行|起诉状|答辩状|代理词|质证|证据目录')
CASE_NO = re.compile(r'[（(〔]\s*(20\d\d)\s*[)）〕]\s*[\u4e00-\u9fff]{1,2}\d{0,4}\s*[\u4e00-\u9fff]{1,4}\s*\d+\s*号')
CN = dict(zip('〇零一二三四五六七八九', '00123456789'))
DATE_AR = re.compile(r'(20\d\d)\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日')
DATE_CN = re.compile(r'([二〇零一三四五六七八九]{4})\s*年\s*([十一二三四五六七八九]{1,3})\s*月\s*([十二三一四五六七八九]{1,3})\s*日')
STOP = re.compile(r'公司|纠纷|案件|合同|建设|工程|施工|开庭|诉讼|有限|责任|' + cfg.place_pattern() + r'|\d+|劳动争议|劳动|争议|房屋|租赁|'
                  r'买卖|民间借贷|借贷|借款|侵权|股权|转让|执行|异议|确认|效力|无效|一审|二审|再审|仲裁|行政|损害|赔偿|'
                  r'追偿|追索|票据|承包|经营|服务|管理|集团|控股|投资|发展|实业|房地产|置业|物业|商贸|科技|人民政府|'
                  r'委员会|村民|居民|返还|排除妨害|健康权|生命权|交通事故|与|诉|及|第三人|等|一案|两案')


def cn_num(s):
    if '十' not in s:
        return int(''.join(CN[c] for c in s))
    a, _, b = s.partition('十')
    return (int(CN[a]) if a else 1) * 10 + (int(CN[b]) if b else 0)


def dates_in(text):
    out = []
    for m in DATE_AR.finditer(text):
        out.append((m.start(), int(m[1]), int(m[2]), int(m[3])))
    for m in DATE_CN.finditer(text):
        try:
            out.append((m.start(), int(''.join(CN[c] for c in m[1])), cn_num(m[2]), cn_num(m[3])))
        except (KeyError, ValueError):
            pass
    res = []
    for pos, y, mth, d in sorted(out):
        try:
            res.append((pos, dt.date(y, mth, d)))
        except ValueError:
            pass
    return res


def sign_date(text):
    """裁判文书落款：书记员之前最近的日期；没有书记员取全文最后一个不晚于今天的日期。"""
    ds = [(p, d) for p, d in dates_in(text) if d <= TODAY]
    if not ds:
        return None
    clerk = text.rfind('书记员')
    if clerk > 0:
        before = [d for p, d in ds if p < clerk and p > clerk - 400]
        if before:
            return before[-1]
    return ds[-1][1]


def read_text(p: Path, md_of):
    ext = p.suffix.lower()
    text = ''
    try:
        if ext == '.pdf':
            with fitz.open(p) as d:
                idx = sorted({0, 1, max(0, d.page_count - 2), d.page_count - 1} & set(range(d.page_count)))
                text = '\n'.join(d[i].get_text() for i in idx)
        elif ext == '.docx':
            text = '\n'.join(x.text for x in docx.Document(p).paragraphs)
        elif ext == '.md':
            text = p.read_text(encoding='utf-8', errors='ignore')
    except Exception:  # noqa: BLE001
        text = ''
    if len(text.strip()) < 80 and p.stem in md_of:
        text = md_of[p.stem].read_text(encoding='utf-8', errors='ignore')
    return text


def grams(s):
    cjk = re.sub(r'[^\u4e00-\u9fff]', '', STOP.sub('', s or ''))
    return {cjk[i:i + 3] for i in range(len(cjk) - 2)}


def norm_no(s):
    return re.sub(r'[\s（）()〔〕号]', '', s or '')


def classify(folder):
    files, md_of = [], {}
    for root, _, names in os.walk(folder):
        for n in names:
            p = Path(root) / n
            if p.suffix.lower() in SKIP or n.startswith('~$'):
                continue
            if p.suffix.lower() == '.md':
                md_of[p.stem] = p
            try:
                files.append((p, dt.date.fromtimestamp(p.stat().st_mtime)))
            except OSError:
                pass
    originals = [(p, t) for p, t in files if p.suffix.lower() not in DERIVED]
    docs, numbers = [], set()
    for p, t in originals:
        if p.suffix.lower() not in TEXT_EXT:
            continue
        if PARTY_FILING.search(p.name):
            docs.append({'name': p.name, 'kind': 'filing', 'date': t, 'final': False, 'appealable': False})
            continue
        if not re.search(r'判决|裁定|调解|裁决|决定|传票|通知|撤诉|终本|执行|送达|受理|开庭|结案', p.name) and p.suffix.lower() != '.pdf':
            continue
        text = read_text(p, md_of)
        head = text[:400]
        m = TITLE.search(head)
        if not (m and COURT_HEAD.search(text[:600])):
            continue
        numbers.update(norm_no(x.group(0)) for x in CASE_NO.finditer(text[:800]))
        kind = m.group(2)
        decision = kind in ('判决书', '裁定书', '调解书', '裁决书', '决定书', '调解协议', '撤诉', '结案通知书')
        date = sign_date(text) if decision else (sign_date(text) or t)
        docs.append({'name': p.name, 'kind': kind, 'date': date or t, 'final': bool(FINAL.search(text)) or kind == '调解书',
                     'appealable': bool(APPEALABLE.search(text)), 'decision': decision,
                     'future': max((d for _, d in dates_in(text)), default=None)})
    last_any = max((t for _, t in originals), default=None)
    return docs, numbers, last_any


def candidate(folder, numbers, jw_cases):
    """目录的 台账 候选：案号命中得 1000 分；否则按当事人字号三字重合计分，并列第一不认。"""
    for c in jw_cases:
        if c.get('court_case_no') and norm_no(c['court_case_no']) in numbers:
            return c, 1000
    fg = grams(Path(folder).name)
    if not fg:
        return None, 0
    score = lambda c: len(fg & grams((c.get('case_name') or '') + ' ' + (c.get('opponent_name') or '')))  # noqa: E731
    ranked = sorted(jw_cases, key=score, reverse=True)
    top = score(ranked[0])
    if top >= 2 and (len(ranked) < 2 or score(ranked[1]) < top):
        return ranked[0], top
    return None, 0


def judge(row, scanned, match):
    folder = row['目录']
    docs, numbers, last_any = scanned
    ldg = f"{match['case_name']}｜{match.get('status')}｜{match.get('court_case_no') or ''}" if match else ''
    if match:
        trial = match.get('trial_date') or ''
        if match.get('status') in ('待接案', '已立案', '庭审阶段') or (trial and trial >= (TODAY - dt.timedelta(days=30)).isoformat()):
            return '是', f"台账 状态「{match.get('status')}」，开庭 {trial or '无'}", ldg, ''
        if match.get('status') == '已结案':
            return '否', '台账 已结案', ldg, ''
    if last_any is None:
        return '否', '目录里没有原始文件', ldg, ''
    if (TODAY - last_any).days > 730:
        return '否', f'两年以上无改动（最近 {last_any}）', ldg, ''
    court = [d for d in docs if d['kind'] != 'filing']
    filings = [d for d in docs if d['kind'] == 'filing']
    if court:
        last = max(court, key=lambda d: d['date'])
        label = f"「{last['name']}」{last['kind']}（{last['date']}）"
        later = [f for f in filings if f['date'] > last['date'] + dt.timedelta(days=10)]
        if later:
            f = max(later, key=lambda x: x['date'])
            if (TODAY - f['date']).days <= 365:
                return '是', f"最新法院文书{label}之后有「{f['name']}」（{f['date']}）", ldg, label
        if last.get('decision'):
            if last['final']:
                return '否', f'最新法院文书为生效文书{label}', ldg, label
            if last['appealable']:
                if (TODAY - last['date']).days <= 30:
                    return '是', f'一审文书{label}尚在上诉期', ldg, label
                return '否', f'一审文书{label}已过上诉期，之后无上诉材料', ldg, label
            if (TODAY - last['date']).days <= 183:
                return '待定', f'近半年的裁判文书{label}，看不出是否终结', ldg, label
            return '否', f'裁判文书{label}半年以上，之后无材料', ldg, label
        if last.get('future') and last['future'] >= TODAY:
            return '是', f"最新法院文书{label}载明的日期 {last['future']} 尚未到", ldg, label
        if (TODAY - last['date']).days <= 365:
            return '是', f'最新法院文书为程序文书{label}，尚无裁判', ldg, label
        return '待定', f'最新法院文书为一年以上的程序文书{label}，未见裁判', ldg, label
    if filings:
        f = max(filings, key=lambda x: x['date'])
        if (TODAY - f['date']).days <= 183:
            return '是', f"无法院文书，近半年有「{f['name']}」（{f['date']}）", ldg, ''
        return '否', f"无法院文书，最近材料「{f['name']}」（{f['date']}）已半年以上", ldg, ''
    if (TODAY - last_any).days <= 183:
        return '待定', f'近半年有改动，但没有法院文书和诉讼材料（最近 {last_any}）', ldg, ''
    return '否', f'无法院文书和诉讼材料，半年以上无改动（最近 {last_any}）', ldg, ''


LIT_DIR = re.compile(r'诉讼|仲裁|执行|案件|被诉')


def inventory():
    """盘点案件目录：单次案件下的每个子目录，加各顾问单位诉讼类子目录下的每个案件目录。"""
    def row(src, unit, d):
        mt = max([f.stat().st_mtime for f in d.rglob('*') if f.is_file()] or [d.stat().st_mtime])
        return {'来源': src, '单位': unit, '名称': d.name, '目录': str(d),
                '最近改动': dt.date.fromtimestamp(mt).isoformat()}
    rows = []
    single, advisor = cfg.path('目录.单次案件'), cfg.path('目录.顾问单位')
    if single and single.is_dir():
        rows += [row('单次案件', '', d) for d in sorted(single.iterdir()) if d.is_dir()]
    if advisor and advisor.is_dir():
        for unit in sorted(advisor.iterdir()):
            if not unit.is_dir() or unit.name.startswith(('_',) + EXCLUDE):
                continue
            for sub in sorted(unit.iterdir()):
                if sub.is_dir() and LIT_DIR.search(sub.name):
                    rows += [row('顾问单位', unit.name, d) for d in sorted(sub.iterdir()) if d.is_dir()]
    return rows


def main():
    rows = inventory()
    if not rows:
        sys.exit('没有可盘点的案件目录：检查 config.json 的 目录.单次案件、目录.顾问单位')
    import importlib.util
    spec = importlib.util.spec_from_file_location('ledger', Path(__file__).resolve().parent / 'ledger.py')
    ledger = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ledger)
    jw_cases = ledger.get_all('cases')
    scanned, cands = {}, {}
    for r in rows:
        try:
            scanned[r['目录']] = classify(r['目录'])
        except Exception as e:  # noqa: BLE001
            scanned[r['目录']] = e
            continue
        cands[r['目录']] = candidate(r['目录'], scanned[r['目录']][1], jw_cases)
    # 一个 台账 案件只归一个目录：同一案件被多个目录认领时，只留得分最高的那个
    owner = {}
    for folder, (c, s) in cands.items():
        if c and (c['id'] not in owner or s > owner[c['id']][1]):
            owner[c['id']] = (folder, s)
    out = []
    for r in rows:
        sc = scanned[r['目录']]
        if isinstance(sc, Exception):
            out.append(['待定', r['来源'], r['单位'] or '', r['名称'], '', f'读取出错：{sc}', r['最近改动'], r['目录']])
            continue
        c, _ = cands[r['目录']]
        match = c if c and owner[c['id']][0] == r['目录'] else None
        try:
            v, basis, ldg, latest = judge(r, sc, match)
        except Exception as e:  # noqa: BLE001
            v, basis, ldg, latest = '待定', f'读取出错：{e}', '', ''
        out.append([v, r['来源'], r['单位'] or '', r['名称'], ldg, basis, r['最近改动'], r['目录']])
    order = {'是': 0, '待定': 1, '否': 2}
    out.sort(key=lambda x: (order[x[0]], x[2], x[3]))

    wb = Workbook()
    ws = wb.active
    ws.title = '案件清单'
    head = ['序号', '在办', '来源', '单位', '案件目录', '台账 中对应案件', '判断依据', '最近改动', '完整路径']
    ws.append(head)
    fills = {'是': 'C6EFCE', '待定': 'FFEB9C', '否': 'FCE9D9'}
    for i, x in enumerate(out, 1):
        ws.append([i] + x)
        ws.cell(i + 1, 2).fill = PatternFill('solid', fgColor=fills[x[0]])
        ws.cell(i + 1, 2).alignment = Alignment(horizontal='center')
    for c in ws[1]:
        c.font = Font(bold=True)
    dv = DataValidation(type='list', formula1='"是,否,待定"', allow_blank=True)
    ws.add_data_validation(dv)
    dv.add(f'B2:B{len(out) + 1}')
    for col, w in zip('ABCDEFGHI', (6, 6, 9, 20, 50, 40, 80, 11, 70)):
        ws.column_dimensions[col].width = w
    ws.freeze_panes = 'C2'
    ws.auto_filter.ref = f'A1:I{len(out) + 1}'
    note = wb.create_sheet('说明')
    for line in [
        '已排除：' + ('、'.join(EXCLUDE) or '无') + '。',
        '判断顺序：先对案件台账里的案件；状态为待接案、已立案、庭审阶段或近期有开庭的直接判在办，标已结案的判不在办。',
        '其余读法院文书正文判断（扫描件已 OCR）：文书类型看正文标题，裁判文书日期取书记员前的落款。',
        '「台账 中对应案件」为空而判为在办的，复核后会补录进 台账。',
        '请复核「是」和「待定」，改好后另存为仓库根目录的「在办案件勾选清单.xlsx」。只有「是」的纳入周研判。',
    ]:
        note.append([line])
    note.column_dimensions['A'].width = 110
    wb.save(OUT)
    from collections import Counter
    cnt = Counter(x[0] for x in out)
    print(dict(cnt), '在办中 台账 已有', sum(1 for x in out if x[0] == '是' and x[4]), '缺', sum(1 for x in out if x[0] == '是' and not x[4]))


if __name__ == '__main__':
    main()
