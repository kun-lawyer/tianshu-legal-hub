"""把微信收到的文件放进案件卷宗里它该在的子目录。只复制，不改原文件，不新建目录，同名已存在就不复制。

用法：
  python 微信文件入卷.py suggest <微信里的文件路径> <卷宗目录>
      按卷宗里已有文件与它的名字相近程度，列出最可能的 5 个目录（含命中的相近文件），供判断放哪。
  python 微信文件入卷.py put <微信里的文件路径> <目标目录>
      复制进目标目录（须是已有目录），打印复制后的路径。
"""
import re
import shutil
import sys
from pathlib import Path

if sys.stdout:
    sys.stdout.reconfigure(encoding='utf-8')
# 文件名里表示版本、批注的字样不参与比对，否则「质证意见（修改备注）」对不上「质证意见」
NOISE = re.compile(r'[（(\[【][^）)\]】]*[）)\]】]|修改|备注|批注|留痕|修订|定稿|终稿|初稿|最终|副本|版|\d+|[\s._\-\u2014]')


def grams(name):
    s = NOISE.sub('', Path(name).stem)
    return {s[i:i + 2] for i in range(len(s) - 1)}


def case_files(case):
    return [f for f in case.rglob('*') if f.is_file() and len(f.relative_to(case).parts) <= 4]


def rank(name, case, files=None):
    """[(目录, (相近程度, 相近文件名, 修改时间))]，最相近的在前。files 可传 case_files(case) 的结果省得重复遍历。"""
    g = grams(name)
    scores = {}
    for f in files if files is not None else case_files(case):
        cur = (len(g & grams(f.name)) / max(1, len(g)), f.name, f.stat().st_mtime)
        if f.parent not in scores or cur[::2] > scores[f.parent][::2]:
            scores[f.parent] = cur
    return sorted(scores.items(), key=lambda kv: (-kv[1][0], -kv[1][2]))


def suggest(src, case):
    ranked = rank(src.name, case)[:5]
    if not ranked or ranked[0][1][0] == 0:
        print('卷宗里没有名字相近的文件，请按材料性质在下列已有目录里选，选不准就放卷宗根目录：')
        for d in sorted(p for p in case.iterdir() if p.is_dir()):
            print(' ', d)
        return
    for d, (hit, name, _) in ranked:
        print(f'{hit:.0%}  {d}  （相近文件：{name}）')


def put(src, dst_dir):
    if not dst_dir.is_dir():
        sys.exit(f'目标目录不存在（本脚本不新建目录）：{dst_dir}')
    dst = dst_dir / src.name
    if dst.exists():
        print(f'已存在，未复制：{dst}')
    else:
        shutil.copy2(src, dst)
        print(f'已复制：{dst}')


if __name__ == '__main__':
    import cfg
    if not cfg.enabled('微信'):
        print('微信模块未启用（config.json 的 微信.启用）')
        sys.exit(0)
    if len(sys.argv) != 4 or sys.argv[1] not in ('suggest', 'put'):
        sys.exit(__doc__)
    src = Path(sys.argv[2])
    if not src.is_file():
        sys.exit(f'源文件不存在：{src}')
    (suggest if sys.argv[1] == 'suggest' else put)(src, Path(sys.argv[3]))
