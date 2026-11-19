"""庭后派单：找出前三天内开过的庭，每场发一个事件，交「庭后跟进」规则问本人结果并写回台账。纯脚本。

开庭取自案件台账的案件开庭日期和开庭日程（同一场只算一次，已取消的不算）。同一场只派一次，记在 日志/庭后派单.json。
"""
import datetime as dt
import hashlib
import importlib.util
import json
import sys
from pathlib import Path

if sys.stdout:
    sys.stdout.reconfigure(encoding='utf-8')
HERE = Path(__file__).resolve().parent


def load(name, file):
    spec = importlib.util.spec_from_file_location(name, HERE / file)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ldg = load('ledger', 'ledger.py')
morning = load('morning', '晨报.py')
STATE = HERE.parent / '日志' / '庭后派单.json'
BACK = 3


def main():
    done = json.loads(STATE.read_text(encoding='utf-8')) if STATE.exists() else {}
    cases = [c for c in ldg.get_all('cases') if c.get('status') != '已结案']
    by_id = {k: c for c in cases for k in (c['id'], c.get('case_number')) if k}
    in_window = lambda d: ldg.DATE.match(d or '') and 1 <= (ldg.TODAY - dt.date.fromisoformat(d)).days <= BACK  # noqa: E731
    found = []
    for c in cases:
        d = (c.get('trial_date') or '')[:10]
        if in_window(d):
            found.append({'date': d, 'time': c.get('trial_time') or '', 'hearing': c['case_name'], 'case_id': c['id'],
                          'case_no': c.get('court_case_no') or '', 'mediating': ldg.MEDIATING in ldg.tag_list(c)})
    for s in ldg.get_all('schedules'):
        d = (s.get('schedule_date') or '')[:10]
        if s.get('schedule_type') != '开庭' or not in_window(d) or (s.get('title') or '').startswith('【已取消】'):
            continue
        case = by_id.get(s.get('case_id'))
        same = next((f for f in found if f['date'] == d and (
            (case and f['case_id'] == case['id']) or
            (f['time'] == (s.get('schedule_time') or '') and morning.grams(f['hearing']) & morning.grams(s['title'])))), None)
        if same:
            same.setdefault('location', s.get('location') or '')
            continue
        found.append({'date': d, 'time': s.get('schedule_time') or '', 'hearing': case['case_name'] if case else s['title'],
                      'case_id': case['id'] if case else '', 'case_no': (case or {}).get('court_case_no') or '',
                      'location': s.get('location') or '', 'mediating': bool(case) and ldg.MEDIATING in ldg.tag_list(case)})
    events = []
    for f in sorted(found, key=lambda f: (f['date'], f['time'])):
        key = f"{f['date']} {f['case_id'] or f['hearing']}"
        if key in done:
            continue
        digest = hashlib.sha1(key.encode('utf-8')).hexdigest()[:10]
        events.append({'id': f'after-{digest}', 'type': 'hearing.after', 'subject': f'after-{digest}',
                       'summary': f"庭后跟进：{f['date'][5:]} {f['hearing']}"[:200], 'detail': json.dumps(f, ensure_ascii=False)})
        done[key] = dt.datetime.now().isoformat(timespec='minutes')
    if not events:
        print('没事可做：前三天开过的庭都已跟进')
        return
    STATE.parent.mkdir(exist_ok=True)
    STATE.write_text(json.dumps(done, ensure_ascii=False, indent=1), encoding='utf-8')
    print(f'派出 {len(events)} 场庭后跟进：' + '；'.join(e['summary'][5:] for e in events))
    print()
    print('```agent-autoloop')
    print(json.dumps({'events': events}, ensure_ascii=False))
    print('```')


if __name__ == '__main__':
    main()
