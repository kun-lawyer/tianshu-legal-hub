"""天枢中台安装向导。由 install.ps1 在装好 Python 依赖后调用，也可单独重跑：python 工具/安装.py

每一步都可跳过、可重跑；已有的配置、凭据、skill、规则不会被静默覆盖，覆盖前先备份。
--answers 应答.json 可预填全部提问（键名见各 ask 调用的第一个参数），用于批量部署或无人值守安装。
"""
import argparse
import datetime as dt
import getpass
import json
import os
import re
import shlex
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import urllib.request
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8', line_buffering=True)
REPO =Path(__file__).resolve().parent.parent
PY = Path(sys.executable)
PYW = PY.with_name('pythonw.exe') if PY.with_name('pythonw.exe').exists() else PY
HOME = Path.home()
AA_HOME = Path(os.environ.get('AGENT_AUTOLOOP_HOME') or HOME / '.agent-autoloop')
AA_PROGRAM = AA_HOME / 'app' / 'bin' / 'agent-autoloop.ts'
AA_INSTALL = 'https://raw.githubusercontent.com/KKKKhazix/agent-autoloop/main/install.ps1'
STAMP = dt.datetime.now().strftime('%Y%m%d-%H%M%S')
# 未启用的模块，其规则不装：脚本虽会安静退出，交给 Agent 的规则却会白白启动一轮
MODULE_RULES = {'微信': ['微信待办', '微信材料收取'], '顾问单位': ['送审收件读取', '送审收件处理'],
                '飞书': ['飞书助理'], '知识库': ['知识库刷新']}
UNBUNDLED_SKILLS = ['guwen-review', 'law-opinion-writer', 'litigation-doc-generator']
ANSWERS = {}


def ask(key, question, default=''):
    if key in ANSWERS:
        print(f'{question}：{ANSWERS[key]}（应答文件）')
        return str(ANSWERS[key])
    hint = f'（回车用 {default}）' if default else '（可留空）'
    return input(f'{question}{hint}：').strip() or default


def yes(key, question, default=True):
    if key in ANSWERS:
        print(f'{question}：{"是" if ANSWERS[key] else "否"}（应答文件）')
        return bool(ANSWERS[key])
    v = input(f'{question} [{"Y/n" if default else "y/N"}]：').strip().lower()
    return default if not v else v.startswith('y')


def secret(key, question):
    return str(ANSWERS[key]) if key in ANSWERS else getpass.getpass(f'{question}：')


STEPS = [0, 11]


def step(title):
    STEPS[0] += 1
    print(f'\n== {STEPS[0]}/{STEPS[1]} {title.split(" ", 1)[-1]}')


def shell():
    return shutil.which('pwsh') or shutil.which('powershell') or 'powershell'


def run_ps(script, *args, capture=True):
    """把 PowerShell 片段写成带 BOM 的临时脚本再运行，避免命令行转义和 5.1 的编码问题。"""
    with tempfile.NamedTemporaryFile('w', suffix='.ps1', delete=False, encoding='utf-8-sig') as f:
        f.write(script)
    try:
        return subprocess.run([shell(), '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', f.name, *map(str, args)],
                              capture_output=capture, text=True, encoding='utf-8', errors='replace')
    finally:
        os.unlink(f.name)


def node_cmd(*args):
    return [shutil.which('node') or 'node', '--disable-warning=ExperimentalWarning', *map(str, args)]


def aa(*args):
    env = dict(os.environ, AGENT_AUTOLOOP_HOME=str(AA_HOME))
    return subprocess.run(node_cmd(AA_PROGRAM, *args), capture_output=True, text=True, encoding='utf-8',
                          errors='replace', env=env)


def expand(p):
    return Path(os.path.expandvars(os.path.expanduser(str(p))))


# 1 配置
def step_config():
    step('1/9 本机配置 config.json')
    path = REPO / 'config.json'
    if path.exists() and not yes('config_overwrite', '已有 config.json，重新填写？（旧的会备份）', False):
        return json.loads(path.read_text(encoding='utf-8'))
    if path.exists():
        shutil.copy2(path, path.with_name(f'config.json.bak-{STAMP}'))
    c = json.loads((REPO / 'config.example.json').read_text(encoding='utf-8'))
    c['本人'] = ask('本人', '您的姓名（台账、待办里的"本人"）', c['本人'])
    c['同事'] = [x for x in ask('同事', '同事姓名，用顿号或逗号分隔').replace('，', '、').replace(',', '、').split('、') if x]
    c['地名停用词'] = [x for x in ask('地名停用词', '本地常见的市、省名，如 某某市、某某省，用顿号分隔').replace('，', '、').split('、') if x]
    root = ask('资料根目录', '案件资料的根目录', 'D:/资料').replace('\\', '/')
    c['资料根目录'] = [root]
    d = c['目录']
    d['本人目录'] = ask('本人目录', '本人的工作目录', root)
    d['单次案件'] = ask('单次案件', '单次（非顾问单位）案件目录，下面每个子文件夹是一个案件', f'{root}/单次案件')
    d['顾问单位'] = ask('顾问单位目录', '顾问单位目录，下面每个子文件夹是一家单位')
    d['传票扫描'] = ask('传票扫描', '传票扫描目录（扫描的传票放进来，自动登记开庭日程）')
    d['事务目录'], d['额外扫描目录'], d['送审收件清单'] = [], [], ''
    for k in ('单次案件', '传票扫描'):
        if d[k] and not expand(d[k]).exists() and yes(f'mkdir_{k}', f'{d[k]} 不存在，现在新建？'):
            expand(d[k]).mkdir(parents=True, exist_ok=True)
    c['微信']['启用'] = yes('微信', '启用微信模块？（读本机微信 4.x 数据库，需自备 WeChatDataAnalysis 导出的密钥）', False)
    c['顾问单位']['启用'] = yes('顾问单位', '启用顾问单位送审模块？（需自备 guwen-review skill）', False)
    if c['顾问单位']['启用']:
        d['送审收件清单'] = ask('送审收件清单', '送审收件清单文件路径', f"{d['顾问单位'] or root}/_微信收件/收件清单.json")
        print('   请照 示例/units.example.json、示例/inbox.example.json 写好自己的单位参数，并在 config.json 的「顾问单位」里指过去。')
    c['工具']['知识库刷新脚本'] = ask('知识库刷新脚本', '知识库每日刷新脚本路径（没有留空）')
    path.write_text(json.dumps(c, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(f'   已写入 {path}')
    return c


def save_config(c):
    (REPO / 'config.json').write_text(json.dumps(c, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


# 2 案件台账
def step_backend(c):
    step('2/9 案件台账后端（台账接口）')
    cred_dir = expand(c.get('台账', {}).get('凭据目录') or '~/.tianshu-ledger')
    cred = cred_dir / 'credentials.json'
    if cred.exists() and not yes('backend_overwrite', f'已有 {cred}，重新填写？', False):
        return True
    base = ask('backend_base', '台账后端地址，如 https://...（没有留空跳过，台账相关功能不可用）')
    if not base:
        print('   跳过。以后可手工建 ' + str(cred) + '，字段 base、username、password。')
        return False
    user = ask('backend_user', '台账账号')
    pwd = secret('backend_password', '台账密码（输入时不显示）')
    try:
        req = urllib.request.Request(base.rstrip('/') + '/api/auth/login', method='POST',
                                     data=json.dumps({'username': user, 'password': pwd}).encode('utf-8'),
                                     headers={'Content-Type': 'application/json'})
        with urllib.request.urlopen(req, timeout=20) as r:
            ok = bool(json.loads(r.read()).get('token'))
    except Exception as e:  # noqa: BLE001
        ok = False
        print(f'   登录测试失败：{e}')
    if not ok and not yes('backend_keep', '登录没通过，仍然保存这组凭据？', False):
        return False
    cred_dir.mkdir(parents=True, exist_ok=True)
    cred.write_text(json.dumps({'base': base, 'username': user, 'password': pwd}, ensure_ascii=False), encoding='utf-8')
    print(f'   {"登录成功，" if ok else ""}凭据已存 {cred}（只在本机）')
    return True


# 3 Agent 工具
def step_agent():
    step('3/9 交给哪个 Agent 干活')
    print('   1. Claude Code\n   2. 其他命令行 Agent（填一条命令模板）')
    kind = ask('agent', '选 1 或 2', '1')
    if kind != '2':
        if not shutil.which('claude'):
            print('   提醒：当前找不到 claude 命令。请先安装并登录 Claude Code，再运行本向导。')
        return {'kind': 'claude'}
    while True:
        tpl = ask('agent_command', '命令模板，用 {prompt} 代表交给它的指令，如 opencode run {prompt}')
        parts = shlex.split(tpl, posix=False)
        if '{prompt}' not in ' '.join(parts):
            print('   模板里要有 {prompt}。')
            if 'agent_command' in ANSWERS:
                sys.exit(1)
            continue
        if not shutil.which(parts[0]):
            print(f'   提醒：当前找不到 {parts[0]}，请确认已安装并能在命令行直接运行。')
        return {'kind': 'command', 'command': [p.strip('"') for p in parts], 'template': tpl}


# 4 规则引擎
def step_engine(agent):
    step('4/9 规则引擎 agent-autoloop')
    if (AA_HOME / 'config.json').exists() and AA_PROGRAM.exists():
        print(f'   已安装：{AA_HOME}')
        return True
    if not yes('engine_install', '没装 agent-autoloop，现在从 GitHub 下载官方安装脚本安装？'):
        print('   跳过。规则不会自动运行；以后装好再重跑本向导。')
        return False
    tmp = Path(tempfile.gettempdir()) / f'agent-autoloop-install-{STAMP}.ps1'
    urllib.request.urlretrieve(AA_INSTALL, tmp)
    args = ['--agent', 'claude'] if agent['kind'] == 'claude' else ['--command', agent['template']]
    if ANSWERS.get('engine_no_service'):
        args.append('--no-service')
    if ANSWERS.get('engine_port'):
        args += ['--port', str(ANSWERS['engine_port'])]
    r = subprocess.run([shell(), '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(tmp), *args])
    tmp.unlink(missing_ok=True)
    if r.returncode != 0 or not AA_PROGRAM.exists():
        print('   引擎没装好，请看上面的报错；装好后重跑本向导。')
        return False
    return True


# 5 skill
def step_skills(agent):
    step('5/9 skill')
    src = sorted(p for p in (REPO / 'skills').iterdir() if p.is_dir()) if (REPO / 'skills').is_dir() else []
    targets = []
    if agent['kind'] == 'claude':
        targets.append(HOME / '.claude' / 'skills')
    else:
        print('   其他 Agent 直接读仓库 skills/ 下的 SKILL.md（AGENTS.md 已写明），不必安装。')
    extra = ask('skills_dir', '您的工具若有自己的 skill 目录，填上可一并复制（没有留空）')
    if extra:
        targets.append(expand(extra))
    for target in targets:
        target.mkdir(parents=True, exist_ok=True)
        # 备份放在 skill 目录之外，免得 Agent 把备份也当成 skill 加载
        backup = target.parent / f'{target.name}_tianshu_backup_{STAMP}'
        for s in src:
            dest = target / s.name
            if dest.exists():
                if not yes('skill_overwrite', f'{dest} 已存在，覆盖？（旧的移到 {backup}）', False):
                    continue
                backup.mkdir(exist_ok=True)
                shutil.move(str(dest), str(backup / s.name))
            shutil.copytree(s, dest)
            print(f'   已装 {s.name} → {target}')
    have = {p.name for t in [HOME / '.claude' / 'skills', *targets] if t.is_dir() for p in t.iterdir()} | \
        {p.name for p in src}
    missing = [s for s in UNBUNDLED_SKILLS if s not in have]
    if missing:
        print('   手册还会用到这些不随仓库提供的 skill：' + '、'.join(missing) + '。没有时手册按通用做法处理。')
    return targets


# 6 语音转写 FunASR、7 扫描件识别 MinerU：体积大，各装独立环境，免得与主环境的依赖版本冲突
DRY = []  # 应答文件里 install_dry_run 为真时，只预演 pip 安装、不真装（测试用）


def uv(*args, **kw):
    """用主环境里的 uv 建虚拟环境、装包；uv 能自动下载所需的 Python 版本。"""
    if subprocess.run([PY, '-m', 'uv', '--version'], capture_output=True).returncode != 0:
        subprocess.run([PY, '-m', 'pip', 'install', '-q', '--disable-pip-version-check', 'uv'], check=True)
    return subprocess.run([PY, '-m', 'uv', *map(str, args)], **kw)


def pip_into(py, *pkgs, backend=None):
    args = ['pip', 'install', '--python', py, *pkgs] + (['--torch-backend', backend] if backend else []) + DRY
    return uv(*args).returncode == 0


def new_venv(path):
    py = path / 'Scripts' / 'python.exe'
    if not py.exists():
        uv('venv', path, '--python', '3.12', '--seed', check=True)
    return py


def gpu_info():
    """返回 (显卡名, 驱动支持的 CUDA 主次版本) 或 None。"""
    try:
        name = subprocess.run(['nvidia-smi', '--query-gpu=name', '--format=csv,noheader'], capture_output=True,
                              text=True, timeout=20).stdout.strip().splitlines()[0]
        top = subprocess.run(['nvidia-smi'], capture_output=True, text=True, timeout=20).stdout
    except (OSError, IndexError, subprocess.TimeoutExpired):
        return None
    m = re.search(r'CUDA Version:\s*(\d+)\.(\d+)', top)
    return (name, (int(m[1]), int(m[2]))) if m else None


# PyPI 上的 torch 在 Windows 是 CPU 版，且版本常领先于 PyTorch 的 CUDA 源（如 PyPI 2.15.0、cu130 源最高 2.14.1），
# 先装再按同版本号换 CUDA 版会找不到包。改由 uv 的 --torch-backend 在解析时就取 CUDA 源，
# 并按 nvidia-smi 报的驱动 CUDA 上限显式选，不用 auto（auto 曾为 CUDA 13.0 的驱动选了 cu132）
CUDA_BACKENDS = [((13, 2), 'cu132'), ((13, 0), 'cu130'), ((12, 9), 'cu129'), ((12, 8), 'cu128'), ((12, 6), 'cu126'),
                 ((12, 4), 'cu124'), ((12, 1), 'cu121'), ((11, 8), 'cu118')]


def step_funasr(c):
    step('6/11 语音转写 FunASR（可选）')
    if not yes('funasr', '安装本机语音转写？（飞书、微信语音转文字用；依赖加模型约 2 至 3 GB，需联网，CPU 即可）', False):
        return
    skill = REPO / 'skills' / 'funasr-transcribe'
    py = new_venv(HOME / '.funasr-local' / 'venv')
    print(f'   独立环境：{py.parent.parent}；依赖装好后下载模型到 ~/.cache/modelscope（较慢，请耐心等）')
    args = [py, skill / 'scripts' / 'setup.py'] + (['--check'] if DRY else [])
    # modelscope 对 500 MB 以上的文件改用多连接分块下载，经代理时实测会卡死不动（867 MB 的主模型停在 0 字节），
    # 门槛调高后整份单连接下载，慢一点但稳
    env = dict(os.environ, MODELSCOPE_PARALLEL_DOWNLOAD_THRESHOLD_MB='100000')
    if subprocess.run(list(map(str, args)), cwd=skill, env=env).returncode != 0:
        print('   安装没完成，请看上面的报错；可稍后单独运行：' + f'{py} {skill / "scripts" / "setup.py"}')
        return
    if not DRY:
        ok = subprocess.run([str(py), str(skill / 'scripts' / 'setup.py'), '--verify'], cwd=skill).returncode == 0
        print('   语音转写已就绪' if ok else '   自检没通过，请看上面的提示')
    c['工具']['funasr_python'] = py.as_posix()


def write_mineru_token(token, targets):
    dirs = [REPO / 'skills' / 'mineru-ocr'] + [t / 'mineru-ocr' for t in targets]
    for d in dirs:
        if not (d / 'config').is_dir():
            continue
        env = d / 'config' / '.env'
        lines = (env if env.exists() else d / 'config' / '.env.example').read_text(encoding='utf-8').splitlines()
        lines = [f'MINERU_API_TOKEN={token}' if ln.startswith('MINERU_API_TOKEN=') else ln for ln in lines]
        env.write_text('\n'.join(lines) + '\n', encoding='utf-8')
        print(f'   Token 已写入 {env}')


def step_mineru(targets):
    step('7/11 扫描件识别 MinerU（可选）')
    gpu = gpu_info()
    print(f'   显卡：{gpu[0]}，驱动支持 CUDA {gpu[1][0]}.{gpu[1][1]}' if gpu else '   没检测到 NVIDIA 显卡')
    print('   local：本机识别，文件不出本机，首次识别时自动下载约 4.6 GB 模型' + ('' if gpu else '（无显卡时很慢）')
          + '\n   cloud：MinerU 云端识别，文件会上传，需申请 Token\n   skip：跳过（传票入库仍可按文件名和文字层登记）')
    mode = ask('mineru', '选 local、cloud 或 skip', 'local' if gpu else 'cloud')
    if mode == 'local':
        py = new_venv(HOME / '.mineru-local' / 'venv')
        print(f'   独立环境：{py.parent.parent}，安装 MinerU 4（下载量较大，请耐心等）')
        tags = [tag for need, tag in CUDA_BACKENDS if gpu and gpu[1] >= need] + ['cpu']
        used = ''
        for t in tags:
            print(f'   用 {t} 版 PyTorch 安装……')
            if pip_into(py, '-U', 'mineru[torch]>=4.0,<5', backend=t):
                used = t
                break
        if not used:
            print('   MinerU 没装上，请看上面的报错；识别会走云端。')
        elif gpu and not DRY:
            ok = subprocess.run([py, '-c', 'import torch;print(torch.cuda.is_available())'], capture_output=True,
                                text=True).stdout.strip() == 'True'
            print(f'   已装 {used} 版 PyTorch，显卡可用' if ok
                  else '   显卡没用上，MinerU 会用 CPU 跑（慢），可按 skills/mineru-ocr/SKILL.md 手动处理。')
        if not DRY:
            kit = py.with_name('mineru-kit.exe')
            print('   本机 MinerU 已就绪' if kit.exists() else f'   没找到 {kit}，本机通道不可用，识别会走云端。')
        print('   建议再配一个云端 Token，本机识别失败时自动回落。')
    if mode in ('local', 'cloud'):
        token = secret('mineru_token', 'MinerU API Token（https://mineru.net/apiManage/token 申请；留空跳过，输入时不显示）')
        if token:
            write_mineru_token(token, targets)


# 6 飞书
def lark_js(c):
    return expand(c['工具'].get('lark_cli') or '%APPDATA%/npm/node_modules/@larksuite/cli/scripts/run.js')


def capture_open_id(js, seconds=180):
    """临时监听收消息事件，抓第一条私聊的发送人 open_id。机器只能有一个监听，所以先停掉自启的收消息程序再用。"""
    proc = subprocess.Popen(node_cmd(js, 'event', 'consume', 'im.message.receive_v1', '--as', 'bot'),
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                            text=True, encoding='utf-8', errors='replace')
    found = []

    def read():
        for line in proc.stdout:
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if e.get('chat_type') == 'p2p' and e.get('sender_id'):
                found.append(e['sender_id'])
                return
    t = threading.Thread(target=read, daemon=True)
    t.start()
    t.join(seconds)
    proc.kill()
    return found[0] if found else ''


def step_feishu(c):
    step('6/9 飞书（可选）')
    if not yes('飞书', '启用飞书？（手机上收晨报晚报、发任务给助理、转发法院短信）', False):
        c['飞书']['启用'] = False
        return ''
    js = lark_js(c)
    if not js.exists():
        if not yes('lark_install', '没装 lark-cli，现在用 npm 安装 @larksuite/cli？'):
            c['飞书']['启用'] = False
            return ''
        subprocess.run([shutil.which('npm') or 'npm.cmd', 'install', '-g', '@larksuite/cli'])
    if not js.exists():
        print(f'   没找到 {js}；装好 lark-cli 后把它的 scripts/run.js 路径填进 config.json 的 工具.lark_cli。')
        c['飞书']['启用'] = False
        return ''
    doctor = subprocess.run(node_cmd(js, 'doctor'), capture_output=True, text=True, encoding='utf-8', errors='replace')
    if '"config_file"' in doctor.stdout and '"not configured"' in doctor.stdout:
        print('   lark-cli 还没绑定飞书应用：接下来会给出一个网址，在浏览器里打开按提示建好应用。')
        subprocess.run(node_cmd(js, 'config', 'init', '--new'))
    print('   请确认飞书开发者后台里这个应用：已开启机器人能力；已订阅事件 im.message.receive_v1；'
          '已开通权限 im:message.p2p_msg:readonly；已发布版本。')
    oid = ask('feishu_open_id', '您的飞书 open_id（ou_ 开头；留空则现在给机器人发一句话自动获取）')
    if not oid:
        print('   请在 3 分钟内用飞书私聊这个机器人，随便发一句话……')
        oid = capture_open_id(js)
        print(f'   获取到：{oid}' if oid else '   没收到消息。以后可手工填 config.json 的 飞书.本人open_id。')
    c['飞书'].update({'启用': bool(oid), '本人open_id': oid})
    if oid and yes('feishu_autostart', '登录 Windows 时自动启动飞书收消息程序？'):
        r = run_ps(TASK_PS, '天枢中台-飞书收短信', PYW, REPO / '脚本' / '飞书收短信.py', REPO)
        print('   已注册计划任务「天枢中台-飞书收短信」并启动' if r.returncode == 0 else f'   计划任务没建成：{r.stderr.strip()[-300:]}')
    print('   晨报晚报、提问卡片要推到飞书，还需在 agent-autoloop 里接飞书通道，见 ~/.agent-autoloop/app/docs/ 的通道一节。')
    return oid


TASK_PS = r'''param($Name, $Exe, $Script, $Dir)
$a = New-ScheduledTaskAction -Execute $Exe -Argument ('"' + $Script + '"') -WorkingDirectory $Dir
$t = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$s = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Seconds 0) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
Register-ScheduledTask -TaskName $Name -Action $a -Trigger $t -Settings $s -Force | Out-Null
Start-ScheduledTask -TaskName $Name
'''


# 7 规则
def step_rules(c, agent, oid):
    step('7/9 合并规则到 agent-autoloop')
    path = AA_HOME / 'rules.json'
    if not path.exists() or not AA_PROGRAM.exists():
        print('   规则引擎没装，跳过。')
        return False
    cur = json.loads(path.read_text(encoding='utf-8'))
    ex = json.loads((REPO / 'rules.example.json').read_text(encoding='utf-8'))
    worker = next((a['worker'] for a in cur.get('agents', {}).values() if a.get('worker')), socket.gethostname().lower())
    off = {m for m in ('微信', '顾问单位', '飞书') if not c.get(m, {}).get('启用')}
    if not c['工具'].get('知识库刷新脚本'):
        off.add('知识库')
    drop = {name for m in off for name in MODULE_RULES[m]}
    # 用模板里全部规则名去替换旧规则：这样关掉模块后重跑，旧装的该模块规则也会被清掉
    names = {r['name'] for r in ex['rules']}
    rules = []
    for r in ex['rules']:
        if r['name'] in drop:
            continue
        r['cwd'] = REPO.as_posix()
        if not oid and 'channels' in r:
            r['channels'] = [ch for ch in r['channels'] if ch != 'feishu']
        rules.append(r)
    used = {r['to'] for r in rules}
    agents = {}
    for key, a in ex['agents'].items():
        if key not in used:
            continue
        a['worker'] = worker
        if key == 'assistant' and agent['kind'] == 'command':
            a = {'name': 'Agent 助理', 'worker': worker, 'use': 'command', 'command': agent['command'],
                 'timeout': a.get('timeout', 120)}
        if a.get('command') and a['command'][0] == 'python':
            a['command'][0] = PY.as_posix()
        agents[key] = a
    channels = dict(ex.get('channels', {}))
    if oid:
        channels['feishu']['answerers'] = [oid]
    else:
        channels.pop('feishu', None)
    out = dict(cur)
    out['timezone'] = cur.get('timezone') or ex.get('timezone')
    out['askFirst'] = list(dict.fromkeys((cur.get('askFirst') or []) + (ex.get('askFirst') or [])))
    out['sources'] = {**cur.get('sources', {}), **ex.get('sources', {})}
    out['agents'] = {**cur.get('agents', {}), **agents}
    out['channels'] = {**cur.get('channels', {}), **channels}
    out['rules'] = [r for r in cur.get('rules', []) if r['name'] not in names] + rules
    backup = path.with_name(f'rules.json.bak-tianshu-{STAMP}')
    shutil.copy2(path, backup)
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    chk = aa('check')
    if chk.returncode != 0:
        shutil.copy2(backup, path)
        print('   规则校验没通过，已恢复原规则：\n' + (chk.stdout + chk.stderr).strip()[-800:])
        return False
    print(f'   已合并 {len(rules)} 条规则（未启用模块跳过 {len(drop)} 条），校验通过；原规则备份在 {backup.name}')
    return True


# 8 面板
SHORTCUT_PS = r'''param($Lnk, $Exe, $Arg, $Dir)
$s = (New-Object -ComObject WScript.Shell).CreateShortcut($Lnk)
$s.TargetPath = $Exe; $s.Arguments = $Arg; $s.WorkingDirectory = $Dir; $s.Save()
'''


def step_panel():
    step('8/9 面板')
    if not yes('panel_shortcut', '在桌面放一个「天枢中台面板」快捷方式？'):
        print(f'   以后运行 {PY} 面板\\server.py 打开面板。')
        return
    desk = ANSWERS.get('desktop_dir') or run_ps("[Environment]::GetFolderPath('Desktop')").stdout.strip()
    lnk = Path(desk) / '天枢中台面板.lnk'
    r = run_ps(SHORTCUT_PS, lnk, PYW, f'"{REPO / "面板" / "server.py"}"', REPO)
    print(f'   已放在 {lnk}' if r.returncode == 0 and lnk.exists() else f'   快捷方式没建成：{r.stderr.strip()[-300:]}')


# 9 自检
def step_selftest(has_backend):
    step('9/9 自检')
    checks = [('台账', [PY, REPO / '脚本' / 'ledger.py', 'active'])] if has_backend else []
    checks.append(('晨报', [PY, REPO / '脚本' / '晨报.py']))
    for label, cmd in checks:
        r = subprocess.run(list(map(str, cmd)), capture_output=True, text=True, encoding='utf-8', errors='replace',
                           cwd=REPO)
        first = (r.stdout.strip() or r.stderr.strip()).splitlines()[:1]
        print(f'   {label}：{"通过" if r.returncode == 0 else "失败"}  {first[0][:120] if first else ""}')
    if AA_PROGRAM.exists():
        print('   规则引擎：' + (aa('rules', 'explain').stdout.strip().splitlines() or ['读不到规则'])[0][:160])
    print('\n装好了。规则引擎装成系统服务的会在登录后自动运行；装时选了不装服务的，运行 agent-autoloop up 启动。'
          '\n可选组件（MinerU 识别扫描件、FunASR 语音转写、微信）的安装见 README「可选组件」一节。')


def main():
    ap = argparse.ArgumentParser(description='天枢中台安装向导')
    ap.add_argument('--answers', help='应答文件（JSON），预填全部提问')
    a = ap.parse_args()
    if a.answers:
        ANSWERS.update(json.loads(Path(a.answers).read_text(encoding='utf-8')))
    if ANSWERS.get('install_dry_run'):
        DRY.append('--dry-run')
    print(f'天枢中台安装向导（仓库：{REPO}）')
    c = step_config()
    has_backend = step_backend(c)
    agent = step_agent()
    step_engine(agent)
    targets = step_skills(agent)
    step_funasr(c)
    step_mineru(targets)
    oid = step_feishu(c)
    save_config(c)
    step_rules(c, agent, oid)
    step_panel()
    step_selftest(has_backend)


if __name__ == '__main__':
    main()
