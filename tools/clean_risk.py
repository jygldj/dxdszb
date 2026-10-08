# -*- coding: utf-8 -*-
"""清洗产物中的高风险源。只做本地文本处理，绝不联网访问任何 URL。
用法: python clean.py [--php]   # 加 --php 则连 PHP 采集接口一并剔除
"""
import re, sys, os

DO_PHP = '--php' in sys.argv

# ① 已知恶意域名（火绒实测拦截 Trojan/JS.Redirector）
BLOCK_HOST = ('jiduo.me',)
# ② 涉黄/盗版采集站关键词（必须按"域名段/参数"精确匹配，禁止裸子串，
#    否则 avap.jilintv.cn 这类正规台会被 "av" 误杀）
BLOCK_KW = ('jiduo', 'n=tttt', 'porn', '/sex', '/adult', 'xxx.', '.xxx')
# ③ PHP 采集接口（非真实流，返回页面常带跳转脚本 → 触发 JS.Redirector）
PHP_API = re.compile(r'\.php\?', re.I)


def risky(u):
    lu = u.lower()
    if any(h in lu for h in BLOCK_HOST):
        return '恶意域名'
    if any(k in lu for k in BLOCK_KW):
        return '涉黄/盗版关键词'
    if DO_PHP and PHP_API.search(u):
        return 'PHP采集接口'
    return None


def host(u):
    m = re.match(r'https?://([^/:]+)', u)
    return (m.group(1) if m else '').lower()


def clean_m3u(path):
    lines = open(path, encoding='utf-8', errors='ignore').read().splitlines()
    out, cut, i = [], [], 0
    while i < len(lines):
        l = lines[i]
        if l.startswith('#EXTINF') and i + 1 < len(lines) and lines[i + 1].startswith('http'):
            u = lines[i + 1]
            r = risky(u)
            if r:
                cut.append((r, l.split(',')[-1].strip(), host(u)))
                i += 2
                continue
            out.append(l)
            out.append(u)
            i += 2
        else:
            out.append(l)
            i += 1
    open(path, 'w', encoding='utf-8').write('\n'.join(out) + '\n')
    return len(cut), cut


def clean_txt(path):
    lines = open(path, encoding='utf-8', errors='ignore').read().splitlines()
    out, cut = [], []
    for l in lines:
        if ',#genre#' in l or not l.strip():
            out.append(l)
            continue
        if ',' in l:
            n, u = l.rsplit(',', 1)
            if u.startswith('http'):
                r = risky(u)
                if r:
                    cut.append((r, n.strip(), host(u)))
                    continue
        out.append(l)
    open(path, 'w', encoding='utf-8').write('\n'.join(out) + '\n')
    return len(cut), cut


print(f'模式: {"恶意域名 + 涉黄关键词 + PHP采集接口" if DO_PHP else "恶意域名 + 涉黄关键词"}')
print('=' * 70)
for f in ['tv.m3u', 'tv_ok.m3u', 'tv_fast.m3u', 'tv_top.m3u']:
    if not os.path.exists(f):
        continue
    n, cut = clean_m3u(f)
    print(f'  {f:14s} 删除 {n} 条')
    for r, nm, h in cut[:6]:
        print(f'      [{r}] {nm}  <- {h}')
for f in ['tv.txt']:
    if not os.path.exists(f):
        continue
    n, cut = clean_txt(f)
    print(f'  {f:14s} 删除 {n} 条')
    for r, nm, h in cut[:6]:
        print(f'      [{r}] {nm}  <- {h}')

print('\n=== 复检：产物中是否还有 jiduo ===')
os.system('grep -c "jiduo" tv.m3u tv_ok.m3u tv_fast.m3u tv_top.m3u tv.txt 2>/dev/null')
