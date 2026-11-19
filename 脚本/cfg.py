"""读取天枢中台的本机配置。

配置文件默认是仓库根目录的 config.json（从 config.example.json 复制后填写），
也可用环境变量 TIANSHU_CONFIG 指到别处。所有路径支持 ~ 和 %APPDATA% 这类环境变量。
"""
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FILE = Path(os.environ.get('TIANSHU_CONFIG') or ROOT / 'config.json')
if not FILE.exists():
    raise SystemExit(f'缺少配置文件 {FILE}：请把 config.example.json 复制为 config.json 并按注释填写')
DATA = json.loads(FILE.read_text(encoding='utf-8'))


def get(key, default=None):
    """按点号取值，如 get('目录.顾问单位')。"""
    cur = DATA
    for part in key.split('.'):
        if not isinstance(cur, dict) or part not in cur:
            return default
        cur = cur[part]
    return cur


def _expand(raw):
    p = Path(os.path.expandvars(os.path.expanduser(str(raw))))
    return p if p.is_absolute() else ROOT / p


def path(key, default=''):
    """取路径，相对路径按仓库根目录解析。未填写时返回 None，调用方据此跳过该功能。"""
    raw = get(key, default)
    return _expand(raw) if raw else None


def paths(key):
    return [_expand(p) for p in get(key, []) or [] if p]


def enabled(module):
    """可选模块开关：微信、飞书、送审收件、知识库。"""
    return bool(get(f'{module}.启用', False))


def load_json(key, default=None):
    p = path(key)
    if p is None or not p.exists():
        return {} if default is None else default
    return json.loads(p.read_text(encoding='utf-8'))


ME = get('本人', '')
COLLEAGUES = set(get('同事', []) or [])
PLACE_WORDS = get('地名停用词', []) or []
TZ_HOURS = get('时区', 8)


def place_pattern():
    """地名停用词拼成正则片段，供各脚本的 STOP 正则使用；未配置时返回不会命中的模式。"""
    words = [w for w in PLACE_WORDS if w]
    return '|'.join(f'{w}?' if w[-1] in '市省县区' else w for w in words) if words else '(?!x)x'


def units():
    """顾问单位参数（units.json 格式见 示例/units.example.json），去掉 _ 开头的说明键。"""
    return {k: v for k, v in load_json('顾问单位.units').items() if not k.startswith('_')}


def inbox():
    return load_json('顾问单位.inbox')


def hub_config():
    """agent-autoloop 中台的连接配置（url、owner 密钥）。"""
    p = path('中台.配置文件', '~/.agent-autoloop/config.json')
    return json.loads(p.read_text(encoding='utf-8'))
