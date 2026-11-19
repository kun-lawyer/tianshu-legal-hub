"""知识库每日刷新：调用配置的知识库刷新脚本（PowerShell，config.json 的 工具.知识库刷新脚本），失败时给中台交一张通知。

不交 failed：刷新一轮要调模型补摘要，失败后让中台自动重派十次代价太大，改为通知本人处理。
"""
import json
import subprocess
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, str(Path(__file__).resolve().parent))
import cfg  # noqa: E402

SCRIPT = cfg.path('工具.知识库刷新脚本')
LOG = SCRIPT.parent / '_daily_update.log' if SCRIPT else None
PWSH = cfg.get('工具.pwsh', 'pwsh')


def main():
    if SCRIPT is None:
        print('未配置知识库刷新脚本')
        return
    run = subprocess.run([PWSH, '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(SCRIPT)],
                         capture_output=True, text=True, encoding='utf-8', errors='replace')
    if run.returncode == 0:
        print('知识库刷新完成')
        return
    tail = LOG.read_text(encoding='utf-8', errors='replace').splitlines()[-8:] if LOG.exists() else []
    notice = {'title': f'知识库刷新失败（退出码 {run.returncode}）',
              'text': '日志最后几行：\n' + '\n'.join(tail) + f'\n\n完整日志：{LOG}',
              'key': 'kb-refresh-failed'}
    print(f'知识库刷新失败，退出码 {run.returncode}')
    print()
    print('```agent-autoloop')
    print(json.dumps({'notice': [notice]}, ensure_ascii=False))
    print('```')


if __name__ == '__main__':
    main()
