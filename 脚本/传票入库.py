"""传票入库：以传票扫描目录（配置 目录.传票扫描）为准，新放进去的传票自动写进案件台账。

纯脚本，不调模型。只看文件夹顶层（年份子目录是归档）。文件名约定「月.日-时：分简称」，日期时间取文件名；
本地 OCR 取案号、法院、法庭（OCR 中间稿放临时目录，不进资料目录）。未配置传票扫描目录时直接跳过。
- 对上 台账 案件且是将来的开庭：更新该案开庭日期时间，并补一条开庭日程（已有同一场则不重复）。
- 对不上案件但读出了案号：照传票建案（案号、法院、案由、委托人取被传唤人、开庭），再建或挂上开庭日程。
- 对不上案件也读不出案号：只建开庭日程，通知里点名，提醒本人关联或建案。
- 已有同一场日程但没挂案件的，挂到对上的案件上。
- 已过的开庭：只记为已处理，不写。
处理过的文件记在 日志/传票入库状态.json（路径+大小+修改时间），文件被替换会重新处理。
"""
import datetime as dt
import importlib.util
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pymupdf as fitz

import cfg

sys.stdout.reconfigure(encoding='utf-8')
HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('ledger', HERE / 'ledger.py')
ldg = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ldg)

FOLDER = cfg.path('目录.传票扫描')
STATE = HERE.parent / '日志' / '传票入库状态.json'
CONVERT = cfg.path('工具.mineru_convert')
EXT = {'.jpg', '.jpeg', '.png', '.pdf'}
BJ = dt.timezone(dt.timedelta(hours=cfg.TZ_HOURS))
TODAY = dt.datetime.now(BJ).date()
DRY = '--dry' in sys.argv
NAME = re.compile(r'^(\d{1,2})\.(\d{1,2})\s*[-－\u2014]?\s*(?:(\d{1,2})\s*(?:[:：;；.]\s*(\d{2})|点|时)?)?(.*)$')
CASE_NO = re.compile(r'[（(〔]\s*20\d\d\s*[)）〕]\s*[\u4e00-\u9fff]{1,2}\s*\d{0,4}\s*[\u4e00-\u9fff]{1,4}\s*\d+\s*号')
COURT = re.compile(r'[\u4e00-\u9fff]{2,20}(人民法院|仲裁委员会)')
ROOM = re.compile(r'第?[一二三四五六七八九十\d]{1,3}\s*(法庭|审判庭|仲裁庭)|[\u4e00-\u9fff]{0,6}(法庭|仲裁庭)')


def parse_name(p: Path):
    m = NAME.match(p.stem)
    if not m:
        return None
    month, day, rest = int(m[1]), int(m[2]), m[5]
    hh, mm = (int(m[3]), m[4] or '00') if m[3] else (None, None)
    year = dt.datetime.fromtimestamp(p.stat().st_mtime, BJ).year
    try:
        when = dt.date(year, month, day)
    except ValueError:
        return None
    # 年底放进来的下一年一月传票
    if (when - dt.datetime.fromtimestamp(p.stat().st_mtime, BJ).date()).days < -200:
        when = when.replace(year=year + 1)
    short = re.sub(r'\s*\(\d+\)$', '', rest).strip(' （）()-_')
    return when, (f'{hh:02d}:{mm}' if hh is not None else ''), short


def ocr_text(p: Path):
    if not CONVERT:
        return ''
    with tempfile.TemporaryDirectory() as tmp:
        pdf = Path(tmp) / 'doc.pdf'
        if p.suffix.lower() == '.pdf':
            shutil.copy2(p, pdf)
        else:
            with fitz.open() as d, fitz.open(p) as img:
                d.insert_pdf(fitz.open('pdf', img.convert_to_pdf()))
                d.save(pdf)
        subprocess.run([sys.executable, str(CONVERT), str(pdf)], capture_output=True, timeout=600)
        md = pdf.with_suffix('.md')
        return md.read_text(encoding='utf-8', errors='ignore') if md.exists() else ''


def match_case(cases, case_no, short):
    if case_no:
        n = ldg.norm_no(case_no)
        hit = [c for c in cases if c.get('court_case_no') and ldg.norm_no(c['court_case_no']) == n]
        if hit:
            return hit[0], '案号'
    g = ldg.grams(short)
    if not g:
        return None, ''
    scored = sorted(((len(g & ldg.grams((c.get('case_name') or '') + (c.get('opponent_name') or ''))), c)
                     for c in cases if c.get('status') != '已结案'), key=lambda x: -x[0])
    if scored and scored[0][0] >= 2 and (len(scored) < 2 or scored[1][0] < scored[0][0]):
        return scored[0][1], '当事人简称'
    return None, ''


CAUSE = re.compile(r'案由[^一-鿿]{0,30}?([一-鿿]{2,15}纠纷)')
SUMMONED = re.compile(r'被传唤人\s*(?:</td>\s*<td[^>]*>|\|)?\s*([^<|\n]{2,40}?)\s*(?:</td>|\||\n|住所)')


ALIASES = [a for v in cfg.units().values()
           if isinstance(v, dict) and '名称' in v for a in [v['名称'], *v.get('别名', [])]]


def standard_name(short, client):
    """案件名称标准格式（本人 2026-11-16 定）：「我方简称与对方简称XX纠纷」。传票文件名的简称两方顺序不定
    （例：乙公司VS甲公司），哪一方对得上顾问单位就放前面；对不上或两方都对得上的保持原顺序。"""
    parts = [p.strip() for p in re.split(r'\s*(?:VS|vs|Vs|诉|与)\s*', short or '') if p.strip()]
    if len(parts) != 2:
        return short or client
    ours = [p for p in parts if any(p == a or (len(a) >= 3 and a in p) for a in ALIASES)]
    if len(ours) == 1 and ours[0] == parts[1]:
        parts.reverse()
    return '与'.join(parts)


def new_case(case_no, court, text, short, when, time_):
    """传票对不上台账里的案件、但读出了案号的，照传票建案（例：乙公司诉甲公司，微信待办只登了开庭日程，
    面板上一直显示案号待补）。委托人取被传唤人（传票是送给本方的）；对方当事人、标的额传票上没有，留空。"""
    cause = (CAUSE.search(text) or [None, ''])[1]
    client = (SUMMONED.search(text) or [None, ''])[1].strip()
    proc = '二审' if '终' in case_no else '再审' if re.search(r'再|监', case_no) else '执行' if '执' in case_no else '一审'
    name = standard_name(short, client) + (cause if cause and cause not in (short or '') else '')
    return ldg.add_case({'case_name': name, 'court_case_no': case_no, 'court_name': court or '', 'client_name': client,
                        'cause': cause, 'case_procedure': proc, 'status': '执行阶段' if proc == '执行' else '庭审阶段',
                        'trial_date': when.isoformat(), 'trial_time': time_,
                        'description': '据传票扫描件自动建案，委托人取传票上的被传唤人；对方当事人、标的额待补。'})


def main():
    if not FOLDER or not FOLDER.is_dir():
        print('没事可做：未配置传票扫描目录（目录.传票扫描）或该目录不存在')
        return
    state = json.loads(STATE.read_text(encoding='utf-8')) if STATE.exists() else {}
    files = [p for p in FOLDER.iterdir() if p.is_file() and p.suffix.lower() in EXT]
    todo = [p for p in files if state.get(str(p)) != f'{p.stat().st_size}:{int(p.stat().st_mtime)}']
    if not todo:
        print('没事可做：传票文件夹没有新文件')
        return
    cases = ldg.get_all('cases')
    written, unmatched, odd = [], [], []
    for p in sorted(todo, key=lambda p: p.stat().st_mtime):
        sig = f'{p.stat().st_size}:{int(p.stat().st_mtime)}'
        parsed = parse_name(p)
        if not parsed:
            odd.append(p.name)
            state[str(p)] = sig
            continue
        when, time_, short = parsed
        if when < TODAY:
            state[str(p)] = sig
            continue
        text = ocr_text(p)
        case_no = (CASE_NO.search(text) or [None])[0]
        court = (COURT.search(text) or [None])[0]
        room = (ROOM.search(text[text.find(court):] if court else text) or [None])[0]
        case, how = match_case(cases, case_no, short)
        if not case and case_no and not DRY:
            case, how = new_case(case_no, court, text, short, when, time_), '传票案号新建'
            cases.append(case)
        location = ' · '.join(x for x in (court, room) if x)
        sched = {'title': short or p.stem, 'schedule_date': when.isoformat(), 'schedule_time': time_,
                 'schedule_type': '开庭', 'location': location,
                 'description': f'据传票扫描件「{p.name}」自动写入' + (f'，案号 {case_no}' if case_no else '')}
        if case:
            # 日程标题也用案件的标准名称，不用传票文件名里的简称
            sched.update(case_id=case['id'], case_name=case['case_name'], title=case['case_name'])
            nxt = case.get('trial_date') or ''
            # 案件只存一个开庭时间：取最近一场将来的开庭
            if not nxt or nxt < TODAY.isoformat() or when.isoformat() <= nxt:
                patch = {'trial_date': when.isoformat(), 'trial_time': time_}
                if any((case.get(k) or '') != v for k, v in patch.items()) and not DRY:
                    ldg.call('PATCH', f"/api/tables/cases/{case['id']}", patch)
                    ldg.log(case, f"天枢中台据传票「{p.name}」更新开庭：{case.get('trial_date') or '空'} {case.get('trial_time') or ''}"
                                 f" → {when} {time_}")
                    case.update(patch)
        dup = ldg.existing_schedule(sched)
        # 先前手工或微信待办登的同一场没挂案件的，挂上，面板才认得出案号
        if dup and case and not dup.get('case_id') and not DRY:
            ldg.call('PATCH', f"/api/tables/schedules/{dup['id']}", {'case_id': case['id'], 'case_name': case['case_name'],
                                                                   'title': case['case_name']})
        if not dup and not DRY:
            ldg.call('POST', '/api/tables/schedules', {'assigned_to': ldg.CRED['username'], **sched})
        line = f"{when.month}月{when.day}日 {time_} {short}" + (f"（{location}）" if location else '')
        if case:
            written.append(line + f" → {case['case_name']}（按{how}对上）" + ('，日程已有' if dup else ''))
        else:
            unmatched.append(line + ('，日程已有' if dup else '，已建日程，未关联案件'))
        state[str(p)] = sig
    if DRY:
        print('\n'.join(written + unmatched + odd))
        return
    STATE.parent.mkdir(exist_ok=True)
    STATE.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding='utf-8')
    if not (written or unmatched or odd):
        print(f'没事可做：{len(todo)} 份新文件都是已过的开庭')
        return
    lines = written + [f'未对上案件：{u}' for u in unmatched] + [f'文件名看不出日期时间：{o}' for o in odd]
    print(f'传票入库：写入 {len(written)} 场，未对上 {len(unmatched)} 场')
    print()
    print('```agent-autoloop')
    print(json.dumps({'notice': [{'title': f'传票入库：{len(written) + len(unmatched)} 场开庭写进案件台账',
                                  'text': '\n'.join(lines), 'key': f'summons-{dt.datetime.now(BJ):%Y%m%d%H%M}',
                                  'fyi': not (unmatched or odd)}]}, ensure_ascii=False))
    print('```')


if __name__ == '__main__':
    main()
