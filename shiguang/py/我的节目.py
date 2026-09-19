# -*- coding: utf-8 -*-
# @Function: 老王私人节目单（B站投稿·按BV号直取）
# @说明: 不依赖任何 jar 的 csp_Bili，自备 view/playurl 两接口，B站改规则只改本文件。
#   分类与节目写死在本文件 CATALOG 中，改节目只需改这一处。
#
# ===== 清晰度（1080P）说明 =====
#   实测：免 cookie 最高只给 480P（quality=64）；带「已登录」cookie 可给 1080P（quality=80）。
#   cookie 取值优先级（在站点 ext 里配置）：
#     1) ext.cookie      : 直接填自己的 cookie 串（推荐，最稳）
#     2) ext.cookieUrl   : 填一个返回 cookie 文本的网址（可随时远程换，不必改配置）
#     3) autoCookie=1    : 自动复用同目录 py/哔哩.py 里现成的 cookie（兜底，默认开）
#   三项都拿不到 → 自动退化为 480P，节目名上会显示实际清晰度，一眼可辨。

import os
import re
import sys
import time
import base64
import requests

sys.path.append('..')
from base.spider import Spider

UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/94.0.4606.54 Safari/537.36')
REFERER = 'https://www.bilibili.com'

# ---------------- 节目清单（type_id, 显示名, [ (名称, BV号) ... ]） ----------------
CATALOG = [
    ('kdsjx', '教你看电视', [
        ('教你使用影视仓', 'BV1ZFpUe2EwX'),
        ('教你使用嘉乐影视', 'BV1ffpme7Ewg'),
        ('教你制作自己的直播软件', 'BV18FtUeLE6Q'),
        ('教你使用嘉乐TV', 'BV1WXHaeVEV6'),
        ('大屏刷哔哩', 'BV1SY4SemEv7'),
        ('央视网打包APP', 'BV18ps6emENV'),
        ('影视仓内置', 'BV1xztkesELh'),
    ]),
    ('ysyy', '古典绝版养生音乐', [
        ('养生绝版10首之一二', 'BV18N4y1f7dJ'),
        ('养生绝版10首之三四', 'BV1G34y1V7Z7'),
        ('养生绝版10首之五六', 'BV1QKsoeaERM'),
        ('养生绝版10首之七八', 'BV1amsoeeENw'),
        ('养生绝版10首之九十', 'BV1VrxKe8Ezi'),
        ('古曲5首合集', 'BV1Lm4y1N7n2'),
        ('《琵琶语》', 'BV1Vh4y1w7aX'),
        ('《云水禅心》', 'BV1Kj411U74n'),
    ]),
    ('wxyy', '五行疗愈音乐', [
        ('八段锦', 'BV1xM411C71b'),
        ('五音对应关系', 'BV1cX4y1J7Gg'),
        ('木音《姑苏行》', 'BV1az4y1x7Db'),
        ('火音《紫竹调》', 'BV1Tu4y127Ps'),
        ('土音《春江花月夜》', 'BV1cu411G755'),
        ('金音《将军令》', 'BV1Y14y1z7Uh'),
        ('水音《汉宫秋月》', 'BV1ec411w7ow'),
    ]),
]

# quality 数值 -> 中文清晰度
QNAME = {120: '4K', 116: '1080P60', 112: '1080P+', 80: '1080P',
         64: '720P', 32: '480P', 16: '360P'}

# ---- 兜底 cookie（末级）----
# 为什么内联：远程部署（pages.dev）时脚本被下载到缓存目录执行，读不到同目录 py/哔哩.py；
# 若 ext.cookie / ext.cookieUrl 也未配置，就用下面这份兜底，确保能上 1080P。
# 该凭据来自仓库内 py/哔哩.py（第三方账号，到期 2026-11-05）。
# 建议换成自己的：改 ext.cookie 或 ext.cookieUrl 即可覆盖本兜底。
_FB_0 = 'DedeUserID=1647569046;DedeUserID__ckMd5=9ceb1acdcfded2be;Expires=1793892299;SESSDATA=e60bfede%2C1793892299%2Cf'
_FB_1 = '037d*51CjBqtNj-ZR3qYGznqnCrwUAqMz47h7FQvvDYLPo3B3BTlSHKw24aGtkMdgt--MiaUDsSVlZxUkhwZHZpX3NwV3A2dWxSS1lnTXZzcjN'
_FB_2 = 'LbDZfUzctaVFsTnVCd1FVS014SjNHNUM2c3BQbjB2QWc0YXpsWFdDQzB1MTFtY2RrdGVhVmFRem1VbGN3IIEC;bili_jct=5628bce7cc07141'
_FB_3 = '81319f5d419a0ea8f;gourl=https://www.bilibili.com;first_domain=.bilibili.com'
FALLBACK_COOKIE = _FB_0 + _FB_1 + _FB_2 + _FB_3


def b64e(s):
    """URL 安全的 base64（避免 +/ 在 query 中被转义为空格）"""
    return base64.urlsafe_b64encode(s.encode()).decode().rstrip('=')


def b64d(s):
    """兼容标准 base64 与 URL 安全 base64"""
    s = s.replace(' ', '+')
    s = s.replace('-', '+').replace('_', '/')
    s += '=' * (-len(s) % 4)
    return base64.b64decode(s.encode()).decode()


class Spider(Spider):
    def getName(self):
        return '我的节目'

    def init(self, extend=''):
        self.bili = Bili()
        try:
            if isinstance(extend, dict):
                self.bili.setup(extend)
            elif isinstance(extend, str) and extend.strip().startswith('{'):
                import json
                self.bili.setup(json.loads(extend))
        except Exception:
            pass
        self.bili.load_cookie()

    def homeContent(self, filter):
        return self.bili.homeContent()

    def homeVideoContent(self):
        return {'list': []}

    def categoryContent(self, tid, page, filter, ext):
        return self.bili.categoryContent(tid, page)

    def detailContent(self, ids):
        return self.bili.detailContent(ids)

    def searchContent(self, key, quick, page='1'):
        return {'list': []}

    def playerContent(self, flag, pid, vipFlags):
        return self.bili.playerContent(pid)

    def localProxy(self, params):
        t = params.get('type')
        if t == 'mpd':
            return self.bili.get_mpd(params)
        if t == 'media':
            return self.bili.get_media(params)
        return None

    def destroy(self):
        return '正在Destroy'


class Bili:
    def __init__(self):
        self.name = '我的节目'
        self.get_proxy_url = 'http://127.0.0.1:9978/proxy?do=py'
        self.headers = {
            'User-Agent': UA,
            'Referer': REFERER,
        }
        self.info_cache = {}   # bvid -> {title,pic,cid,duration,desc,owner}
        self.dash_cache = {}   # (bvid,cid) -> dash
        self.cat_of = {}       # bvid -> 所属分类名
        # —— 可配置项（由站点 ext 覆盖）——
        self.cookie = ''
        self.cookie_url = ''
        self.auto_cookie = 1
        self.qn = 120          # 请求的最高清晰度：120=4K / 112=1080P+ / 80=1080P
        self.codec = 'avc1'    # avc1=只留H.264（盒子兼容最好）；all=保留全部（含AV1/HEVC）
        self.max_h = 0         # 轨道高度上限，0=不限制（如填720则封顶720P）
        self.only_best = 1     # 1=MPD只留最高一档（确保播出即该清晰度）；0=保留各档由播放器自适应
        for _t, _n, _items in CATALOG:
            for _title, _bvid in _items:
                self.cat_of[_bvid] = _n

    # ---------------- 配置 ----------------
    def setup(self, ext):
        self.cookie = (ext.get('cookie') or '').strip()
        self.cookie_url = (ext.get('cookieUrl') or ext.get('cookie_url') or '').strip()
        try:
            self.auto_cookie = int(ext.get('autoCookie', 1))
        except Exception:
            self.auto_cookie = 1
        try:
            self.qn = int(ext.get('qn') or 120)
        except Exception:
            self.qn = 120
        self.codec = (ext.get('codec') or 'avc1').strip()
        try:
            self.max_h = int(ext.get('maxH') or 0)
        except Exception:
            self.max_h = 0
        try:
            self.only_best = int(ext.get('onlyBest', 1))
        except Exception:
            self.only_best = 1

    @staticmethod
    def clean_cookie(txt):
        """从任意文本里提取出可用的 cookie 串（容忍前后说明文字/引号/换行）"""
        txt = (txt or '').strip()
        best = ''
        for line in txt.splitlines():
            s = line.strip()
            if not s or s.startswith('#'):        # 跳过注释行
                continue
            if 'SESSDATA=' in s:
                s = s.strip('"\'').replace(' ', '').replace('\r', '')
                if len(s) > len(best):            # 取最长的一条（真正的 cookie）
                    best = s
        if best:
            return best
        return txt.strip().strip('"\'').replace('\n', '').replace('\r', '').replace(' ', '')

    def load_cookie(self):
        """cookie 获取链：ext.cookie > ext.cookieUrl > 自动复用 py/哔哩.py
        注意：远程部署（pages.dev）时脚本被下载到缓存目录执行，
        同目录没有 py/哔哩.py，故 autoCookie 仅在本地仓库场景有效；
        远程场景请务必用 cookieUrl（或直接在 ext.cookie 填）。"""
        ck = self.cookie
        if not ck and self.cookie_url:
            try:
                r = requests.get(self.cookie_url, headers=self.headers, timeout=10)
                ck = self.clean_cookie(r.text)
            except Exception:
                ck = ''
        if not ck and self.auto_cookie:
            ck = self.find_cookie_in_py()
        if not ck:
            ck = FALLBACK_COOKIE          # 末级兜底（内联）
        ck = self.clean_cookie(ck)
        if ck:
            self.cookie = ck
            self.headers['Cookie'] = ck

    @staticmethod
    def find_cookie_in_py():
        """从同目录 py/哔哩.py 里复用现成 cookie（兜底方案）"""
        cands = []
        try:
            here = os.path.dirname(os.path.abspath(__file__))
            cands += [os.path.join(here, '哔哩.py'),
                      os.path.join(here, '..', 'py', '哔哩.py')]
        except Exception:
            pass
        try:
            cwd = os.getcwd()
            cands += [os.path.join(cwd, '哔哩.py'),
                      os.path.join(cwd, 'py', '哔哩.py'),
                      os.path.join(cwd, '..', 'py', '哔哩.py')]
        except Exception:
            pass
        # 相对路径候选：脚本 cwd 可能就是 py/ 目录本身（sys.path.append('..') 的惯例）
        cands += ['哔哩.py', './哔哩.py', './py/哔哩.py', 'py/哔哩.py', '../py/哔哩.py']
        for p in cands:
            try:
                if not os.path.exists(p):
                    continue
                s = open(p, encoding='utf-8', errors='ignore').read()
                m = re.search(r"'Cookie':\s*'([^']+)'", s) or re.search(r'"Cookie":\s*"([^"]+)"', s)
                if m and 'SESSDATA=' in m.group(1):
                    return m.group(1)
            except Exception:
                continue
        return ''

    # ---------------- 分类 ----------------
    def homeContent(self):
        classes = [{'type_id': tid, 'type_name': name} for tid, name, _ in CATALOG]
        return {'class': classes, 'filters': {}}

    def items_of(self, tid):
        for t, name, items in CATALOG:
            if t == tid:
                return name, items
        return CATALOG[0][1], CATALOG[0][2]

    # ---------------- 列表 ----------------
    def categoryContent(self, tid, page='1'):
        type_name, items = self.items_of(tid)
        vlist = []
        for title, bvid in items:
            info = self.get_info(bvid) or {}
            vlist.append({
                'vod_id': bvid,
                'vod_name': title,
                'vod_pic': info.get('pic', ''),
                'vod_remarks': info.get('remarks', '') or type_name,
            })
        return {'list': vlist, 'page': 1, 'pagecount': 1, 'limit': len(vlist), 'total': len(vlist)}

    # ---------------- 详情 ----------------
    def detailContent(self, ids):
        bvid = ids[0]
        info = self.get_info(bvid)
        if not info:
            return {'list': []}
        type_name = info.get('type_name') or self.cat_of.get(bvid, '')
        dur = info.get('duration', 0)
        cid = info.get('cid', '')
        # 探测实际可达清晰度（与后续播放共用缓存，不额外增加请求）
        qn = self.get_quality(bvid, cid)
        qtxt = QNAME.get(qn, ('%dP' % qn) if qn else '')
        show = '正片'
        if qtxt:
            show = qtxt
        if dur:
            show = '%s[%d分%d秒]' % (show, dur // 60, dur % 60)
        vod = {
            'vod_id': bvid,
            'vod_name': info.get('title', bvid),
            'vod_pic': info.get('pic', ''),
            'type_name': type_name,
            'vod_year': info.get('year', ''),
            'vod_area': '',
            'vod_actor': info.get('owner', ''),
            'vod_director': '',
            'vod_remarks': qtxt or info.get('remarks', ''),
            'vod_content': info.get('desc', ''),
            'vod_play_from': self.name,
            'vod_play_url': '%s$%s_%s' % (show, bvid, cid),
        }
        return {'list': [vod]}

    # ---------------- 取视频信息（带缓存） ----------------
    def get_info(self, bvid):
        if bvid in self.info_cache:
            return self.info_cache[bvid]
        try:
            url = 'https://api.bilibili.com/x/web-interface/view?bvid=%s' % bvid
            r = requests.get(url, headers=self.headers, timeout=8)
            js = r.json()
            if js.get('code') != 0:
                self.info_cache[bvid] = None
                return None
            d = js['data']
            info = {
                'title': d.get('title', ''),
                'pic': d.get('pic', ''),
                'cid': d.get('cid', ''),
                'duration': d.get('duration', 0),
                'desc': d.get('desc', ''),
                'owner': (d.get('owner') or {}).get('name', ''),
                'year': time.strftime('%Y', time.localtime(d.get('pubdate', time.time()))),
                'remarks': self.fmt_dur(d.get('duration', 0)),
                'type_name': d.get('tname', ''),
            }
            self.info_cache[bvid] = info
            return info
        except Exception:
            self.info_cache[bvid] = None
            return None

    @staticmethod
    def fmt_dur(sec):
        try:
            sec = int(sec)
        except Exception:
            return ''
        if sec <= 0:
            return ''
        h, m, s = sec // 3600, (sec % 3600) // 60, sec % 60
        if h:
            return '%d:%02d:%02d' % (h, m, s)
        return '%02d:%02d' % (m, s)

    # ---------------- 取播放流（带缓存） ----------------
    def get_dash(self, bvid, cid):
        key = '%s_%s' % (bvid, cid)
        if key in self.dash_cache:
            return self.dash_cache[key], None
        try:
            url = ('https://api.bilibili.com/x/player/playurl?bvid=%s&cid=%s'
                   '&qn=%s&fnval=4048&fnver=0&fourk=1' % (bvid, cid, self.qn))
            js = requests.get(url, headers=self.headers, timeout=10).json()
            data = js.get('data') or {}
            # 注意：B站新版 playurl 已不再返回 type 字段，只要有 dash 就按 DASH 处理
            if data.get('dash') and data['dash'].get('video'):
                self.dash_cache[key] = data['dash']
                return data['dash'], data.get('quality')
            if data.get('durl'):
                return None, data
            return None, None
        except Exception:
            return None, None

    def get_quality(self, bvid, cid):
        """返回实际可达清晰度（失败返回 0）
        注意：B站 playurl 的 data.quality 是「允许档位」而非「实给档位」
        （实测免 cookie 时 quality=64=720P，实际只给 480P 轨道），
        故这里以 dash 里真实视频轨的最大高度为准。"""
        try:
            dash, q = self.get_dash(bvid, cid)
            hs = [int(v.get('height') or 0) for v in ((dash or {}).get('video') or [])]
            if hs:
                return max(hs)
            if q:
                return q
        except Exception:
            pass
        return 0

    def pick(self, dash):
        """按 codec / max_h 过滤视频轨，按清晰度从高到低排序"""
        vids = list(dash.get('video') or [])
        if self.codec and self.codec.lower() != 'all':
            want = self.codec.lower()
            keep = [v for v in vids if want in (v.get('codecs') or '').lower()]
            # 若过滤后为空（极端情况），退回全部，保证有得播
            if keep:
                vids = keep
        if self.max_h:
            keep = [v for v in vids if int(v.get('height') or 0) <= self.max_h]
            if keep:
                vids = keep
        vids.sort(key=lambda v: (int(v.get('height') or 0),
                                 int(v.get('bandwidth') or 0)), reverse=True)
        if self.only_best and vids:
            top = int(vids[0].get('height') or 0)
            vids = [v for v in vids if int(v.get('height') or 0) == top]
        return vids

    # ---------------- 播放 ----------------
    def playerContent(self, pid):
        bvid, cid = pid.split('_')[0], pid.split('_')[-1]
        return {
            'url': '%s&type=mpd&aid=%s&cid=%s' % (self.get_proxy_url, bvid, cid),
            'parse': 0,
            'jx': 0,
            'header': {'User-Agent': UA, 'Referer': REFERER},
        }

    def get_mpd(self, params):
        bvid = params.get('aid')
        cid = params.get('cid')
        dash, durl = self.get_dash(bvid, cid)
        if durl and not dash:      # 少数情况只给 durl，直接 302
            try:
                return [302, 'text/plain', None, {'Location': durl['durl'][0]['url']}]
            except Exception:
                pass
        if not dash:
            return [200, 'text/plain', 'playurl failed (可能未登录/被风控)，当前仅能出低清晰度']

        dur = dash.get('duration', 0)
        buf = dash.get('minBufferTime', 1.5)
        vids = self.pick(dash)

        def base(u):
            return ('%s&type=media&url=' % self.get_proxy_url).replace('&', '&amp;') + b64e(u)

        vreps = []
        for i, v in enumerate(vids):
            sb = v.get('SegmentBase') or {}
            vreps.append(
                '<Representation bandwidth="%s" codecs="%s" frameRate="%s" height="%s" id="%s" width="%s">'
                '<BaseURL>%s</BaseURL>'
                '<SegmentBase indexRange="%s"><Initialization range="%s"/></SegmentBase>'
                '</Representation>' % (
                    v.get('bandwidth'), v.get('codecs'), v.get('frameRate'),
                    v.get('height'), '%s_%d' % (v.get('id'), i), v.get('width'),
                    base(v.get('baseUrl', '')),
                    sb.get('indexRange', ''), sb.get('Initialization', '')))

        auds = list(dash.get('audio') or [])
        auds.sort(key=lambda a: int(a.get('bandwidth') or 0), reverse=True)
        areps = []
        for i, a in enumerate(auds):
            sb = a.get('SegmentBase') or {}
            areps.append(
                '<Representation audioSamplingRate="44100" bandwidth="%s" codecs="%s" id="a%s_%d">'
                '<BaseURL>%s</BaseURL>'
                '<SegmentBase indexRange="%s"><Initialization range="%s"/></SegmentBase>'
                '</Representation>' % (
                    a.get('bandwidth'), a.get('codecs'), a.get('id'), i,
                    base(a.get('baseUrl', '')),
                    sb.get('indexRange', ''), sb.get('Initialization', '')))

        mpd = '\n'.join([
            '<?xml version="1.0" encoding="UTF-8"?>',
            '<MPD xmlns="urn:mpeg:dash:schema:mpd:2011" profiles="urn:mpeg:dash:profile:isoff-on-demand:2011" '
            'type="static" mediaPresentationDuration="PT%sS" minBufferTime="PT%sS">' % (dur, buf),
            '<Period>',
            '<AdaptationSet mimeType="video/mp4" startWithSAP="1" scanType="progressive" segmentAlignment="true">',
            '\n'.join(vreps),
            '</AdaptationSet>',
            '<AdaptationSet mimeType="audio/mp4" startWithSAP="1" segmentAlignment="true" lang="und">',
            '\n'.join(areps),
            '</AdaptationSet>',
            '</Period>',
            '</MPD>',
        ])
        return [200, 'application/dash+xml', mpd]

    def get_media(self, params):
        """转发 B站分片：Range 必须如实回传，否则 1080P 大文件会卡死/失败"""
        try:
            url = b64d(params['url'])
        except Exception:
            return [200, 'text/plain', 'bad url']
        headers = {'User-Agent': UA, 'Referer': REFERER}
        if self.cookie:
            headers['Cookie'] = self.cookie
        if params.get('range'):
            headers['Range'] = params['range']
        try:
            r = requests.get(url, headers=headers, timeout=30, stream=True)
            code = r.status_code
            body = r.content
            hdr = {}
            for k in ('Content-Range', 'Content-Type', 'Content-Length', 'Accept-Ranges'):
                v = r.headers.get(k)
                if v:
                    hdr[k] = v
            return [code, hdr.get('Content-Type', 'application/octet-stream'), body, hdr]
        except Exception as e:
            return [200, 'text/plain', 'media error: %s' % e]


if __name__ == '__main__':
    pass
