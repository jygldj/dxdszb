# -*- coding: utf-8 -*-
# 🎧 华飞看盘 · 喜马拉雅《每日大盘预判》点播源
# ---------------------------------------------------------------------------
# 数据来源：喜马拉雅移动端公开接口（免签名、免登录、无防盗链）
#   列表  https://mobile.ximalaya.com/mobile/v1/album/track?albumId=3652352&pageId=N&pageSize=30&device=android
#   详情  https://mobile.ximalaya.com/mobile/v1/track/{trackId}      → playUrl64 = mp3 直链
# 实测结论（2026-10-01）：
#   该专辑 7888 集、免费、日更（早/午/盘三更）；直链 http/https 均可取，无需 Referer。
#   该主播其余 11 个专辑均为 XIMI 会员专属，接口 isPaid=true 且不返回播放地址，故不收录。
# ---------------------------------------------------------------------------
from base.spider import Spider
import json
import math

ALBUM_ID = '3652352'
PAGE_SIZE = 30
TOTAL = 7888
UA = 'ting_6.6.99(Mozilla/5.0 (Linux; Android 11; SM-W2021 Build/RP1A.200720.012; wv) AppleWebKit/537.36)'
API_LIST = 'https://mobile.ximalaya.com/mobile/v1/album/track?albumId=%s&pageId=%s&pageSize=%s&device=android'
API_TRACK = 'https://mobile.ximalaya.com/mobile/v1/track/%s'

TOTAL_PAGES = int(math.ceil(TOTAL / float(PAGE_SIZE)))


class Spider(Spider):

    def init(self, extend=''):
        self.headers = {
            'User-Agent': UA,
            'Referer': 'https://www.ximalaya.com/',
            'Accept': '*/*',
        }
        return {}

    # ---------- 基础 ----------

    def _json(self, url):
        try:
            r = self.fetch(url, headers=self.headers)
            if r is not None and r.status_code == 200:
                return json.loads(r.text)
        except Exception:
            pass
        return None

    def _fmt_remark(self, it):
        dur = int(it.get('duration') or 0)
        m, s = divmod(dur, 60)
        play = int(it.get('playtimes') or 0)
        ptxt = ('%.1f万' % (play / 10000.0)) if play >= 10000 else str(play)
        return '%d:%02d · %s次' % (m, s, ptxt)

    def _vod(self, it):
        return {
            'vod_id': str(it.get('trackId') or ''),
            'vod_name': it.get('title') or '',
            'vod_pic': (it.get('coverLarge') or it.get('coverMiddle') or '')
                       .split('!')[0] + '!op_type=3&columns=300&rows=300',
            'vod_remarks': self._fmt_remark(it),
        }

    # ---------- 首页 ----------

    def homeContent(self, filter):
        return {
            'class': [
                {'type_id': 'latest', 'type_name': '🆕 最新更新'},
                {'type_id': 'archive', 'type_name': '📚 早期归档'},
            ],
            'filters': {},
        }

    def homeVod(self):
        data = self._json(API_LIST % (ALBUM_ID, 1, 12))
        lst = ((data or {}).get('data') or {}).get('list') or []
        return {'list': [self._vod(x) for x in lst]}

    # ---------- 分类 ----------

    def categoryContent(self, tid, pg, filter, extend):
        try:
            pg = int(pg)
        except Exception:
            pg = 1
        # archive：从最早期一页起正序回放
        api_page = pg if tid != 'archive' else max(1, TOTAL_PAGES - pg + 1)
        data = self._json(API_LIST % (ALBUM_ID, api_page, PAGE_SIZE))
        lst = ((data or {}).get('data') or {}).get('list') or []
        vods = [self._vod(x) for x in lst]
        return {
            'list': vods,
            'page': pg,
            'pagecount': TOTAL_PAGES,
            'limit': PAGE_SIZE,
            'total': TOTAL,
        }

    # ---------- 详情 ----------

    def detailContent(self, ids):
        track_id = str(ids[0])
        j = self._json(API_TRACK % track_id)
        if not j:
            return {'list': []}
        dur = int(j.get('duration') or 0)
        m, s = divmod(dur, 60)
        play = int(j.get('playtimes') or 0)
        created = j.get('createdAt') or 0
        import datetime
        try:
            dt = datetime.datetime.utcfromtimestamp(created / 1000.0) + datetime.timedelta(hours=8)
            datestr = dt.strftime('%Y-%m-%d %H:%M')
        except Exception:
            datestr = ''
        content = '专辑：%s\n时长：%d:%02d\n播放：%s 次\n发布：%s\n主播：%s' % (
            j.get('albumTitle') or '', m, s, play, datestr, j.get('nickname') or '华飞看盘')
        return {'list': [{
            'vod_id': track_id,
            'vod_name': j.get('title') or '',
            'vod_pic': (j.get('coverLarge') or '').split('!')[0] + '!op_type=3&columns=300&rows=300',
            'vod_content': content,
            'vod_play_from': '🎧喜马拉雅',
            'vod_play_url': '%s$%s' % (j.get('title') or track_id, track_id),
        }]}

    # ---------- 播放 ----------

    def playerContent(self, flag, id, vipFlags):
        j = self._json(API_TRACK % str(id))
        url = ''
        if j:
            url = j.get('playUrl64') or j.get('playPathAacv224') or j.get('playUrl32') or ''
            url = url.replace('http://', 'https://')
        return {
            'parse': 0,
            'playUrl': '',
            'url': url,
            'header': {'User-Agent': UA},
        }

    # ---------- 搜索（喜马拉雅无公开搜索接口，改为近期节目本地过滤）----------

    def searchContent(self, key, quick, pg='1'):
        lst = []
        for i in range(1, 11):
            data = self._json(API_LIST % (ALBUM_ID, i, PAGE_SIZE))
            if not data:
                break
            for it in ((data.get('data') or {}).get('list') or []):
                if key and key in (it.get('title') or ''):
                    lst.append(self._vod(it))
        return {'list': lst, 'page': 1, 'pagecount': 1, 'limit': len(lst), 'total': len(lst)}
