"""开庭前作战包派单：找出七天内的开庭，每场发一个事件交 Agent 出作战包。纯脚本。

开庭来源与晨报相同（案件台账里的案件开庭和开庭日程，已去重）。同一场只派一次，记在 日志/作战包派单.json。
"""
import datetime as dt
import hashlib
import importlib.util
import json
import re
import sys
from pathlib import Path

if sys.stdout:
    sys.stdout.reconfigure(encoding='utf-8')
HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('morning', HERE / '晨报.py')
morning = importlib.util.module_from_spec(spec)
spec.loader.exec_module(morning)
STATE = HERE.parent / '日志' / '作战包派单.json'
AHEAD = 7  # 本人 2026-11-16 定：开庭前七天出


def main():
    rows, error = morning.agenda()
    if error:
        print(f'没跑成：{error}')
        sys.exit(1)
    done = json.loads(STATE.read_text(encoding='utf-8')) if STATE.exists() else {}
    events = []
    for left, day, what, name, *rest in rows:
        if not what.startswith('开庭') or not 0 <= left <= AHEAD:
            continue
        time_ = (re.search(r'\d{1,2}:\d{2}', what) or [''])[0]
        # 今天已经开过的庭不再派（如开庭当天才标调解中、改了案号，派单键变了）
        if left == 0 and time_ and dt.datetime.now(morning.BJ).strftime('%H:%M') >= time_.zfill(5):
            continue
        # 键用案件编号：改案件名称、补案号不会重复派单；转调解才另派一次
        cid = next((r.get('case_id') for r in (rest[0] if rest else []) if r.get('case_id')), '')
        key = f'{day.isoformat()} {time_} {cid}' + (' 调解' if '【调解中】' in name else '') if cid else f'{day.isoformat()} {time_} {name}'
        if key in done:
            continue
        digest = hashlib.sha1(key.encode('utf-8')).hexdigest()[:10]
        detail = {'date': day.isoformat(), 'time': time_, 'hearing': name}
        # 标了调解中的案件，名字带标记，派单键随之变化：已出过作战包的会再派一次，改出调解准备单
        if '【调解中】' in name:
            detail['mode'] = '调解'
        events.append({'id': f'prep-{digest}', 'type': 'hearing.prep', 'subject': f'hearing-{digest}',
                       'summary': f'{day.month}月{day.day}日 {time_} {name}'[:200],
                       'detail': json.dumps(detail, ensure_ascii=False)})
        done[key] = dt.datetime.now().isoformat(timespec='minutes')
    if not events:
        print('没事可做：七天内的开庭都已派过作战包')
        return
    STATE.parent.mkdir(exist_ok=True)
    STATE.write_text(json.dumps(done, ensure_ascii=False, indent=1), encoding='utf-8')
    print(f'派出 {len(events)} 份作战包：' + '；'.join(e['summary'] for e in events))
    print()
    print('```agent-autoloop')
    print(json.dumps({'events': events}, ensure_ascii=False))
    print('```')


if __name__ == '__main__':
    main()
