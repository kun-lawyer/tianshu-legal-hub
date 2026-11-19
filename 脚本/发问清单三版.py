"""发问清单三版：由提交版（母版直出、只有问题）生成「带回答」「自己看」两版，红字插在每问之后。

用法：python 发问清单三版.py <提交版.docx> <回答.json>
回答.json 写成 {"1": ["建议回答：……", "如答不利：……", "参考（内部粗算……）：……"], "2": [...]}，键是题号。
  带回答：只放「建议回答」开头的行，开庭时可给同事或当事人看；只放事实性的回答，策略意图不放；
  自己看：全部行，含答不利时的应对、内部粗算和发问意图，只给本人。
  「【仅自己看】」标记：写在行首，整行只进自己看；写在行中，带回答只取标记前的部分。标记本身不出现在成稿里。
输出与提交版同目录，文件名加「（带回答）」「（自己看）」。2026-11-15 某建设工程案询问鉴定人清单即此三版，本人定为标准。
"""
import copy
import json
import re
import sys
from pathlib import Path

from docx import Document
from docx.oxml.ns import qn
from docx.shared import RGBColor
from docx.text.paragraph import Paragraph

sys.stdout.reconfigure(encoding='utf-8')
if len(sys.argv) != 3:
    sys.exit(__doc__)
src = Path(sys.argv[1])
answers = {int(k): v for k, v in json.loads(Path(sys.argv[2]).read_text(encoding='utf-8')).items()}


MARK = '【仅自己看】'


def for_others(ln):
    ln = ln.split(MARK)[0].rstrip()
    return ln if ln.startswith('建议回答') else None


def build(pick, suffix):
    doc = Document(src)
    done = 0
    for p in list(doc.paragraphs):
        m = re.match(r'^(\d+)[.．、]', p.text.strip())
        if not m or int(m[1]) not in answers:
            continue
        lines = [x for x in map(pick, answers[int(m[1])]) if x]
        anchor = p._element
        rpr = p.runs[0]._element.find(qn('w:rPr')) if p.runs else None
        for line in lines:
            new_p = copy.deepcopy(p._element)
            for child in list(new_p):
                if child.tag != qn('w:pPr'):
                    new_p.remove(child)
            anchor.addnext(new_p)
            anchor = new_p
            run = Paragraph(new_p, p._parent).add_run(line)
            if rpr is not None:
                run._element.insert(0, copy.deepcopy(rpr))
            run.font.color.rgb = RGBColor(0xFF, 0x00, 0x00)
            run.font.bold = False
        done += bool(lines)
    out = src.with_name(f'{src.stem}（{suffix}）{src.suffix}')
    doc.save(out)
    print(f'已生成 {out}（{done} 题有红字）')


missing = sorted(set(answers) - {int(m[1]) for p in Document(src).paragraphs if (m := re.match(r'^(\d+)[.．、]', p.text.strip()))})
if missing:
    sys.exit(f'回答里的题号在提交版里找不到：{missing}')
build(for_others, '带回答')
build(lambda ln: ln.replace(MARK, ''), '自己看')
