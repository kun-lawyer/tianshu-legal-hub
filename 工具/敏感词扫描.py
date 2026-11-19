"""发布前扫描仓库里的敏感信息。

用法：python 工具/敏感词扫描.py [词表文件]

不带词表只查通用模式（手机号、身份证号、微信号、飞书 open_id、本机用户路径、真实案号）。
词表每行一个词（本人、同事、客户、单位名称等），放在仓库外，不要提交。
有命中时退出码为 1，可挂到 git pre-commit 钩子里。
"""
import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8')
ROOT = Path(__file__).resolve().parent.parent
SKIP_DIRS = {'.git', '__pycache__', 'node_modules', '日志', '晨报', '晚报', '周研判', '助理产出'}
TEXT = {'.py', '.md', '.json', '.html', '.js', '.ts', '.txt', '.ps1', '.cmd', '.yml', '.yaml', '.toml', '.cfg', ''}
PATTERNS = {
    '手机号': re.compile(r'(?<!\d)1[3-9]\d{9}(?!\d)'),
    '身份证号': re.compile(r'(?<!\d)\d{17}[\dXx](?!\d)'),
    '微信号': re.compile(r'wxid_[a-z0-9]{6,}'),
    '飞书 open_id': re.compile(r'\bou_[0-9a-f]{20,}'),
    '本机用户路径': re.compile(r'[Cc]:[\\/]+Users[\\/]+(?!Public|<|你的用户名|xxx)[^\\/\s"\']+'),
    '真实案号': re.compile(r'[（(]20\d\d[）)][京津冀晋蒙辽吉黑沪苏浙皖闽赣鲁豫鄂湘粤桂琼渝川贵云藏陕甘青宁新]\d{2,4}'),
}


def main():
    words = []
    if len(sys.argv) > 1:
        words = [w.strip() for w in Path(sys.argv[1]).read_text(encoding='utf-8').splitlines() if w.strip()]
    me = Path(__file__).resolve()
    hits = 0
    for f in sorted(ROOT.rglob('*')):
        if not f.is_file() or f == me or SKIP_DIRS & set(f.relative_to(ROOT).parts) or f.suffix.lower() not in TEXT:
            continue
        try:
            lines = f.read_text(encoding='utf-8').splitlines()
        except (UnicodeDecodeError, OSError):
            continue
        for no, line in enumerate(lines, 1):
            found = [w for w in words if w in line]
            found += [name for name, p in PATTERNS.items() if p.search(line)]
            if found:
                hits += 1
                print(f'{f.relative_to(ROOT)}:{no}  [{"、".join(found)}]  {line.strip()[:120]}')
    print(f'共 {hits} 处' if hits else '未发现敏感信息')
    sys.exit(1 if hits else 0)


if __name__ == '__main__':
    main()
