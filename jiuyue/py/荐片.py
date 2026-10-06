# -*- coding: utf-8 -*-
"""
荐片（com.jp.app）· OK影视 py 源 —— 维护手册
================================================================
数据来源：荐片 APP 接口。域名见 JP_API_BASE，可在 api.json 站点 ext.api 覆盖。
参照实现：shiguang/open/jianpian_open.js（js 版，逻辑与本文件等价，可交叉验证）。


【一、接口与字段】——API 变更时按图索骥
------------------------------------------------------------
  分类      GET /api/v2/settings/homeCategory
            -> data: [{id, name, pid}]
               实测：88=首页  99=Netflix  1=电影  2=电视剧  67=短剧
                     3=动漫   4=综艺     50=纪录片

  首页推荐  GET /api/slide/list?pos_id=88
            -> data: [{jump_id, title, thumbnail}]      jump_id 即 vod_id

  分类列表  GET /api/crumb/list?fcate_pid=<tid>&category_id=&area=&year=
                               &type=&sort=&page=<pg>
            -> data: [{id, title, path(封面), mask(角标)}]
               注：7 个参数必须齐全（空值也要传），缺参接口易返回 500

  Netflix   GET /api/dyTag/tpl2_data?id=70&page=<pg>       （tid=99 专用）
            -> data: [{id, title, path, mask}]

  详情      GET /api/video/detailv2?id=<vod_id>
            -> data.title / thumbnail / year / area / mask / description
            -> data.source_list_source: [{name(线路名),
                   source_list: [{source_name, url}, ...]}]

  搜索      GET /api/v2/search/videoV2?key=<kw>&category_id=88
                                      &page=<pg>&pageSize=20
            -> data: [{id, title, thumbnail, mask}]

  图片域名  GET /api/v2/settings/resourceDomainConfig
            -> data.imgDomain（逗号分隔多域名，取第一个）
               关键：接口返回的图片是相对路径（/upload/...），必须拼 imghost
                     前缀，否则列表/详情全无封面。


【二、播放地址组装】
------------------------------------------------------------
  一条线路 = source_list_source[i]
     其 source_list 内每项 {source_name, url} -> 拼成 "集名$url"
     同一线路多集用 '#' 连接；线路之间用 '$$$' 分隔
  播放：url 含 .m3u8/.mp4 -> 直连；其余 -> 加 'tvbox-xg:' 前缀走边下边播


【三、2026-10-05 踩坑实录】——改代码前务必先读，避免重蹈覆辙
------------------------------------------------------------
  坑1 网络栈用错（最致命，整站无数据）
      本源是【视频源】，必须走 requests（与 道玄央视网.py 同款）。
      曾误按音频源 财经节目.py 的 self.fetch() 改写，真机全站空白。
      => self.fetch() 仅作"无 requests 时"的兜底，不是主通道。

  坑2 分类被硬编码误删
      id=88 名为「首页」、id=99 名为「Netflix」。
      曾写死 `if tid in ('88','99'): continue`，导致 py 版比 js 版少一个
      Netflix。正确做法：分类按【名称】剔除（JP_CATE_REMOVE）；
      88/99 只"不挂筛选器"，分类本身照常展示。

  坑3 入参类型陷阱（最隐蔽 —— 异常被外层 try 吞掉，界面只显示"找不到数据"）
      · pg 可能是字符串 '1'，执行 '1' <= 0 抛 TypeError
      · ext 可能是 JSON 字符串，执行 ext.get() 抛 AttributeError
      => categoryContent / searchContent 入口一律先做类型归一化。

  坑4 playerContent 收到的是【纯 URL】
      框架传入的是 vod_play_url 中 '$' 之后的部分，本身不含 '$'。
      曾按 "标题$url" split，len(parts)<2 直接返回空 url
      -> 真机"播放地址加载失败"。现兼容两种形态。

  坑5 线路去重不能按域名
      同一域名下多条线路：有的地址完全相同（真马甲，可安全合并），
      有的地址与集数均不同（真实独立源，砍掉即误杀）。
      实证：cqhnq 的「VIP/极速蓝光/高速蓝光」地址一致；
            而其「蓝光线路」地址不同(dsdy_...)、集数 1 vs 2。
      => 按【播放地址签名】去重，只砍真重复。


【四、常用调整】
------------------------------------------------------------
  线路条数上限  JP_MAX_LINES   0=不截断（当前采用，能播优先）；8/12=收敛
  线路优先顺序  JP_LINE_ORDER  按线路名关键词排序
                               （注意：无法预判真实流畅度，仅供排序参考）
  过滤黑名单    JP_LINE_REMOVE(线路) / JP_TITLE_REMOVE(片名) / JP_CATE_REMOVE(分类)
  换 API 域名   api.json 站点 ext.api  或  本文件 JP_API_BASE
"""

import json
import re
import sys
import urllib.parse

sys.path.append('..')
from base.spider import Spider

# ---------------------------------------------------------------------------
# 网络层：本源是【视频源】，主通道走 requests（与 道玄央视网.py 同款，实测可播）。
#   曾误用音频源 财经节目.py 的 self.fetch()，导致真机全站无数据（见坑1）。
#   self.fetch() 在此仅作兜底：万一真机 py 引擎无 requests，也不会因导入失败整站崩溃。
# ---------------------------------------------------------------------------
try:
    import requests
    from urllib3 import disable_warnings

    disable_warnings()
    HAS_REQUESTS = True
except Exception:
    requests = None
    HAS_REQUESTS = False

# ============================ 配置区 ============================
JP_API_BASE = 'https://api.ztcgi.com'

JP_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36',
    'Referer': 'https://www.jianpianapp.com',
    'Accept': 'application/json',
}

JP_TIMEOUT = 15

# 线路排序优先级：仅按"线路名关键词"排，与实际流畅度无必然关联（见手册第四节）
JP_LINE_ORDER = ['蓝光', 'ft', '官', 'ace', '1080p', 'dytt']
# 线路排除关键词
JP_LINE_REMOVE = ['广告', '666', 'mymv']
# 每个节目最多保留的线路数（在按地址去重之后生效）。
#   去重后剩下的都是真实独立源，静态排序无法预判优劣，砍得越狠、漏掉流畅源概率越高；
#   OK影视 的自动换源/快速跳转会依次遍历，故保留越多容错越高。
#   取值：0 或负数 = 不截断（当前采用，约 17~22 条，能播/流畅第一）；8=更清爽；12=平衡。
JP_MAX_LINES = 0
# 片名排除关键词
JP_TITLE_REMOVE = ['广告', '破解', '群']
# 分类排除关键词（按名称剔除。id=88「首页」由此规则去掉；id=99「Netflix」不受影响）
JP_CATE_REMOVE = ['推荐', '首页']
# ================================================================


def _safe_json_parse(s, default=None):
    """安全解析 JSON：用于把可能是 JSON 字符串的 ext 转成 dict（见坑3）。"""
    if default is None:
        default = {}
    if not s or isinstance(s, dict):
        return s or default
    try:
        return json.loads(s)
    except Exception:
        return default


def _extract_domain(url):
    """从播放地址提取短域名，用于线路名后缀（如「极速蓝光(cqhnq)」），仅作显示用。"""
    if not url:
        return ''
    clean = url.replace('https://', '').replace('http://', '')
    domain = clean.split('/')[0]
    if '-' in domain:
        return domain.split('-')[0]
    if '.' in domain:
        parts = domain.split('.')
        if len(parts) > 2:
            return parts[-2]
        elif len(parts) == 2:
            return parts[0]
    return domain


class Spider(Spider):
    def getName(self):
        return '荐片'

    def init(self, extend=''):
        self.index = {}          # vod_id -> 原始数据缓存（当前只写不读，供调试/扩展）
        self.imghost = ''
        self.api_base = JP_API_BASE

        # api.json 站点的 ext 可覆盖 API 域名（ext 可能是 dict，也可能是 JSON 字符串）
        if isinstance(extend, str):
            extend = _safe_json_parse(extend, {})
        if isinstance(extend, dict):
            custom_api = (extend.get('api') or extend.get('host') or '').strip().rstrip('/')
            if custom_api:
                self.api_base = custom_api

        # 视频源标准会话（verify=False 兼容自签/CDN 边缘节点）
        self.session = None
        if HAS_REQUESTS:
            try:
                self.session = requests.Session()
                self.session.verify = False
                self.session.headers.update(JP_HEADERS)
            except Exception:
                self.session = None

        # 取图片域名：接口返回相对路径，无前缀则封面全空
        # （与 js 版一致；取不到时退回历史兜底域名）
        try:
            conf = self._get('/api/v2/settings/resourceDomainConfig')
            domains = ((conf or {}).get('data') or {}).get('imgDomain') or ''
            if domains:
                arr = [d.strip() for d in domains.split(',') if d.strip()]
                if arr:
                    self.imghost = 'https://' + arr[0]
        except Exception:
            self.imghost = ''
        if not self.imghost:
            self.imghost = 'https://img.jgsfnl.com'
        return {}

    def isVideoFormat(self, url):
        """视频源标识（对齐 道玄央视网.py）。"""
        return bool(url) and ('.m3u8' in url or '.mp4' in url)

    def manualVideoCheck(self):
        return False

    def _pic(self, path):
        """相对图片路径 -> 绝对地址（js 版用 imghost 拼接）。"""
        p = path or ''
        if not p:
            return ''
        if p.startswith('http'):
            return p
        return (self.imghost or '') + p

    # ---------------- 请求工具 ----------------
    def _get(self, path, params=None):
        """GET 一个接口并解析 JSON。

        通道 1（主）：requests.Session —— 视频源标准写法（见坑1）
        通道 2（备）：self.fetch —— 仅当环境无 requests 时兜底
        """
        if params is None:
            params = {}
        url = f'{self.api_base}{path}'
        if params:
            url += '?' + urllib.parse.urlencode(params)

        # ---- 通道 1：requests ----
        if self.session is not None:
            try:
                r = self.session.get(url, headers=JP_HEADERS, timeout=JP_TIMEOUT)
                if r is not None and r.status_code == 200:
                    txt = (r.text or '').strip()
                    if txt.startswith('{') or txt.startswith('['):
                        return json.loads(txt)
            except Exception:
                pass

        # ---- 通道 2：框架 fetch 兜底 ----
        try:
            r = self.fetch(url, headers=JP_HEADERS)
            if r is not None and getattr(r, 'status_code', None) == 200:
                txt = r.text
                if isinstance(txt, (bytes, bytearray)):
                    txt = txt.decode('utf-8', 'ignore')
                txt = (txt or '').strip()
                if txt.startswith('{') or txt.startswith('['):
                    return json.loads(txt)
        except Exception:
            pass
        return None

    # ---------------- 首页分类 ----------------
    def homeContent(self, filter):
        try:
            js = self._get('/api/v2/settings/homeCategory')
            if not js or not js.get('data'):
                return {'class': [], 'filters': {}}

            data = js['data']
            if not isinstance(data, list):
                return {'class': [], 'filters': {}}

            classes = []
            for item in data:
                if not item or not item.get('id'):
                    continue
                tid = str(item['id'])
                name = item.get('name', '')

                # 按【名称】剔除（见坑2）：id=88「首页」在此被去掉，
                # id=99「Netflix」必须保留——切勿改成按 id 硬编码排除。
                if any(word.lower() in name.lower() for word in JP_CATE_REMOVE):
                    continue

                classes.append({'type_id': tid, 'type_name': name})

            # 通用筛选（分类/地区/年代/排序），各分类共用
            common_filter = [
                {"key": "cateId", "name": "分类", "value": [
                    {"v": "", "n": "全部"}, {"v": "1", "n": "剧情"}, {"v": "2", "n": "爱情"},
                    {"v": "3", "n": "动画"}, {"v": "4", "n": "喜剧"}, {"v": "5", "n": "战争"},
                    {"v": "6", "n": "歌舞"}, {"v": "7", "n": "古装"}, {"v": "8", "n": "奇幻"},
                    {"v": "9", "n": "冒险"}, {"v": "10", "n": "动作"}, {"v": "11", "n": "科幻"},
                    {"v": "12", "n": "悬疑"}, {"v": "13", "n": "犯罪"}, {"v": "14", "n": "家庭"},
                    {"v": "15", "n": "传记"}, {"v": "16", "n": "运动"}, {"v": "18", "n": "惊悚"},
                    {"v": "20", "n": "短片"}, {"v": "21", "n": "历史"}, {"v": "22", "n": "音乐"},
                    {"v": "23", "n": "西部"}, {"v": "24", "n": "武侠"}, {"v": "25", "n": "恐怖"}
                ]},
                {"key": "area", "name": "地区", "value": [
                    {"v": "", "n": "全部"}, {"v": "1", "n": "国产"}, {"v": "3", "n": "中国香港"},
                    {"v": "6", "n": "中国台湾"}, {"v": "5", "n": "美国"}, {"v": "18", "n": "韩国"},
                    {"v": "2", "n": "日本"}
                ]},
                {"key": "year", "name": "年代", "value": [
                    {"v": "", "n": "全部"}, {"v": "162", "n": "2026"}, {"v": "107", "n": "2025"},
                    {"v": "119", "n": "2024"}, {"v": "153", "n": "2023"}, {"v": "101", "n": "2022"},
                    {"v": "118", "n": "2021"}, {"v": "16", "n": "2020"}, {"v": "7", "n": "2019"},
                    {"v": "2", "n": "2018"}, {"v": "3", "n": "2017"}, {"v": "22", "n": "2016"},
                    {"v": "2015", "n": "2015以前"}
                ]},
                {"key": "sort", "name": "排序", "value": [
                    {"v": "update", "n": "最新"}, {"v": "hot", "n": "最热"}, {"v": "rating", "n": "评分"}
                ]},
            ]

            filter_obj = {}
            for c in classes:
                # 与 js 版一致：88(首页)/99(Netflix) 不挂筛选器，但分类本身照常展示
                if c['type_id'] in ('88', '99'):
                    continue
                filter_obj[c['type_id']] = common_filter

            return {'class': classes, 'filters': filter_obj}
        except Exception:
            return {'class': [], 'filters': {}}

    def homeVideoContent(self):
        try:
            js = self._get('/api/slide/list', {'pos_id': '88'})
            if not js or not js.get('data'):
                return {'list': []}

            data = js['data']
            if not isinstance(data, list):
                return {'list': []}

            vlist = []
            for item in data:
                if not item or not item.get('jump_id'):
                    continue
                vid = str(item['jump_id'])
                name = item.get('title', '')

                if any(word.lower() in name.lower() for word in JP_TITLE_REMOVE):
                    continue

                vlist.append({
                    'vod_id': vid,
                    'vod_name': name,
                    'vod_pic': self._pic(item.get('thumbnail', '')),
                    'vod_remarks': '',
                })
                self.index[vid] = item

            return {'list': vlist}
        except Exception:
            return {'list': []}

    # ---------------- 分类列表 ----------------
    def categoryContent(self, tid, page, filter, ext):
        try:
            # ---- 入参归一化（见坑3：类型不定，异常会被吞成"找不到数据"）----
            # pg 可能是字符串 '1'，'1' <= 0 会抛 TypeError
            try:
                page = int(page)
            except Exception:
                page = 1
            if page <= 0:
                page = 1

            # ext 可能是 JSON 字符串，ext.get() 会抛 AttributeError
            if isinstance(ext, str):
                ext = _safe_json_parse(ext, {})
            if not isinstance(ext, dict):
                ext = {}

            # tid=99 是 Netflix，走推荐接口（与 js 版 DyTag(70) 一致）
            if str(tid) == '99':
                return self._dy_tag(70, page)

            # 7 个参数必须齐全（空值也要传），缺参接口易返回 500
            params = {
                'fcate_pid': tid,
                'category_id': '',
                'area': '',
                'year': '',
                'type': '',
                'sort': '',
                'page': page,
            }

            if ext:
                if ext.get('area'):
                    params['area'] = ext['area']
                if ext.get('year'):
                    params['year'] = ext['year']
                if ext.get('cateId'):
                    params['type'] = ext['cateId']
                if ext.get('sort'):
                    params['sort'] = ext['sort']

            js = self._get('/api/crumb/list', params)
            if not js or not js.get('data'):
                return {'list': [], 'page': page, 'pagecount': 1, 'limit': 0, 'total': 0}

            data = js['data']
            if not isinstance(data, list):
                return {'list': [], 'page': page, 'pagecount': 1, 'limit': 0, 'total': 0}

            vlist = []
            for item in data:
                if not item or not item.get('id'):
                    continue
                vid = str(item['id'])
                name = item.get('title', '')

                if any(word.lower() in name.lower() for word in JP_TITLE_REMOVE):
                    continue

                vlist.append({
                    'vod_id': vid,
                    'vod_name': name,
                    'vod_pic': self._pic(item.get('path', '')),
                    'vod_remarks': item.get('mask', ''),
                })
                self.index[vid] = item

            return {
                'list': vlist,
                'page': page,
                'pagecount': 99999,
                'limit': len(vlist),
                'total': 99999,
            }
        except Exception:
            return {'list': [], 'page': page, 'pagecount': 1, 'limit': 0, 'total': 0}

    def _dy_tag(self, tag_id, page):
        """推荐/专题接口，tid=99（Netflix）专用；js 版对应 DyTag(70)。"""
        try:
            js = self._get('/api/dyTag/tpl2_data', {'id': tag_id, 'page': page})
            if not js or not js.get('data'):
                return {'list': [], 'page': page, 'pagecount': 1, 'limit': 0, 'total': 0}

            data = js['data']
            if not isinstance(data, list):
                return {'list': [], 'page': page, 'pagecount': 1, 'limit': 0, 'total': 0}

            vlist = []
            for item in data:
                if not item:
                    continue
                vid = str(item.get('id', ''))
                if not vid:
                    continue
                name = item.get('title', '')

                if any(word.lower() in name.lower() for word in JP_TITLE_REMOVE):
                    continue

                vlist.append({
                    'vod_id': vid,
                    'vod_name': name,
                    'vod_pic': self._pic(item.get('path', '')),
                    'vod_remarks': item.get('mask', ''),
                })
                self.index[vid] = item

            return {
                'list': vlist,
                'page': page,
                'pagecount': 99999,
                'limit': len(vlist),
                'total': 99999,
            }
        except Exception:
            return {'list': [], 'page': page, 'pagecount': 1, 'limit': 0, 'total': 0}

    # ---------------- 详情 ----------------
    def detailContent(self, ids):
        vid = ids[0]
        try:
            js = self._get('/api/video/detailv2', {'id': vid})
            if not js or not js.get('data'):
                return {'list': []}

            v = js['data']
            self.index[vid] = v

            vod = {
                'vod_id': vid,
                'vod_name': v.get('title', ''),
                'vod_pic': self._pic(v.get('thumbnail', '')),
                'type_name': '',
                'vod_year': v.get('year', ''),
                'vod_area': v.get('area', ''),
                'vod_remarks': v.get('mask', ''),
                'vod_content': v.get('description', ''),
            }

            # ---- 收集播放线路：source_list_source -> [(线路名, "集名$url#...")] ----
            play_form = []
            play_urls = []

            source_list = v.get('source_list_source')
            if source_list and isinstance(source_list, list):
                for item in source_list:
                    if not item:
                        continue

                    form_name = item.get('name', '未知线路')
                    domain = ''

                    # 线路名加域名后缀便于辨识（仅显示用，不参与判断）
                    source_list_data = item.get('source_list')
                    if source_list_data and isinstance(source_list_data, list) and source_list_data:
                        first_url = source_list_data[0].get('url', '')
                        domain = _extract_domain(first_url)
                        if len(domain) > 8:
                            domain = domain[:8]
                        if domain:
                            form_name = f"{form_name}({domain})"

                    if any(p.lower() in form_name.lower() for p in JP_LINE_REMOVE):
                        continue

                    urls = []
                    if source_list_data and isinstance(source_list_data, list):
                        for src in source_list_data:
                            if src and src.get('source_name') and src.get('url'):
                                urls.append(f"{src['source_name']}${src['url']}")

                    if urls:
                        play_form.append(form_name)
                        play_urls.append('#'.join(urls))

            # 按线路名关键词排优先级（JP_LINE_ORDER）
            combined = list(zip(play_form, play_urls))
            combined.sort(key=lambda x: (
                JP_LINE_ORDER.index(next((k for k in JP_LINE_ORDER if k.lower() in x[0].lower()), 'z'))
                if any(k.lower() in x[0].lower() for k in JP_LINE_ORDER) else 999
            ))

            # 去重：按【播放地址签名】，不是按域名（见坑5）。
            #   地址完全相同的（真马甲）合并；地址/集数不同的（真实独立源）保留。
            seen_sig = set()
            deduped = []
            for form, urls in combined:
                sig = '|'.join(u.split('$', 1)[-1] for u in urls.split('#'))
                if sig in seen_sig:
                    continue
                seen_sig.add(sig)
                deduped.append((form, urls))

            # 限量（0 = 不截断，当前采用）
            if JP_MAX_LINES > 0:
                deduped = deduped[:JP_MAX_LINES]

            if deduped:
                play_form, play_urls = zip(*deduped)
                # 与 js 版一致：常规线路改称边下边播
                play_form = [f.replace('常规线路', '边下边播') for f in play_form]
                vod['vod_play_from'] = '$$$'.join(play_form)
                vod['vod_play_url'] = '$$$'.join(play_urls)
            else:
                vod['vod_play_from'] = ''
                vod['vod_play_url'] = ''

            return {'list': [vod]}
        except Exception:
            return {'list': []}

    # ---------------- 搜索 ----------------
    def searchContent(self, key, quick, page='1'):
        if not key:
            return {'list': [], 'page': page, 'pagecount': 1, 'limit': 0, 'total': 0}

        try:
            # page 可能传字符串/None，先归一化（同坑3）
            try:
                page = int(page)
            except Exception:
                page = 1
            max_pages = 5
            results = []
            for p in range(page, page + max_pages):
                js = self._get('/api/v2/search/videoV2', {
                    'key': key,
                    'category_id': '88',
                    'page': p,
                    'pageSize': '20'
                })
                if js and js.get('data'):
                    data = js['data']
                    if isinstance(data, list):
                        for item in data:
                            if not item or not item.get('id'):
                                continue
                            vid = str(item['id'])
                            name = item.get('title', '')

                            # 接口返回含模糊匹配结果，再按关键词精确过一遍
                            if not re.search(re.escape(key), name, re.IGNORECASE):
                                continue

                            if any(word.lower() in name.lower() for word in JP_TITLE_REMOVE):
                                continue

                            results.append({
                                'vod_id': vid,
                                'vod_name': name,
                                'vod_pic': self._pic(item.get('thumbnail', '')),
                                'vod_remarks': item.get('mask', ''),
                            })
                            self.index[vid] = item

            return {
                'list': results,
                'page': page,
                'pagecount': max_pages,
                'limit': len(results),
                'total': len(results),
            }
        except Exception:
            return {'list': [], 'page': page, 'pagecount': 1, 'limit': 0, 'total': 0}

    # ---------------- 播放 ----------------
    def playerContent(self, flag, pid, vipFlags):
        # 框架传入的 pid 是 vod_play_url 中 '$' 之后的【纯 URL】（见坑4）。
        #   切勿假定它是 "标题$url"：纯 URL 不含 '$'，按 '$' split 会得到空 url，
        #   真机表现为"播放地址加载失败"。此处兼容两种形态。
        try:
            url = (pid or '').strip()
            if '$' in url:
                url = url.split('$', 1)[1].strip()
            if not url:
                return {'url': '', 'parse': 0, 'jx': 0}

            ua = JP_HEADERS.get('User-Agent', '')
            # m3u8/mp4 直连；其余（下载类直链）走 tvbox-xg 边下边播
            if '.m3u8' in url or '.mp4' in url:
                return {'url': url, 'parse': 0, 'jx': 0, 'header': {'User-Agent': ua}}
            return {'url': f'tvbox-xg:{url}', 'parse': 0, 'jx': 0, 'header': {'User-Agent': ua}}
        except Exception:
            return {'url': '', 'parse': 0, 'jx': 0}

    def destroy(self):
        return 'ok'


if __name__ == '__main__':
    # 本地自测入口（真机不会执行）。需先能 import base.spider。
    spider = Spider()
    spider.init()
    print(f"Name: {spider.getName()}")
    print(f"API: {spider.api_base}")
    print(f"imghost: {spider.imghost}")

    result = spider.homeContent(None)
    print(f"Home: {json.dumps(result, ensure_ascii=False)[:500]}")
