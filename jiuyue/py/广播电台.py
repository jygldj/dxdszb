# -*- coding: utf-8 -*-
# 📻广播电台点播 - 直读 gbdt.txt(M3U) 转点播，不依赖 live2vod
# 源优先取线上 pages.dev（跟随 gbdt.txt 更新），不可达时用内嵌兜底副本，永不为空
import re
from base.spider import Spider

M3U_URL = "https://dxdszb.pages.dev/jiuyue/json/gbdt.txt"
UA = "okhttp/4.12.0"

FALLBACK_M3U = """#EXTM3U
#EXTINF:-1 ,CNR 中华之声
http://ngcdn001.cnr.cn/live/zhzs/index.m3u8
#EXTINF:-1 ,CNR 中国之声
http://ngcdn001.cnr.cn/live/zgzs/index.m3u8
#EXTINF:-1 ,CNR 中国交通广播
http://ngcdn001.cnr.cn/live/gsgljtgb/index.m3u8
#EXTINF:-1 ,CNR 神州之声
http://ngcdn001.cnr.cn/live/szzs/index.m3u8
#EXTINF:-1 ,CNR 经济之声
http://ngcdn002.cnr.cn/live/jjzs/index.m3u8
#EXTINF:-1 ,CNR 老年之声
http://ngcdn001.cnr.cn/live/lnzs/index.m3u8
#EXTINF:-1 ,CNR 音乐之声
http://ngcdn001.cnr.cn/live/yyzs/index.m3u8
#EXTINF:-1 ,北京新闻广播 FM100.6
http://ls.qingting.fm/live/339.m3u8
#EXTINF:-1 ,北京音乐广播 FM97.4
http://live.xmcdn.com/live/95/64.m3u8
#EXTINF:-1 ,北京音乐广播 FM97.4
http://ls.qingting.fm/live/332.m3u8
#EXTINF:-1 ,北京交通广播 FM103.9
http://live.xmcdn.com/live/93/64.m3u8
#EXTINF:-1 ,北京交通广播 FM103.9
http://ls.qingting.fm/live/336.m3u8
#EXTINF:-1 ,北京文艺广播 FM87.6
http://live.xmcdn.com/live/94/64.m3u8
#EXTINF:-1 ,重庆新闻广播 FM96.8
http://live.xmcdn.com/live/128/64.m3u8
#EXTINF:-1 ,重庆新闻广播 FM96.8
http://ls.qingting.fm/live/1498.m3u8
#EXTINF:-1 ,重庆经济广播 FM101.5
https://satellitepull.cnr.cn/live/wxcqjjgb/playlist.m3u8
#EXTINF:-1 ,重庆交通广播 FM95.5
http://live.xmcdn.com/live/130/64.m3u8
#EXTINF:-1 ,重庆交通广播 FM95.5
http://ls.qingting.fm/live/1500.m3u8
#EXTINF:-1 ,重庆音乐广播 FM88.1
http://live.xmcdn.com/live/131/64.m3u8
#EXTINF:-1 ,重庆音乐广播 FM88.1
http://ls.qingting.fm/live/647.m3u8
#EXTINF:-1 ,重庆都市广播 FM93.8
http://live.xmcdn.com/live/132/64.m3u8
#EXTINF:-1 ,甘肃新闻综合广播 FM96.1
https://satellitepull.cnr.cn/live/wxgsxwzhgb/playlist.m3u8
#EXTINF:-1 ,甘肃交通广播 FM103.5
http://ls.qingting.fm/live/3939.m3u8
#EXTINF:-1 ,甘肃交通广播 FM103.5
https://satellitepull.cnr.cn/live/wxgsjtgb/playlist.m3u8
"""


class Spider(Spider):

    def init(self, extend=''):
        self.groups = []   # 分组名有序表
        self.map = {}      # 分组名 -> [{name, urls:[...]}]
        self._load()
        return {}

    def _load(self):
        text = ''
        try:
            r = self.fetch(M3U_URL, headers={'User-Agent': UA})
            if r.status_code == 200 and '#EXTM3U' in r.text:
                text = r.text
        except Exception:
            pass
        if '#EXTM3U' not in text:
            text = FALLBACK_M3U
        raw = re.findall(r'#EXTINF:-?\d+\s*,\s*(.*?)\s*\r?\n\s*(https?://\S+)', text)
        order, data = [], {}
        for name, url in raw:
            name = name.strip()
            g = '央广CNR' if name.startswith('CNR') else name[:2]
            if g not in data:
                data[g] = []
                order.append(g)
            for ch in data[g]:
                if ch['name'] == name:
                    if url not in ch['urls']:
                        ch['urls'].append(url)
                    break
            else:
                data[g].append({'name': name, 'urls': [url]})
        self.map = data
        self.groups = order

    def _vod(self, g, ch):
        return {
            'vod_id': g + '@@' + ch['name'],
            'vod_name': ch['name'],
            'vod_pic': '',
            'vod_remarks': ('%d源' % len(ch['urls'])) if len(ch['urls']) > 1 else g,
        }

    def homeContent(self, filter):
        return {'class': [{'type_id': g, 'type_name': g} for g in self.groups], 'filters': {}}

    def homeVod(self):
        lst = []
        for g in self.groups:
            for ch in self.map[g]:
                lst.append(self._vod(g, ch))
        return {'list': lst[:12]}

    def categoryContent(self, tid, pg, filter, extend):
        lst = [self._vod(tid, ch) for ch in self.map.get(tid, [])]
        return {'list': lst, 'page': 1, 'pagecount': 1, 'limit': len(lst), 'total': len(lst)}

    def detailContent(self, ids):
        parts = ids[0].split('@@')
        if len(parts) != 2:
            return {'list': []}
        g, name = parts
        ch = None
        for c in self.map.get(g, []):
            if c['name'] == name:
                ch = c
                break
        if not ch:
            return {'list': []}
        play = '#'.join('线路%d$%s' % (i + 1, u) for i, u in enumerate(ch['urls']))
        return {'list': [{
            'vod_id': ids[0],
            'vod_name': name,
            'vod_content': '%s · %d 条可用源' % (g, len(ch['urls'])),
            'vod_play_from': '📻广播电台',
            'vod_play_url': play,
        }]}

    def playerContent(self, flag, id, vipFlags):
        return {'parse': 0, 'playUrl': '', 'url': id, 'header': {'User-Agent': UA}}

    def searchContent(self, key, quick, pg='1'):
        lst = []
        for g in self.groups:
            for ch in self.map[g]:
                if key in ch['name']:
                    lst.append(self._vod(g, ch))
        return {'list': lst, 'page': 1}
