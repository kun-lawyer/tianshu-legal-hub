"""每日晚报（北京时间 18:00）：今日送审与自动化办了什么、还有什么等你定、明后两天的开庭期限。

纯脚本，不调模型。写入 晚报/YYYY-MM-DD.md，并交一张仅告知的通知。
今日送审一栏只在启用顾问单位模块且配置了 目录.送审收件清单 时出。
"""
import datetime as dt
import importlib.util
import json
import sys
import urllib.request
from collections import Counter
from pathlib import Path

import cfg

sys.stdout.reconfigure(encoding='utf-8')
HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('morning', HERE / '晨报.py')
morning = importlib.util.module_from_spec(spec)
spec.loader.exec_module(morning)
fs = importlib.util.spec_from_file_location('feishu_brief', HERE / '飞书简报.py')
fsm = importlib.util.module_from_spec(fs)
fs.loader.exec_module(fsm)

BJ = dt.timezone(dt.timedelta(hours=cfg.TZ_HOURS))
TODAY = dt.datetime.now(BJ).date()
WEEK = '一二三四五六日'
MANIFEST = cfg.path('目录.送审收件清单') if cfg.enabled('顾问单位') else None
OUT_DIR = HERE.parent / '晚报'
CONFIG = cfg.hub_config()


def hub_state():
    req = urllib.request.Request(CONFIG['url'].rstrip('/') + '/v1/state',
                                 headers={'Authorization': f"Bearer {CONFIG['owner']}"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())


def bj_date(iso):
    return dt.datetime.fromisoformat(iso.replace('Z', '+00:00')).astimezone(BJ).date() if iso else None


def main():
    md = [f'# 晚报 {TODAY.isoformat()}（周{WEEK[TODAY.weekday()]}）', '']
    card_lines = []

    # 今日送审（未启用顾问单位模块或未配置收件清单时整段不出）
    if MANIFEST:
        items = json.loads(MANIFEST.read_text(encoding='utf-8')) if MANIFEST.exists() else []
        today_items = [i for i in items if str(i.get('received', '')).startswith(TODAY.isoformat())]
        md.append('## 今日送审')
        if not today_items:
            md.append('今天没有收到送审文件。')
        else:
            st = Counter(i.get('status') for i in today_items)
            md.append(f"收到 {len(today_items)} 件：" + '，'.join(f'{k} {v}' for k, v in st.items()))
            for i in today_items:
                who = i.get('sender_name') or '发件人未识别'
                where = i.get('target') or i.get('note') or ''
                md.append(f"- {i['received'][11:]}｜{who}｜{i['name']}｜{i.get('status')}{('｜' + where) if where else ''}")
            card_lines.append(f"送审 {len(today_items)} 件（" + '，'.join(f'{k} {v}' for k, v in st.items()) + '）')
        md.append('')

    # 今日自动化与待你决定
    md += ['## 中台今天']
    try:
        s = hub_state()
        runs = [r for r in s.get('runs', []) if bj_date(r.get('finishedAt') or r.get('createdAt')) == TODAY]
        stopped = [r for r in runs if r.get('state') == 'cancelled' and not r.get('error')]
        failed = [r for r in runs if r.get('error')]
        # 只列办成了事的，不报轮次和 Token（本人 2026-11-17 定：没用）
        worked = [r for r in runs if fsm.useful(r)]
        md += [f"- {r['rule']}：{fsm.summary_of(r)}" for r in worked] or ['今天没有实际办成的事。']
        for r in failed:
            md.append(f"- ⚠️ {r['rule']} 第 {r['id']} 轮没跑成：{r.get('error') or ''}")
        for r in stopped:
            md.append(f"- {r['rule']} 第 {r['id']} 轮被手动停止")
        waiting = [c for c in s.get('cards', []) if c.get('state') == 'open']
        md += ['', f'## 等你决定（{len(waiting)}）']
        md += [f"- #{c['id']} {c['title']}" for c in waiting] or ['无。']
        card_lines.append(f"中台今天办成 {len(worked)} 件事" + (f"，{len(failed)} 轮没跑成" if failed else ''))
        if waiting:
            card_lines.append(f"等你决定 {len(waiting)} 件：" + '；'.join(c['title'] for c in waiting[:5]))
    except OSError as e:
        md.append(f'⚠️ 读不到中台状态：{e}')

    # 各文件夹工作记录里的未完成事项：有明确期限的已转待办，其余按文件夹列（本人 2026-11-17 定）
    left_md = []
    try:
        yl = importlib.util.spec_from_file_location('leftover', HERE / '遗留事项.py')
        ylm = importlib.util.module_from_spec(yl)
        yl.loader.exec_module(ylm)
        left = ylm.scan()
        if left['created']:
            left_md += ['', '## 从工作记录转成的待办'] + [f'- {x}' for x in left['created']]
        left_md += ['', f"## 遗留未核（{left['folders']} 个文件夹 {left['items']} 项，近 {ylm.DAYS} 天的工作记录）"]
        left_md += [f'- {x}' for x in left['lines']] or ['无。']
        if left['lines']:
            left_md.append('办完的在飞书说"遗留第 N 项办完了"，或运行 python 脚本/遗留事项.py done N。')
            card_lines.append(f"遗留未核 {left['folders']} 个文件夹")
    except Exception as e:  # noqa: BLE001
        left_md = ['', f'⚠️ 遗留事项没扫成：{str(e)[:120]}']
    md += left_md

    # 明后两天
    rows, error = morning.agenda()
    soon = [r for r in (rows or []) if 1 <= r[0] <= 2]
    week = [r for r in (rows or []) if 3 <= r[0] <= 7 and ('期限' in r[2] or r[2].startswith('开庭'))]
    md += ['', '## 明后两天']
    if error:
        md.append(f'⚠️ {error}')
    md += [f'- {d.isoformat()}（周{WEEK[d.weekday()]}）｜{w}｜{n}' for _, d, w, n, *_ in soon] or ['无安排。']
    if week:
        md += ['', '## 本周后几天的开庭与期限']
        md += [f'- {d.isoformat()}（周{WEEK[d.weekday()]}）｜{w}｜{n}' for _, d, w, n, *_ in week]
    for _, d, w, n, *_ in soon[:8]:
        card_lines.append(f"{'明天' if d == TODAY + dt.timedelta(days=1) else '后天'} {w}｜{n}")

    OUT_DIR.mkdir(exist_ok=True)
    out = OUT_DIR / f'{TODAY.isoformat()}.md'
    out.write_text('\n'.join(md) + '\n', encoding='utf-8')

    # 飞书发详细版（按手机排版）；卡片只留摘要，规则里已设只投桌面通知，不在飞书重复
    try:
        sent = fsm.send(fsm.evening_md(rows or [], morning.open_tasks(), fsm.hub_state(), error or '') + '\n'.join(left_md))
    except Exception as e:  # noqa: BLE001
        sent = str(e)[:200]
    card_lines.append(f'全文：{out}')
    if cfg.enabled('飞书'):
        card_lines.append('飞书已发详细版' if not sent else f'⚠️ 飞书详细版没发出：{sent}')
    head = f"明后两天 {len(soon)} 项安排" if soon else '明后两天无安排'
    receipt = {'notice': [{'title': f'晚报 {TODAY.isoformat()}：{head}', 'text': '\n'.join(card_lines),
                           'key': f'evening-{TODAY.isoformat()}', 'fyi': True}]}
    print(f'晚报已写入 {out}，{head}')
    print()
    print('```agent-autoloop')
    print(json.dumps(receipt, ensure_ascii=False))
    print('```')


if __name__ == '__main__':
    main()
