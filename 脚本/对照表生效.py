"""把本人确认后的 送审人对照表.xlsx 转成 送审人.json，供 送审收件.py 自动归档用。

只收「确认单位」不为空的行；单位名称须是 units.json 里的名称。经办人为空的照收，归档时会推卡片问。
"""
import json
import sys
from pathlib import Path

from openpyxl import load_workbook

sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, str(Path(__file__).resolve().parent))
import cfg  # noqa: E402

if not cfg.enabled('顾问单位'):
    print('顾问单位模块未启用（config.json 的 顾问单位.启用）')
    sys.exit(0)

ROOT = Path(__file__).resolve().parent.parent
UNITS = cfg.units()
by_name = {v['名称']: k for k, v in UNITS.items()}
ws = load_workbook(ROOT / '送审人对照表.xlsx')['送审人']
h = [c.value for c in ws[1]]
out, bad = {}, []
for r in ws.iter_rows(min_row=2, values_only=True):
    row = dict(zip(h, r))
    unit_name = (row['确认单位'] or '').strip()
    if not unit_name:
        continue
    key = by_name.get(unit_name)
    if not key:
        bad.append(f"{row['微信名']}：单位「{unit_name}」不在单位列表里")
        continue
    out[row['微信号']] = {'name': row['微信名'], 'unit': key, 'person': (row['确认经办人'] or '').strip()}
(ROOT / '送审人.json').write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding='utf-8')
print(f'生效 {len(out)} 人，其中缺经办人 {sum(1 for v in out.values() if not v["person"])} 人')
for b in bad:
    print('  跳过', b)
