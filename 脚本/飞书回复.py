"""飞书随身助理的回复工具：给本人发 Markdown 消息或文件。只发给本人私聊。

用法：
  python 飞书回复.py text <Markdown 文件>      把文件内容作为富文本消息发出
  python 飞书回复.py file <文件路径>            把文件（docx、pdf、xlsx 等）发到飞书
"""
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cfg  # noqa: E402

if sys.stdout:
    sys.stdout.reconfigure(encoding='utf-8')
NODE = cfg.get('工具.node', 'node')
LARK = [NODE, str(cfg.path('工具.lark_cli') or '')]
OWNER = cfg.get('飞书.本人open_id', '')


def send(args, cwd=None):
    out = subprocess.run(LARK + ['im', '+messages-send', '--user-id', OWNER, '--as', 'bot', *args],
                         capture_output=True, text=True, encoding='utf-8', errors='replace', cwd=cwd, timeout=180)
    try:
        res = json.loads(out.stdout)
    except ValueError:
        res = {'ok': False, 'error': (out.stdout + out.stderr)[-300:]}
    if res.get('ok') is False or out.returncode != 0:
        print('发送失败：', json.dumps(res.get('error', res), ensure_ascii=False)[:300])
        sys.exit(1)
    print('已发送')


def main():
    if not cfg.enabled('飞书'):
        print('飞书模块未启用（config.json 的 飞书.启用）')
        sys.exit(0)
    if len(sys.argv) != 3 or sys.argv[1] not in ('text', 'file'):
        print(__doc__)
        sys.exit(1)
    kind, path = sys.argv[1], Path(sys.argv[2])
    if not path.exists():
        print(f'没有这个文件：{path}')
        sys.exit(1)
    if kind == 'text':
        send(['--markdown', path.read_text(encoding='utf-8')])
        return
    # lark-cli 只收当前目录下的相对路径：复制到临时目录再发
    with tempfile.TemporaryDirectory() as tmp:
        shutil.copy2(path, Path(tmp) / path.name)
        send(['--file', path.name], cwd=tmp)


if __name__ == '__main__':
    main()
