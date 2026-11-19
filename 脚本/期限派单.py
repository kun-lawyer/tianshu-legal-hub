"""期限派单：举证期限、上诉期限前三天，每个期限发一个出稿事件，交「面板出稿」规则准备材料。纯脚本。

期限来源与晨报相同（案件台账案件上的举证期限、上诉期限）。同一个期限只派一次，记在 日志/期限派单.json。
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
spec = importlib.util.spec_from_file_location('morning', HERE / '晨报.py')
morning = importlib.util.module_from_spec(spec)
spec.loader.exec_module(morning)
STATE = HERE.parent / '日志' / '期限派单.json'
AHEAD = 3


def main():
    rows, error = morning.agenda()
    if error:
        print(f'没跑成：{error}')
        sys.exit(1)
    done = json.loads(STATE.read_text(encoding='utf-8')) if STATE.exists() else {}
    events = []
    for left, day, what, name, refs in rows:
        if '期限' not in what or not 0 <= left <= AHEAD:
            continue
        case_id = next((r['case_id'] for r in refs if r.get('case_id')), '')
        key = f'{day.isoformat()} {what} {case_id or name}'
        if key in done:
            continue
        digest = hashlib.sha1(key.encode('utf-8')).hexdigest()[:10]
        detail = {'from': '日程', 'title': name, 'date': day.isoformat(), 'kind': what.split()[0], 'case_id': case_id, 'by': '定时'}
        events.append({'id': f'deadline-{digest}', 'type': 'draft.request', 'subject': f'draft-{case_id or digest}',
                       'summary': f'出稿：{what.split()[0]} {day.month}月{day.day}日 {name}'[:200],
                       'detail': json.dumps(detail, ensure_ascii=False)})
        done[key] = dt.datetime.now().isoformat(timespec='minutes')
    if not events:
        print('没事可做：三天内的期限都已派过')
        return
    STATE.parent.mkdir(exist_ok=True)
    STATE.write_text(json.dumps(done, ensure_ascii=False, indent=1), encoding='utf-8')
    print(f'派出 {len(events)} 个期限的出稿：' + '；'.join(e['summary'][3:] for e in events))
    print()
    print('```agent-autoloop')
    print(json.dumps({'events': events}, ensure_ascii=False))
    print('```')


if __name__ == '__main__':
    main()
