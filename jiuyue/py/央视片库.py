# -*- coding: utf-8 -*-
# 央视网片库（点播 VOD）drpy 源
# 分类：电视剧 / 动画片 / 纪录片 / 特别节目
# 清晰度：高清(850=640x368) / 标清(450=480x272) / 流畅(450=480x272)
# 统一走明文旅，含 DRM 自检降级，保证不花屏
# 纯点播源，与 py/央视网.py（直播源）互不影响
import re
import datetime
import requests
from urllib3 import disable_warnings
from base.spider import Spider

disable_warnings()

API = 'https://api.cntv.cn'
UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36'


class Spider(Spider):

    CATS = [
        ('电视剧', 'CHAL1460955853485115'),
        ('动画片', 'CHAL1460955899450127'),
        ('纪录片', 'CHAL1460955924871139'),
        ('特别节目', 'CHAL1460955953877151'),
    ]

    TYPES = {
        '电视剧': '都市,农村,革命,古装,年代,现代,励志,军旅,历史,战争,言情,青春,家庭,乡村,谍战,犯罪,刑侦,武侠,商战,婚恋,爱情,生活,伦理,剧情,动作,悬疑,传奇,民国',
        '动画片': '亲子,校园,冒险,历史,益智,搞笑,青春,奇幻,动作,热血,神话,科幻,生活,社会,教育,励志,体育,经典,剧情,古代,宠物',
        '纪录片': '人文历史,探索,人物,社会,军事,科技',
        '特别节目': '文化,科教,社会,新闻,经济,青少,综艺,音乐,军事,戏曲,影视',
    }

    AREAS = ['内地（大陆）', '港澳台', '其他']

    # 播放线路（高 -> 低），目标码率 kbps
    # 实测结论（2026-09 逐档 SPS 复核）：
    #   1) plain 明文旅（qcloud / lxdns）服务端仅两档真实可用：
    #        450 -> 480x272（主单默认列出）
    #        850 -> 640x368（隐藏档，主单不列，可直拼取得）
    #      1200 / 2000 请求一律回落 480x272（与 450 同流），故明文天花板 640x368；
    #   2) enc / h5e 边缘节点（dhls / dh5）的 1200 / 2000 为真 1280x720，但整轨
    #      UDRM 加密——每帧内嵌 udrmGetLicense 授权 NAL，第三方播放器无授权
    #      → 图像花屏、声音正常。官方网页播放器靠 hls.js + WASM(vod.worker.js)
    #      实时软解，drpy 无此链路，故不可用。
    # 设计：只走 plain 明文旅；取流前校验首个分片（TS 同步字 + 无 udrm 标记），
    #       命中加密流或不可用即自动降档，确保任何线路都不花屏。
    LINES = ['高清', '标清', '流畅']
    TARGET = {'高清': 850, '标清': 450, '流畅': 450}
    PLAIN_HOST = 'hls.cntv.lxdns.com'
    DRM_MARK = b'udrm'

    def init(self, extend=''):
        self.session = requests.Session()
        self.session.verify = False
        self.session.headers.update({
            'User-Agent': UA,
            'Referer': 'https://tv.cctv.com/',
            'Accept': 'application/json, text/plain, */*',
        })
        self.header = {'User-Agent': UA, 'Referer': 'https://tv.cctv.com/'}

    def getName(self):
        return '央视片库'

    def isVideoFormat(self, url):
        return bool(url) and ('.m3u8' in url or '.mp4' in url)

    def manualVideoCheck(self):
        return False

    def destroy(self):
        try:
            self.session.close()
        except Exception:
            pass

    def _get_json(self, path, params):
        try:
            r = self.session.get(API + path, params=params, timeout=15)
            return r.json()
        except Exception as e:
            print('[央视片库] 请求失败 %s: %s' % (path, e))
            return {}

    def homeContent(self, filter):
        classes = [{'type_id': n, 'type_name': n} for n, _ in self.CATS]
        year = datetime.datetime.now().year
        filters = {}
        for name, _ in self.CATS:
            fl = []
            types = self.TYPES.get(name, '')
            if types:
                fl.append({'key': 'sc', 'name': '类型', 'value':
                           [{'n': '全部', 'v': ''}] + [{'n': t, 'v': t} for t in types.split(',')]})
            fl.append({'key': 'area', 'name': '地区', 'value':
                       [{'n': '全部', 'v': ''}] + [{'n': a, 'v': a} for a in self.AREAS]})
            fl.append({'key': 'year', 'name': '年代', 'value':
                       [{'n': '全部', 'v': ''}] + [{'n': str(y), 'v': str(y)} for y in range(year, 1999, -1)]})
            fl.append({'key': 'letter', 'name': '字母', 'value':
                       [{'n': '全部', 'v': ''}] + [{'n': chr(c), 'v': chr(c)} for c in range(65, 91)]})
            filters[name] = fl
        return {'class': classes, 'filters': filters}

    def homeVideoContent(self):
        return self.categoryContent('电视剧', 1, False, {})

    def categoryContent(self, tid, pg, filter, extend):
        extend = extend or {}
        ch = dict(self.CATS).get(tid, '')
        if not ch:
            return {'list': [], 'page': 1, 'pagecount': 1, 'limit': 24, 'total': 0}
        params = {
            'channelid': ch,
            'fc': tid,
            'sc': extend.get('sc') or '',
            'area': extend.get('area') or '',
            'year': extend.get('year') or '',
            'letter': extend.get('letter') or '',
            'p': str(pg),
            'n': '24',
            'topv': '1',
            'serviceId': 'tvcctv',
        }
        data = (self._get_json('/list/getVideoAlbumList', params) or {}).get('data') or {}
        total = int(data.get('total') or 0)
        vlist = []
        for it in data.get('list') or []:
            vid = (it.get('video') or {}).get('id') or it.get('id') or ''
            if not vid:
                continue
            cnt = str(it.get('count') or '')
            vlist.append({
                'vod_id': vid,
                'vod_name': Spider._album_name(it.get('title') or ''),
                'vod_pic': it.get('image2') or it.get('image') or '',
                'vod_remarks': (cnt + '集') if cnt.isdigit() else (it.get('sc') or ''),
                'vod_year': it.get('year') or '',
                'vod_area': it.get('area') or '',
                'vod_actor': it.get('actors') or '',
            })
        pagecount = max(1, (total + 23) // 24)
        return {'list': vlist, 'page': int(pg), 'pagecount': pagecount, 'limit': 24, 'total': total}

    def detailContent(self, ids):
        vid = ids[0]
        meta = self._album_info(vid)
        aid = meta.get('aid') or (vid if vid.startswith('VIDA') else '')
        eps = self._episodes(aid) if aid else []
        if not eps:
            guid = self._guid_from_page(vid)
            if guid:
                eps = [{'name': '正片', 'id': vid, 'guid': guid, 'raw': '', 'image': '', 'brief': '', 'time': ''}]
        if not eps:
            return {'list': []}
        first = eps[0]
        title = meta.get('title') or self._album_name(first.get('raw') or first.get('name') or '')
        play = '#'.join('%s$%s' % (e['name'], e['guid'] or e['id']) for e in eps)
        vod = {
            'vod_id': vid,
            'vod_name': title,
            'vod_pic': meta.get('image') or first.get('image') or '',
            'vod_content': meta.get('brief') or first.get('brief') or '',
            'vod_year': (first.get('time') or '')[:4],
            'vod_remarks': '%d集' % len(eps),
            'vod_play_from': '$$$'.join(self.LINES),
            'vod_play_url': '$$$'.join([play] * len(self.LINES)),
        }
        return {'list': [vod]}

    def playerContent(self, flag, id, vipFlags):
        guid = id or ''
        if not re.match(r'^[0-9a-fA-F]{32}$', guid):
            guid = self._guid_from_page(id)
        if not guid:
            return {'parse': 0, 'url': '', 'header': ''}
        info = self._video_info(guid)
        plain = info.get('hls') or ''
        want = self.TARGET.get(flag, 450)
        # 仅用 plain 明文旅；按目标档起逐级降档，取第一个通过明文校验的档
        for br in self._ladder(want):
            url = self._band_url(plain, guid, br)
            if url and self._usable(url):
                return {'parse': 0, 'url': url, 'header': self.header}
        # 兜底：plain 主单原样返回
        return {'parse': 0, 'url': plain, 'header': self.header}

    @staticmethod
    def _ladder(want):
        # plain 家族仅 450 / 850 两档真实存在；1200/2000 必回落 480x272，
        # 纳入降级链只会白跑两次请求并取到低质流，故排除。
        seq = []
        for b in (want, 850, 450):
            if b not in seq:
                seq.append(b)
        return seq

    # ---------- 内部方法 ----------

    def _album_info(self, vid):
        d = (self._get_json('/NewVideoset/getVideoAlbumInfoByVideoId',
                            {'id': vid, 'serviceId': 'tvcctv'}) or {}).get('data') or {}
        return {
            'aid': d.get('id') or '',
            'title': Spider._album_name(d.get('title') or ''),
            'image': d.get('image2') or d.get('image') or '',
            'brief': d.get('brief') or '',
        }

    def _episodes(self, aid):
        for mode in ('0', '1'):
            j = self._get_json('/NewVideo/getVideoListByAlbumIdNew', {
                'id': aid, 'serviceId': 'tvcctv', 'pub': '1',
                'mode': mode, 'part': '0', 'n': '1000', 'sort': 'asc',
            })
            items = ((j.get('data') or {}).get('list')) or []
            eps = []
            for i, it in enumerate(items, 1):
                g = it.get('guid') or ''
                if g:
                    eps.append({
                        'name': self._ep_name(it.get('title') or '') or ('第%d集' % i),
                        'id': it.get('id') or '',
                        'guid': g,
                        'raw': it.get('title') or '',
                        'image': it.get('image') or '',
                        'brief': it.get('brief') or '',
                        'time': it.get('time') or it.get('focus_date') or '',
                    })
            if eps:
                return eps
        return []

    @staticmethod
    def _ep_name(t):
        t = (t or '').strip()
        if '》' in t:
            t = t.split('》', 1)[1].strip()
        return t.strip()

    @staticmethod
    def _album_name(t):
        m = re.search(r'《([^》]+)》', t or '')
        if m:
            return m.group(1).strip()
        return (t or '').strip().strip('《》')

    @staticmethod
    def _video_page(vid):
        m = re.search(r'(\d{2})(\d{2})(\d{2})$', vid or '')
        if not m:
            return ''
        return 'https://tv.cctv.com/20%s/%s/%s/%s.shtml' % (m.group(1), m.group(2), m.group(3), vid)

    def _guid_from_page(self, vid):
        url = self._video_page(vid)
        if not url:
            return ''
        try:
            h = self.session.get(url, headers=self.header, timeout=15).text
            m = re.search(r'guid\s*=\s*["\']([0-9a-fA-F]{32})["\']', h)
            if m:
                return m.group(1)
        except Exception as e:
            print('[央视片库] 取guid失败: %s' % e)
        return ''

    def _video_info(self, guid):
        try:
            j = self.session.get('https://vdn.apps.cntv.cn/api/getHttpVideoInfo.do',
                                 params={'pid': guid}, timeout=15).json()
            if j.get('ack') == 'yes':
                man = j.get('manifest') or {}
                return {
                    'enc': man.get('hls_enc_url') or '',
                    'h5e': man.get('hls_h5e_url') or '',
                    'hls': j.get('hls_url') or '',
                }
        except Exception as e:
            print('[央视片库] 取流失败: %s' % e)
        return {}

    def _band_url(self, plain, guid, br):
        # 由 plain 主单反推同族分档 URL：
        #   /asp/hls/main/<path>/main.m3u8  ->  /asp/hls/<br>/<path>/<br>.m3u8
        if plain:
            m = re.match(r'^(https?://[^/]+/asp/hls/)main/(.+)/main\.m3u8', plain)
            if m:
                return '%s%d/%s/%d.m3u8' % (m.group(1), br, m.group(2), br)
        return 'https://%s/asp/hls/%d/0303000a/3/default/%s/%d.m3u8' % (
            self.PLAIN_HOST, br, guid, br)

    def _usable(self, band_url):
        # 取该档首个分片头部：须为 MPEG-TS（0x47 同步字）且不含 UDRM 授权标记
        # （udrm 标记 = 加密流，第三方播放必花屏，直接弃用）
        try:
            text = self.session.get(band_url, headers=self.header, timeout=10).text
            seg = next((l.strip() for l in text.splitlines() if l.strip() and not l.startswith('#')), '')
            if not seg:
                return False
            base = band_url[:band_url.rfind('/') + 1]
            u = seg if seg.startswith('http') else base + seg
            r = self.session.get(u, headers=self.header, timeout=10, stream=True)
            buf = next(r.iter_content(131072), b'')
            r.close()
            if not buf or buf[0] != 0x47:
                return False
            return self.DRM_MARK not in buf
        except Exception:
            return False
