"""微信图片只有缩略图时，在微信 PC 端自动点开原图，让微信把原图下载到本机。

微信 4.x 的聊天区是自绘界面，UI 自动化读不到消息元素，所以按图找：本机已有的缩略图（_t.dat）解密后，
在微信窗口截图里做模板匹配，找到就点，等 Img 目录里出现原图（_h.dat 或 .dat）后关掉图片查看器。
找不到就把聊天区往上滚，最多 MAX_SCROLL 屏。会话用 Ctrl+F 搜备注名打开。
定时任务调用时只在电脑空闲 idle 秒以上才动手，免得抢正在打字的焦点；办完把原来的前台窗口和微信最小化状态还原。

用法（单独测试）：python 微信点开原图.py <会话显示名> <Img目录> <图片md5> [hd]
"""
import ctypes
import sys
import time
from pathlib import Path

import cv2
import numpy as np
import win32api
import win32clipboard
import win32con
import win32gui
import win32process
from PIL import ImageGrab

ctypes.windll.shcore.SetProcessDpiAwareness(2)
MAX_SCROLL = 25
# 真图匹配一般在 0.94 以上，相似邻图能到 0.80，取 0.9
MATCH = 0.9
WAIT = 15


class LASTINPUTINFO(ctypes.Structure):
    _fields_ = [('cbSize', ctypes.c_uint), ('dwTime', ctypes.c_uint)]


def idle_seconds():
    li = LASTINPUTINFO(ctypes.sizeof(LASTINPUTINFO))
    ctypes.windll.user32.GetLastInputInfo(ctypes.byref(li))
    return (ctypes.windll.kernel32.GetTickCount() - li.dwTime) / 1000


def wechat_windows():
    out = []

    def f(h, _):
        if win32gui.GetClassName(h).startswith('Qt') and win32gui.IsWindowVisible(h):
            try:
                pid = win32process.GetWindowThreadProcessId(h)[1]
                hp = win32api.OpenProcess(0x1000, False, pid)
                exe = win32process.GetModuleFileNameEx(hp, 0)
            except Exception:  # noqa: BLE001
                return
            if exe.lower().endswith('weixin.exe'):
                out.append(h)
    win32gui.EnumWindows(f, None)
    return out


def main_window():
    hs = [h for h in wechat_windows() if win32gui.GetWindowText(h) in ('Weixin', '微信')]
    return hs[0] if hs else None


def key(*vks):
    for v in vks:
        win32api.keybd_event(v, 0, 0, 0)
    for v in reversed(vks):
        win32api.keybd_event(v, 0, win32con.KEYEVENTF_KEYUP, 0)


def front(h):
    if win32gui.IsIconic(h):
        win32gui.ShowWindow(h, win32con.SW_RESTORE)
    fg = win32gui.GetForegroundWindow()
    a, b = win32process.GetWindowThreadProcessId(fg)[0] if fg else 0, win32api.GetCurrentThreadId()
    if a:
        ctypes.windll.user32.AttachThreadInput(b, a, True)
    try:
        win32gui.BringWindowToTop(h)
        win32gui.SetForegroundWindow(h)
    except Exception:  # noqa: BLE001
        pass
    finally:
        if a:
            ctypes.windll.user32.AttachThreadInput(b, a, False)
    time.sleep(0.6)
    return win32gui.GetForegroundWindow() == h


def get_clip():
    try:
        win32clipboard.OpenClipboard()
        try:
            return win32clipboard.GetClipboardData(win32con.CF_UNICODETEXT)
        finally:
            win32clipboard.CloseClipboard()
    except Exception:  # noqa: BLE001
        return None


def set_clip(text):
    win32clipboard.OpenClipboard()
    try:
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardData(win32con.CF_UNICODETEXT, text)
    finally:
        win32clipboard.CloseClipboard()


def open_chat(h, name):
    key(win32con.VK_CONTROL, ord('F'))
    time.sleep(0.5)
    key(win32con.VK_CONTROL, ord('A'))
    set_clip(name)
    key(win32con.VK_CONTROL, ord('V'))
    time.sleep(1.5)
    key(win32con.VK_RETURN)
    time.sleep(2)


def shot(h):
    L, T, R, B = win32gui.GetWindowRect(h)
    img = cv2.cvtColor(np.array(ImageGrab.grab(bbox=(L, T, R, B), all_screens=True)), cv2.COLOR_RGB2GRAY)
    return img, L, T


def scaled(tpl, s):
    return cv2.resize(tpl, None, fx=s, fy=s, interpolation=cv2.INTER_AREA if s < 1 else cv2.INTER_CUBIC)


def find(img, tpl, scales=None):
    """多尺度模板匹配，返回 (分数, 中心点, 倍数)。缩略图在聊天气泡里按 DPI 放大，约 1 至 3 倍。"""
    best = (0, None, None)
    for s in scales or np.arange(0.6, 4.01, 0.1):
        t = scaled(tpl, s)
        if t.shape[0] >= img.shape[0] or t.shape[1] >= img.shape[1]:
            break
        _, v, _, loc = cv2.minMaxLoc(cv2.matchTemplate(img, t, cv2.TM_CCOEFF_NORMED))
        if v > best[0]:
            best = (v, (loc[0] + t.shape[1] // 2, loc[1] + t.shape[0] // 2), s)
    return best


def beaten(img, c, s, v, others):
    """同一会话里连发的几张相似照片会互相匹配上（例：某客户 11-15 连发五张单据照片，邻图也有 0.80）。
    在找到的位置上拿其他缩略图按同一倍数比一次，有比本图更像的，说明这个气泡是别的图。"""
    for o in others:
        t = scaled(o, s)
        th, tw = t.shape
        x0, y0 = max(0, c[0] - tw // 2 - 12), max(0, c[1] - th // 2 - 12)
        roi = img[y0:c[1] + th // 2 + 12, x0:c[0] + tw // 2 + 12]
        if roi.shape[0] < th or roi.shape[1] < tw:
            continue
        if cv2.minMaxLoc(cv2.matchTemplate(roi, t, cv2.TM_CCOEFF_NORMED))[1] > v:
            return True
    return False


def siblings(d, md5, decode):
    """同目录里前后两天内的其他缩略图，最多 40 张。"""
    me = (d / f'{md5}_t.dat').stat().st_mtime
    out = []
    for p in sorted(d.glob('*_t.dat'), key=lambda p: abs(p.stat().st_mtime - me))[:41]:
        if p.name[:-6] == md5 or abs(p.stat().st_mtime - me) > 2 * 86400:
            continue
        try:
            t = cv2.imdecode(np.frombuffer(decode(p.read_bytes())[0], np.uint8), cv2.IMREAD_GRAYSCALE)
        except Exception:  # noqa: BLE001
            continue
        if t is not None:
            out.append(t)
    return out


def click(x, y):
    win32api.SetCursorPos((x, y))
    win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, 0, 0)
    win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, 0, 0)


def scroll_up(h):
    """一格滚轮约 15 像素，滚 40 格约大半屏，相邻两屏有重叠，不会漏过气泡。"""
    L, T, R, B = win32gui.GetWindowRect(h)
    win32api.SetCursorPos((L + int((R - L) * 0.65), T + int((B - T) * 0.4)))
    for _ in range(8):
        win32api.mouse_event(win32con.MOUSEEVENTF_WHEEL, 0, 0, 120 * 5)
        time.sleep(0.05)
    time.sleep(0.8)


def original(d, md5, hd):
    hp, p = d / f'{md5}_h.dat', d / f'{md5}.dat'
    if hp.exists():
        return hp
    return p if p.exists() and not hd else None


def fetch_one(h, job, decode):
    d, md5 = Path(job['dir']), job['md5']
    got = original(d, md5, job.get('hd'))
    if got:
        return got, ''
    tpl = cv2.imdecode(np.frombuffer(decode((d / f'{md5}_t.dat').read_bytes())[0], np.uint8), cv2.IMREAD_GRAYSCALE)
    if tpl is None:
        return None, '缩略图解不开'
    others = siblings(d, md5, decode)
    for _ in range(MAX_SCROLL + 1):
        img, L, T = shot(h)
        v, c, s = find(img, tpl)
        if v >= MATCH and not beaten(img, c, s, v, others):
            before = set(wechat_windows())
            click(L + c[0], T + c[1])
            for _ in range(WAIT * 2):
                time.sleep(0.5)
                got = original(d, md5, job.get('hd')) or (d / f'{md5}.dat' if (d / f'{md5}.dat').exists() else None)
                if got and (got.name.endswith('_h.dat') or not job.get('hd')):
                    break
            time.sleep(0.5)
            new = [w for w in wechat_windows() if w not in before]
            for w in new:
                win32gui.PostMessage(w, win32con.WM_CLOSE, 0, 0)
            if not new:
                key(win32con.VK_ESCAPE)
            time.sleep(0.6)
            front(h)
            got = original(d, md5, job.get('hd')) or (d / f'{md5}.dat' if (d / f'{md5}.dat').exists() else None)
            return (got, '') if got else (None, '点开了，但原图没下载下来')
        scroll_up(h)
    return None, f'在会话里往上翻了 {MAX_SCROLL} 屏没找到这张图'


def fetch(jobs, decode, idle=0):
    """jobs：[{chat, dir, md5, hd}]；decode：dat 解密函数（返回 (bytes, ext)）。返回 {md5: (原图路径或 None, 原因)}。"""
    out = {}
    if not jobs:
        return out
    if idle and idle_seconds() < idle:
        return {j['md5']: (None, f'电脑正在使用，等空闲 {idle} 秒后再自动点开原图') for j in jobs}
    h = main_window()
    if not h:
        return {j['md5']: (None, '微信没在运行，无法自动点开原图') for j in jobs}
    prev_fg, was_min = win32gui.GetForegroundWindow(), win32gui.IsIconic(h)
    clip = get_clip()
    try:
        if not front(h):
            return {j['md5']: (None, '微信窗口没能置前（可能锁屏）') for j in jobs}
        by_chat = {}
        for j in jobs:
            by_chat.setdefault(j['chat'], []).append(j)
        for chat, js in by_chat.items():
            open_chat(h, chat)
            for j in js:
                try:
                    out[j['md5']] = fetch_one(h, j, decode)
                except Exception as e:  # noqa: BLE001
                    out[j['md5']] = (None, f'自动点开原图出错：{e}')
                # 下一张从会话底部重新找
                if len(js) > 1:
                    open_chat(h, chat)
    finally:
        if clip is not None:
            try:
                set_clip(clip)
            except Exception:  # noqa: BLE001
                pass
        if was_min:
            win32gui.ShowWindow(h, win32con.SW_MINIMIZE)
        if prev_fg and prev_fg != h and win32gui.IsWindow(prev_fg):
            front(prev_fg)
    return out


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import cfg
    if not cfg.enabled('微信'):
        print('微信模块未启用（config.json 的 微信.启用）')
        sys.exit(0)
    import 微信材料收取 as m
    if len(sys.argv) < 4:
        sys.exit(__doc__)
    job = {'chat': sys.argv[1], 'dir': sys.argv[2], 'md5': sys.argv[3], 'hd': len(sys.argv) > 4}
    print(m.session(lambda: fetch([job], m.decode_dat))())
