# -*- coding: utf-8 -*-
"""V6 逻辑单测（离线，不打 CDN）：
  1) parse_pdt 各格式
  2) swap_host 正确性（V6：只换 host，query 原样）
  3) probe_live 的 stale 判定（PDT 老 → 判失败）
  4) probe_live 的 no-PDT 兜底（序号推进 → 判活；不推进 → 判死）
  5) verify 的「先换 host 再验活」顺序
  6) 节点表：dtocnc 已恢复入池（V5 的 0% 系 ACL 误判）
  7) fetch_cctv_m3u 的 V6 三铁律：主流程零取流 / 写盘顺序反转 / repeat=2
  8) host × cdn 静态判定
"""
import os
import re
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


def strip_doc(src):
    """去掉注释与文档字符串，只留可执行代码。"""
    body = '\n'.join(ln for ln in src.splitlines() if not ln.strip().startswith('#'))
    for marker in ('"""', "'''"):
        while marker in body:
            a = body.find(marker)
            b = body.find(marker, a + len(marker))
            if b < 0:
                break
            body = body[:a] + body[b + len(marker):]
    return body


print('=== 1) parse_pdt ===')
ck(Y.parse_pdt('2026-10-24T23:17:55+08:00') == 1792855075, '带时区偏移')
ck(Y.parse_pdt('2026-10-24T15:17:55Z') == 1792855075, 'Z 时区')
ck(Y.parse_pdt('2026-10-24T23:17:55.500+08:00') == 1792855075, '带毫秒')
ck(Y.parse_pdt('2026-10-24T23:17:55+0800') == 1792855075, '紧凑时区')
ck(Y.parse_pdt('garbage') is None, '非法输入返回 None')
ck(Y.parse_pdt('2026-10-24T07:17:55-08:00') == 1792855075, '负时区偏移')

print('\n=== 2) swap_host（V6：只换 host，query 原样）===')
u = 'http://hlsliveali-cdn.ysp.cctv.cn/VKEY/2024078201.m3u8?from=player&svrtime=1&pid=2&cdn=5506'
sw, ch = Y.swap_host(u, 'live-dtocnc-cdn.ysp.cctv.cn')
ck(sw.startswith('http://live-dtocnc-cdn.ysp.cctv.cn/VKEY/'), 'host 已换')
ck('pid=2' in sw and 'svrtime=1' in sw and 'from=player' in sw, '其余 query 原样保留')
ck('cdn=5505' in sw, 'cdn 成对替换为该 host 的配对值（5506→5505）')
ck(ch is True, 'changed 标志为 True')
ck(Y.swap_host('bad-url', 'x') == ('bad-url', False), '非法 URL 原样返回')
ck(Y.host_of(sw) == 'live-dtocnc-cdn.ysp.cctv.cn', 'host_of 正确')
ck(Y.is_good_host(u) is True, 'is_good_host 对 hlsliveali 为 True')
ck(Y.is_good_host(sw) is True, 'is_good_host 对 dtocnc 也为 True（V6 已恢复入池）')
ck(Y.swap_host(u, Y.host_of(u))[1] is False, '换到自己不产生变更')

print('\n=== 3) probe_once 的实时性判定（用假 session，不打网络）===')


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
    """按模式返回不同内容，并记录被请求的 URL 列表。"""

    def __init__(self, mode):
        self.mode = mode
        self.n = 0
        self.urls = []

    def get(self, url, headers=None, timeout=8, stream=False):
        self.n += 1
        self.urls.append(url)
        if self.mode == 'stale':
            body = ('#EXTM3U\n#EXT-X-PROGRAM-DATE-TIME:%s\n#EXTINF:5.0,\nseg.ts\n'
                    % time.strftime('%Y-%m-%dT%H:%M:%S+08:00',
                                    time.localtime(time.time() - 600)))
        elif self.mode == 'nopdt':
            body = '#EXTM3U\n#EXTINF:5.0,\nseg%d.ts\n' % self.n
        else:
            body = ('#EXTM3U\n#EXT-X-PROGRAM-DATE-TIME:%s\n#EXTINF:5.0,\nseg.ts\n'
                    % time.strftime('%Y-%m-%dT%H:%M:%S+08:00', time.localtime()))
        return FakeResp(body)


fs = FakeSess('fresh')
ok, d = Y.probe_once('http://x/y.m3u8', fs)
ck(ok is True and 'lag=' in d, '新鲜流返回 lag 明细: %s' % d)
ck(len(fs.urls) == 2, 'probe_once 只请求 2 个 URL（列表+分片），实测 %d 个' % len(fs.urls))
ck(fs.urls[0] != fs.urls[1], '两个 URL 不同（重复请求同一列表 URL 必 403）')
ck(fs.urls[0] == 'http://x/y.m3u8', '第 1 个请求是播放列表')

ok2, d2 = Y.probe_once('http://x/y.m3u8', FakeSess('stale'))
ck(ok2 is False and 'stale' in d2, '陈旧流被判失败: %s' % d2)

print('\n=== 4) probe_once 的 no-PDT 不判死 ===')
ok3, d3 = Y.probe_once('http://x/y.m3u8', FakeSess('nopdt'))
ck(ok3 is True and 'nopdt' in d3, 'no-PDT 但分片可取 → 判活: %s' % d3)

# probe_live 应为 probe_once 的薄封装（V6 修复：不再重复请求列表）
src_pl = inspect.getsource(Y.probe_live)
ck('probe_once' in src_pl, 'probe_live 已改为 probe_once 的薄封装')
ck('sess.get' not in src_pl, 'probe_live 内不再直接发请求（避免重复拉列表）')
ck(Y.probe_live('http://x/y.m3u8', FakeSess('fresh'))[0] is True,
   'probe_live 仍可正常判活（兼容旧调用方）')

print('\n=== 5) verify 的顺序：换 host 先于验活 ===')
src = inspect.getsource(Y.verify)
ck('probe_live' in src, 'verify 使用 probe_live（含实时性校验）')
ck('swap_host' in src, 'verify 含换 host 救回')
i_swap = src.find('swap_host')
i_probe = src.find('probe_live')
ck(i_swap != -1 and i_probe != -1 and i_swap < i_probe,
   '换 host 调用在验活调用之前')
ck('PRIMARY_HOST' in src, 'PRIMARY_HOST 在 verify 中（作为兜底归一目标）')
# 【V6】verify 的候选顺序改为「原 host 优先」——V5 的「一律先换 PRIMARY_HOST」
#   已废止（那会把所有线路压到同一边缘池，消灭跨集群冗余）。
ck('order = [cur] if cur in HOST_CDN else []' in src,
   'V6：verify 优先用原 host（保留签发时的集群分布）')

print('\n=== 6) 节点表：dtocnc 已恢复入池（V5 的 0% 系 ACL 误判）===')
ck('live-dtocnc-cdn.ysp.cctv.cn' in Y.GOOD_HOSTS,
   'live-dtocnc-cdn 已回到 GOOD_HOSTS（2026-10-25 复测原 host 6/6 全 200）')
ck(Y.PRIMARY_HOST == 'hlsliveali-cdn.ysp.cctv.cn', 'PRIMARY_HOST 指向稳定节点')
ck(Y.PRIMARY_HOST in Y.GOOD_HOSTS, 'PRIMARY_HOST 属于 GOOD_HOSTS')
ck('live-dtocnc-cdn.ysp.cctv.cn' in Y.ALT_HOSTS, 'dtocnc 在 ALT_HOSTS（可作救回）')
ck(callable(getattr(Y, 'probe_alt_host', None)),
   'probe_alt_host 已定义（V6 修掉 NameError 隐患）')

print('\n=== 7) fetch_cctv_m3u 的 V6 三铁律 ===')
src2 = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          'fetch_cctv_m3u.py'), encoding='utf-8').read()
body = strip_doc(src2)

# 拆出 sign_channels / sample_health 两段分别断言
def func_body(src, name):
    """截取某个函数的源码块（按顶格 def 边界切分）。"""
    m = re.search(r'^def %s\(' % name, src, re.M)
    if not m:
        return ''
    start = m.start()
    nxt = re.search(r'^def \w+\(', src[start + 10:], re.M)
    end = start + 10 + (nxt.start() if nxt else len(src))
    return src[start:end]


sign_src = strip_doc(func_body(src2, 'sign_channels'))
samp_src = strip_doc(func_body(src2, 'sample_health'))

ck(sign_src != '', '找到 sign_channels 函数体')
ck(samp_src != '', '找到 sample_health 函数体')

# 甲案：主流程零取流
ck('Y.verify(' not in sign_src, '甲案：sign_channels 内已无 verify 调用')
ck('probe_live' not in sign_src, '甲案：sign_channels 内已无 probe_live 调用')
ck('probe_once' not in sign_src, '甲案：sign_channels 内已无 probe_once 调用')
ck('probe_segment' not in sign_src, '甲案：sign_channels 内已无 probe_segment 调用')
ck('Y.play_url' in sign_src, '甲案：仍走签发接口（额度独立、从不封）')
ck('Y.swap_host(u,' not in sign_src, 'V6：正常签发不换 host（原生 host 优先）')
ck('Y.swap_host(fallback, h)' in sign_src,
   'V6：重签耗尽才主动换 host（编排到同族另一集群）')
ck('u = u or url' not in body, '代码中已移除「复用第一条 URL」的写法')
ck('written_urls' in sign_src, '维护 written_urls 去重集合')
ck('used_hosts' in sign_src, 'V6：同频道备选线强制异 host（跨集群冗余）')
ck('if h in used_hosts' in sign_src, 'V6：同 host 时重签换一枚 token')
ck('Y.cluster_peers(' in sign_src, 'V6：重签耗尽仍同 host 时主动换到同族另一集群')

# 乙案：写盘顺序反转
ck('.reverse()' in sign_src, '乙案：频道内线路顺序反转（最后签发放线路1）')
ck('channel_entries' in sign_src, '乙案：线路先入 channel_entries 再统一反转')

# 丙案：repeat 默认 2
m_rep = re.search(r"'--repeat',\s*type=int,\s*default=(\d+)", src2)
ck(m_rep is not None and m_rep.group(1) == '2',
   '丙案：--repeat 默认值为 2（实测 %s）'
   % (m_rep.group(1) if m_rep else '未找到'))

# 体检用一次性 token
ck('Y.play_url' in samp_src, '体检自行签发 token（一次性）')
ck('probe_once' in samp_src, '体检用 probe_once 实测（单次列表）')
ck('Y.verify(' not in samp_src, '体检不用 verify（避免耗额度）')
ck("u = Y.play_url(cnlid, livepid, defn, sess)" in samp_src,
   '体检 token 与发布 token 完全独立（一次性，用完即弃）')

print('\n=== 8) host × cdn 静态判定 ===')
ck(Y.cdn_of(u) == '5506', 'cdn_of 正确取到 5506')
ck(Y.cdn_of('http://h/p') == '', '无 query 时 cdn_of 返回空')
ck(Y.path_family('http://h/ABCDEF0123456789.m3u8?a=1') == 'std',
   '标准族路径识别')
ck(Y.path_family('http://h/xxx_fhd.m3u8?a=1') == 'ysp',
   'ysp 后缀族路径识别')
ck(Y.path_family('http://h/whatever') == '', '无法识别的路径返回空')
ck(Y.HOST_CDN['hlsliveali-cdn.ysp.cctv.cn'] == '5506', 'host↔cdn 配对表正确')
ck(Y.HOST_CDN['live-dtocnc-cdn.ysp.cctv.cn'] == '5505', 'dtocnc↔5505 配对正确')
ck(Y.HOST_CDN['outlivecloud-cdn.ysp.cctv.cn'] == '5401', 'outlivecloud↔5401 配对正确')
ck(Y.HOST_CDN['mobileliveali-cdn.ysp.cctv.cn'] == '5205', 'mobileliveali↔5205 配对正确')

# 集群池（跨集群冗余编排用）
std_u = 'http://hlsliveali-cdn.ysp.cctv.cn/ABCDEF0123456789ABCDEF.m3u8?from=player&cdn=5506'
ysp_u = 'http://mobileliveali-cdn.ysp.cctv.cn/xxx_fhd.m3u8?from=player&cdn=5205'
ck(Y.cluster_of(std_u) == 'std', 'std 族识别')
ck(Y.cluster_of(ysp_u) == 'ysp', 'ysp 族识别')
peers = Y.cluster_peers(std_u)
ck('live-dtocnc-cdn.ysp.cctv.cn' in peers, 'std 族同簇可换 host')
ck('mobileliveali-cdn.ysp.cctv.cn' not in peers, '跨族 host 不在同簇列表（跨族会 404）')
ck('hlsliveali-cdn.ysp.cctv.cn' not in peers, '同簇列表排除自身')
peers_y = Y.cluster_peers(ysp_u)
ck('mobilelive-cnc-cdn.ysp.cctv.cn' in peers_y, 'ysp 族同簇可换 host')
ck('hlsliveali-cdn.ysp.cctv.cn' not in peers_y, 'ysp 族不与 std 族互串')

print('\n=== 结果 ===')
if fails:
    print('  失败 %d 项:' % len(fails))
    for f in fails:
        print('   - %s' % f)
    sys.exit(1)
print('  全部通过')
