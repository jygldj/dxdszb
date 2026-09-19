# -*- coding: utf-8 -*-
# @Function: 老王私人节目单（B站投稿·按BV号直取）
# @说明: 不依赖任何 jar 的 csp_Bili，自备 view/playurl 两接口，B站改规则只改本文件。
#   分类与节目写死在本文件 CATALOG 中，改节目只需改这一处。
#   免 cookie 可播（480P/360P）；在站点 ext 里给 cookie 可上 1080P。

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


class Spider(Spider):
    def getName(self):
        return '我的节目'

    def init(self, extend=''):
        self.bili = Bili()
        # 站点 ext 可传 {"cookie": "SESSDATA=..."} 提升清晰度
        try:
            if isinstance(extend, dict):
                ck = extend.get('cookie') or ''
                if ck:
                    self.bili.headers['Cookie'] = ck
        except Exception:
            pass

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
        for _t, _n, _items in CATALOG:
            for _title, _bvid in _items:
                self.cat_of[_bvid] = _n

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
        show = '正片'
        if dur:
            show = '%s[%d分%d秒]' % ('正片', dur // 60, dur % 60)
        vod = {
            'vod_id': bvid,
            'vod_name': info.get('title', bvid),
            'vod_pic': info.get('pic', ''),
            'type_name': type_name,
            'vod_year': info.get('year', ''),
            'vod_area': '',
            'vod_actor': info.get('owner', ''),
            'vod_director': '',
            'vod_remarks': info.get('remarks', ''),
            'vod_content': info.get('desc', ''),
            'vod_play_from': self.name,
            'vod_play_url': '%s$%s_%s' % (show, bvid, info.get('cid', '')),
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
        key = '%s_%s' % (bvid, cid)
        dash = self.dash_cache.get(key)
        if not dash:
            try:
                url = ('https://api.bilibili.com/x/player/playurl?bvid=%s&cid=%s'
                       '&qn=120&fnval=4048&fnver=0&fourk=1' % (bvid, cid))
                js = requests.get(url, headers=self.headers, timeout=8).json()
                data = js.get('data') or {}
                # 注意：B站新版 playurl 已不再返回 type 字段，只要有 dash 就按 DASH 处理
                if data.get('dash') and data['dash'].get('video'):
                    dash = data['dash']
                    self.dash_cache[key] = dash
                elif data.get('durl'):
                    return [302, 'text/plain', None, {'Location': data['durl'][0]['url']}]
                else:
                    return [200, 'text/plain', 'playurl failed: code=%s msg=%s' % (js.get('code'), js.get('message'))]
            except Exception as e:
                return [200, 'text/plain', 'playurl error: %s' % e]

        dur = dash.get('duration', 0)
        buf = dash.get('minBufferTime', 1.5)

        def base(u):
            return ('%s&type=media&url=' % self.get_proxy_url).replace('&', '&amp;') + \
                   base64.b64encode(u.encode()).decode()

        vids = []
        for v in dash.get('video', []):
            sb = v.get('SegmentBase') or {}
            vids.append(
                '<Representation bandwidth="%s" codecs="%s" frameRate="%s" height="%s" id="%s" width="%s">'
                '<BaseURL>%s</BaseURL>'
                '<SegmentBase indexRange="%s"><Initialization range="%s"/></SegmentBase>'
                '</Representation>' % (
                    v.get('bandwidth'), v.get('codecs'), v.get('frameRate'),
                    v.get('height'), v.get('id'), v.get('width'),
                    base(v.get('baseUrl', '')),
                    sb.get('indexRange', ''), sb.get('Initialization', '')))

        auds = []
        for a in dash.get('audio', []):
            sb = a.get('SegmentBase') or {}
            auds.append(
                '<Representation audioSamplingRate="44100" bandwidth="%s" codecs="%s" id="%s">'
                '<BaseURL>%s</BaseURL>'
                '<SegmentBase indexRange="%s"><Initialization range="%s"/></SegmentBase>'
                '</Representation>' % (
                    a.get('bandwidth'), a.get('codecs'), a.get('id'),
                    base(a.get('baseUrl', '')),
                    sb.get('indexRange', ''), sb.get('Initialization', '')))

        mpd = '\n'.join([
            '<?xml version="1.0" encoding="UTF-8"?>',
            '<MPD xmlns="urn:mpeg:dash:schema:mpd:2011" profiles="urn:mpeg:dash:profile:isoff-on-demand:2011" '
            'type="static" mediaPresentationDuration="PT%sS" minBufferTime="PT%sS">' % (dur, buf),
            '<Period>',
            '<AdaptationSet mimeType="video/mp4" startWithSAP="1" scanType="progressive" segmentAlignment="true">',
            '\n'.join(vids),
            '</AdaptationSet>',
            '<AdaptationSet mimeType="audio/mp4" startWithSAP="1" segmentAlignment="true" lang="und">',
            '\n'.join(auds),
            '</AdaptationSet>',
            '</Period>',
            '</MPD>',
        ])
        return [200, 'application/dash+xml', mpd]

    def get_media(self, params):
        try:
            url = base64.b64decode(params['url'].encode()).decode()
        except Exception:
            return [200, 'text/plain', 'bad url']
        headers = {'User-Agent': UA, 'Referer': REFERER}
        if params.get('range'):
            headers['Range'] = params['range']
        try:
            r = requests.get(url, headers=headers, timeout=15, stream=True)
            return [206, 'application/octet-stream', r.content]
        except Exception as e:
            return [200, 'text/plain', 'media error: %s' % e]


if __name__ == '__main__':
    pass
