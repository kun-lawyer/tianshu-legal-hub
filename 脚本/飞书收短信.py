"""飞书收短信：监听发给「天枢中台」机器人的私聊，本人转发的法院短信交给中台的「法院短信」规则。

常驻进程，由计划任务在登录时启动。经 lark-cli 本机事件总线收消息，与中台飞书通道共用一条长连接。
只收本人私聊的文字、语音、图片和文件（语音经本机 FunASR 转文字）；不像法院短信的交给随身助理。
图片和文件存到 日志/飞书附件/<日期>/，等本人 90 秒内补的一句文字一起交出；没补文字就单独按图办。

另一用法：python 飞书收短信.py --sms <文本文件>  把文本当法院短信提交（随身助理从截图里抄出短信后用）。
"""
import datetime as dt
import hashlib
import json
import re
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cfg  # noqa: E402

if __name__ == '__main__' and not cfg.enabled('飞书'):
    print('飞书模块未启用（config.json 的 飞书.启用）')
    sys.exit(0)

NODE = cfg.get('工具.node', 'node')
LARK = [NODE, str(cfg.path('工具.lark_cli') or '')]
OWNER = cfg.get('飞书.本人open_id', '')
FUNASR = (cfg.get('工具.funasr') or '').rstrip('/')
CONFIG = cfg.hub_config()
LOG = Path(__file__).resolve().parent.parent / '日志' / '飞书收短信.log'
ATTACH = LOG.parent / '飞书附件'
COURT_HINT = re.compile(r'法院|案号|传票|送达|立案|开庭|zxfw|执行局|仲裁委')
WAIT = 90
_pending = {'files': [], 'first': '', 'timer': None}
_lock = threading.Lock()


def log(msg):
    LOG.parent.mkdir(exist_ok=True)
    with LOG.open('a', encoding='utf-8') as f:
        f.write(f'{dt.datetime.now(dt.timezone(dt.timedelta(hours=8))):%Y-%m-%d %H:%M:%S} {msg}\n')


def reply(text):
    content = json.dumps({'text': text}, ensure_ascii=False)
    subprocess.run(LARK + ['im', '+messages-send', '--user-id', OWNER, '--as', 'bot', '--msg-type', 'text',
                           '--content', content], capture_output=True, timeout=60)


MARKER = re.compile(r'!\[[^\]]*\]\([^)]*\)|<(?:file|audio|media|video|image|img)\b[^>]*>')


def message_text(event):
    content = event.get('content') or ''
    if event.get('message_type') == 'text':
        try:
            return json.loads(content).get('text', content) if content.startswith('{') else content
        except ValueError:
            return content
    if event.get('message_type') == 'post':
        try:
            post = json.loads(content)
            body = post.get('content') or next(iter(post.values()), {}).get('content', [])
            return '\n'.join(''.join(seg.get('text', '') for seg in line) for line in body)
        except (ValueError, AttributeError, StopIteration):
            # lark-cli 已把 post 渲染成文本，图片留作 ![Image](img_xxx) 标记，去掉
            return MARKER.sub('', content)
    return ''


def funasr_ready():
    """本机 FunASR（funasr-transcribe skill，地址取 config.json 的 工具.funasr）没开就拉起来，空闲 10 分钟它自己关。"""
    if not FUNASR:
        return False

    def up():
        try:
            urllib.request.urlopen(FUNASR + '/health', timeout=3)
            return True
        except OSError:
            return False
    if up():
        return True
    server = cfg.path('工具.funasr_server', './skills/funasr-transcribe/scripts/server.py')
    if not server or not server.exists():
        return False
    # pythonw 启动的本进程没有控制台，服务用 python.exe 起
    py = cfg.path('工具.funasr_python')
    if not py or not py.exists():
        py = Path(sys.executable).with_name('python.exe')
    subprocess.Popen([str(py), str(server)], cwd=server.parent, creationflags=subprocess.CREATE_NO_WINDOW,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(90):
        time.sleep(2)
        if up():
            return True
    return False


def fetch(message_id, dest):
    """按消息 ID 下载其中全部图片、文件、语音到 dest/lark-im-resources/。返回 (路径列表, 出错列表)。
    lark-cli 推来的事件 content 已渲染成可读文本，不再是含 image_key/file_key 的 JSON，故改由 mget 取。"""
    dest.mkdir(parents=True, exist_ok=True)
    r = subprocess.run(LARK + ['im', '+messages-mget', '--message-ids', message_id, '--as', 'bot',
                               '--download-resources', '--no-reactions'],
                       cwd=dest, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=180)
    try:
        data = json.loads(r.stdout)
    except ValueError:
        return [], [f'失败：{(r.stdout + r.stderr).strip()[-200:]}']
    refs = []

    def walk(x):
        if isinstance(x, dict):
            if 'key' in x and ('local_path' in x or 'error' in x):
                refs.append(x)
            else:
                for v in x.values():
                    walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)
    walk(data)
    got, bad = [], []
    for ref in refs:
        p = dest / ref['local_path'] if ref.get('local_path') else None
        if ref.get('error') or not p or not p.exists():
            bad.append(f"失败：{ref.get('key')} {r.stderr.strip()[-150:]}")
        else:
            got.append(p)
    if not refs and data.get('ok') is False:
        bad.append(f"失败：{json.dumps(data.get('error'), ensure_ascii=False)[:200]}")
    return got, bad


def voice_text(event):
    """飞书语音：下载 opus 交 FunASR 转文字。返回 (文字, 出错说明)。"""
    with tempfile.TemporaryDirectory(prefix='fsvoice_') as tmp:
        got, bad = fetch(event.get('message_id', ''), Path(tmp))
        if not got:
            return '', f"语音下载失败：{bad[0][3:] if bad else '消息里没有语音'}"
        audio = got[0]
        if not funasr_ready():
            return '', '本机 FunASR 没起来' if FUNASR else '未配置语音转写（config.json 的 工具.funasr）'
        req = urllib.request.Request(FUNASR + '/transcribe', method='POST',
                                     data=json.dumps({'file_path': str(audio)}).encode('utf-8'),
                                     headers={'Content-Type': 'application/json'})
        try:
            with urllib.request.urlopen(req, timeout=600) as resp:
                data = json.loads(resp.read().decode('utf-8'))
        except (OSError, ValueError) as e:
            return '', f'转写出错：{e}'
    text = (data.get('text') or '').strip() if data.get('success') else ''
    return text, ('' if text else f"转写为空：{data.get('error') or '没听出内容'}")


def attachments(event):
    """消息里的图片（含图文混排里的图）和文件，下载到 日志/飞书附件/<日期>/。返回 (路径列表, 出错列表)。
    文件尽量保留原名（取自 content 里 <file key=".." name=".."/> 标记），重名加序号。"""
    content = event.get('content') or ''
    day = ATTACH / dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).strftime('%Y-%m-%d')
    names = {}
    for tag in re.findall(r'<(?:file|media|video)\b[^>]*>', content):
        a = dict(re.findall(r'(\w+)="([^"]*)"', tag))
        if a.get('key') and (a.get('name') or a.get('file_name')):
            names[a['key']] = a.get('name') or a.get('file_name')
    got, bad = fetch(event.get('message_id') or '', day)
    out = []
    for p in got:
        name = re.sub(r'[\\/:*?"<>|]', '_', names.get(p.stem) or next((v for k, v in names.items() if k in p.name), '') or p.name)
        if not Path(name).suffix:
            name += p.suffix
        target, n = day / name, 1
        while target.exists():
            target, n = day / f'{Path(name).stem}（{n}）{Path(name).suffix}', n + 1
        p.replace(target)
        out.append(str(target))
    return out, bad


def _flush():
    """等满 WAIT 秒没有文字跟上，图片单独交给随身助理。"""
    with _lock:
        files, first = _pending['files'][:], _pending['first']
        _pending.update(files=[], first='', timer=None)
    if not files:
        return
    try:
        text = '本人只发了图片或文件，没附文字。先逐个打开看，判断要办什么；看不出来就交 ask 问。'
        resp = submit_task(first, with_files(text, files))
        log(f"附件 {len(files)} 个 → 事件 {resp.get('event')}（无文字）")
    except (urllib.error.URLError, OSError, ValueError) as e:
        reply(f'中台没接上，这{len(files)}个附件没交出去：{e}')
        log(f'附件提交失败：{e}')


def hold(files, message_id):
    with _lock:
        first_one = not _pending['files']
        _pending['files'] += files
        _pending['first'] = _pending['first'] or message_id
        if _pending['timer']:
            _pending['timer'].cancel()
        _pending['timer'] = threading.Timer(WAIT, _flush)
        _pending['timer'].daemon = True
        _pending['timer'].start()
    if first_one:
        reply(f'收到图片／文件。要办什么请接着发一句文字；{WAIT} 秒内没有文字就直接按内容办。')


def take_pending():
    with _lock:
        files = _pending['files'][:]
        if _pending['timer']:
            _pending['timer'].cancel()
        _pending.update(files=[], first='', timer=None)
    return files


def with_files(text, files):
    if not files:
        return text
    return text[:3000] + '\n\n本人随消息发来的附件（已存本机，图片逐张打开看）：\n' + '\n'.join(f'- {p}' for p in files)


def submit(event_id, text):
    first = text.splitlines()[0] if text else ''
    body = {'id': event_id, 'type': 'sms.received', 'subject': 'sms', 'summary': first[:200], 'detail': text[:4000]}
    req = urllib.request.Request(CONFIG['url'].rstrip('/') + '/v1/events', method='POST',
                                 data=json.dumps(body, ensure_ascii=False).encode('utf-8'),
                                 headers={'Authorization': f"Bearer {CONFIG['source']['key']}",
                                          'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())


SESSION = Path(__file__).resolve().parent.parent / '日志' / '飞书会话.json'


def chat_subject(text=''):
    """随身助理的会话对象：距上一句不到两小时接着同一个会话，超过就另起（省掉每句话都带全部历史的开销）。
    开头说「新话题」立即另起，说「接着」则不管隔多久都接上一个。"""
    now = time.time()
    try:
        st = json.loads(SESSION.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        st = {}
    head = text.lstrip()[:4]
    if not st.get('subject') or head.startswith('新话题') or (now - st.get('last', 0) > 7200 and not head.startswith('接着')):
        st['subject'] = f'feishu-chat-{int(now)}'
    st['last'] = now
    SESSION.parent.mkdir(exist_ok=True)
    SESSION.write_text(json.dumps(st), encoding='utf-8')
    return st['subject']


def submit_task(message_id, text):
    first = text.splitlines()[0] if text else ''
    body = {'id': f'task-{message_id}', 'type': 'assistant.task', 'subject': chat_subject(text),
            'summary': first[:200], 'detail': text[:4000]}
    req = urllib.request.Request(CONFIG['url'].rstrip('/') + '/v1/events', method='POST',
                                 data=json.dumps(body, ensure_ascii=False).encode('utf-8'),
                                 headers={'Authorization': f"Bearer {CONFIG['source']['key']}",
                                          'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())


def handle(line):
    try:
        event = json.loads(line)
    except ValueError:
        return
    if event.get('type') != 'im.message.receive_v1':
        return
    if event.get('sender_id') != OWNER or event.get('chat_type') != 'p2p':
        log(f"忽略非本人或非私聊消息 sender={event.get('sender_id')} chat={event.get('chat_type')}")
        return
    mtype = event.get('message_type')
    files, bad = attachments(event) if mtype in ('image', 'post', 'file', 'media') else ([], [])
    if bad:
        reply(f'有 {len(bad)} 个图片／文件没下载下来（{bad[0][3:80]}），请重发或改发文字。')
        log(f"附件下载失败 id={event.get('message_id')}：{bad}")
    if mtype == 'audio':
        heard, err = voice_text(event)
        if not heard:
            reply(f'这条语音没转成文字（{err}），请改发文字。')
            log(f'语音转写失败：{err}')
            return
        reply(f'听到的是：{heard}')
        log(f'语音转写：{heard[:40]}')
        text = f'[以下据飞书语音转写，人名、数字可能有误] {heard}'
    else:
        text = message_text(event).strip()
    if not text:
        # 单发的图片、文件先存着，等本人补一句要办什么
        if files:
            hold(files, event.get('message_id') or '')
            log(f'收附件 {len(files)} 个，等文字')
        elif not bad:
            reply(f'这类消息（{mtype}）处理不了，请发文字、语音、图片或文件。')
            log(f"不支持的消息类型 {mtype} id={event.get('message_id')} content={(event.get('content') or '')[:120]}")
        return
    files = take_pending() + files
    if not (COURT_HINT.search(text) and re.search(r'【.*(法院|仲裁)】|zxfw|送达|传票', text)):
        # 不是法院短信就是交给随身助理的活：同一会话接着上一轮做
        try:
            resp = submit_task(event.get('message_id') or hashlib.sha256(text.encode()).hexdigest()[:16],
                               with_files(text, files))
        except (urllib.error.URLError, OSError, ValueError) as e:
            reply(f'中台没接上，这条没交出去：{e}')
            log(f'任务提交失败：{e}')
            return
        reply(('收到，正在办。' + (f'（连同 {len(files)} 个附件）' if files else '')) if not resp.get('duplicate') else '这条已经在办了。')
        log(f"任务 → 事件 {resp.get('event')}：{text[:40]}" + (f'（附件 {len(files)}）' if files else ''))
        return
    digest = hashlib.sha256(text.encode('utf-8')).hexdigest()[:16]
    text = with_files(text, files)
    try:
        resp = submit(f'sms-{digest}', text)
    except (urllib.error.URLError, OSError, ValueError) as e:
        reply(f'中台没接上，短信未提交：{e}。中台恢复后请重发一次。')
        log(f'提交失败：{e}')
        return
    if resp.get('duplicate'):
        reply('这条短信之前已提交过，不重复处理。')
    elif resp.get('routed'):
        reply(f"已收到，交给「{'、'.join(resp['routed'])}」处理（事件 #{resp.get('event')}），结果会推到这里。")
    else:
        reply('已记下，但没有规则接手，请检查中台 rules.json。')
    log(f"提交 sms-{digest} → 事件 {resp.get('event')} routed={resp.get('routed')} dup={resp.get('duplicate')}")


def main():
    log('启动')
    while True:
        # stdin 保持打开：lark-cli consume 在 stdin 关闭时退出
        proc = subprocess.Popen(LARK + ['event', 'consume', 'im.message.receive_v1', '--as', 'bot'],
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                text=True, encoding='utf-8', errors='replace',
                                creationflags=subprocess.CREATE_NO_WINDOW)
        for line in proc.stdout:
            line = line.strip()
            if line.startswith('{'):
                try:
                    handle(line)
                except Exception as e:  # noqa: BLE001
                    log(f'处理出错：{e}')
        code = proc.wait()
        log(f'监听退出（{code}），10 秒后重启')
        time.sleep(10)


def sms_from_file(path):
    text = Path(path).read_text(encoding='utf-8').strip()
    if not text:
        sys.exit('文件是空的')
    resp = submit(f"sms-{hashlib.sha256(text.encode('utf-8')).hexdigest()[:16]}", text)
    log(f"截图抄出的短信 → 事件 {resp.get('event')} routed={resp.get('routed')} dup={resp.get('duplicate')}")
    print(json.dumps(resp, ensure_ascii=False))


if __name__ == '__main__':
    if len(sys.argv) == 3 and sys.argv[1] == '--sms':
        sms_from_file(sys.argv[2])
    else:
        main()
