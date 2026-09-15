# -*- coding: utf-8 -*-
# 道玄央视网 drpy 源（直播 + 点播）
# 甲案：直播走「央视频」签名取流，点播走「央视网」明文片库
#
# 【直播】央视频 bkliveinfo.ysp.cctv.cn
#   签名栈：TEA-CBC + 自定义 Base64 + GuardTime + CKey 报文（移植自 央视网.py）。
#   为何不用央视网官方 CDN：cdrm 族（ldncctvwbcd / ldcctvwbcd / ldocctvwbcd / wstvwbcd /
#   cetvwbcd / sportswbcd / hdgcwbcd）全部 UDRM 加密，第三方播放必花屏——
#   2026-09 经 ffmpeg 实际解码证实（I 帧 concealing DC/AC/MV errors），非明文；
#   央视频签名线路为明文 H.264，实测 cctv1 / cctv5 / cctv13 解码 0 错误、真 1080p。
#   清晰度线路：高清 fhd(1080p) / 标清 shd(720p) / 流畅 hd(540p)，逐档自动回退；
#   4K 频道（cctv164k/cctv4k/cctv8k）高清档优先试 uhd 出真 4K。
#   分片请求头回传 UA:qqlive + Referer:https://www.yangshipin.cn/（兼容性加固）。
#   关键：央视频按出口 IP **不定时限流**——命中窗口一次即通，窗口封死则一律 403
#   （主公 2026-09-14 亲证："刚才大部分能放，这会儿全灭，含标清/流畅"）。
#   对策两层：
#     ① _ysp_probe 预检重取——反复换新 token（常落到不同 CDN 节点）+ 抖动退避，
#        试到 m3u8 与最新分片实测可取，才交给播放器；
#     ② 同档重复挂线（LIVE_LINE_REPEAT）——每档 N 条线路 = 壳子自动换源时的 N 次重试。
#   实测：不重取成功率约 5/10；启用预检后单频道可达 4/4、4/4、3/4。
#   注：央视付费中 10 台（世界地理/风云音乐/兵器科技/风云足球/高尔夫·网球/女性时尚/
#       央视文化精品/央视台球/电视指南/卫生健康）主公实测"播放地址加载失败"，已按令删除。
#
# 【点播】央视网片库 api.cntv.cn + 明文旅（hls.cntv.lxdns.com）
#   明文天花板 640x368（850 档），1200/2000 请求回落 480x272；
#   enc / h5e 边缘节点为 UDRM 加密，第三方播放必花屏，故弃用。
#   取流前校验首分片（TS 同步字 + 无 udrm 标记），命中加密即降档，确保不花屏。
import re
import time
import random
import struct
import datetime
from base64 import b64encode, b64decode
import requests
from urllib3 import disable_warnings
from base.spider import Spider

disable_warnings()

API = 'https://api.cntv.cn'
UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36'
YSP_UA = 'qqlive'


class Spider(Spider):

    # ==================== 点播配置 ====================
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

    # 点播线路（高 -> 低），目标码率 kbps；plain 明文旅仅 450 / 850 两档真实可用
    VOD_LINES = ['高清', '标清', '流畅']
    VOD_TARGET = {'高清': 850, '标清': 450, '流畅': 450}
    PLAIN_HOST = 'hls.cntv.lxdns.com'
    DRM_MARK = b'udrm'

    # ==================== 直播配置（央视频） ====================
    LIVE_TID = '央视直播'
    LIVE_TAGS = [('全部', ''), ('央视', '央视'), ('CGTN', 'CGTN'),
                 ('付费', '央视付费'), ('卫视', '卫视'), ('其他', '其他')]

    # 清晰度线路（高 -> 低），值为央视频 defn 代码
    LIVE_LINE_DEFS = [('高清', 'fhd'), ('标清', 'shd'), ('流畅', 'hd')]
    # 每档重复挂几条线路。每条线路在 playerContent 时都会触发一次独立的签名取流
    # （新 token / 可能新节点），故 N 条 = 壳子自动换源时的 N 次重试机会。
    # 央视频按 IP 不定时限流，撞开窗口靠的就是"多试几次"，故默认 2。
    LIVE_LINE_REPEAT = 2

    # 4K 频道：高清档优先试 uhd
    LIVE_TOP = {'cctv164k': 'uhd', 'cctv4k': 'uhd', 'cctv8k': 'uhd'}

    # 起播预检重取次数（央视频单 token 复用即 403，须换新 token 试到可用为止）
    LIVE_PROBE_TRIES = 6
    # 每次重取之间的抖动退避区间（秒）——避免连发自造心跳，反被限流加重
    LIVE_PROBE_GAP = (0.5, 1.2)

    # 频道表 (key, 显示名, 分组, cnlid, livepid)
    LIVE_CHANNELS = [
        # ---------- 央视 ----------
        ('cctv1', 'CCTV-1 综合', '央视', '2024078201', '600001859'),
        ('cctv2', 'CCTV-2 财经', '央视', '2024075401', '600001800'),
        ('cctv3', 'CCTV-3 综艺', '央视', '2024068501', '600001801'),
        ('cctv4', 'CCTV-4 中文国际', '央视', '2029797101', '600001814'),
        ('cctv5', 'CCTV-5 体育', '央视', '2024078401', '600001818'),
        ('cctv5p', 'CCTV-5+ 体育赛事', '央视', '2024078001', '600001817'),
        ('cctv6', 'CCTV-6 电影', '央视', '2013693901', '600108442'),
        ('cctv7', 'CCTV-7 国防军事', '央视', '2024072001', '600004092'),
        ('cctv8', 'CCTV-8 电视剧', '央视', '2029793001', '600001803'),
        ('cctv9', 'CCTV-9 纪录', '央视', '2024078601', '600004078'),
        ('cctv10', 'CCTV-10 科教', '央视', '2024078701', '600001805'),
        ('cctv11', 'CCTV-11 戏曲', '央视', '2027248701', '600001806'),
        ('cctv12', 'CCTV-12 社会与法', '央视', '2027248801', '600001807'),
        ('cctv13', 'CCTV-13 新闻', '央视', '2029797201', '600001811'),
        ('cctv14', 'CCTV-14 少儿', '央视', '2027248901', '600001809'),
        ('cctv15', 'CCTV-15 音乐', '央视', '2027249001', '600001815'),
        ('cctv16', 'CCTV-16 奥林匹克', '央视', '2027249101', '600098637'),
        ('cctv164k', 'CCTV-16(4K)', '央视', '2027249301', '600099502'),
        ('cctv17', 'CCTV-17 农业农村', '央视', '2027249401', '600001810'),
        ('cctv4k', 'CCTV-4K', '央视', '2029810301', '600002264'),
        ('cctv8k', 'CCTV-8K', '央视', '2026774101', '600156816'),
        # ---------- CGTN ----------
        ('cgtn', 'CGTN', 'CGTN', '2024181701', '600014550'),
        ('cgtnfy', 'CGTN 法语', 'CGTN', '2024181801', '600084704'),
        ('cgtney', 'CGTN 俄语', 'CGTN', '2024181901', '600084758'),
        ('cgtnalby', 'CGTN 阿拉伯语', 'CGTN', '2024182001', '600084782'),
        ('cgtnxby', 'CGTN 西班牙语', 'CGTN', '2024182101', '600084744'),
        ('cgtnwyjl', 'CGTN 外语纪录', 'CGTN', '2024182301', '600084781'),
        # ---------- 央视付费 ----------
        ('cctvfyjc', '风云剧场', '央视付费', '2025637103', '600099658'),
        ('cctvdyjc', '第一剧场', '央视付费', '2026874203', '600099655'),
        ('cctvhjjc', '怀旧剧场', '央视付费', '2026874303', '600099620'),
        # 注：世界地理/风云音乐/兵器科技/风云足球/高尔夫·网球/女性时尚/央视文化精品/
        #     央视台球/电视指南/卫生健康 —— 主公实测"播放地址加载失败"，已按令删除。
        # ---------- 卫视 ----------
        ('bjws', '北京卫视', '卫视', '2024052703', '600002309'),
        ('jsws', '江苏卫视', '卫视', '2024171103', '600002521'),
        ('dfws', '东方卫视', '卫视', '2024054503', '600002483'),
        ('zjws', '浙江卫视', '卫视', '2024054703', '600002520'),
        ('hnws', '湖南卫视', '卫视', '2024054803', '600002475'),
        ('hbws', '湖北卫视', '卫视', '2024171203', '600002508'),
        ('gdws', '广东卫视', '卫视', '2024060903', '600002485'),
        ('gxws', '广西卫视', '卫视', '2024060703', '600002509'),
        ('hljws', '黑龙江卫视', '卫视', '2029797003', '600002498'),
        ('hnws2', '海南卫视', '卫视', '2024055603', '600002506'),
        ('cqws', '重庆卫视', '卫视', '2024061103', '600002531'),
        ('szws', '深圳卫视', '卫视', '2024061303', '600002481'),
        ('scws', '四川卫视', '卫视', '2024061403', '600002516'),
        ('henanws', '河南卫视', '卫视', '2029797303', '600002525'),
        ('fjdnhz', '福建东南卫视', '卫视', '2024061503', '600002484'),
        ('gzhws', '贵州卫视', '卫视', '2024061603', '600002490'),
        ('jxws', '江西卫视', '卫视', '2024061703', '600002503'),
        ('lnws', '辽宁卫视', '卫视', '2024171303', '600002505'),
        ('ahws', '安徽卫视', '卫视', '2024171403', '600002532'),
        ('hbws2', '河北卫视', '卫视', '2024171503', '600002493'),
        ('sdws', '山东卫视', '卫视', '2029787903', '600002513'),
        ('tjws', '天津卫视', '卫视', '2019927003', '600152137'),
        ('jlws', '吉林卫视', '卫视', '2025561503', '600190405'),
        ('shanxiws', '陕西卫视', '卫视', '2029795103', '600190400'),
        ('nxws', '宁夏卫视', '卫视', '2025608503', '600190737'),
        ('nmgws', '内蒙古卫视', '卫视', '2025561203', '600190401'),
        ('ynws', '云南卫视', '卫视', '2025561303', '600190402'),
        ('shanxiws2', '山西卫视', '卫视', '2025560803', '600190407'),
        ('qhws', '青海卫视', '卫视', '2025559103', '600190406'),
        ('xzws', '西藏卫视', '卫视', '2025558003', '600190403'),
        ('xjws', '新疆卫视', '卫视', '2019927403', '600152138'),
        # ---------- 其他 ----------
        ('cetv1', '中国教育电视台', '其他', '2022823801', '600171827'),
        ('gxpd', '国学频道', '其他', '2029360403', '600213139'),
    ]

    # ==================== 央视频加密常量 ====================
    DELTA = 0x9e3779b9
    ROUNDS = 16
    LOG_ROUNDS = 4
    SALT_LEN = 2
    ZERO_LEN = 7
    TEA_CKEY = bytes.fromhex('59b2f7cf725ef43c34fdd7c123411ed3')
    GUARD_TEA_KEY = bytes.fromhex('110DBEC10C23E7D2E56A1CAD6914EF1B')

    XOR_KEY = [0x84, 0x2E, 0xED, 0x08, 0xF0, 0x66, 0xE6, 0xEA,
               0x48, 0xB4, 0xCA, 0xA9, 0x91, 0xED, 0x6F, 0xF3]
    GUARD_XOR_KEY = [0xB3, 0xC9, 0x53, 0xA0, 0x69, 0x13, 0xAD, 0x4D]

    STANDARD_ALPHABET = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/='
    CUSTOM_ALPHABET = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-='

    # ==================== 生命周期 ====================
    def init(self, extend=''):
        # 央视网（点播 / 页面）会话：浏览器 UA
        self.session = requests.Session()
        self.session.verify = False
        self.session.headers.update({
            'User-Agent': UA,
            'Referer': 'https://tv.cctv.com/',
            'Accept': 'application/json, text/plain, */*',
        })
        self.header = {'User-Agent': UA, 'Referer': 'https://tv.cctv.com/'}

        # 央视频（直播）会话：qqlive UA
        self.ysp = requests.Session()
        self.ysp.verify = False
        self.ysp.headers.update({
            'User-Agent': YSP_UA,
            'Connection': 'Keep-Alive',
            'Accept': 'application/json',
        })
        # 分片请求头（回传给播放器）：UA:qqlive 为央视频客户端标识；
        # Referer 作兼容性加固（部分边缘节点校验来源页）。
        # 导致 403 的主因是 playurl token 复用受限（见 _ysp_probe），非请求头。
        self.ysp_header = {'User-Agent': YSP_UA,
                           'Referer': 'https://www.yangshipin.cn/'}
        self.guid = self._gen_guid()

    def getName(self):
        return '道玄央视网'

    def isVideoFormat(self, url):
        return bool(url) and ('.m3u8' in url or '.mp4' in url)

    def manualVideoCheck(self):
        return False

    def destroy(self):
        for s in (getattr(self, 'session', None), getattr(self, 'ysp', None)):
            try:
                s.close()
            except Exception:
                pass

    # ==================== 首页 / 分类 ====================
    def homeContent(self, filter):
        classes = [{'type_id': self.LIVE_TID, 'type_name': '央视直播'}]
        classes += [{'type_id': n, 'type_name': n} for n, _ in self.CATS]

        filters = {}
        # 直播：按分组筛选
        filters[self.LIVE_TID] = [{
            'key': 'group', 'name': '分组',
            'value': [{'n': n, 'v': v} for n, v in self.LIVE_TAGS]}]
        # 点播：类型 / 地区 / 年代 / 字母
        year = datetime.datetime.now().year
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
        return self.categoryContent(self.LIVE_TID, 1, False, {})

    def categoryContent(self, tid, pg, filter, extend):
        if tid == self.LIVE_TID:
            return self._live_category(pg, extend)
        return self._vod_category(tid, pg, extend)

    # ==================== 详情 / 播放 ====================
    def detailContent(self, ids):
        vid = ids[0] if ids else ''
        if vid.startswith('LIVE:'):
            return self._live_detail(vid[5:])
        return self._vod_detail(vid)

    def playerContent(self, flag, id, vipFlags):
        if id.startswith('LIVE:'):
            return self._live_player(flag, id[5:])
        return self._vod_player(flag, id)

    # ==================== 直播：实现（央视频签名） ====================
    def _live_meta(self, ch):
        for code, name, group, cnlid, livepid in self.LIVE_CHANNELS:
            if code == ch:
                return {'code': code, 'name': name, 'group': group,
                        'cnlid': cnlid, 'livepid': livepid}
        return None

    def _live_category(self, pg, extend):
        tag = (extend or {}).get('group') or ''
        out = []
        for code, name, _group, _cnlid, _pid in self.LIVE_CHANNELS:
            _g = _group
            if tag and _g != tag:
                continue
            out.append({
                'vod_id': 'LIVE:' + code,
                'vod_name': name,
                'vod_pic': '',
                'vod_remarks': _g,
            })
        return {'list': out, 'page': 1, 'pagecount': 1,
                'limit': len(out) or 1, 'total': len(out)}

    @classmethod
    def _live_lines(cls):
        """展开线路标签：每档重复 LIVE_LINE_REPEAT 条。

        每条线路在 playerContent 时都是一次独立的签名取流（新 token / 可能新节点），
        故 N 条 = 壳子（如 OK影视）自动换源时可自动重试 N 次。
        """
        out = []
        for name, defn in cls.LIVE_LINE_DEFS:
            for i in range(max(1, cls.LIVE_LINE_REPEAT)):
                out.append((name if i == 0 else '%s%d' % (name, i + 1), defn))
        return out

    def _live_detail(self, ch):
        meta = self._live_meta(ch)
        name = meta['name'] if meta else ch
        play = 'LIVE:%s$LIVE:%s' % (ch, ch)
        lines = self._live_lines()
        vod = {
            'vod_id': 'LIVE:' + ch,
            'vod_name': name,
            'vod_pic': '',
            'vod_content': '央视频直播 · %s' % name,
            'vod_remarks': '直播',
            'vod_play_from': '$$$'.join(n for n, _ in lines),
            'vod_play_url': '$$$'.join([play] * len(lines)),
        }
        return {'list': [vod]}

    def _defn_chain(self, ch, line_defn):
        """清晰度回退链：高清档对 4K 频道优先 uhd，其余按 用户档 -> fhd -> shd -> hd。"""
        seq = []
        top = self.LIVE_TOP.get(ch)
        if line_defn == 'fhd' and top:
            seq.append(top)
        for d in (line_defn, 'fhd', 'shd', 'hd'):
            if d not in seq:
                seq.append(d)
        return seq

    def _live_player(self, flag, ch):
        meta = self._live_meta(ch)
        if not meta:
            return {'parse': 0, 'url': '', 'header': ''}
        # 线路标签可能是 "高清2" 之类（同档重复挂线），剥掉尾号再匹配档位
        base_flag = re.sub(r'\d+$', '', flag or '')
        line_defn = 'fhd'
        for n, d in self.LIVE_LINE_DEFS:
            if n == base_flag:
                line_defn = d
                break
        for defn in self._defn_chain(ch, line_defn):
            # 优先返回"预检可用"的 token；预检失败则退回单次取流兜底
            url = self._ysp_probe(meta['cnlid'], meta['livepid'], defn) \
                or self._ysp_play_url(meta['cnlid'], meta['livepid'], defn)
            if url:
                return {'parse': 0, 'url': url, 'header': self.ysp_header}
        return {'parse': 0, 'url': '', 'header': self.ysp_header}

    def _ysp_probe(self, cnlid, livepid, defn):
        """预检重取：反复换新 playurl，直到"播放列表 + 最新分片"实测都可取。

        央视频按出口 IP 不定时限流：命中窗口时一次即通，窗口封死时一律 403。
        每次重取都是新 token（且常落到不同 CDN 节点），故多试可撞开窗口；
        TVBox / OK影视只持一个 URL、无法自行续签，故由本源代其"试到能取才交出"。
        相邻两次尝试之间加抖动退避，避免连发被判定异常、反而加重限流。
        """
        for i in range(self.LIVE_PROBE_TRIES):
            if i:
                time.sleep(random.uniform(*self.LIVE_PROBE_GAP))
            url = self._ysp_play_url(cnlid, livepid, defn)
            if not url:
                continue
            try:
                m = self.ysp.get(url, headers=self.ysp_header, timeout=8)
                if m.status_code != 200:
                    continue
                segs = [l.strip() for l in m.text.splitlines()
                        if l.strip() and not l.startswith('#')]
                if not segs:
                    continue
                seg = segs[-1]  # 取最新分片，贴近真实起播位置
                base = url[:url.rfind('/') + 1]
                su = seg if seg.startswith('http') else base + seg
                r = self.ysp.get(su, headers=self.ysp_header, timeout=8, stream=True)
                buf = next(r.iter_content(65536), b'')
                r.close()
                if buf and buf[0] == 0x47 and len(buf) > 10000:
                    print('[道玄央视网] 直播命中 %s · 节点 %s · 第 %d 次尝试'
                          % (defn, url.split('/')[2], i + 1))
                    return url
            except Exception:
                pass
        return None

    # ==================== 央视频取流 ====================
    def _ysp_play_url(self, cnlid, livepid, defn):
        try:
            self.guid = self._gen_guid()
            ck = self._gen_ckey(cnlid)
            flowid = self._gen_flowid()

            params = {
                "atime": "120",
                "livepid": livepid,
                "cnlid": cnlid,
                "appVer": "V8.22.1035.3031",
                "app_version": "300090",
                "caplv": "1",
                "cmd": "2",
                "defn": defn,
                "device": "iPhone",
                "encryptVer": "4.2",
                "getpreviewinfo": "0",
                "hevclv": "33",
                "lang": "zh-Hans_JP",
                "livequeue": "0",
                "logintype": "1",
                "nettype": "1",
                "newnettype": "1",
                "newplatform": "4330403",
                "platform": "4330403",
                "sdtfrom": "v3021",
                "spacode": "23",
                "spaudio": "1",
                "spdemuxer": "6",
                "spdrm": "2",
                "spdynamicrange": "7",
                "spflv": "1",
                "spflvaudio": "1",
                "sphdrfps": "60",
                "sphttps": "0",
                "spvcode": "MSgzMDoyMTYwLDYwOjIxNjB8MzA6MjE2MCw2MDoyMTYwKTsyKDMwOjIxNjAsNjA6MjE2MHwzMDoyMTYwLDYwOjIxNjAp",
                "spvideo": "4",
                "stream": "1",
                "system": "1",
                "sysver": "ios18.2.1",
                "uhd_flag": "4",
                "cKey": ck['ckey'],
                "guid": self.guid,
                "fntick": ck['params']['Timestamp'],
                "flowid": flowid,
                "playbacktime": "0",
            }

            resp = self.ysp.get(
                "https://bkliveinfo.ysp.cctv.cn",
                params=params,
                timeout=15,
            )
            data = resp.json()
            if data.get('iretcode') == 0 and data.get('playurl'):
                return data['playurl']
            print('[道玄央视网] 央视频 defn=%s 无 playurl: %s' % (defn, data.get('iretcode')))
        except Exception as e:
            print('[道玄央视网] 央视频取流失败 defn=%s: %s' % (defn, e))
        return None

    # ==================== GUID / FlowID ====================
    def _gen_guid(self):
        return '{:08x}{:04x}{:04x}{:04x}{:012x}'.format(
            random.randint(0, 0xffffffff),
            random.randint(0, 0xffff),
            random.randint(0, 0xffff),
            random.randint(0, 0xffff),
            random.randint(0, 0xffffffffffff),
        )

    def _gen_flowid(self):
        p = [
            random.randint(0, 0xffff), random.randint(0, 0xffff),
            random.randint(0, 0xffff),
            random.randint(0, 0x0fff) | 0x4000,
            random.randint(0, 0x3fff) | 0x8000,
            random.randint(0, 0xffff), random.randint(0, 0xffff),
            random.randint(0, 0xffff),
        ]
        return '{:04X}{:04X}-{:04X}-{:04X}-{:04X}-{:04X}{:04X}{:04X}_4330403'.format(*p)

    # ==================== 签名 ====================
    def _calc_sig(self, buf):
        s = 0
        for b in buf:
            s = (0x83 * s + (b & 0xFF)) & 0x7FFFFFFF
        return s

    # ==================== 自定义 Base64 ====================
    def _b64enc(self, data):
        enc = b64encode(data).decode()
        return enc.translate(str.maketrans(self.STANDARD_ALPHABET, self.CUSTOM_ALPHABET)).rstrip('=')

    def _b64dec(self, text):
        text = text.rstrip('=')
        if len(text) % 4:
            text += '=' * (4 - len(text) % 4)
        return b64decode(text.translate(str.maketrans(self.CUSTOM_ALPHABET, self.STANDARD_ALPHABET)))

    # ==================== XOR ====================
    def _xor(self, arr):
        return [arr[i] ^ self.XOR_KEY[i & 0xF] for i in range(len(arr))]

    # ==================== TEA ====================
    def _tea_enc(self, data, key):
        if len(data) < 8:
            data = data.ljust(8, b'\0')
        y, z = struct.unpack('>II', data[:8])
        k = struct.unpack('>4I', key[:16])
        s = 0
        for _ in range(self.ROUNDS):
            s = (s + self.DELTA) & 0xFFFFFFFF
            y = (y + (((z << 4) + k[0]) ^ (z + s) ^ ((z >> 5) + k[1]))) & 0xFFFFFFFF
            z = (z + (((y << 4) + k[2]) ^ (y + s) ^ ((y >> 5) + k[3]))) & 0xFFFFFFFF
        return struct.pack('>II', y, z)

    def _tea_dec(self, data, key):
        y, z = struct.unpack('>II', data[:8])
        k = struct.unpack('>4I', key[:16])
        s = (self.DELTA << self.LOG_ROUNDS) & 0xFFFFFFFF
        for _ in range(self.ROUNDS):
            z = (z - (((y << 4) + k[2]) ^ (y + s) ^ ((y >> 5) + k[3]))) & 0xFFFFFFFF
            y = (y - (((z << 4) + k[0]) ^ (z + s) ^ ((z >> 5) + k[1]))) & 0xFFFFFFFF
            s = (s - self.DELTA) & 0xFFFFFFFF
        return struct.pack('>II', y, z)

    # ==================== CBC 加密 ====================
    def _cbc_enc(self, p_in, n_len, p_key):
        pad_salt_zero = n_len + 1 + self.SALT_LEN + self.ZERO_LEN
        n_pad = pad_salt_zero % 8
        if n_pad:
            n_pad = 8 - n_pad

        out = b''
        src = [0] * 8
        src[0] = (random.randint(0, 255) & 0xF8) | n_pad
        si = 1

        while n_pad:
            src[si] = random.randint(0, 255)
            si += 1
            n_pad -= 1

        iv_p = [0] * 8
        iv_c = [0] * 8

        # salt
        i = 0
        while i < self.SALT_LEN:
            if si < 8:
                src[si] = random.randint(0, 255)
                si += 1
                i += 1
            if si == 8:
                for j in range(8):
                    src[j] ^= iv_c[j]
                tb = list(self._tea_enc(bytes(src), p_key))
                for j in range(8):
                    tb[j] ^= iv_p[j]
                iv_p = list(src)
                iv_c = list(tb)
                out += bytes(tb)
                si = 0

        # body
        pi = 0
        while n_len:
            if si < 8:
                src[si] = p_in[pi]
                pi += 1
                si += 1
                n_len -= 1
            if si == 8:
                for j in range(8):
                    src[j] ^= iv_c[j]
                tb = list(self._tea_enc(bytes(src), p_key))
                for j in range(8):
                    tb[j] ^= iv_p[j]
                iv_p = list(src)
                iv_c = list(tb)
                out += bytes(tb)
                si = 0

        # zero
        i = 0
        while i < self.ZERO_LEN:
            if si < 8:
                src[si] = 0
                si += 1
                i += 1
            if si == 8:
                for j in range(8):
                    src[j] ^= iv_c[j]
                tb = list(self._tea_enc(bytes(src), p_key))
                for j in range(8):
                    tb[j] ^= iv_p[j]
                iv_p = list(src)
                iv_c = list(tb)
                out += bytes(tb)
                si = 0

        # last
        if si > 0:
            for j in range(si, 8):
                src[j] = 0
            for j in range(8):
                src[j] ^= iv_c[j]
            tb = list(self._tea_enc(bytes(src), p_key))
            for j in range(8):
                tb[j] ^= iv_p[j]
            out += bytes(tb)

        return out

    # ==================== CBC 解密 ====================
    def _cbc_dec(self, p_in, n_len, p_key):
        if n_len % 8 != 0 or n_len < 16:
            return None
        dest = list(self._tea_dec(p_in[:8], p_key))
        n_pad = dest[0] & 0x07
        out_len = n_len - 1 - n_pad - self.SALT_LEN - self.ZERO_LEN
        if out_len < 0:
            return None

        iv_pre = [0] * 8
        iv_cur = list(p_in[:8])
        off = 8
        di = 1 + n_pad

        # skip salt
        sc = 1
        while sc <= self.SALT_LEN:
            if di < 8:
                di += 1
                sc += 1
            elif di == 8:
                iv_pre = list(iv_cur)
                if off + 8 > n_len:
                    return None
                iv_cur = list(p_in[off:off + 8])
                for j in range(8):
                    dest[j] ^= iv_cur[j]
                dest = list(self._tea_dec(bytes(dest), p_key))
                off += 8
                di = 0

        # copy plain
        plain = []
        rem = out_len
        while rem > 0:
            if di < 8:
                plain.append(dest[di] ^ iv_pre[di])
                di += 1
                rem -= 1
            elif di == 8:
                iv_pre = list(iv_cur)
                if off + 8 > n_len:
                    return None
                iv_cur = list(p_in[off:off + 8])
                for j in range(8):
                    dest[j] ^= iv_cur[j]
                dest = list(self._tea_dec(bytes(dest), p_key))
                off += 8
                di = 0
        return bytes(plain)

    # ==================== Guard Time ====================
    def _last5(self, v):
        v = str(v)
        return v[-5:] if len(v) >= 5 else ''

    def _gen_guard_time(self, ts, guid):
        body = struct.pack('>I', ts)
        for part in [self._last5(guid), self._last5('null'), self._last5('null'), '-1']:
            pb = part.encode()
            body += struct.pack('>H', len(pb)) + pb

        plain = struct.pack('>H', len(body)) + body
        chk = self._calc_sig(list(plain))

        enc = self._cbc_enc(plain, len(plain), self.GUARD_TEA_KEY) + struct.pack('>I', chk)
        el = list(enc)
        for i in range(len(el)):
            el[i] ^= self.GUARD_XOR_KEY[i & 7]
        return bytes(el).hex().upper()

    # ==================== CKey ====================
    def _encrypt_ckey(self, data):
        chk = self._calc_sig(list(data))
        enc = self._cbc_enc(data, len(data), self.TEA_CKEY) + struct.pack('>I', chk)
        return '--01' + self._b64enc(bytes(self._xor(list(enc))))

    def _build_pkt(self, params):
        d = b''
        d += bytes.fromhex('0000004200000004000004d2')  # 12-byte header
        d += struct.pack('>I', params['Platform'])
        d += struct.pack('>I', 0)  # sig placeholder
        d += struct.pack('>I', params['Timestamp'])

        for k in ['Sdtfrom', 'randFlag', 'appVer', 'vid', 'guid']:
            v = params[k].encode()
            d += struct.pack('>H', len(v)) + v

        d += struct.pack('>I', 1)  # part1
        d += struct.pack('>I', 1)  # isDlna

        for v in [b'2622783A', b'nil']:
            d += struct.pack('>H', len(v)) + v

        uuid4 = params['uuid4'].encode()
        d += struct.pack('>H', len(uuid4)) + uuid4

        d += struct.pack('>H', 3) + b'nil'  # bundleID1

        for v in [b'v0.1.000', b'com.cctv.yangshipin.app.iphone',
                  b'4330403', b'ex_json_bus', b'ex_json_vs']:
            d += struct.pack('>H', len(v)) + v

        cgt = params['ck_guard_time'].encode()
        d += struct.pack('>H', len(cgt)) + cgt

        buf = struct.pack('>H', len(d)) + d
        sig = self._calc_sig(list(buf))
        return buf[:18] + struct.pack('>I', sig) + buf[22:]

    def _gen_ckey(self, cnlid):
        ts = int(time.time())
        cgt = self._gen_guard_time(ts, self.guid)
        params = {
            'Platform': 4330403,
            'Timestamp': ts,
            'Sdtfrom': 'dcgh',
            'vid': cnlid,
            'guid': self.guid,
            'appVer': 'V8.22.1035.3031',
            'randFlag': '_zj1A5Gh6QYcxWjIUGos2w==',
            'uuid4': '57eab0c4-2c58-44c6-8ae9-dd2757525dc5',
            'ck_guard_time': cgt,
        }
        pkt = self._build_pkt(params)
        return {'ckey': self._encrypt_ckey(pkt), 'params': params}

    # ==================== 点播：实现（央视网） ====================
    def _get_json(self, path, params):
        try:
            r = self.session.get(API + path, params=params, timeout=15)
            return r.json()
        except Exception as e:
            print('[道玄央视网] 请求失败 %s: %s' % (path, e))
            return {}

    def _vod_category(self, tid, pg, extend):
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

    def _vod_detail(self, vid):
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
            'vod_play_from': '$$$'.join(self.VOD_LINES),
            'vod_play_url': '$$$'.join([play] * len(self.VOD_LINES)),
        }
        return {'list': [vod]}

    def _vod_player(self, flag, id):
        guid = id or ''
        if not re.match(r'^[0-9a-fA-F]{32}$', guid):
            guid = self._guid_from_page(id)
        if not guid:
            return {'parse': 0, 'url': '', 'header': ''}
        info = self._video_info(guid)
        plain = info.get('hls') or ''
        want = self.VOD_TARGET.get(flag, 450)
        for br in self._ladder(want):
            url = self._band_url(plain, guid, br)
            if url and self._usable(url):
                return {'parse': 0, 'url': url, 'header': self.header}
        return {'parse': 0, 'url': plain, 'header': self.header}

    @staticmethod
    def _ladder(want):
        # plain 家族仅 450 / 850 两档真实存在；1200/2000 必回落 480x272，故排除。
        seq = []
        for b in (want, 850, 450):
            if b not in seq:
                seq.append(b)
        return seq

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
            print('[道玄央视网] 取guid失败: %s' % e)
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
            print('[道玄央视网] 取流失败: %s' % e)
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
