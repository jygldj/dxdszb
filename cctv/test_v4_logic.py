# -*- coding: utf-8 -*-
"""V5 逻辑单测（离线，不打 CDN）：
  1) parse_pdt 各格式
  2) swap_host 正确性
  3) probe_live 的 stale 判定（PDT 老 → 判失败）
  4) probe_live 的 no-PDT 兜底（序号推进 → 判活；不推进 → 判死）
  5) verify 的「先换 host 再验活」顺序（V5 核心）
  6) 节点表：live-dtocnc-cdn 已从可用列表剔除
  7) fetch_cctv_m3u 中「不验活不写盘」「不重复 URL」「签发后立即换 host」
"""
import os
import sys
import time
import inspect

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ysp_sign as Y

fails = []


def ck(cond, msg):
    print('  %s %s' % ('PASS' if cond else 'FAIL', msg))
    if not cond:
        fails.append(msg)


print('=== 1) parse_pdt ===')
ck(Y.parse_pdt('2026-10-24T23:17:55+08:00') == 1792855075, '带时区偏移')
ck(Y.parse_pdt('2026-10-24T15:17:55Z') == 1792855075, 'Z 时区')
ck(Y.parse_pdt('2026-10-24T23:17:55.500+08:00') == 1792855075, '带毫秒')
ck(Y.parse_pdt('2026-10-24T23:17:55+0800') == 1792855075, '紧凑时区')
ck(Y.parse_pdt('garbage') is None, '非法输入返回 None')
ck(Y.parse_pdt('2026-10-24T07:17:55-08:00') == 1792855075, '负时区偏移')

print('\n=== 2) swap_host ===')
u = 'http://hlsliveali-cdn.ysp.cctv.cn/VKEY/2024078203.m3u8?from=player&svrtime=1&pid=2&cdn=5506'
sw = Y.swap_host(u, 'live-dtocnc-cdn.ysp.cctv.cn')
ck(sw.startswith('http://live-dtocnc-cdn.ysp.cctv.cn/VKEY/'), 'host 已换')
ck('cdn=5506' in sw and 'pid=2' in sw and 'svrtime=1' in sw, 'query 原样保留')
ck(Y.swap_host('bad-url', 'x') == 'bad-url', '非法 URL 原样返回')
ck(Y.host_of(sw) == 'live-dtocnc-cdn.ysp.cctv.cn', 'host_of 正确')
ck(Y.is_good_host(u) is True, 'is_good_host 对 hlsliveali 为 True')
ck(Y.is_good_host(sw) is False, 'is_good_host 对 dtocnc 为 False')

print('\n=== 3) probe_live 的 stale 判定（用假 session，不打网络）===')


class FakeResp(object):
    def __init__(self, text):
        self.text = text
        self.status_code = 200
        self.content = text.encode()

    def iter_content(self, n):
        # 伪分片：TS 同步字 + 足够长度
        yield b'\x47' + b'\x00' * 20000

    def close(self):
        pass


class FakeSess(object):
    """按 URL 里的标记返回不同内容。"""

    def __init__(self, mode):
        self.mode = mode
        self.n = 0

    def get(self, url, headers=None, timeout=8, stream=False):
        self.n += 1
        if self.mode == 'stale':
            body = ('#EXTM3U\n#EXT-X-PROGRAM-DATE-TIME:%s\n#EXTINF:5.0,\nseg.ts\n'
                    % time.strftime('%Y-%m-%dT%H:%M:%S+08:00',
                                    time.localtime(time.time() - 600)))
        elif self.mode == 'nopdt-adv':
            # 无 PDT，但序号推进（第 1 次 seg1，第 2 次 seg2）
            body = '#EXTM3U\n#EXTINF:5.0,\nseg%d.ts\n' % self.n
        elif self.mode == 'nopdt-frozen':
            # 无 PDT，且序号不推进
            body = '#EXTM3U\n#EXTINF:5.0,\nseg.ts\n'
        else:
            body = ('#EXTM3U\n#EXT-X-PROGRAM-DATE-TIME:%s\n#EXTINF:5.0,\nseg.ts\n'
                    % time.strftime('%Y-%m-%dT%H:%M:%S+08:00', time.localtime()))
        return FakeResp(body)


ok, d = Y.probe_live('http://x/y.m3u8', FakeSess('fresh'))
ck(ok is True and 'lag=' in d, '新鲜流返回 lag 明细: %s' % d)
ok2, d2 = Y.probe_live('http://x/y.m3u8', FakeSess('stale'))
ck(ok2 is False and 'stale' in d2, '陈旧流被判失败: %s' % d2)

print('\n=== 4) probe_live 的 no-PDT 兜底（V5 新增）===')
ok3, d3 = Y.probe_live('http://x/y.m3u8', FakeSess('nopdt-adv'))
ck(ok3 is True and 'adv' in d3, 'no-PDT 但序号推进 → 判活: %s' % d3)
ok4, d4 = Y.probe_live('http://x/y.m3u8', FakeSess('nopdt-frozen'))
ck(ok4 is False and 'no-PDT' in d4, 'no-PDT 且序号不推进 → 判死: %s' % d4)
ok5, d5 = Y.probe_live('http://x/y.m3u8', FakeSess('nopdt-frozen'),
                       fallback_check=False)
ck(ok5 is False and 'no-PDT' in d5, 'fallback_check=False 时 no-PDT 仍判死: %s' % d5)

print('\n=== 5) verify 的 V5 核心：先换 host 再验活 ===')
src = inspect.getsource(Y.verify)
ck('probe_live' in src, 'verify 使用 probe_live（含实时性校验）')
ck('swap_host' in src, 'verify 含换 host 救回')
# 顺序断言：swap_host 的调用位置应早于 probe_live
i_swap = src.find('swap_host')
i_probe = src.find('probe_live')
ck(i_swap != -1 and i_probe != -1 and i_swap < i_probe,
   '换 host 调用在验活调用之前（顺序反了会白死一次）')
ck('PRIMARY_HOST' in src, 'verify 首选 PRIMARY_HOST')

print('\n=== 6) 节点表：0% 可用节点已剔除 ===')
ck('live-dtocnc-cdn.ysp.cctv.cn' not in Y.GOOD_HOSTS,
   'live-dtocnc-cdn 不在 GOOD_HOSTS（实测 0% 可用）')
ck('live-dtocnc-cdn.ysp.cctv.cn' not in Y.ALT_HOSTS,
   'live-dtocnc-cdn 不在 ALT_HOSTS（不应再被尝试）')
ck(Y.PRIMARY_HOST == 'hlsliveali-cdn.ysp.cctv.cn', 'PRIMARY_HOST 指向稳定节点')
ck(Y.PRIMARY_HOST in Y.GOOD_HOSTS, 'PRIMARY_HOST 属于 GOOD_HOSTS')

print('\n=== 7) fetch_cctv_m3u 中「不验活不写盘」「不重复 URL」「签发后换 host」 ===')
src2 = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          'fetch_cctv_m3u.py'), encoding='utf-8').read()
body = '\n'.join(ln for ln in src2.splitlines() if not ln.strip().startswith('#'))
for marker in ('"""', "'''"):
    while marker in body:
        a = body.find(marker)
        b = body.find(marker, a + len(marker))
        if b < 0:
            break
        body = body[:a] + body[b + len(marker):]

ck('Y.verify(' in src2, 'sign_channels 调用 verify')
ck('u = u or url' not in body, '代码中已移除「复用第一条 URL」的写法')
ck('written_urls' in src2, '维护 written_urls 去重集合')
ck('验活失败' in src2, '验活失败有明确日志')
ck('Y.swap_host(u, Y.PRIMARY_HOST)' in body, '签发后立即换 host 到 PRIMARY_HOST')
ck('seen_hosts' not in body, '已移除「刻意分散到不同节点」逻辑（V4 策略被证伪）')
ck('probe_live' in src2, '抽样体检改用 probe_live（含实时性判定）')

print('\n=== 结果 ===')
if fails:
    print('  失败 %d 项:' % len(fails))
    for f in fails:
        print('   - %s' % f)
    sys.exit(1)
print('  全部通过')
