# -*- coding: utf-8 -*-
"""静态风险审计：只做本地文本分析，绝不联网访问任何 URL"""
import re, collections, json, sys

FILES = ['tv.m3u', 'tv_ok.m3u', 'tv_fast.m3u', 'tv_top.m3u', 'tv.txt']

# 1) 已知恶意域名（火绒实测拦截）
BLOCK_HOST = {'jiduo.me', 'kkk.jjjj.jiduo.me'}
# 2) 疑似盗版/成人采集站的命名特征
BLOCK_KW = ('jiduo', 'tttt', 'av', 'sex', 'porn', 'adult', 'jiuma', 'qyapi')
# 3) 廉价动态域名 TLD（垃圾源重灾区，需人工复核）
RISK_TLD = ('.top', '.sbs', '.xyz', '.cc', '.me', '.fun', '.cyou', '.bond',
            '.shop', '.click', '.link', '.lol', '.ru', '.su', '.pw', '.tk', '.ml')
# 4) PHP 采集接口（非真实流媒体，返回页面常带跳转脚本 → 触发 JS.Redirector）
PHP_API = re.compile(r'\.php\?', re.I)


def urls_of(path):
    """返回 [(行号, 频道名, url)]"""
    out = []
    try:
        lines = open(path, encoding='utf-8', errors='ignore').read().splitlines()
    except FileNotFoundError:
        return out
    cur = ''
    for i, l in enumerate(lines, 1):
        l = l.strip()
        if l.startswith('#EXTINF'):
            cur = l.split(',')[-1].strip()
        elif l.startswith('http'):
            out.append((i, cur, l))
        elif ',' in l and l.rsplit(',', 1)[1].startswith('http'):  # txt 格式
            n, u = l.rsplit(',', 1)
            out.append((i, n.strip(), u))
    return out


def host(u):
    m = re.match(r'https?://([^/:]+)', u)
    return (m.group(1) if m else '').lower()


report = {}
for f in FILES:
    rows = urls_of(f)
    bad_host, bad_kw, php, risk_tld = [], [], [], []
    for ln, name, u in rows:
        h = host(u)
        lu = u.lower()
        if h in BLOCK_HOST or any(h.endswith('.' + d) for d in BLOCK_HOST if not d.startswith('.')):
            bad_host.append((ln, name, u))
        elif any(k in lu for k in BLOCK_KW):
            bad_kw.append((ln, name, u))
        if PHP_API.search(u):
            php.append((ln, name, u))
        elif any(h.endswith(t) for t in RISK_TLD):
            risk_tld.append((ln, name, u))
    report[f] = {'total': len(rows), 'bad_host': bad_host, 'bad_kw': bad_kw,
                 'php': php, 'risk_tld': risk_tld}

print('=' * 78)
print('静态风险审计（未联网访问任何地址）')
print('=' * 78)
for f in FILES:
    r = report[f]
    print(f"\n【{f}】总条目 {r['total']}")
    print(f"  🔴 已知恶意域名 : {len(r['bad_host'])}")
    print(f"  🔴 涉黄/盗版关键词: {len(r['bad_kw'])}")
    print(f"  🟠 PHP 采集接口  : {len(r['php'])}")
    print(f"  🟡 廉价动态域名   : {len(r['risk_tld'])}")

print('\n' + '=' * 78)
print('一、已知恶意域名明细（必须删除）')
print('=' * 78)
seen = set()
for f in FILES:
    for ln, name, u in report[f]['bad_host']:
        k = (f, ln)
        if k in seen:
            continue
        seen.add(k)
        print(f'  {f}:{ln}  [{name}] {u[:110]}')

print('\n' + '=' * 78)
print('二、PHP 采集接口域名 TOP（火绒主要误报源，建议整批剔除）')
print('=' * 78)
c = collections.Counter()
for f in FILES:
    for ln, name, u in report[f]['php']:
        c[host(u)] += 1
for h, n in c.most_common(20):
    print(f'  {n:4d}  {h}')

print('\n' + '=' * 78)
print('三、廉价动态域名 TOP（垃圾源重灾区，建议整批剔除）')
print('=' * 78)
c2 = collections.Counter()
for f in FILES:
    for ln, name, u in report[f]['risk_tld']:
        c2[host(u)] += 1
for h, n in c2.most_common(25):
    print(f'  {n:4d}  {h}')
