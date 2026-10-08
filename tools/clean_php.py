# -*- coding: utf-8 -*-
"""甲方案：整批剔除 PHP 采集接口源。
保底规则：若某频道剔除后无任何可用源，则保留其最快的一条（依据 speed.tsv），
          并归入「⚠️仅采集源·无替代」分组沉底，避免重要频道整体消失。
只做本地文本处理，绝不联网访问任何 URL。
"""
import re, os, collections, json

PHP_API = re.compile(r'\.php\?', re.I)
BLOCK_HOST = ('jiduo.me',)
BLOCK_KW = ('jiduo', 'n=tttt', 'porn', '/sex', '/adult', 'xxx.', '.xxx')
KEEP_GROUP = '⚠️仅采集源·无替代'
SUFFIX = '·采集'


def risky(u):
    lu = u.lower()
    if any(h in lu for h in BLOCK_HOST):
        return '恶意域名'
    if any(k in lu for k in BLOCK_KW):
        return '涉黄/盗版'
    return None


def base(n):
    return re.sub(r'·备\d+$', '', n)


def load_speed():
    """url -> kbps，用于保底时挑最快的一条"""
    d = {}
    p = os.path.join(os.path.dirname(__file__), 'speed.tsv')
    if not os.path.exists(p):
        return d
    for l in open(p, encoding='utf-8', errors='ignore'):
        r = l.rstrip('\n').split('\t')
        if len(r) >= 7:
            try:
                d[r[-1].lstrip('|')] = int(r[4])
            except (ValueError, IndexError):
                pass
    return d


def process_m3u(path):
    lines = [l.rstrip('\n') for l in open(path, encoding='utf-8', errors='ignore')]
    entries = []          # [(extinf, url)]
    i = 0
    while i < len(lines):
        l = lines[i]
        if l.startswith('#EXTINF') and i + 1 < len(lines) and lines[i + 1].startswith('http'):
            entries.append((l, lines[i + 1]))
            i += 2
        else:
            i += 1

    spd = load_speed()
    # 1) 硬性风险（恶意/涉黄）无条件删除
    entries = [e for e in entries if not risky(e[1])]
    # 2) PHP 接口：按频道聚合，全 PHP 的频道保底留最快一条
    by = collections.OrderedDict()
    for ext, u in entries:
        n = ext.split(',')[-1].strip()
        by.setdefault(base(n), []).append((ext, u))
    out, cut, kept = [], 0, 0
    for b, items in by.items():
        good = [x for x in items if not PHP_API.search(x[1])]
        if good:
            out.extend(good)
            cut += len(items) - len(good)
        else:
            best = max(items, key=lambda x: spd.get(x[1], 0))
            ext, u = best
            ext = re.sub(r'group-title="[^"]*"', f'group-title="{KEEP_GROUP}"', ext)
            nm = ext.split(',')[-1].strip()
            ext = ext[:ext.rfind(',') + 1] + nm + SUFFIX
            out.append((ext, u))
            cut += len(items) - 1
            kept += 1
    # 沉底排序：保底条目放最后
    out.sort(key=lambda x: 1 if KEEP_GROUP in x[0] else 0)
    with open(path, 'w', encoding='utf-8') as f:
        for ext, u in out:
            f.write(ext + '\n' + u + '\n')
    return len(entries), cut, kept, len(out)


def process_txt(path):
    lines = [l.rstrip('\n') for l in open(path, encoding='utf-8', errors='ignore')]
    head, items = [], []   # items: [(name, url)]
    for l in lines:
        if ',#genre#' in l or not l.strip():
            head.append(l)
            continue
        if ',' in l:
            n, u = l.rsplit(',', 1)
            if u.startswith('http'):
                items.append((n.strip(), u))
                continue
        head.append(l)
    items = [x for x in items if not risky(x[1])]
    spd = load_speed()
    by = collections.OrderedDict()
    for n, u in items:
        by.setdefault(base(n), []).append((n, u))
    out, cut, kept = [], 0, 0
    for b, lst in by.items():
        good = [x for x in lst if not PHP_API.search(x[1])]
        if good:
            out.extend(good)
            cut += len(lst) - len(good)
        else:
            best = max(lst, key=lambda x: spd.get(x[1], 0))
            out.append((best[0] + SUFFIX, best[1]))
            cut += len(lst) - 1
            kept += 1
    with open(path, 'w', encoding='utf-8') as f:
        for l in head:
            f.write(l + '\n')
        for n, u in out:
            f.write(f'{n},{u}\n')
    return len(items), cut, kept, len(out)


print('甲方案：整批剔除 PHP 采集接口（含无替代源频道的保底保留）')
print('=' * 72)
for f in ['tv.m3u', 'tv_ok.m3u', 'tv_fast.m3u', 'tv_top.m3u']:
    if os.path.exists(f):
        a, c, k, o = process_m3u(f)
        print(f'  {f:14s} 原 {a:5d} → 剔 {c:4d} → 余 {o:5d}  保底保留频道 {k}')
for f in ['tv.txt']:
    if os.path.exists(f):
        a, c, k, o = process_txt(f)
        print(f'  {f:14s} 原 {a:5d} → 剔 {c:4d} → 余 {o:5d}  保底保留频道 {k}')
print('\n=== 复检：残留 PHP 接口（应仅剩保底条目）===')
for f in ['tv.m3u', 'tv_ok.m3u', 'tv_fast.m3u', 'tv_top.m3u', 'tv.txt']:
    if os.path.exists(f):
        n = sum(1 for l in open(f, encoding='utf-8', errors='ignore') if PHP_API.search(l))
        print(f'  {f:14s} PHP接口残留 {n}')
