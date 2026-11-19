"""按案号、当事人在单次案件、顾问单位两处案件根目录里找案件文件夹。只读。

用法：python 找案件目录.py <案号或关键词> [更多关键词 ...]
输出按得分排序的候选目录（最多 8 个）。案号命中目录名或目录内文件名得分最高。
"""
import os
import re
import sys
from pathlib import Path

if sys.stdout:  # 面板用 pythonw 加载本模块时没有标准输出
    sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, str(Path(__file__).resolve().parent))
import cfg  # noqa: E402

SINGLE = cfg.path('目录.单次案件')
ADVISOR = cfg.path('目录.顾问单位')
LIT = re.compile(r'诉讼|仲裁|执行|案件|被诉')


def norm(s):
    return re.sub(r'[\s（）()〔〕\[\]【】号]', '', s)


def case_dirs():
    for d in SINGLE.iterdir() if SINGLE and SINGLE.is_dir() else []:
        if d.is_dir():
            yield d
    for unit in ADVISOR.iterdir() if ADVISOR and ADVISOR.is_dir() else []:
        if not unit.is_dir() or unit.name.startswith('_'):
            continue
        for sub in unit.iterdir():
            if sub.is_dir() and LIT.search(sub.name):
                for d in sub.iterdir():
                    if d.is_dir():
                        yield d


def search(words):
    """返回 [(得分, 目录, 理由)]，高分在前。"""
    numbers = [norm(w) for w in words if re.search(r'\d{4}.*[民刑行执赔破仲].*\d+', w)]
    names = [w for w in words if w not in numbers and len(w) >= 2]
    scored = []
    for d in case_dirs():
        score, why = 0, []
        nd = norm(d.name)
        for n in numbers:
            if n in nd:
                score += 100
                why.append('目录名含案号')
        for w in names:
            if w in d.name:
                score += 10
                why.append(f'目录名含「{w}」')
        if numbers and score < 100:
            for root, _, files in os.walk(d):
                if any(n in norm(f) for f in files for n in numbers):
                    score += 80
                    why.append('目录内文件名含案号')
                    break
        if score:
            scored.append((score, str(d), '；'.join(why)))
    return sorted(scored, reverse=True)


# ===== 非诉、行政事务的文件夹 =====

NONLIT = re.compile(r'非诉')
ADMIN_ROOTS = cfg.paths('目录.事务目录')
# 几乎每个事务名里都有的词，不作为对上的依据
STOP = {'公司', '有限', '集团', '责任', '法律', '意见', '答复', '合同', '审查', '协议', '律师', '事务', '业务', '非诉',
        '修改', '材料', '文件', '相关', '工作', '情况', '说明', '处理', '建议', '后续', '问题', '审核', '一份', '两份'}
WHO = re.compile(r'(?:私聊|群[：:]?)\s*([^\s（(，,。；;]+)')


def grams(s):
    s = re.sub(r'[\d\W_a-zA-Z]+', ' ', s)
    return {w[i:i + 2] for w in s.split() for i in range(len(w) - 1)} - STOP


def overlap(name, text_grams):
    return len(grams(name) & text_grams)


def unit_of(client, text):
    """顾问单位目录：按客户名（没有就按事项文字）与单位目录名的二字词重合度挑，至少重合 2 个。"""
    g = grams(client) if client else grams(text)
    units = ADVISOR.iterdir() if ADVISOR and ADVISOR.is_dir() else []
    best = sorted(((overlap(u.name, g), u) for u in units if u.is_dir() and not u.name.startswith('_')),
                  key=lambda x: -x[0])
    if best and best[0][0] >= 2 and (len(best) == 1 or best[1][0] < best[0][0]):
        return best[0][1]
    return None


def loose(title, desc='', client='', cat='非诉'):
    """不挂案件的非诉、行政事务找文件夹。返回 {'folder', 'candidates', 'fallback'}：
    folder 是能唯一确定的事务文件夹；fallback 是确定不了时退而打开的上级目录（顾问单位的非诉目录）。
    非诉事务文件夹按「序号.月.日-联系人-事项」命名（例：64.11.8-张三-李四答复），联系人名和事项词都参与比对。"""
    named = re.search(r'(?:^|\]\s*)卷宗[：:]\s*(.+?)\s*$', desc or '', re.M)
    if named and Path(named[1]).is_dir():
        return {'folder': named[1], 'candidates': [named[1]], 'fallback': ''}
    text = f'{title}\n{desc}'
    g = grams(text)
    people = [p for p in WHO.findall(desc or '') if len(p) >= 2]

    def overlap(name, g):
        # 文件夹名里有来源联系人的，加 3 分；同音错字（王惠芳／王慧芳）按首尾字对上，加 2 分
        bonus = max([3 if p in name else 2 if len(p) >= 3 and p[0] in name and p[-1] in name else 0 for p in people] or [0])
        return len(grams(name) & g) + bonus
    if cat == '行政':
        dirs = [d for r in ADMIN_ROOTS if r.is_dir() for d in [r, *(x for x in r.iterdir() if x.is_dir())]]
        fallback = ''
    else:
        unit = unit_of(client, text)
        if not unit:
            return {'folder': '', 'candidates': [], 'fallback': ''}
        roots = [s for s in unit.iterdir() if s.is_dir() and NONLIT.search(s.name)]
        dirs = [d for r in roots for d in r.iterdir() if d.is_dir()]
        fallback = str(roots[0]) if len(roots) == 1 else str(unit)
    scored = sorted(((overlap(d.name, g), d.stat().st_mtime, str(d)) for d in dirs), reverse=True)
    scored = [x for x in scored if x[0] >= 2]
    sure = bool(scored) and (len(scored) == 1 or scored[1][0] < scored[0][0])
    return {'folder': scored[0][2] if sure else '', 'candidates': [x[2] for x in scored[:8]], 'fallback': fallback}


def main(words):
    scored = search(words)
    for score, path, why in scored[:8]:
        print(f'{score:>4} | {path} | {why}')
    if not scored:
        print('没找到')


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    main(sys.argv[1:])
