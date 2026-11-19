"""本机微信 4.x 数据库实时读取：用已保存的密钥把指定库连同 WAL 解密到本机临时目录，供只读查询。

密钥取自 WeChatDataAnalysis 保存的 account_keys.json，解密结果只放 %TMP%\\wx_shared，不出本机；
各脚本共用同一份解密结果（2026-11-16 起），LIVE 变量仅为兼容旧写法保留，不再使用。
WCDB 加密参数（与 SQLCipher 4 一致）：页 4096，每页尾部保留 80 字节（IV 16 + HMAC 64），
密钥 PBKDF2-HMAC-SHA512 256000 次，以第一页前 16 字节为盐。
WAL 里是尚未写回主库的最新页：只取盐值与 WAL 头一致、校验和链连续的帧，按顺序覆盖到主库对应页。
"""
import hashlib
import json
import os
import sqlite3
import struct
from pathlib import Path

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

import cfg

PAGE, RESERVE, ITER = 4096, 80, 256000
SQLITE_HEADER = b'SQLite format 3\x00'
LIVE = Path(os.environ['TMP']) / 'wx_live'
KEYS = cfg.path('微信.密钥文件')


def account():
    """取当前账号的密钥与 db_storage 目录（选 db_storage 最近有写入的那个）。"""
    if not cfg.enabled('微信'):
        raise RuntimeError('微信模块未启用（config.json 的 微信.启用）')
    if KEYS is None or not KEYS.exists():
        raise RuntimeError(f'没找到微信密钥文件（config.json 的 微信.密钥文件）：{KEYS}')
    best = None
    for info in json.loads(KEYS.read_text(encoding='utf-8')).values():
        store = Path(info.get('db_key_source_db_storage_path') or '')
        msg = store / 'message'
        if msg.exists():
            mt = max(p.stat().st_mtime for p in msg.glob('*.db'))
            if not best or mt > best[2]:
                best = (bytes.fromhex(info['db_key']), store, mt)
    if not best:
        raise RuntimeError('没找到可用的微信密钥与数据目录')
    return best[0], best[1]


def _keys(raw, salt):
    enc = hashlib.pbkdf2_hmac('sha512', raw, salt, ITER, 32)
    return enc


def _decrypt_page(enc_key, page, pgno):
    iv = page[PAGE - RESERVE:PAGE - RESERVE + 16]
    start = 16 if pgno == 1 else 0
    dec = Cipher(algorithms.AES(enc_key), modes.CBC(iv)).decryptor()
    body = dec.update(page[start:PAGE - RESERVE]) + dec.finalize()
    return (SQLITE_HEADER if pgno == 1 else b'') + body + page[PAGE - RESERVE:]


def _wal_checksum(data, s1, s2, big):
    fmt = '>' if big else '<'
    for i in range(0, len(data), 8):
        a, b = struct.unpack(fmt + 'II', data[i:i + 8])
        s1 = (s1 + a + s2) & 0xFFFFFFFF
        s2 = (s2 + b + s1) & 0xFFFFFFFF
    return s1, s2


def _wal_pages(wal_path):
    """返回已提交的 WAL 帧 {页号: 加密页}，后写的覆盖先写的。"""
    if not wal_path.exists() or wal_path.stat().st_size < 32:
        return {}
    data = wal_path.read_bytes()
    magic, _, page_size, _, salt1, salt2, c1, c2 = struct.unpack('>IIIIIIII', data[:32])
    if magic not in (0x377F0682, 0x377F0683) or page_size != PAGE:
        return {}
    big = magic == 0x377F0683
    s1, s2 = _wal_checksum(data[:24], 0, 0, big)
    if (s1, s2) != (c1, c2):
        return {}
    pages, pending, off = {}, {}, 32
    while off + 24 + PAGE <= len(data):
        pgno, commit, fs1, fs2, fc1, fc2 = struct.unpack('>IIIIII', data[off:off + 24])
        if (fs1, fs2) != (salt1, salt2):
            break
        s1, s2 = _wal_checksum(data[off:off + 8], s1, s2, big)
        s1, s2 = _wal_checksum(data[off + 24:off + 24 + PAGE], s1, s2, big)
        if (s1, s2) != (fc1, fc2):
            break
        pending[pgno] = data[off + 24:off + 24 + PAGE]
        if commit:
            pages.update(pending)
            pending = {}
        off += 24 + PAGE
    return pages


SHARED = Path(os.environ['TMP']) / 'wx_shared'


def decrypt(rel):
    """把 db_storage 下的某个库（如 'message/message_0.db'）连同 WAL 解密到临时目录，返回明文库路径。

    各脚本共用一份：文件名带源库和 WAL 的大小、修改时间，源库没变就直接用现成的，不重复解密；
    变了就另写一个新文件，不覆盖别的进程正开着的旧文件（Windows 不让覆盖），旧文件没人用时顺手删掉。
    """
    raw, store = account()
    src = store / rel
    wal_path = src.with_name(src.name + '-wal')
    sig = '-'.join(f'{st.st_size:x}{int(st.st_mtime * 1000):x}' for st in (q.stat() for q in (src, wal_path) if q.exists()))
    stem = rel.replace('/', '__')
    dst = SHARED / f'{stem}.{hashlib.sha1(sig.encode()).hexdigest()[:12]}.db'
    SHARED.mkdir(exist_ok=True)
    if dst.exists():
        return dst
    data = src.read_bytes()
    enc_key = _keys(raw, data[:16])
    wal = _wal_pages(wal_path)
    npages = max(len(data) // PAGE, max(wal, default=0))
    tmp = SHARED / f'{stem}.{os.getpid()}.tmp'
    with tmp.open('wb') as out:
        for i in range(npages):
            pgno = i + 1
            page = wal.get(pgno) or data[i * PAGE:(i + 1) * PAGE]
            if len(page) < PAGE:
                break
            out.write(_decrypt_page(enc_key, page, pgno))
    try:
        os.replace(tmp, dst)
    except OSError:  # 另一个进程刚写好同一份并已打开
        tmp.unlink(missing_ok=True)
    for old in SHARED.glob(f'{stem}.*.db'):
        if old != dst:
            try:
                old.unlink()
                for tail in ('-shm', '-wal'):
                    old.with_name(old.name + tail).unlink(missing_ok=True)
            except OSError:
                pass
    return dst


def connect(rel):
    return sqlite3.connect(f'file:{decrypt(rel)}?mode=ro', uri=True)
