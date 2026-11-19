"""庭前作战包排版：把内容文件排成 A4 详版 docx（红色章标题、表格、提示框、页眉“内部资料”、页脚页码）。

用法：python 作战包排版.py <输出.docx> <内容.py>

内容文件是一段 Python，只调用下面这些函数（写法照抄 手册/作战包范例/ 下的范例内容文件）：
  页眉 = '甲公司诉乙公司建设工程施工合同纠纷'     页眉左段，后面自动接“　庭前作战包　内部资料，不得对外提交”
  h1('一、案件速览')                          章标题（红色黑体，下划线）
  h2('（一）主目标')                          节标题
  para('正文……', indent=True)               正文段；**加粗**；color=RED 为红字
  bullets(['……', '……'])                     ▪ 列表
  table(['列1', '列2'], [['a', 'b']], [宽cm, 宽cm])   表格，首行红底白字，金额自动右对齐；各列宽合计 16.6
  kv([('案号', '……'), ...])                 左右两栏的速览表
  callout('标题', ['第一行', ...])            左边红条的提示框（放“三句话口径”）
封面、目录也在内容文件里写（见范例开头），本脚本只管版式。2026-11-15 某建设工程案第三次开庭的作战包即由此排出，本人定为标准。
"""
import re
import sys

from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

sys.stdout.reconfigure(encoding='utf-8')
if len(sys.argv) != 3:
    sys.exit(__doc__)
OUT, CONTENT = sys.argv[1], sys.argv[2]
RED = RGBColor(0x8B, 0x1A, 0x1A)
GRAY = RGBColor(0x59, 0x59, 0x59)
BODY = '仿宋_GB2312'
HEI = '黑体'
TITLE = '方正小标宋简体'

doc = Document()
sec = doc.sections[0]
sec.page_width, sec.page_height = Cm(21), Cm(29.7)
sec.top_margin = sec.bottom_margin = Cm(2.3)
sec.left_margin = sec.right_margin = Cm(2.2)

st = doc.styles['Normal']
st.font.name = 'Times New Roman'
st.font.size = Pt(12)
st.element.rPr.rFonts.set(qn('w:eastAsia'), BODY)


def set_font(run, east=BODY, size=None, bold=None, color=None):
    run.font.name = 'Times New Roman'
    rpr = run._element.get_or_add_rPr()
    rf = rpr.find(qn('w:rFonts'))
    if rf is None:
        rf = OxmlElement('w:rFonts')
        rpr.insert(0, rf)
    rf.set(qn('w:eastAsia'), east)
    if size:
        run.font.size = Pt(size)
    if bold is not None:
        run.font.bold = bold
    if color is not None:
        run.font.color.rgb = color


def fmt(p, align='both', indent=True, before=0, after=4, line=1.4):
    pf = p.paragraph_format
    pf.alignment = {'both': WD_ALIGN_PARAGRAPH.JUSTIFY, 'center': WD_ALIGN_PARAGRAPH.CENTER,
                    'left': WD_ALIGN_PARAGRAPH.LEFT, 'right': WD_ALIGN_PARAGRAPH.RIGHT}[align]
    pf.space_before, pf.space_after = Pt(before), Pt(after)
    pf.line_spacing = line
    if indent:
        pf.first_line_indent = Pt(24)


def add_rich(p, text, size=12, east=BODY, color=None, base_bold=False):
    text = re.sub(r'"([^"]*)"', '“\\1”', text)
    for i, seg in enumerate(re.split(r'\*\*', text)):
        if seg:
            set_font(p.add_run(seg), east, size, base_bold or i % 2 == 1, color)


def para(text, indent=True, size=12, align='both', color=None, after=4):
    p = doc.add_paragraph()
    fmt(p, align, indent, after=after)
    add_rich(p, text, size, color=color)
    return p


def bullets(items, size=12):
    for it in items:
        p = doc.add_paragraph()
        fmt(p, 'both', False, after=2)
        p.paragraph_format.left_indent = Pt(18)
        p.paragraph_format.first_line_indent = Pt(-12)
        add_rich(p, '▪ ' + it, size)


def bottom_border(p, color='8B1A1A', sz='12'):
    ppr = p._element.get_or_add_pPr()
    b, bt = OxmlElement('w:pBdr'), OxmlElement('w:bottom')
    for k, v in (('val', 'single'), ('sz', sz), ('space', '2'), ('color', color)):
        bt.set(qn('w:' + k), v)
    b.append(bt)
    ppr.append(b)


def h1(text):
    p = doc.add_paragraph()
    fmt(p, 'left', False, before=14, after=6, line=1.2)
    p.paragraph_format.keep_with_next = True
    add_rich(p, text, 15, HEI, RED, True)
    bottom_border(p)


def h2(text):
    p = doc.add_paragraph()
    fmt(p, 'left', False, before=8, after=3, line=1.2)
    p.paragraph_format.keep_with_next = True
    add_rich(p, text, 12.5, HEI, RGBColor(0x26, 0x26, 0x26), True)


def shade(cell, fill):
    s = OxmlElement('w:shd')
    s.set(qn('w:val'), 'clear')
    s.set(qn('w:color'), 'auto')
    s.set(qn('w:fill'), fill)
    cell._element.get_or_add_tcPr().append(s)


def borders(tbl, color='BFBFBF', sz='4'):
    b = OxmlElement('w:tblBorders')
    for e in ('top', 'left', 'bottom', 'right', 'insideH', 'insideV'):
        x = OxmlElement('w:' + e)
        for k, v in (('val', 'single'), ('sz', sz), ('space', '0'), ('color', color)):
            x.set(qn('w:' + k), v)
        b.append(x)
    tbl._element.tblPr.append(b)


def cell_margins(tbl, top=60, bottom=60, left=90, right=90):
    m = OxmlElement('w:tblCellMar')
    for k, v in (('top', top), ('bottom', bottom), ('left', left), ('right', right)):
        x = OxmlElement('w:' + k)
        x.set(qn('w:w'), str(v))
        x.set(qn('w:type'), 'dxa')
        m.append(x)
    tbl._element.tblPr.append(m)


def cell_text(cell, text, size=10.5, east=BODY, bold=False, color=None, align='both'):
    cell.text = ''
    for i, ln in enumerate(text.split('\n')):
        p = cell.paragraphs[0] if i == 0 else cell.add_paragraph()
        fmt(p, align, False, after=0, line=1.25)
        add_rich(p, ln, size, east, color, bold)


NUM = re.compile(r'^[-+]?\d{1,3}(,\d{3})*\.\d{2}$')


def fix_widths(t, widths):
    t.autofit = False
    for gc, w in zip(t._element.tblGrid.findall(qn('w:gridCol')), widths):
        gc.set(qn('w:w'), str(int(w * 567)))
    lay = OxmlElement('w:tblLayout')
    lay.set(qn('w:type'), 'fixed')
    t._element.tblPr.append(lay)
    for row in t.rows:
        row._tr.get_or_add_trPr().append(OxmlElement('w:cantSplit'))
        for i, w in enumerate(widths):
            row.cells[i].width = Cm(w)


def table(headers, rows, widths, size=10.5, num_right=True, first_bold=False):
    t = doc.add_table(rows=1, cols=len(headers))
    t.alignment = WD_TABLE_ALIGNMENT.CENTER
    borders(t)
    cell_margins(t)
    for i, h in enumerate(headers):
        c = t.rows[0].cells[i]
        shade(c, '8B1A1A')
        cell_text(c, h, size, HEI, False, RGBColor(0xFF, 0xFF, 0xFF), 'center')
    th = OxmlElement('w:tblHeader')
    th.set(qn('w:val'), 'true')
    t.rows[0]._tr.get_or_add_trPr().append(th)
    for ri, row in enumerate(rows):
        cells = t.add_row().cells
        for i, v in enumerate(row):
            al = 'right' if (num_right and NUM.match(v.replace('*', ''))) else 'left'
            cell_text(cells[i], v, size, HEI if (first_bold and i == 0) else BODY, False, None, al)
            if ri % 2 == 1:
                shade(cells[i], 'F7F2EF')
    fix_widths(t, widths)
    doc.add_paragraph().paragraph_format.space_after = Pt(2)
    return t


def kv(rows, w1=2.6, w2=14.0, size=10.5):
    t = doc.add_table(rows=0, cols=2)
    t.alignment = WD_TABLE_ALIGNMENT.CENTER
    borders(t)
    cell_margins(t)
    for k, v in rows:
        c = t.add_row().cells
        shade(c[0], 'F2E6E1')
        cell_text(c[0], k, size, HEI, False, RED, 'center')
        cell_text(c[1], v, size, align='left')
    fix_widths(t, [w1, w2])
    doc.add_paragraph().paragraph_format.space_after = Pt(2)


def callout(title, lines, fill='FBF1EC', edge='8B1A1A'):
    t = doc.add_table(rows=1, cols=1)
    t.alignment = WD_TABLE_ALIGNMENT.CENTER
    b = OxmlElement('w:tblBorders')
    for e, on in (('top', 0), ('left', 1), ('bottom', 0), ('right', 0)):
        x = OxmlElement('w:' + e)
        if on:
            for k, v in (('val', 'single'), ('sz', '36'), ('space', '0'), ('color', edge)):
                x.set(qn('w:' + k), v)
        else:
            x.set(qn('w:val'), 'nil')
        b.append(x)
    t._element.tblPr.append(b)
    cell_margins(t, 100, 100, 180, 140)
    c = t.rows[0].cells[0]
    shade(c, fill)
    fix_widths(t, [16.6])
    cell_text(c, title, 11.5, HEI, False, RGBColor(int(edge[:2], 16), int(edge[2:4], 16), int(edge[4:], 16)), 'left')
    for ln in lines:
        p = c.add_paragraph()
        fmt(p, 'both', False, after=1, line=1.3)
        add_rich(p, ln, 11)
    doc.add_paragraph().paragraph_format.space_after = Pt(2)


def field(p, code):
    r = p.add_run()
    f1 = OxmlElement('w:fldChar')
    f1.set(qn('w:fldCharType'), 'begin')
    r._element.append(f1)
    r2 = p.add_run()
    it = OxmlElement('w:instrText')
    it.set(qn('xml:space'), 'preserve')
    it.text = code
    r2._element.append(it)
    r3 = p.add_run()
    f2 = OxmlElement('w:fldChar')
    f2.set(qn('w:fldCharType'), 'end')
    r3._element.append(f2)
    for x in (r, r2, r3):
        set_font(x, BODY, 9, color=GRAY)


页眉 = ''
exec(open(CONTENT, encoding='utf-8').read())

hp = sec.header.paragraphs[0]
fmt(hp, 'right', False, after=0, line=1)
add_rich(hp, (页眉 + '　' if 页眉 else '') + '庭前作战包　内部资料，不得对外提交', 9, BODY, GRAY)
bottom_border(hp, 'BFBFBF', '4')
fp = sec.footer.paragraphs[0]
fmt(fp, 'center', False, after=0, line=1)
add_rich(fp, '第 ', 9, BODY, GRAY)
field(fp, 'PAGE')
add_rich(fp, ' 页　共 ', 9, BODY, GRAY)
field(fp, 'NUMPAGES')
add_rich(fp, ' 页', 9, BODY, GRAY)

doc.save(OUT)
print('已生成', OUT)
