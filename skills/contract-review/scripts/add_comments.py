# -*- coding: utf-8 -*-
"""
合同审查：Word 文档批注添加脚本
用法: python add_comments.py <input.docx> <comments.json> [--author 张三律师]

comments.json 格式:
{
  "author": "张三律师",
  "comments": [
    {
      "search_text": "需要批注的原文片段",
      "comment": "【风险等级】高\n【修改建议】...\n【修改原因】..."
    }
  ]
}
"""
import sys
import json
import os
import shutil
import zipfile
import tempfile
import datetime
from lxml import etree

sys.stdout.reconfigure(encoding='utf-8')


def create_comments_xml(comments_data, author):
    """创建 comments.xml 内容"""
    now = datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    W_URI = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
    R_URI = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
    ns = {'w': W_URI, 'r': R_URI}
    W = '{' + W_URI + '}'

    root = etree.Element(f'{W}comments', nsmap=ns)

    for i, c in enumerate(comments_data):
        comment_el = etree.SubElement(root, f'{W}comment')
        comment_el.set(f'{W}id', str(i))
        comment_el.set(f'{W}author', author)
        comment_el.set(f'{W}date', f'{now}Z')
        comment_el.set(f'{W}initials', ''.join([ch[0] for ch in author if '\u4e00' <= ch <= '\u9fff'][:2]) or 'CR')

        lines = c["comment"].split('\n')
        for line in lines:
            p = etree.SubElement(comment_el, f'{W}p')
            r = etree.SubElement(p, f'{W}r')
            t = etree.SubElement(r, f'{W}t')
            t.text = line

    return etree.tostring(root, xml_declaration=True, encoding='UTF-8', standalone=True)


def get_para_text(para, W):
    """获取段落的纯文本"""
    texts = []
    for t in para.iter(f'{W}t'):
        if t.text:
            texts.append(t.text)
    return ''.join(texts)


def add_comments_to_docx(input_path, comments_data, author="审核律师"):
    """向 Word 文档添加批注"""
    W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'

    temp_dir = tempfile.mkdtemp()
    temp_input = os.path.join(temp_dir, 'input.docx')
    temp_output = os.path.join(temp_dir, 'output.docx')

    shutil.copy2(input_path, temp_input)

    success_count = 0
    fail_count = 0

    with zipfile.ZipFile(temp_input, 'r') as zin:
        with zipfile.ZipFile(temp_output, 'w', zipfile.ZIP_DEFLATED) as zout:
            for item in zin.infolist():
                data = zin.read(item.filename)

                if item.filename == 'word/document.xml':
                    tree = etree.fromstring(data)
                    body = tree.find(f'{W}body')
                    all_paragraphs = list(body.iter(f'{W}p'))

                    for comment_id, c in enumerate(comments_data):
                        search = c["search_text"]
                        target_para = None

                        for para in all_paragraphs:
                            pt = get_para_text(para, W)
                            if search in pt:
                                target_para = para
                                break

                        if target_para is not None:
                            range_start = etree.Element(f'{W}commentRangeStart')
                            range_start.set(f'{W}id', str(comment_id))
                            target_para.insert(0, range_start)

                            range_end = etree.SubElement(target_para, f'{W}commentRangeEnd')
                            range_end.set(f'{W}id', str(comment_id))

                            ref_run = etree.SubElement(target_para, f'{W}r')
                            ref_rpr = etree.SubElement(ref_run, f'{W}rPr')
                            ref_style = etree.SubElement(ref_rpr, f'{W}rStyle')
                            ref_style.set(f'{W}val', 'CommentReference')
                            comment_ref = etree.SubElement(ref_run, f'{W}commentReference')
                            comment_ref.set(f'{W}id', str(comment_id))

                            print(f'[OK] 批注 {comment_id}: {search[:30]}...')
                            success_count += 1
                        else:
                            print(f'[WARN] 未找到: {search[:30]}...')
                            fail_count += 1

                    data = etree.tostring(tree, xml_declaration=True, encoding='UTF-8', standalone=True)
                    zout.writestr(item, data)

                elif item.filename == '[Content_Types].xml':
                    tree = etree.fromstring(data)
                    CT = '{http://schemas.openxmlformats.org/package/2006/content-types}'

                    has_comments = any(
                        o.get('PartName') == '/word/comments.xml'
                        for o in tree.findall(f'{CT}Override')
                    )
                    if not has_comments:
                        override = etree.SubElement(tree, f'{CT}Override')
                        override.set('PartName', '/word/comments.xml')
                        override.set('ContentType',
                                     'application/vnd.openxmlformats-officedocument.wordprocessingml.comments+xml')

                    data = etree.tostring(tree, xml_declaration=True, encoding='UTF-8', standalone=True)
                    zout.writestr(item, data)

                elif item.filename == 'word/_rels/document.xml.rels':
                    tree = etree.fromstring(data)
                    REL = '{http://schemas.openxmlformats.org/package/2006/relationships}'

                    max_id = 0
                    for rel in tree.findall(f'{REL}Relationship'):
                        rid = rel.get('Id', '')
                        if rid.startswith('rId'):
                            try:
                                num = int(rid[3:])
                                if num > max_id:
                                    max_id = num
                            except ValueError:
                                pass

                    new_rel = etree.SubElement(tree, f'{REL}Relationship')
                    new_rel.set('Id', f'rId{max_id + 1}')
                    new_rel.set('Type',
                                'http://schemas.openxmlformats.org/officeDocument/2006/relationships/comments')
                    new_rel.set('Target', 'comments.xml')

                    data = etree.tostring(tree, xml_declaration=True, encoding='UTF-8', standalone=True)
                    zout.writestr(item, data)

                else:
                    zout.writestr(item, data)

            comments_xml = create_comments_xml(comments_data, author)
            zout.writestr('word/comments.xml', comments_xml)

    shutil.copy2(temp_output, input_path)
    shutil.rmtree(temp_dir)

    print(f'\n批注添加完成！成功 {success_count} 条，跳过 {fail_count} 条，署名：{author}')
    return success_count, fail_count


if __name__ == '__main__':
    if len(sys.argv) < 3:
        print("用法: python add_comments.py <input.docx> <comments.json> [--author 张三律师]")
        sys.exit(1)

    input_docx = sys.argv[1]
    comments_json = sys.argv[2]
    author = "审核律师"

    if '--author' in sys.argv:
        idx = sys.argv.index('--author')
        if idx + 1 < len(sys.argv):
            author = sys.argv[idx + 1]

    with open(comments_json, 'r', encoding='utf-8') as f:
        data = json.load(f)

    comments = data.get('comments', [])
    author = data.get('author', author)

    add_comments_to_docx(input_docx, comments, author)
