# -*- coding: utf-8 -*-
# @Function: 老王私人节目单（B站投稿·按BV号直取）
# @说明: 不依赖任何 jar 的 csp_Bili，自备 view/playurl 两接口，B站改规则只改本文件。
#   分类与节目写死在本文件 CATALOG 中，改节目只需改这一处。
#
# ===== 两条播放链路 =====
#   [默认] B站官方 playurl -> DASH -> 9978 代理出 MPD
#   [特例]「诗词文章」分类走移动云盘（见下方 YUN139 登记表）
#          原因：B站把该两篇重转码成 H.264 Level 5.1，超出电视盒子硬解能力，
#                1080P 软解必卡；移动云盘官方转出的是 Main@Level 4.0，可硬解。
#          新增/调整：把文件传到你自己的移动云盘 -> 建分享链接 -> 把「链接ID + contentID」
#                     填进 YUN139 即可。取 ID 的办法见同目录说明或让末将来办。
#
# ===== 清晰度（1080P）说明 =====
#   实测：免 cookie 最高只给 480P（quality=64）；带「已登录」cookie 可给 1080P（quality=80）。
#   cookie 取值优先级：
#     1) ext.cookie      : 直接填 cookie 串（不建议，会随 api.json 落到公网）
#     2) ext.cookieUrl   : 自定义网址（可选，优先级最高）
#     3) 设备本地文件    : http://127.0.0.1:9978/file/TVBox/bili_cookie.txt  ← 推荐主场
#                          （同一 App 内另有多个站点已在用此路径，目录是通的）
#     4) autoCookie=1    : 复用同目录 py/哔哩.py（仅本地仓库场景有效）
#   ——凭据一律不写在本文件里，避免随 pages.dev 部署落到公网。
#   四项都拿不到 → 退化为 480P（节目名会如实标注），此时请检查设备 TVBox 目录下有无 bili_cookie.txt。

import os
import re
import sys
import time
import base64
import json
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
    ('scwz', '诗词文章', [
        ('沁园春·酒钢颂', 'BV1bUhizLE7B'),
        ('钢铁赋·酒钢魂——献给老一辈的酒钢人', 'BV1a6hizSEp9'),
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

# ---- 移动云盘（139 外链）：绕开 B站转码导致的 Level 超标 ----
# 根因：B站对新投稿/重新投稿的稿件统一输出 H.264 Level 5.1，
#       超出多数电视盒子的硬解码能力，只能 CPU 软解 → 1080P 必卡；
#       而同一支片子上传移动云盘，官方转出来是 Main@Level 4.0（实测 SPS），可正常硬解。
# 做法：播放时现场向 139 官方外链接口换取 HLS 地址；该地址有效期约 8 小时，
#       故每次都现取，绝不硬编码。
# 登记格式：{ BV号: (分享链接ID, 文件 contentID) }
YUN139 = {
    'BV1bUhizLE7B': ('2xTrF5Zaqdgwr', 'FpuTNMwAIuR5a_D4OVZNLdL2aYBpjCJGe'),
    'BV1a6hizSEp9': ('2xTrDHT7B9apj', 'FqRBa_3r9c1IGyrA55ulNhrJu99YZ_VvS'),
}
YUN139_TTL = 1800   # 播放地址缓存秒数（官方 8 小时有效期，取一半以内比较稳）
YUN139_INFO = ('https://share-kd-njs.yun.139.com/yun-share/richlifeApp/'
               'devapp/IOutLink/getContentInfoFromOutLink')
YUN139_KEY = b'PVGDwmcvfs1uV3d1'     # 139 前端写死的固定密钥（公开常量）
YUN139_HDR = {
    'Accept': 'application/json, text/plain, */*',
    'Content-Type': 'application/json;charset=UTF-8',
    'Origin': 'https://yun.139.com',
    'Referer': 'https://yun.139.com/',
    'User-Agent': UA,
}


def _gf_mul(a, b):
    """GF(2^8) 乘法，模多项式 0x11b"""
    r = 0
    while b:
        if b & 1:
            r ^= a
        a = ((a << 1) ^ 0x11b) & 0xff if a & 0x80 else a << 1
        b >>= 1
    return r & 0xff


def _aes_tables():
    """运行时生成 S 盒与 GF 乘法表（免手写 256 项常量，且可被交叉验证）"""
    mul = dict((n, [_gf_mul(x, n) for x in range(256)]) for n in (2, 3, 9, 11, 13, 14))
    exp, log = [0] * 256, [0] * 256
    x = 1
    for i in range(255):
        exp[i] = x
        log[x] = i
        x = _gf_mul(x, 3)
    sbox, inv = [0] * 256, [0] * 256
    for a in range(256):
        v = 0 if a == 0 else exp[(255 - log[a]) % 255]      # GF 逆元；逆元为0处定义为0
        s, t = v ^ 0x63, v                                   # 仿射变换
        for _k in range(4):
            t = ((t << 1) | (t >> 7)) & 0xff
            s ^= t
        sbox[a] = s
        inv[s] = a
    return sbox, inv, mul


_SBOX, _ISBOX, _MUL = _aes_tables()
_SR = [4 * ((c + r) % 4) + r for c in range(4) for r in range(4)]
_ISR = [4 * ((c - r) % 4) + r for c in range(4) for r in range(4)]


class _AES128(object):
    """纯标准库 AES-128 加解密（不依赖 requests 之外的任何三方库）
    用途：139 官方一旦把响应改成密文，这段就是兜底；明文响应时不会用到它。"""

    def __init__(self, key):
        key = list(key[:16])
        w = [key[4 * i:4 * i + 4] for i in range(4)]
        rcon = 1
        for i in range(4, 44):
            t = list(w[i - 1])
            if i % 4 == 0:
                t = t[1:] + t[:1]
                t = [_SBOX[b] for b in t]
                t[0] ^= rcon
                rcon = _gf_mul(rcon, 2)
            w.append([w[i - 4][j] ^ t[j] for j in range(4)])
        self.w = w

    def encrypt_block(self, b):
        s = list(b)
        w = self.w
        for c in range(4):
            k = w[c]
            o = 4 * c
            s[o] ^= k[0]; s[o + 1] ^= k[1]; s[o + 2] ^= k[2]; s[o + 3] ^= k[3]
        for rnd in range(1, 10):
            y = [_SBOX[s[i]] for i in _SR]
            m2, m3 = _MUL[2], _MUL[3]
            s = [m2[y[0]] ^ m3[y[1]] ^ y[2] ^ y[3],
                 y[0] ^ m2[y[1]] ^ m3[y[2]] ^ y[3],
                 y[0] ^ y[1] ^ m2[y[2]] ^ m3[y[3]],
                 m3[y[0]] ^ y[1] ^ y[2] ^ m2[y[3]],
                 m2[y[4]] ^ m3[y[5]] ^ y[6] ^ y[7],
                 y[4] ^ m2[y[5]] ^ m3[y[6]] ^ y[7],
                 y[4] ^ y[5] ^ m2[y[6]] ^ m3[y[7]],
                 m3[y[4]] ^ y[5] ^ y[6] ^ m2[y[7]],
                 m2[y[8]] ^ m3[y[9]] ^ y[10] ^ y[11],
                 y[8] ^ m2[y[9]] ^ m3[y[10]] ^ y[11],
                 y[8] ^ y[9] ^ m2[y[10]] ^ m3[y[11]],
                 m3[y[8]] ^ y[9] ^ y[10] ^ m2[y[11]],
                 m2[y[12]] ^ m3[y[13]] ^ y[14] ^ y[15],
                 y[12] ^ m2[y[13]] ^ m3[y[14]] ^ y[15],
                 y[12] ^ y[13] ^ m2[y[14]] ^ m3[y[15]],
                 m3[y[12]] ^ y[13] ^ y[14] ^ m2[y[15]]]
            k0, k1, k2, k3 = w[4 * rnd], w[4 * rnd + 1], w[4 * rnd + 2], w[4 * rnd + 3]
            s[0] ^= k0[0]; s[1] ^= k0[1]; s[2] ^= k0[2]; s[3] ^= k0[3]
            s[4] ^= k1[0]; s[5] ^= k1[1]; s[6] ^= k1[2]; s[7] ^= k1[3]
            s[8] ^= k2[0]; s[9] ^= k2[1]; s[10] ^= k2[2]; s[11] ^= k2[3]
            s[12] ^= k3[0]; s[13] ^= k3[1]; s[14] ^= k3[2]; s[15] ^= k3[3]
        y = [_SBOX[s[i]] for i in _SR]
        k0, k1, k2, k3 = w[40], w[41], w[42], w[43]
        return bytes([y[0] ^ k0[0], y[1] ^ k0[1], y[2] ^ k0[2], y[3] ^ k0[3],
                      y[4] ^ k1[0], y[5] ^ k1[1], y[6] ^ k1[2], y[7] ^ k1[3],
                      y[8] ^ k2[0], y[9] ^ k2[1], y[10] ^ k2[2], y[11] ^ k2[3],
                      y[12] ^ k3[0], y[13] ^ k3[1], y[14] ^ k3[2], y[15] ^ k3[3]])

    def decrypt_block(self, b):
        s = list(b)
        w = self.w
        for c in range(4):
            k = w[40 + c]
            o = 4 * c
            s[o] ^= k[0]; s[o + 1] ^= k[1]; s[o + 2] ^= k[2]; s[o + 3] ^= k[3]
        m9, m11, m13, m14 = _MUL[9], _MUL[11], _MUL[13], _MUL[14]
        for rnd in range(9, 0, -1):
            y = [_ISBOX[t] for t in [s[i] for i in _ISR]]
            k0, k1, k2, k3 = w[4 * rnd], w[4 * rnd + 1], w[4 * rnd + 2], w[4 * rnd + 3]
            y[0] ^= k0[0]; y[1] ^= k0[1]; y[2] ^= k0[2]; y[3] ^= k0[3]
            y[4] ^= k1[0]; y[5] ^= k1[1]; y[6] ^= k1[2]; y[7] ^= k1[3]
            y[8] ^= k2[0]; y[9] ^= k2[1]; y[10] ^= k2[2]; y[11] ^= k2[3]
            y[12] ^= k3[0]; y[13] ^= k3[1]; y[14] ^= k3[2]; y[15] ^= k3[3]
            s = [m14[y[0]] ^ m11[y[1]] ^ m13[y[2]] ^ m9[y[3]],
                 m9[y[0]] ^ m14[y[1]] ^ m11[y[2]] ^ m13[y[3]],
                 m13[y[0]] ^ m9[y[1]] ^ m14[y[2]] ^ m11[y[3]],
                 m11[y[0]] ^ m13[y[1]] ^ m9[y[2]] ^ m14[y[3]],
                 m14[y[4]] ^ m11[y[5]] ^ m13[y[6]] ^ m9[y[7]],
                 m9[y[4]] ^ m14[y[5]] ^ m11[y[6]] ^ m13[y[7]],
                 m13[y[4]] ^ m9[y[5]] ^ m14[y[6]] ^ m11[y[7]],
                 m11[y[4]] ^ m13[y[5]] ^ m9[y[6]] ^ m14[y[7]],
                 m14[y[8]] ^ m11[y[9]] ^ m13[y[10]] ^ m9[y[11]],
                 m9[y[8]] ^ m14[y[9]] ^ m11[y[10]] ^ m13[y[11]],
                 m13[y[8]] ^ m9[y[9]] ^ m14[y[10]] ^ m11[y[11]],
                 m11[y[8]] ^ m13[y[9]] ^ m9[y[10]] ^ m14[y[11]],
                 m14[y[12]] ^ m11[y[13]] ^ m13[y[14]] ^ m9[y[15]],
                 m9[y[12]] ^ m14[y[13]] ^ m11[y[14]] ^ m13[y[15]],
                 m13[y[12]] ^ m9[y[13]] ^ m14[y[14]] ^ m11[y[15]],
                 m11[y[12]] ^ m13[y[13]] ^ m9[y[14]] ^ m14[y[15]]]
        y = [_ISBOX[t] for t in [s[i] for i in _ISR]]
        k0, k1, k2, k3 = w[0], w[1], w[2], w[3]
        return bytes([y[0] ^ k0[0], y[1] ^ k0[1], y[2] ^ k0[2], y[3] ^ k0[3],
                      y[4] ^ k1[0], y[5] ^ k1[1], y[6] ^ k1[2], y[7] ^ k1[3],
                      y[8] ^ k2[0], y[9] ^ k2[1], y[10] ^ k2[2], y[11] ^ k2[3],
                      y[12] ^ k3[0], y[13] ^ k3[1], y[14] ^ k3[2], y[15] ^ k3[3]])


_YUN_CI = None


def _yun_ci():
    global _YUN_CI
    if _YUN_CI is None:
        _YUN_CI = _AES128(YUN139_KEY)
    return _YUN_CI


def aes_encrypt(text):
    """139 请求体加密：base64(随机IV + AES-128-CBC(明文+PKCS7))"""
    try:
        ci = _yun_ci()
        raw = text.encode('utf-8')
        n = 16 - len(raw) % 16
        raw += bytes([n]) * n
        iv = os.urandom(16)
        out = b''
        prev = iv
        for i in range(0, len(raw), 16):
            blk = bytes([raw[i + j] ^ prev[j] for j in range(16)])
            eb = ci.encrypt_block(blk)
            out += eb
            prev = eb
        return base64.b64encode(iv + out).decode()
    except Exception:
        return ''


def aes_decrypt(text):
    """139 响应解密：base64(IV + AES-128-CBC(密文))，注意必须做 CBC 链式异或"""
    try:
        ci = _yun_ci()
        raw = base64.b64decode(''.join(text.split()))
        out = b''
        prev = raw[:16]                       # 第一块的 IV
        for i in range(16, len(raw), 16):
            blk = ci.decrypt_block(raw[i:i + 16])
            out += bytes([blk[j] ^ prev[j] for j in range(16)])
            prev = raw[i:i + 16]              # CBC：与前一个密文块异或，不能用 IV
        n = out[-1]
        if 1 <= n <= 16 and out[-n:] == bytes([n]) * n:
            out = out[:-n]
        return out.decode('utf-8', 'replace')
    except Exception:
        return ''

# ---- cookie 来源：以「设备本地文件」为主，凭据不落公网 ----
# 电视端 catvod/pyfile 服务常见根路径，逐个探测，取第一个真正含 SESSDATA 的。
# 主公只需把主公自己的 cookie 存成一行文本，放到设备 TVBox 目录下即可。
LOCAL_COOKIE_PATHS = [
    'http://127.0.0.1:9978/file/TVBox/bili_cookie.txt',
    'http://127.0.0.1:9978/file/Documents/bili_cookie.txt',
    'http://127.0.0.1:9978/file/Download/bili_cookie.txt',
    'http://127.0.0.1:9978/file/bili_cookie.txt',
]
DEFAULT_COOKIE_URL = LOCAL_COOKIE_PATHS[0]


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
        self.yun_cache = {}    # bvid -> (移动云盘播放地址, 时间戳)
        # —— 可配置项（由站点 ext 覆盖）——
        self.cookie = ''
        self.cookie_url = ''
        self.auto_cookie = 1
        self.cookie_src = ''      # 记录本次 cookie 来源，便于排障
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
        urls = []
        if self.cookie_url:
            urls.append(self.cookie_url)
        for u in LOCAL_COOKIE_PATHS:
            if u not in urls:
                urls.append(u)
        self.cookie_src = ''
        for u in urls:
            try:
                r = requests.get(u, headers=self.headers, timeout=6)
                got = self.clean_cookie(r.text)
                # 只认真正含 SESSDATA 的内容；404 页面/空文本一律跳过
                if got and 'SESSDATA=' in got:
                    ck = got
                    self.cookie_src = u
                    break
            except Exception:
                continue
        if not ck and self.auto_cookie:
            ck = self.find_cookie_in_py()   # 仅本地仓库场景有效（读到 py/哔哩.py）
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
        # 探测实际可达清晰度（走移动云盘的不必再问 B站，省一次请求）
        if bvid in YUN139:
            qn = 80
        else:
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

    def yun139_url(self, bvid):
        """从移动云盘换取 HLS 播放地址（Main@L4.0，老盒子可硬解）
        正常情况服务端直接回明文 JSON；若哪天改成密文，自动落到底部的 AES 解密。
        失败返回 ''，调用方会原样回落 B站，不会比现在更糟。"""
        if bvid not in YUN139:
            return ''
        now = time.time()
        got = self.yun_cache.get(bvid)
        if got and now - got[1] < YUN139_TTL:
            return got[0]
        link, cid = YUN139[bvid]
        body = json.dumps(
            {'getContentInfoFromOutLinkReq': {'contentId': cid, 'linkID': link, 'account': ''},
             'commonAccountInfo': {'account': '', 'accountType': 1}},
            ensure_ascii=False)
        # 先按明文请求（实测有效）；若哪天官方改成密文，自动换 AES-CBC 重试
        for mode in (0, 1):
            try:
                payload = aes_encrypt(body).encode('ascii') if mode else body.encode('utf-8')
                r = requests.post(YUN139_INFO, data=payload, headers=YUN139_HDR, timeout=15)
                t = r.text.strip()
                if not t.startswith('{'):
                    t = aes_decrypt(t)
                js = json.loads(t)
                pu = (((js.get('data') or {}).get('contentInfo') or {}).get('presentURL'))
                if pu:
                    self.yun_cache[bvid] = (pu, now)
                    return pu
            except Exception:
                continue
        return ''

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
        yurl = self.yun139_url(bvid)
        if yurl:
            # 移动云盘 HLS：实测 ts 分片不需要任何防盗链头，给个 UA 足够
            return {
                'url': yurl,
                'parse': 0,
                'jx': 0,
                'header': {'User-Agent': UA},
            }
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
