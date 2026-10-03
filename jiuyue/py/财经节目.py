# -*- coding: utf-8 -*-
# 🎧 财经电台聚合 · 喜马拉雅点播源
# ---------------------------------------------------------------------------
# 数据来源：喜马拉雅移动端公开接口（免签名、免登录、无防盗链）
#   列表  https://mobile.ximalaya.com/mobile/v1/album/track?albumId=<ID>&pageId=N&pageSize=30&device=android
#   详情  https://mobile.ximalaya.com/mobile/v1/track/{trackId}      → playUrl64 = mp3 直链
# 实测结论（2026-10-03）：
#   · 华飞看盘《每日大盘预判》专辑 3652352：7888 集、免费、日更；直链 http/https 均可取。
#   · 雪球《财经有深度》专辑 299146：3689 集、免费、日更；同为喜马拉雅官号，接口同源可用。
#   · 雪球其余节目（厚雪晚班车 / 六点半热评 / 厚雪长波 等）已在喜马拉雅开设官方专辑，
#     如需收录，只需把对应 albumId 追加进下方 ALBUMS 即可（见“雪球节目扩展”说明）。
#
# 时间窗口：本源默认【只展示最近 RECENT_DAYS 天内】的节目（含“后续更新”——
#   因为接口按时间倒序返回，新发布的节目天然落在窗口最前，刷新即见）。
#   想要“近10天”把 RECENT_DAYS 改成 10；也可在 ALBUMS 单项里写 'recent': 10 单独覆盖。
# ---------------------------------------------------------------------------
from base.spider import Spider
import json
import math
import time
import datetime

PAGE_SIZE = 30
RECENT_DAYS = 30        # ← 仅展示最近 N 天内的节目；改 10 即“近10天”
MAX_RECENT_PAGES = 60   # 时间窗口翻页安全上限（60*30=1800 集，远超日常所需）
UA = 'ting_6.6.99(Mozilla/5.0 (Linux; Android 11; SM-W2021 Build/RP1A.200720.012; wv) AppleWebKit/537.36)'
API_LIST = 'https://mobile.ximalaya.com/mobile/v1/album/track?albumId=%s&pageId=%s&pageSize=%s&device=android'
API_TRACK = 'https://mobile.ximalaya.com/mobile/v1/track/%s'

# 主播 / 节目清单：每个节目 = 一个喜马拉雅专辑（albumId）。
# type_id 直接用 albumId，categoryContent 据此拉取对应专辑的单集。
# 想加更多雪球节目：打开该专辑的喜马拉雅链接（.../album/<数字>），把数字填进 aid 即可。
# recent 留空则用全局 RECENT_DAYS；可单独写 'recent': 10 让该节目只显示近 10 天。
ALBUMS = [
    {'aid': '3652352', 'name': '📈 华飞看盘·每日大盘预判', 'host': '华飞看盘',
     'fallback': 7888},  # 原单专辑源，行为不变
    {'aid': '299146',  'name': '🌐 雪球·财经有深度',       'host': '雪球',
     'fallback': 3689},  # 雪球官号旗舰日更节目（实测 3689 集、免费）
    # ── 雪球节目扩展（按需取消注释 / 追加）──────────────────────────────
    # {'aid': 'XXXXXXX', 'name': '🌐 雪球·六点半热评', 'host': '雪球', 'fallback': 1492},
    # {'aid': 'XXXXXXX', 'name': '🌐 雪球·追基零距离', 'host': '雪球', 'fallback': 1510},
    # {'aid': 'XXXXXXX', 'name': '🌐 厚雪长波',         'host': '雪球', 'fallback': 124},
    # {'aid': 'XXXXXXX', 'name': '🌐 厚雪晚班车',       'host': '雪球', 'fallback': 141},
    # ──────────────────────────────────────────────────────────────────
]
_ALBUM_BY_ID = {a['aid']: a for a in ALBUMS}


def _pub_date(ms):
    """毫秒时间戳 → 本地（UTC+8）'MM-DD HH:MM'。"""
    try:
        dt = datetime.datetime.utcfromtimestamp(ms / 1000.0) + datetime.timedelta(hours=8)
        return dt.strftime('%m-%d %H:%M')
    except Exception:
        return ''


class Spider(Spider):

    def init(self, extend=''):
        self.headers = {
            'User-Agent': UA,
            'Referer': 'https://www.ximalaya.com/',
            'Accept': '*/*',
        }
        # trackId -> 主播名 映射，供详情页标注主播（TVBox 仅回传 trackId）
        self._track_host = {}
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

    def _vod(self, it, host=''):
        tid = str(it.get('trackId') or '')
        if tid:
            self._track_host[tid] = host or it.get('nickname') or ''
        ca = int(it.get('createdAt') or 0)
        date = _pub_date(ca) if ca else ''
        remark = self._fmt_remark(it)
        if date:
            remark = date + ' · ' + remark
        return {
            'vod_id': tid,
            'vod_name': it.get('title') or '',
            'vod_pic': (it.get('coverLarge') or it.get('coverMiddle') or '')
                       .split('!')[0] + '!op_type=3&columns=300&rows=300',
            'vod_remarks': remark,
        }

    def _album_page_raw(self, aid, pg):
        """拉取某专辑第 pg 页原始单集列表（接口按时间倒序：新→旧）。"""
        data = self._json(API_LIST % (aid, pg, PAGE_SIZE)) or {}
        return (data.get('data') or {}).get('list') or []

    def _gather_recent(self, aid, host, days=None):
        """聚合最近 days 天内的单集（默认取该节目 recent 或全局 RECENT_DAYS）。

        接口按时间倒序返回，逐页拉取，遇到整页都早于截止点的页即停止（其后更旧）。
        新发布的节目天然落在最前，刷新即进入窗口——即“后续更新”自动生效。
        """
        if days is None:
            days = _ALBUM_BY_ID.get(aid, {}).get('recent', RECENT_DAYS)
        cutoff = int(time.time() * 1000) - days * 86400 * 1000
        vods = []
        for pg in range(1, MAX_RECENT_PAGES + 1):
            lst = self._album_page_raw(aid, pg)
            if not lst:
                break
            any_new = False
            for it in lst:
                ca = int(it.get('createdAt') or 0)
                if ca and ca < cutoff:
                    continue
                any_new = True
                vods.append(self._vod(it, host))
            if not any_new:
                break
        return vods

    # ---------- 首页 ----------

    def homeContent(self, filter):
        return {
            'class': [
                {'type_id': a['aid'], 'type_name': a['name']} for a in ALBUMS
            ],
            'filters': {},
        }

    def homeVod(self):
        # 首页推荐：默认展示第一个节目的窗口内最新 12 集
        a = ALBUMS[0]
        vods = self._gather_recent(a['aid'], a['host'])
        return {'list': vods[:12]}

    # ---------- 分类（按节目/专辑浏览，窗口内切片分页）----------

    def categoryContent(self, tid, pg, filter, extend):
        try:
            pg = int(pg)
        except Exception:
            pg = 1
        if pg < 1:
            pg = 1
        aid = tid if tid in _ALBUM_BY_ID else ALBUMS[0]['aid']
        host = _ALBUM_BY_ID.get(aid, {}).get('host', '')
        vods = self._gather_recent(aid, host)
        total = len(vods)
        pagecount = max(1, int(math.ceil(total / float(PAGE_SIZE))))
        if pg > pagecount:
            pg = pagecount
        start = (pg - 1) * PAGE_SIZE
        return {
            'list': vods[start:start + PAGE_SIZE],
            'page': pg,
            'pagecount': pagecount,
            'limit': PAGE_SIZE,
            'total': total,
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
        datestr = _pub_date(created)
        host = self._track_host.get(track_id) \
            or j.get('nickname') \
            or (j.get('userInfo') or {}).get('nickname') \
            or ('雪球' if '雪球' in (j.get('albumTitle') or '') else '华飞看盘')
        content = '专辑：%s\n时长：%d:%02d\n播放：%s 次\n发布：%s\n主播：%s' % (
            j.get('albumTitle') or '', m, s, play, datestr, host)
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

    # ---------- 搜索（遍历全部节目，在窗口内做标题本地过滤）----------

    def searchContent(self, key, quick, pg='1'):
        lst = []
        for a in ALBUMS:
            for v in self._gather_recent(a['aid'], a['host']):
                if key and key in (v.get('vod_name') or ''):
                    lst.append(v)
        return {'list': lst, 'page': 1, 'pagecount': 1, 'limit': len(lst), 'total': len(lst)}
