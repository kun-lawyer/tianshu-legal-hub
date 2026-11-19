#!/usr/bin/env python3
"""公文排版脚本 - 使用 lxml 程序化处理 OOXML 格式替换。

用法:
    python format_gongwen.py <unpacked_dir> [--skip-styles] [--skip-punctuation]

参数:
    unpacked_dir         已解包的 docx 目录（包含 word/document.xml）
    --skip-styles        跳过 styles.xml 的处理
    --skip-punctuation   跳过标点规范化（默认开启：中英标点统一、双引号配对、省略号/破折号规整）
    --skip-page-number   不加页码（默认开启：页脚居中阿拉伯数字，原文档已有页脚时自动跳过）
"""

import sys
import re
import uuid
import copy
from pathlib import Path
from lxml import etree

# OOXML 命名空间
NS = {
    "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
    "w14": "http://schemas.microsoft.com/office/word/2010/wordml",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "mc": "http://schemas.openxmlformats.org/markup-compatibility/2006",
}

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
W14 = "http://schemas.microsoft.com/office/word/2010/wordml"


def qn(tag):
    """生成带命名空间的标签名，如 qn('w:p') -> '{http://...}p'"""
    prefix, local = tag.split(":")
    return f"{{{NS[prefix]}}}{local}"


def get_val(elem, attr="val"):
    """获取 w:val 属性值"""
    return elem.get(qn("w:" + attr)) if elem is not None else None


def set_val(elem, val, attr="val"):
    """设置 w:val 属性值"""
    elem.set(qn("w:" + attr), str(val))


def get_or_create(parent, tag_qn, insert_before=None):
    """获取或创建子元素"""
    child = parent.find(tag_qn)
    if child is None:
        child = etree.SubElement(parent, tag_qn)
        # 如果指定了 insert_before，尝试调整位置
        if insert_before:
            for i, sibling in enumerate(parent):
                if sibling.tag == insert_before:
                    parent.remove(child)
                    parent.insert(i, child)
                    break
    return child


def get_para_text(para):
    """获取段落的纯文本内容"""
    texts = []
    for r in para.findall(qn("w:r")):
        for t in r.findall(qn("w:t")):
            if t.text:
                texts.append(t.text)
    return "".join(texts)


def get_font_size(para):
    """获取段落中最大的字号（sz val）"""
    sizes = []
    # 检查段落级 rPr
    ppr = para.find(qn("w:pPr"))
    if ppr is not None:
        rpr = ppr.find(qn("w:rPr"))
        if rpr is not None:
            sz = rpr.find(qn("w:sz"))
            if sz is not None:
                v = get_val(sz)
                if v:
                    sizes.append(int(v))
    # 检查 run 级 rPr
    for r in para.findall(qn("w:r")):
        rpr = r.find(qn("w:rPr"))
        if rpr is not None:
            sz = rpr.find(qn("w:sz"))
            if sz is not None:
                v = get_val(sz)
                if v:
                    sizes.append(int(v))
    return max(sizes) if sizes else 0


def is_centered(para):
    """判断段落是否居中"""
    ppr = para.find(qn("w:pPr"))
    if ppr is not None:
        jc = ppr.find(qn("w:jc"))
        if jc is not None:
            return get_val(jc) == "center"
    return False


def is_right_aligned(para):
    """判断段落是否右对齐"""
    ppr = para.find(qn("w:pPr"))
    if ppr is not None:
        jc = ppr.find(qn("w:jc"))
        if jc is not None:
            return get_val(jc) == "right"
    return False


def is_empty_para(para):
    """判断是否为空段落（无文本内容）"""
    return get_para_text(para).strip() == ""


def has_pbdr(para):
    """判断段落是否包含边框装饰"""
    ppr = para.find(qn("w:pPr"))
    if ppr is not None:
        return ppr.find(qn("w:pBdr")) is not None
    return False


def _is_heading_like(text):
    """标题应为短行且无句末标点。以序号开头但成句成段的是正文，不能按标题加粗。"""
    return len(text) <= 50 and not re.search(r"[。！？]", text)


def is_level1_heading(text):
    """判断是否为一级标题（一、二、...）"""
    t = text.strip()
    if not re.match(r"^[一二三四五六七八九十]+、", t):
        return False
    return _is_heading_like(t)


def is_level2_heading(text):
    """判断是否为二级标题（(一)(二)...）"""
    t = text.strip()
    if not re.match(r"^[（(][一二三四五六七八九十]+[）)]", t):
        return False
    return _is_heading_like(t)


def gen_para_id():
    """生成唯一的 paraId"""
    return uuid.uuid4().hex[:8].upper()


# ==================== 标点规范化 ====================

# 各种双引号变体（含 ASCII 直引号）统一按出现次序配对为中文弯引号
_DOUBLE_QUOTES = set('"“”‟„「」')
# 保护区间：URL、邮箱内部的标点不动
_PROTECT_RE = re.compile(r'(?:https?://|www\.)\S+|[\w.\-]+@[\w.\-]+', re.IGNORECASE)


def _is_cn(ch):
    """判断单个字符是否为中文（基本汉字区）"""
    return bool(ch) and "一" <= ch <= "鿿"


def _normalize_run_text(t_nodes):
    """单个 w:t 内的变长替换：省略号、破折号。就地改写。"""
    for t in t_nodes:
        s = t.text
        if not s:
            continue
        new = re.sub(r"\.{3,}", "……", s)
        new = re.sub(r"。{2,}", "……", new)
        new = re.sub(r"…{1,}", "……", new)
        new = re.sub(r"-{2,}", "\u2014\u2014", new)
        new = re.sub("\u2014{1,}", "\u2014\u2014", new)
        if new != s:
            t.text = new


def _protected_mask(text):
    """标记 URL/邮箱区间，这些位置的标点不规范化"""
    mask = [False] * len(text)
    for m in _PROTECT_RE.finditer(text):
        for i in range(m.start(), m.end()):
            mask[i] = True
    return mask


def normalize_punctuation_in_para(para):
    """规范化单个段落的标点，保留 run 结构（等长替换写回）。

    规则：半角标点→全角（仅含中文段落）、中文后逗号句号→全角、双引号按次序配对、
    省略号/破折号规整。单引号不处理（避免英文撇号 don't 被误伤）。
    """
    t_nodes = [t for t in para.iter(qn("w:t"))]
    if not t_nodes:
        return

    # 1) 单 w:t 内的变长规则
    _normalize_run_text(t_nodes)

    # 2) 拼整段文本做等长替换
    segments = [(t, t.text or "") for t in t_nodes]
    full = "".join(s for _, s in segments)
    if not full:
        return

    contains_cn = bool(re.search(r"[一-鿿]", full))
    mask = _protected_mask(full)

    out = []
    dq_open = True  # 段落内下一个双引号是否为左引号
    for i, ch in enumerate(full):
        if mask[i]:
            out.append(ch)
            continue
        prev = full[i - 1] if i > 0 else ""
        nxt = full[i + 1] if i + 1 < len(full) else ""
        if ch in _DOUBLE_QUOTES:
            out.append("“" if dq_open else "”")
            dq_open = not dq_open
        elif ch == "," and _is_cn(prev):
            out.append("，")
        elif ch == "." and _is_cn(prev):
            out.append("。")
        elif ch == "(" and contains_cn:
            out.append("（")
        elif ch == ")" and contains_cn:
            out.append("）")
        elif ch == ";" and contains_cn:
            out.append("；")
        elif ch == "?" and contains_cn:
            out.append("？")
        elif ch == "!" and contains_cn:
            out.append("！")
        elif ch == ":" and contains_cn and not (
            prev.isascii() and prev.isalnum() and nxt.isascii() and nxt.isalnum()
        ):
            # 冒号两侧都是英文/数字时保留（如时间 12:30、标准号 9001:2015）
            out.append("：")
        else:
            out.append(ch)

    new_full = "".join(out)
    if new_full == full:
        return

    # 3) 等长写回各 w:t
    idx = 0
    for t, s in segments:
        n = len(s)
        if n:
            seg = new_full[idx:idx + n]
            if seg != s:
                t.text = seg
        idx += n


# ==================== 格式设置函数 ====================

def set_font(rpr, font_east="仿宋_GB2312", font_ascii="Times New Roman",
             font_hAnsi="Times New Roman", font_cs=None):
    """设置 rPr 中的字体"""
    rfonts = rpr.find(qn("w:rFonts"))
    if rfonts is None:
        rfonts = etree.SubElement(rpr, qn("w:rFonts"))
        # 移到 rPr 的第一个子元素位置
        rpr.remove(rfonts)
        rpr.insert(0, rfonts)

    # 清除所有现有属性
    for attr in list(rfonts.attrib.keys()):
        del rfonts.attrib[attr]

    rfonts.set(qn("w:hint"), "eastAsia")
    rfonts.set(qn("w:ascii"), font_ascii)
    rfonts.set(qn("w:hAnsi"), font_hAnsi)
    rfonts.set(qn("w:eastAsia"), font_east)
    if font_cs:
        rfonts.set(qn("w:cs"), font_cs)


def set_font_size(rpr, sz_val, sz_cs_val=None):
    """设置 rPr 中的字号"""
    sz = rpr.find(qn("w:sz"))
    if sz is None:
        sz = etree.SubElement(rpr, qn("w:sz"))
    set_val(sz, sz_val)

    szCs = rpr.find(qn("w:szCs"))
    if sz_cs_val is not None:
        if szCs is None:
            szCs = etree.SubElement(rpr, qn("w:szCs"))
        set_val(szCs, sz_cs_val)
    else:
        # 移除 szCs
        if szCs is not None:
            rpr.remove(szCs)


def set_bold(rpr, bold=True):
    """设置/取消加粗"""
    b = rpr.find(qn("w:b"))
    bCs = rpr.find(qn("w:bCs"))
    if bold:
        if b is None:
            etree.SubElement(rpr, qn("w:b"))
        if bCs is None:
            etree.SubElement(rpr, qn("w:bCs"))
    else:
        if b is not None:
            rpr.remove(b)
        if bCs is not None:
            rpr.remove(bCs)


def ensure_ppr(para):
    """确保段落有 pPr 元素，没有则创建"""
    ppr = para.find(qn("w:pPr"))
    if ppr is None:
        ppr = etree.SubElement(para, qn("w:pPr"))
        # 移到段落第一个子元素
        para.remove(ppr)
        para.insert(0, ppr)
    return ppr


def ensure_rpr_in_ppr(ppr):
    """确保 pPr 中有 rPr 元素"""
    rpr = ppr.find(qn("w:rPr"))
    if rpr is None:
        rpr = etree.SubElement(ppr, qn("w:rPr"))
    return rpr


def ensure_rpr_in_run(run):
    """确保 run 中有 rPr 元素"""
    rpr = run.find(qn("w:rPr"))
    if rpr is None:
        rpr = etree.SubElement(run, qn("w:rPr"))
        run.remove(rpr)
        run.insert(0, rpr)
    return rpr


def set_spacing(ppr, line="560", line_rule="exact", before=None, after=None):
    """设置段落间距"""
    spacing = ppr.find(qn("w:spacing"))
    if spacing is None:
        spacing = etree.SubElement(ppr, qn("w:spacing"))

    # 清除所有现有属性
    for attr in list(spacing.attrib.keys()):
        del spacing.attrib[attr]

    # 固定行距段落默认段前段后归零；否则段落级无 after 会回退到模板 docDefaults 的段后间距（默认 200=10磅）
    if line is not None:
        if before is None:
            before = 0
        if after is None:
            after = 0

    if before is not None:
        spacing.set(qn("w:before"), str(before))
    if after is not None:
        spacing.set(qn("w:after"), str(after))
    if line is not None:
        spacing.set(qn("w:line"), str(line))
    if line_rule is not None:
        spacing.set(qn("w:lineRule"), line_rule)


def set_indent(ppr, first_line="560", first_line_chars="200"):
    """设置首行缩进"""
    ind = ppr.find(qn("w:ind"))
    if ind is None:
        ind = etree.SubElement(ppr, qn("w:ind"))

    # 清除所有现有属性
    for attr in list(ind.attrib.keys()):
        del ind.attrib[attr]

    ind.set(qn("w:firstLine"), first_line)
    ind.set(qn("w:firstLineChars"), first_line_chars)


def remove_indent(ppr):
    """移除缩进"""
    ind = ppr.find(qn("w:ind"))
    if ind is not None:
        ppr.remove(ind)


def set_alignment(ppr, jc_val):
    """设置段落对齐方式"""
    jc = ppr.find(qn("w:jc"))
    if jc is None:
        jc = etree.SubElement(ppr, qn("w:jc"))
    set_val(jc, jc_val)


# ==================== 段落分类 ====================

def classify_paragraphs(body):
    """分类所有段落，返回分类结果字典"""
    paras = body.findall(qn("w:p"))
    result = {
        "titles": [],       # 标题段落
        "greeting": None,   # 称呼行
        "signatures": [],   # 签名段落
        "level1": [],       # 一级标题
        "level2": [],       # 二级标题
        "body": [],         # 正文
        "decorations": [],  # 装饰性段落（待删除）
        "empty": [],        # 空段落
    }

    # 第一遍：识别标题和签名
    title_end_idx = -1
    for i, p in enumerate(paras):
        if is_centered(p) and get_font_size(p) >= 36 and not is_empty_para(p):
            result["titles"].append(p)
            title_end_idx = i

    # 兜底：源文档标题未预先居中放大时上面识别不到，会静默把标题当正文排。
    # 取首个非空段落，短、无句末标点、非称呼行、非编号标题的，认定为公文标题。
    if not result["titles"]:
        for i, p in enumerate(paras):
            if is_empty_para(p):
                continue
            text = get_para_text(p).strip()
            if (text and len(text) <= 30 and not re.search(r"[。！？：:]", text)
                    and not is_right_aligned(p)
                    and not is_level1_heading(text) and not is_level2_heading(text)):
                result["titles"].append(p)
                title_end_idx = i
                print(f"    [兜底] 首段“{text}”按公文标题处理（源文档未预设居中放大）")
            break

    for p in paras:
        if is_right_aligned(p) and not is_empty_para(p):
            result["signatures"].append(p)

    # 第二遍：识别称呼行（标题后首个非空段落）
    if title_end_idx >= 0:
        for i in range(title_end_idx + 1, len(paras)):
            p = paras[i]
            if is_empty_para(p):
                continue
            text = get_para_text(p).strip()
            if text and not is_right_aligned(p):
                # 称呼行通常不缩进，或者以冒号结尾
                ppr = p.find(qn("w:pPr"))
                has_indent = False
                if ppr is not None:
                    ind = ppr.find(qn("w:ind"))
                    if ind is not None:
                        fl = ind.get(qn("w:firstLine"))
                        if fl and int(fl) > 0:
                            has_indent = True
                if not has_indent or text.endswith("：") or text.endswith(":"):
                    result["greeting"] = p
                break

    # 第三遍：分类剩余段落
    for p in paras:
        if p in result["titles"] or p in result["signatures"] or p is result["greeting"]:
            continue

        if has_pbdr(p) and is_empty_para(p):
            result["decorations"].append(p)
            continue

        if is_empty_para(p):
            result["empty"].append(p)
            continue

        text = get_para_text(p)
        if is_level1_heading(text):
            result["level1"].append(p)
        elif is_level2_heading(text):
            result["level2"].append(p)
        else:
            result["body"].append(p)

    return result


# ==================== 主要处理函数 ====================

def process_page_setup(body):
    """处理页面设置"""
    sect_pr = body.find(qn("w:sectPr"))
    if sect_pr is None:
        sect_pr = etree.SubElement(body, qn("w:sectPr"))

    # 页面大小 - A4
    pg_sz = sect_pr.find(qn("w:pgSz"))
    if pg_sz is None:
        pg_sz = etree.SubElement(sect_pr, qn("w:pgSz"))
    # 保留 orient 属性（如果有）
    orient = pg_sz.get(qn("w:orient"))
    for attr in list(pg_sz.attrib.keys()):
        del pg_sz.attrib[attr]
    pg_sz.set(qn("w:w"), "11906")
    pg_sz.set(qn("w:h"), "16838")
    if orient:
        pg_sz.set(qn("w:orient"), orient)

    # 页边距
    pg_mar = sect_pr.find(qn("w:pgMar"))
    if pg_mar is None:
        pg_mar = etree.SubElement(sect_pr, qn("w:pgMar"))
    for attr in list(pg_mar.attrib.keys()):
        del pg_mar.attrib[attr]
    # 页边距：上下 1440 DXA = 2.54cm，左右 1800 DXA = 3.18cm（Word 默认页边距）
    pg_mar.set(qn("w:top"), "1440")
    pg_mar.set(qn("w:right"), "1800")
    pg_mar.set(qn("w:bottom"), "1440")
    pg_mar.set(qn("w:left"), "1800")
    pg_mar.set(qn("w:header"), "851")
    pg_mar.set(qn("w:footer"), "992")
    pg_mar.set(qn("w:gutter"), "0")

    # 分栏
    cols = sect_pr.find(qn("w:cols"))
    if cols is None:
        cols = etree.SubElement(sect_pr, qn("w:cols"))
    for attr in list(cols.attrib.keys()):
        del cols.attrib[attr]
    cols.set(qn("w:space"), "720")
    cols.set(qn("w:num"), "1")

    # 文档网格
    doc_grid = sect_pr.find(qn("w:docGrid"))
    if doc_grid is None:
        doc_grid = etree.SubElement(sect_pr, qn("w:docGrid"))
    for attr in list(doc_grid.attrib.keys()):
        del doc_grid.attrib[attr]
    doc_grid.set(qn("w:linePitch"), "360")
    doc_grid.set(qn("w:charSpace"), "0")


def process_title(para):
    """处理标题段落"""
    ppr = ensure_ppr(para)

    # 设置居中
    set_alignment(ppr, "center")

    # 段落级 rPr
    rpr = ensure_rpr_in_ppr(ppr)
    set_font(rpr, font_east="方正公文小标宋", font_ascii="方正公文小标宋",
             font_hAnsi="方正公文小标宋", font_cs="方正公文小标宋")
    set_font_size(rpr, "44", "52")
    set_bold(rpr, True)

    # 标题行距
    set_spacing(ppr, line="560", line_rule="exact")

    # 移除缩进
    remove_indent(ppr)

    # run 级 rPr
    for run in para.findall(qn("w:r")):
        run_rpr = ensure_rpr_in_run(run)
        set_font(run_rpr, font_east="方正公文小标宋", font_ascii="方正公文小标宋",
                 font_hAnsi="方正公文小标宋", font_cs="方正公文小标宋")
        set_font_size(run_rpr, "44", "52")
        set_bold(run_rpr, True)


def insert_blank_line_after_title(body, last_title_para):
    """在最后一个标题段落后插入空行"""
    paras = list(body)
    idx = list(body).index(last_title_para)

    # 检查下一个段落是否已经是空行
    if idx + 1 < len(paras):
        next_p = paras[idx + 1]
        if is_empty_para(next_p) and not has_pbdr(next_p):
            # 已有空行，只需确保格式正确
            ppr = ensure_ppr(next_p)
            rpr = ensure_rpr_in_ppr(ppr)
            set_font(rpr)
            set_font_size(rpr, "28")
            # 设置行距
            set_spacing(ppr, line="560", line_rule="exact")
            return

    # 创建新空段落
    blank_p = etree.Element(qn("w:p"))
    blank_p.set(qn("w14:paraId"), gen_para_id())

    ppr = etree.SubElement(blank_p, qn("w:pPr"))
    # 行距
    set_spacing(ppr, line="560", line_rule="exact")
    rpr = etree.SubElement(ppr, qn("w:rPr"))
    set_font(rpr)
    set_font_size(rpr, "28")

    # 插入到标题后
    last_title_para.addnext(blank_p)


def process_greeting(para):
    """处理称呼行"""
    ppr = ensure_ppr(para)

    # 移除缩进
    remove_indent(ppr)

    # 行距
    set_spacing(ppr, line="560", line_rule="exact")

    # 段落级 rPr
    rpr = ensure_rpr_in_ppr(ppr)
    set_font(rpr)
    set_font_size(rpr, "28")
    set_bold(rpr, True)

    # run 级
    for run in para.findall(qn("w:r")):
        run_rpr = ensure_rpr_in_run(run)
        set_font(run_rpr)
        set_font_size(run_rpr, "28")
        set_bold(run_rpr, True)


def process_body_para(para):
    """处理正文段落"""
    ppr = ensure_ppr(para)

    # 首行缩进
    set_indent(ppr, first_line="560", first_line_chars="200")
    set_alignment(ppr, "both")
    set_alignment(ppr, "both")

    # 行距，清除段前段后
    set_spacing(ppr, line="560", line_rule="exact")

    # 段落级 rPr
    rpr = ensure_rpr_in_ppr(ppr)
    set_font(rpr)
    set_font_size(rpr, "28")

    # run 级
    for run in para.findall(qn("w:r")):
        run_rpr = ensure_rpr_in_run(run)
        set_font(run_rpr)
        set_font_size(run_rpr, "28")


def process_level1_heading(para):
    """处理一级标题段落"""
    ppr = ensure_ppr(para)

    # 一级标题缩进 562
    set_indent(ppr, first_line="562", first_line_chars="200")
    set_alignment(ppr, "both")
    set_alignment(ppr, "both")

    # 行距
    set_spacing(ppr, line="560", line_rule="exact")

    # 段落级 rPr
    rpr = ensure_rpr_in_ppr(ppr)
    set_font(rpr)
    set_font_size(rpr, "28")
    set_bold(rpr, True)

    # run 级
    for run in para.findall(qn("w:r")):
        run_rpr = ensure_rpr_in_run(run)
        set_font(run_rpr)
        set_font_size(run_rpr, "28")
        set_bold(run_rpr, True)


def process_level2_heading(para):
    """处理二级标题段落"""
    ppr = ensure_ppr(para)

    # 二级标题也缩进
    set_indent(ppr, first_line="560", first_line_chars="200")
    set_alignment(ppr, "both")
    set_alignment(ppr, "both")

    # 行距
    set_spacing(ppr, line="560", line_rule="exact")

    # 段落级 rPr
    rpr = ensure_rpr_in_ppr(ppr)
    set_font(rpr)
    set_font_size(rpr, "28")
    set_bold(rpr, True)

    # run 级
    for run in para.findall(qn("w:r")):
        run_rpr = ensure_rpr_in_run(run)
        set_font(run_rpr)
        set_font_size(run_rpr, "28")
        set_bold(run_rpr, True)


def process_signature(para):
    """处理签名段落"""
    ppr = ensure_ppr(para)

    # 保持右对齐
    set_alignment(ppr, "right")

    # 移除缩进
    remove_indent(ppr)

    # 签名区域：检查是否有留白 spacing，如有则保留 before，否则不设置 spacing
    existing_spacing = ppr.find(qn("w:spacing"))
    before_val = None
    if existing_spacing is not None:
        before_val = existing_spacing.get(qn("w:before"))

    # 清除行距，但保留 before 留白
    if before_val:
        set_spacing(ppr, line=None, line_rule=None, before=before_val)
    else:
        # 签名段落不设置行距
        spacing = ppr.find(qn("w:spacing"))
        if spacing is not None:
            ppr.remove(spacing)

    # 段落级 rPr
    rpr = ensure_rpr_in_ppr(ppr)
    set_font(rpr)
    set_font_size(rpr, "28")

    # run 级
    for run in para.findall(qn("w:r")):
        run_rpr = ensure_rpr_in_run(run)
        set_font(run_rpr)
        set_font_size(run_rpr, "28")


def process_empty_para(para):
    """处理空段落 - 统一格式"""
    ppr = ensure_ppr(para)
    set_spacing(ppr, line="560", line_rule="exact")

    rpr = ensure_rpr_in_ppr(ppr)
    set_font(rpr)
    set_font_size(rpr, "28")


def remove_decorations(body, decoration_paras):
    """删除装饰性段落"""
    for p in decoration_paras:
        body.remove(p)


def process_styles(styles_path):
    """处理 styles.xml，统一默认样式中的字体和字号"""
    if not styles_path.exists():
        print(f"  styles.xml 不存在，跳过: {styles_path}")
        return

    tree = etree.parse(str(styles_path))
    root = tree.getroot()

    # 处理默认 run 属性 (w:docDefaults/w:rPrDefault/w:rPr)
    doc_defaults = root.find(qn("w:docDefaults"))
    if doc_defaults is not None:
        rpr_default = doc_defaults.find(qn("w:rPrDefault"))
        if rpr_default is not None:
            rpr = rpr_default.find(qn("w:rPr"))
            if rpr is not None:
                set_font(rpr)
                set_font_size(rpr, "28")

    # 处理 Normal 样式
    for style in root.findall(qn("w:style")):
        style_id = style.get(qn("w:styleId"))
        style_type = style.get(qn("w:type"))

        if style_type == "paragraph":
            # 处理段落样式中的 rPr
            rpr = style.find(qn("w:rPr"))
            if rpr is not None:
                # 不处理标题样式（Heading 系列），因为我们在 document.xml 中直接处理
                name_elem = style.find(qn("w:name"))
                name_val = get_val(name_elem) if name_elem is not None else ""
                if name_val and "heading" in name_val.lower():
                    continue

                set_font(rpr)
                set_font_size(rpr, "28")

            # 处理段落样式中的 pPr
            ppr = style.find(qn("w:pPr"))
            if ppr is not None:
                spacing = ppr.find(qn("w:spacing"))
                if spacing is not None:
                    # 统一行距
                    for attr in list(spacing.attrib.keys()):
                        del spacing.attrib[attr]
                    spacing.set(qn("w:line"), "560")
                    spacing.set(qn("w:lineRule"), "exact")

    # 写回
    tree.write(str(styles_path), xml_declaration=True, encoding="UTF-8", standalone=True)
    print(f"  styles.xml 已处理: {styles_path}")


FOOTER_XML = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<w:ftr xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
    '<w:p><w:pPr><w:jc w:val="center"/></w:pPr>'
    '{run}<w:r>{rpr}<w:fldChar w:fldCharType="begin"/></w:r>'
    '<w:r>{rpr}<w:instrText xml:space="preserve"> PAGE </w:instrText></w:r>'
    '<w:r>{rpr}<w:fldChar w:fldCharType="separate"/></w:r>'
    '<w:r>{rpr}<w:t>1</w:t></w:r>'
    '<w:r>{rpr}<w:fldChar w:fldCharType="end"/></w:r>'
    '</w:p></w:ftr>'
)
FOOTER_RPR = ('<w:rPr><w:rFonts w:ascii="Times New Roman" w:hAnsi="Times New Roman" '
              'w:eastAsia="宋体"/><w:sz w:val="28"/><w:szCs w:val="28"/></w:rPr>')


def add_page_number(unpacked, body):
    """页脚居中加阿拉伯数字页码（四号）；原文档已有页脚的不动，避免毁掉母版页脚"""
    sect_pr = body.find(qn("w:sectPr"))
    if sect_pr.find(qn("w:footerReference")) is not None:
        print("    已有页脚，跳过页码")
        return
    word = unpacked / "word"
    name = "footer_gw.xml"
    (word / name).write_text(FOOTER_XML.format(run="", rpr=FOOTER_RPR), encoding="utf-8")

    rels_path = word / "_rels" / "document.xml.rels"
    rels = etree.parse(str(rels_path))
    rid = "rIdGwFooter"
    if not any(r.get("Id") == rid for r in rels.getroot()):
        rel = etree.SubElement(rels.getroot(), "{http://schemas.openxmlformats.org/package/2006/relationships}Relationship")
        rel.set("Id", rid)
        rel.set("Type", "http://schemas.openxmlformats.org/officeDocument/2006/relationships/footer")
        rel.set("Target", name)
        rels.write(str(rels_path), xml_declaration=True, encoding="UTF-8", standalone=True)

    ct_path = unpacked / "[Content_Types].xml"
    ct = etree.parse(str(ct_path))
    part = "/word/" + name
    if not any(o.get("PartName") == part for o in ct.getroot()):
        o = etree.SubElement(ct.getroot(), "{http://schemas.openxmlformats.org/package/2006/content-types}Override")
        o.set("PartName", part)
        o.set("ContentType", "application/vnd.openxmlformats-officedocument.wordprocessingml.footer+xml")
        ct.write(str(ct_path), xml_declaration=True, encoding="UTF-8", standalone=True)

    ref = etree.Element(qn("w:footerReference"))
    ref.set(qn("w:type"), "default")
    ref.set(qn("r:id"), rid)
    sect_pr.insert(0, ref)
    print("    已加页码（页脚居中）")


def format_document(unpacked_dir, skip_styles=False, fix_punctuation=True, page_number=True):
    """主处理函数"""
    unpacked = Path(unpacked_dir)
    doc_path = unpacked / "word" / "document.xml"
    styles_path = unpacked / "word" / "styles.xml"

    if not doc_path.exists():
        print(f"错误: 找不到 {doc_path}")
        sys.exit(1)

    print(f"正在处理: {doc_path}")

    # 解析 XML
    tree = etree.parse(str(doc_path))
    root = tree.getroot()
    body = root.find(qn("w:body"))

    if body is None:
        print("错误: 找不到 w:body 元素")
        sys.exit(1)

    # 1. 页面设置
    print("  [1/8] 设置页面参数...")
    process_page_setup(body)
    if page_number:
        add_page_number(unpacked, body)

    # 标点规范化（可选，需在分类前执行，使编号/标题识别基于规范文本）
    if fix_punctuation:
        print("  [*] 规范化标点...")
        n = 0
        for p in body.iter(qn("w:p")):
            normalize_punctuation_in_para(p)
            n += 1
        print(f"    已扫描 {n} 段（含表格内段落）")

    # 2. 分类段落
    print("  [2/8] 分析文档结构...")
    classified = classify_paragraphs(body)
    print(f"    标题: {len(classified['titles'])} 段")
    print(f"    称呼: {'有' if classified['greeting'] is not None else '无'}")
    print(f"    正文: {len(classified['body'])} 段")
    print(f"    一级标题: {len(classified['level1'])} 段")
    print(f"    二级标题: {len(classified['level2'])} 段")
    print(f"    签名: {len(classified['signatures'])} 段")
    print(f"    装饰: {len(classified['decorations'])} 段")
    print(f"    空段落: {len(classified['empty'])} 段")

    # 3. 处理标题
    print("  [3/8] 处理标题...")
    for p in classified["titles"]:
        process_title(p)

    # 4. 标题后空行
    print("  [4/8] 处理标题后空行...")
    if classified["titles"]:
        insert_blank_line_after_title(body, classified["titles"][-1])

    # 5. 处理称呼行
    print("  [5/8] 处理称呼行...")
    if classified["greeting"] is not None:
        process_greeting(classified["greeting"])

    # 6. 处理正文、标题、签名
    print("  [6/8] 处理正文段落...")
    for p in classified["body"]:
        process_body_para(p)

    for p in classified["level1"]:
        process_level1_heading(p)

    for p in classified["level2"]:
        process_level2_heading(p)

    for p in classified["signatures"]:
        process_signature(p)

    for p in classified["empty"]:
        process_empty_para(p)

    # 7. 删除装饰
    print("  [7/8] 清理装饰元素...")
    remove_decorations(body, classified["decorations"])

    # 8. 处理 styles.xml
    if not skip_styles:
        print("  [8/8] 处理 styles.xml...")
        process_styles(styles_path)
    else:
        print("  [8/8] 跳过 styles.xml（--skip-styles）")

    # 写回 document.xml
    tree.write(str(doc_path), xml_declaration=True, encoding="UTF-8", standalone=True)
    print(f"排版完成: {doc_path}")


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    unpacked_dir = sys.argv[1]
    skip_styles = "--skip-styles" in sys.argv
    fix_punctuation = "--skip-punctuation" not in sys.argv
    page_number = "--skip-page-number" not in sys.argv

    format_document(unpacked_dir, skip_styles, fix_punctuation, page_number)


if __name__ == "__main__":
    main()
