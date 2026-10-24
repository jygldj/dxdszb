#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""签名栈一致性校验：cctv/ysp_sign.py  vs  jiuyue/py/道玄央视网.py

【为什么需要这个】
央视频签名栈在仓内有两处实现：
  1. `cctv/ysp_sign.py`          —— 定时抓取脚本用（Actions 每小时跑）
  2. `jiuyue/py/道玄央视网.py`   —— drpy 源用（主公在 OK影视/TVBox 里看）
两处若参数漂移（如 TEA 密钥、GuardTime 常量、Base64 字母表被改动其一），
后果是**一边能签一边不能签**，且报错信息极难定位（表现为"央视频全线 403"）。
故本脚本用「静态常量比对 + 加密往返自检」双向验证二者一致。

【检查项】
  A. 静态常量：DELTA / ROUNDS / LOG_ROUNDS / SALT_LEN / ZERO_LEN /
     TEA_CKEY / GUARD_TEA_KEY / XOR_KEY / GUARD_XOR_KEY /
     STANDARD_ALPHABET / CUSTOM_ALPHABET / YSP_API / YSP_UA
  B. 频道表：LIVE_CHANNELS 的 (key, cnlid, livepid) 三元组全等
  C. 加密往返：cbc_enc → cbc_dec 还原原文（验算法实现自洽）
  D. Base64 往返：b64enc → b64dec 还原原文

【用法】
    python3 cctv/check_sign_sync.py
退出码 0 = 完全一致；1 = 存在差异（须人工核对）。
"""

import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import ysp_sign as Y

DRPY = os.path.join(HERE, '..', 'jiuyue', 'py', '道玄央视网.py')

# 需在 drpy 源中比对的常量名 -> 取值方式
SCALARS = {
    'DELTA': 'DELTA',
    'ROUNDS': 'ROUNDS',
    'LOG_ROUNDS': 'LOG_ROUNDS',
    'SALT_LEN': 'SALT_LEN',
    'ZERO_LEN': 'ZERO_LEN',
    'TEA_CKEY': 'TEA_CKEY',
    'GUARD_TEA_KEY': 'GUARD_TEA_KEY',
    'XOR_KEY': 'XOR_KEY',
    'GUARD_XOR_KEY': 'GUARD_XOR_KEY',
    'STANDARD_ALPHABET': 'STANDARD_ALPHABET',
    'CUSTOM_ALPHABET': 'CUSTOM_ALPHABET',
    'YSP_API': 'YSP_API',
    'YSP_UA': 'YSP_UA',
}

# 需在 drpy 源中比对的类属性名（在 class Spider 内）
CLASS_ATTRS = {
    'DELTA': 'DELTA',
    'ROUNDS': 'ROUNDS',
    'LOG_ROUNDS': 'LOG_ROUNDS',
    'SALT_LEN': 'SALT_LEN',
    'ZERO_LEN': 'ZERO_LEN',
    'TEA_CKEY': 'TEA_CKEY',
    'GUARD_TEA_KEY': 'GUARD_TEA_KEY',
    'XOR_KEY': 'XOR_KEY',
    'GUARD_XOR_KEY': 'GUARD_XOR_KEY',
    'STANDARD_ALPHABET': 'STANDARD_ALPHABET',
    'CUSTOM_ALPHABET': 'CUSTOM_ALPHABET',
}


def norm(v):
    """把 Python 字面量归一化成可比字符串。

    需消除以下"表述差异但值相同"的情况：
      - bytes.fromhex('59b2...')  vs  b'\\x59\\xb2...'  vs  '59b2...'（hex 串）
      - 十六进制字面量 [0x84, 0x2E]  vs  十进制列表 [132, 46]
      - hex 串大小写差异
    故：bytes 一律转 hex 小写；列表元素一律转 int 后再比。
    """
    if isinstance(v, bytes):
        return 'bytes:' + v.hex().lower()
    if isinstance(v, str) and re.fullmatch(r'[0-9a-fA-F]{16,}', v):
        # 裸 hex 串（如 drpy 源里的 bytes.fromhex('59b2...') 求值后已是 bytes，
        # 但若解析失败残留字符串，也按 hex 归一）
        return 'bytes:' + v.lower()
    if isinstance(v, (list, tuple)):
        try:
            return '[' + ','.join(str(int(x)) for x in v) + ']'
        except (TypeError, ValueError):
            return '[' + ','.join(norm(x) for x in v) + ']'
    return str(v)


def eval_literal(s):
    """把源码里的字面量文本安全求值。

    支持：数字、字符串、列表/元组（含 0x 十六进制）、bytes.fromhex(...)。
    bytes.fromhex 的结果直接归一为 hex 小写字符串，避免与 bytes 类型比较时失配。
    """
    s = s.strip().rstrip(',')
    try:
        if s.startswith('bytes.fromhex('):
            inner = s[len('bytes.fromhex('):].rstrip(')').strip().strip("'\"")
            return 'bytes:' + inner.lower()
        v = eval(s, {'__builtins__': {}}, {})
        if isinstance(v, bytes):
            return 'bytes:' + v.hex().lower()
        if isinstance(v, (list, tuple)):
            return v
        return v
    except Exception:
        return s


def parse_drpy(text):
    """从 drpy 源码文本里抽出常量与频道表。"""
    out = {'const': {}, 'cls': {}, 'channels': []}

    # 类属性（缩进 4 空格，形如 "    NAME = value"）
    # 列表常量在 drpy 源里跨两行（如 XOR_KEY），故用 DOTALL 匹配到方括号闭合
    for name in CLASS_ATTRS:
        if name in ('XOR_KEY', 'GUARD_XOR_KEY'):
            m = re.search(r'^\s{4}' + name + r'\s*=\s*\[(.*?)\]',
                          text, re.MULTILINE | re.DOTALL)
            if m:
                out['cls'][name] = '[' + m.group(1) + ']'
            continue
        m = re.search(r'^\s{4}' + name + r'\s*=\s*(.+?)\s*$',
                      text, re.MULTILINE)
        if m:
            out['cls'][name] = m.group(1).strip()

    # 模块级常量（drpy 源里 YSP_UA 是模块级；YSP_API 硬编码在 _ysp_play_url 里）
    m = re.search(r'^YSP_UA\s*=\s*(.+?)\s*$', text, re.MULTILINE)
    if m:
        out['const']['YSP_UA'] = m.group(1).strip()
    m = re.search(r'["\'](https://[a-z0-9.]*ysp\.cctv\.cn)["\']', text)
    if m:
        out['const']['YSP_API'] = '"%s"' % m.group(1)
    # 频道表：LIVE_CHANNELS = [ ... ]
    m = re.search(r'LIVE_CHANNELS\s*=\s*\[(.*?)\n\s*\]', text, re.DOTALL)
    if m:
        body = m.group(1)
        for line in body.splitlines():
            t = re.search(r"\(\s*'([a-z0-9_]+)'\s*,\s*'([^']*)'\s*,"
                          r"\s*'([^']*)'\s*,\s*'(\d+)'\s*,\s*'(\d+)'\s*\)", line)
            if t:
                out['channels'].append(
                    (t.group(1), t.group(2), t.group(3), t.group(4), t.group(5)))
    return out


def main():
    if not os.path.exists(DRPY):
        print('[check] 找不到 drpy 源: %s' % DRPY)
        return 1

    text = open(DRPY, encoding='utf-8').read()
    d = parse_drpy(text)
    diffs = []

    print('[check] cctv/ysp_sign.py  vs  jiuyue/py/道玄央视网.py')
    print('')

    # ---------- A. 常量比对 ----------
    print('=== A. 签名常量 ===')
    for name, attr in SCALARS.items():
        mine = norm(getattr(Y, attr))
        if name in d['cls']:
            other = norm(eval_literal(d['cls'][name]))
            src = '类属性'
        elif name in d['const']:
            other = norm(eval_literal(d['const'][name]))
            src = '模块级'
        else:
            other = '(drpy 源中未找到)'
            src = '-'
        ok = (mine == other)
        print('  %-20s %-8s %s' % (name, '一致' if ok else '★不一致',
                                  mine[:60]))
        if not ok:
            diffs.append('常量 %s：ysp_sign=%s / drpy=%s' % (name, mine[:80], other[:80]))

    # ---------- B. 频道表比对 ----------
    print('')
    print('=== B. 频道表 ===')
    mine_ch = [(c[0], c[1], c[2], c[3], c[4]) for c in Y.LIVE_CHANNELS]
    drpy_ch = d['channels']
    print('  ysp_sign 频道数 = %d' % len(mine_ch))
    print('  drpy   频道数 = %d' % len(drpy_ch))
    if mine_ch == drpy_ch:
        print('  频道表     一致')
    else:
        print('  频道表     ★不一致')
        diffs.append('频道表不一致')
        s_mine = set(x[0] for x in mine_ch)
        s_d = set(x[0] for x in drpy_ch)
        only_mine = sorted(s_mine - s_d)
        only_d = sorted(s_d - s_mine)
        if only_mine:
            print('    仅 ysp_sign 有: %s' % ', '.join(only_mine))
        if only_d:
            print('    仅 drpy 源有:   %s' % ', '.join(only_d))
        for a, b in zip(mine_ch, drpy_ch):
            if a != b:
                print('    首个差异: %s vs %s' % (a, b))
                break

    # ---------- C. 加密往返自检 ----------
    print('')
    print('=== C. 算法往返自检（ysp_sign 自身） ===')
    try:
        plain = b'dxdszb-cctv-sign-sync-test-0123456789'
        key = Y.TEA_CKEY
        enc = Y.cbc_enc(plain, len(plain), key)
        dec = Y.cbc_dec(enc, len(enc), key)
        ok = (dec == plain)
        print('  TEA-CBC 往返  %s（%d 字节）' % ('一致' if ok else '★失败', len(plain)))
        if not ok:
            diffs.append('TEA-CBC 往返失败')
    except Exception as e:
        print('  TEA-CBC 往返  ★异常: %s' % str(e)[:70])
        diffs.append('TEA-CBC 往返异常')

    # ---------- D. Base64 往返 ----------
    try:
        raw = bytes(range(256))
        e2 = Y.b64enc(raw)
        d2 = Y.b64dec(e2)
        ok = (d2 == raw)
        print('  Base64 往返  %s（%d 字节）' % ('一致' if ok else '★失败', len(raw)))
        if not ok:
            diffs.append('Base64 往返失败')
    except Exception as e:
        print('  Base64 往返  ★异常: %s' % str(e)[:70])
        diffs.append('Base64 往返异常')

    # ---------- 结论 ----------
    print('')
    if diffs:
        print('★ 发现 %d 处差异：' % len(diffs))
        for x in diffs:
            print('   - %s' % x)
        print('')
        print('处置：以 cctv/ysp_sign.py 为准，回改 drpy 源（git 可回退），')
        print('或反之——但**必须让二者一致**，否则会出现"一边能签一边 403"。')
        return 1

    print('✓ 两处签名栈完全一致，无漂移。')
    return 0


if __name__ == '__main__':
    sys.exit(main())
