# -*- coding: utf-8 -*-
# @Function: 老王私人节目单·第二版 —— 移动云盘「视频」「歌曲」两个文件夹专用站点
# @说明: 本文件独立自足，不依赖也不修改 py/我的节目.py。
#   机制与「我的节目.py」的云盘目录模式一致：分享目录下的每个子文件夹自动变成一个栏目，
#   里面的视频/音频自动成为节目。加节目 = 往云盘传文件，本文件一行都不用改。
#
# ===== 老王要填的只有下面 YUN139_SOURCES 两行 =====
#   每个来源 = 一个云盘文件夹（分享链接）。
#   'link' 可以填「整条分享链接」，也可以只填链接里 #/w/i/ 后面那一段 ID，两种都认。
#   'pwd'  填提取码，没设就留空。
#   例：{'name': '视频', 'link': 'https://yun.139.com/shareweb/#/w/i/2xTrDHT7B9apj', 'pwd': ''}
#
# ★ 排障第一口诀：云盘里明明有子目录，OK影视却没这个栏目
#   ⇒ 九成是那个目录里的文件后缀不在 YUN139_KEEP 里（漏配音频后缀会让整个栏目消失）。
#
# ===== 播放链路 =====
#   播放时现场向 139 官方外链接口换取 HLS 地址（有效期约 8 小时），故每次都现取，绝不硬编码。
#   未转码完成（回执里没有 presentURL）的文件自动跳过，转好即出现。
#   凭据一律不写在本文件里 —— 本仓库经 Cloudflare Pages 公开发布，写进去等于挂到公网。
#   本文件只用「公开分享链接」，不需要账号密码。

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

# ==================== 老王填这里（两条） ====================
#   'name'  ：栏目显示名（照抄云盘文件夹名最省事）
#   'link'  ：分享链接，整条链接或只填 #/w/i/ 后面那段 ID 都认
#   'pwd'   ：提取码，没设就留空
#   'sub'   ：这一栏对应分享里的哪个子文件夹（留空 = 分享根目录本身）
#   'merge' ：1 = 该文件夹连同其子文件夹合成一栏（推荐，栏目名好认）
#             0 = 子文件夹各自成一栏（我的节目.py 的老规矩）
YUN139_SOURCES = [
    {'name': '视频', 'link': 'https://yun.139.com/shareweb/#/w/i/2ygb7ZodBvmzz',
     'pwd': 'y0cy', 'sub': '视频', 'merge': 1},
    {'name': '歌曲', 'link': 'https://yun.139.com/shareweb/#/w/i/2ygb7ZodBvmzz',
     'pwd': 'y0cy', 'sub': '歌曲', 'merge': 1},
]
# ==========================================================

YUN139_TREE_TTL = 1800      # 目录树缓存秒数（播放地址 8 小时有效，缓存取半小时）
YUN139_DEPTH = 3            # 递归层数：1=只看每个来源根目录下的直属目录（与我的节目.py 一致）
YUN139_PAGE = 200           # 每页取多少个条目（云盘接口单页上限）
YUN139_MAXPAGES = 10        # 最多翻多少页，防接口不认分页时打转
YUN139_MERGE_DEFAULT = 1    # 来源没写 'merge' 时的默认：1=合成一栏 / 0=子文件夹各成一栏

# 目录名 -> 显示名（不配就用目录名）。例：{'video': '视频'}
YUN139_ALIAS = {}

# 视频后缀：云盘会转成 HLS 视频流（带 RESOLUTION，可测清晰度）
YUN139_EXTS = ('mp4', 'mkv', 'ts', 'flv', 'm4v', 'mov', 'avi', 'rmvb', 'webm',
               'mpg', 'mpeg')
# 音频后缀：云盘同样会转，只是出的是「纯音轨 m3u8」（CODECS=mp4a.40.2，无分辨率）
YUN139_AUDIO_EXTS = ('mp3', 'flac', 'm4a', 'aac', 'wav', 'ogg', 'oga', 'opus',
                     'ape', 'wma', 'aiff', 'aif', 'amr')
YUN139_KEEP = YUN139_EXTS + YUN139_AUDIO_EXTS   # 实际收录范围（视频+音频）

YUN139_INFO = ('https://share-kd-njs.yun.139.com/yun-share/richlifeApp/'
               'devapp/IOutLink/getContentInfoFromOutLink')
YUN139_LIST = ('https://share-kd-njs.yun.139.com/yun-share/richlifeApp/'
               'devapp/IOutLink/getOutLinkInfoV6')
YUN139_KEY = b'PVGDwmcvfs1uV3d1'     # 139 前端写死的固定密钥（公开常量）
YUN139_HDR = {
    'Accept': 'application/json, text/plain, */*',
    'Content-Type': 'application/json;charset=UTF-8',
    'Origin': 'https://yun.139.com',
    'Referer': 'https://yun.139.com/',
    'User-Agent': UA,
}


# ---------------- 纯标准库 AES-128（139 改密文响应时的兜底，明文时用不到） ----------------
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
    """运行时生成 S 盒与 GF 乘法表"""
    mul = dict((n, [_gf_mul(x, n) for x in range(256)]) for n in (2, 3, 9, 11, 13, 14))
    exp, log = [0] * 256, [0] * 256
    x = 1
    for i in range(255):
        exp[i] = x
        log[x] = i
        x = _gf_mul(x, 3)
    sbox, inv = [0] * 256, [0] * 256
    for a in range(256):
        v = 0 if a == 0 else exp[(255 - log[a]) % 255]
        s, t = v ^ 0x63, v
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
    """纯标准库 AES-128 加解密（不依赖 requests 之外的任何三方库）"""

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
    """139 响应解密：base64(IV + AES-128-CBC(密文))"""
    try:
        ci = _yun_ci()
        raw = base64.b64decode(''.join(text.split()))
        out = b''
        prev = raw[:16]
        for i in range(16, len(raw), 16):
            blk = ci.decrypt_block(raw[i:i + 16])
            out += bytes([blk[j] ^ prev[j] for j in range(16)])
            prev = raw[i:i + 16]
        n = out[-1]
        if 1 <= n <= 16 and out[-n:] == bytes([n]) * n:
            out = out[:-n]
        return out.decode('utf-8', 'replace')
    except Exception:
        return ''


def parse_share(text):
    """从老王粘贴的任意文本里抽出（链接ID, 提取码）
    认这些写法：#/w/i/<ID>、整条 https 链接、只给 <ID>、以及后面的「提取码: abcd」。"""
    s = (text or '').strip()
    if not s:
        return '', ''
    pwd = ''
    m = re.search(r'(?:提取码|密码|pwd|passwd|code)\s*[:：=]?\s*([0-9A-Za-z]{4})', s)
    if m:
        pwd = m.group(1)
    link = ''
    m = re.search(r'#/w/i/([0-9A-Za-z_-]+)', s)
    if m:
        link = m.group(1)
    else:
        m = re.search(r'https?://[^\s]*?([0-9A-Za-z_-]{10,})/?\s*$', s)
        if m:
            link = m.group(1)
        else:
            m = re.search(r'^\s*([0-9A-Za-z_-]{6,})\s*$', s.splitlines()[0] if s else '')
            if m:
                link = m.group(1)
    return link, pwd


class Spider(Spider):
    def getName(self):
        return '我的节目2'

    def init(self, extend=''):
        self.yun = Yun()
        self.yun.load_sources(extend)

    def homeContent(self, filter):
        return self.yun.homeContent()

    def homeVideoContent(self):
        return {'list': []}

    def categoryContent(self, tid, page, filter, ext):
        return self.yun.categoryContent(tid)

    def detailContent(self, ids):
        return self.yun.detailContent(ids)

    def searchContent(self, key, quick, page='1'):
        return {'list': []}

    def playerContent(self, flag, pid, vipFlags):
        return self.yun.playerContent(pid)

    def destroy(self):
        return '正在Destroy'


class Yun:
    def __init__(self):
        self.name = '我的节目2'
        self.sources = []          # [{name, link, pwd}]
        self.tree = None           # [{tid, name, items}]
        self.tree_ts = 0
        self.index = {}            # vid -> item
        self.q_cache = {}
        self.p_cache = {}          # (link,pwd) -> 目录清单

    # ---------------- 配置 ----------------
    def load_sources(self, extend):
        """来源优先级：站点 ext.sources（JSON 文本/数组）> 本文件 YUN139_SOURCES"""
        srcs = None
        try:
            if isinstance(extend, dict):
                srcs = extend.get('sources')
            elif isinstance(extend, str) and extend.strip():
                s = extend.strip()
                if s.startswith('{') or s.startswith('['):
                    srcs = json.loads(s).get('sources') if s.startswith('{') else json.loads(s)
        except Exception:
            srcs = None
        if not isinstance(srcs, list) or not srcs:
            srcs = YUN139_SOURCES
        out = []
        for it in srcs:
            if not isinstance(it, dict):
                link, pwd = parse_share(str(it))
                nm, sub, mg = '', '', YUN139_MERGE_DEFAULT
            else:
                nm = (it.get('name') or '').strip()
                link, pwd = parse_share(it.get('link') or it.get('dir') or it.get('url') or '')
                pwd = (it.get('pwd') or it.get('passwd') or pwd or '').strip()
                sub = (it.get('sub') or it.get('folder') or '').strip()
                try:
                    mg = int(it.get('merge', YUN139_MERGE_DEFAULT))
                except Exception:
                    mg = YUN139_MERGE_DEFAULT
            if not link:
                continue                     # 没填链接的来源直接跳过，不影响别的
            out.append({'name': nm or ('云盘' + str(len(out) + 1)),
                        'link': link, 'pwd': pwd, 'sub': sub, 'merge': mg})
        self.sources = out

    # ---------------- 列一个目录（带分页与缓存） ----------------
    def list_dir(self, link, pwd, pca):
        ck = '%s|%s|%s' % (link, pwd, pca)
        if ck in self.p_cache:
            return self.p_cache[ck]
        ca_all, co_all = [], []
        seen = set()
        pca_in = pca or 'root'
        bnum = 1
        for _p in range(YUN139_MAXPAGES):
            data = self._post(link, pwd, pca_in, bnum, bnum + YUN139_PAGE - 1)
            ca = data.get('caLst') or []
            co = data.get('coLst') or []
            if not ca and not co:
                break
            for c in ca:
                k = c.get('caID') or ''
                if k and k not in seen:
                    seen.add(k)
                    ca_all.append(c)
            for f in co:
                k = f.get('coID') or ''
                if k and k not in seen:
                    seen.add(k)
                    co_all.append(f)
            if len(ca) + len(co) < YUN139_PAGE:
                break
            bnum += YUN139_PAGE
        out = {'caLst': ca_all, 'coLst': co_all}
        self.p_cache[ck] = out
        return out

    def _post(self, link, pwd, pca, bnum, enum):
        """列一个目录：实测明文请求即可；若官方改成密文，自动落 AES 解密"""
        body = json.dumps(
            {'getOutLinkInfoReq': {'account': '', 'linkID': link,
                                   'passwd': pwd or '', 'caSrt': 0, 'coSrt': 1,
                                   'srtDr': 1, 'bNum': bnum, 'eNum': enum,
                                   'pCaID': pca},
             'commonAccountInfo': {'account': '', 'accountType': 1}},
            ensure_ascii=False)
        for mode in (0, 1):
            try:
                payload = aes_encrypt(body).encode('ascii') if mode else body.encode('utf-8')
                r = requests.post(YUN139_LIST, data=payload, headers=YUN139_HDR, timeout=12)
                t = r.text.strip()
                if not t.startswith('{'):
                    t = aes_decrypt(t)
                js = json.loads(t)
                if str(js.get('resultCode')) == '0':
                    return js.get('data') or {}
                break        # 明文拿到合法响应但不是成功码，就不必再试加密体
            except Exception:
                continue
        return {}

    # ---------------- 扫目录树：每个「有节目的目录」生成一个栏目 ----------------
    @staticmethod
    def _files(data, dirname):
        items = []
        for f in data.get('coLst') or []:
            suf = (f.get('coSuffix') or '').lower()
            if suf not in YUN139_KEEP:
                continue
            pu = f.get('presentURL')
            if not pu:
                continue          # 还没转好就先不显示，转好会自动出现
            items.append({'id': f.get('coID') or '',
                          'name': re.sub(r'\.[^.]+$', '', f.get('coName') or ''),
                          'url': pu,
                          'pic': f.get('thumbnailURL') or f.get('bthumbnailURL') or '',
                          'size': int(f.get('coSize') or 0),
                          'dir': dirname,
                          'audio': suf in YUN139_AUDIO_EXTS})
        return items

    # ---------------- 扫目录树 ----------------
    def _resolve_start(self, src):
        """把来源定位到起始目录：'sub' 填了就从分享根目录里挑出那个子文件夹"""
        sub = (src.get('sub') or '').strip()
        if not sub:
            return ''
        try:
            for c in self.list_dir(src['link'], src['pwd'], '').get('caLst') or []:
                if (c.get('caID') or '') == sub or (c.get('caName') or '').strip() == sub:
                    return c.get('caID') or ''
        except Exception:
            pass
        return ''

    def _mk_items(self, si, raw):
        """编上唯一节目号并登记进索引（同一文件只算一次）"""
        items, seen = [], set()
        for it in raw:
            cid = it.get('id') or ''
            if not cid or cid in seen:
                continue
            seen.add(cid)
            it2 = dict(it)
            it2['id'] = 'y2_%d_%s' % (si, cid)
            items.append(it2)
            self.index[it2['id']] = it2
        return items

    def _collect(self, link, pwd, pca, depth, name):
        """把一个目录连同其子目录里的可播放文件全收上来（子文件夹内容并入一栏）"""
        try:
            data = self.list_dir(link, pwd, pca)
        except Exception:
            return []
        items = self._files(data, name)
        if depth > 1:
            for c in data.get('caLst') or []:
                cid = c.get('caID') or ''
                if cid:
                    items += self._collect(link, pwd, cid, depth - 1,
                                           c.get('caName') or name)
        return items

    def _scan_split(self, out, si, link, pwd, start, nm0, depth, data):
        """子文件夹各自成一栏（我的节目.py 的老规矩）"""
        if depth <= 0:
            return
        got = self._files(data, nm0)
        if got:
            out.append({'tid': 'y2_%d_%s' % (si, start or 'root'),
                        'name': YUN139_ALIAS.get(nm0, nm0),
                        'items': self._mk_items(si, got)})
        for c in data.get('caLst') or []:
            cn = c.get('caName') or ''
            cid = c.get('caID') or ''
            if not cid:
                continue
            self._scan_split(out, si, link, pwd, cid, cn, depth - 1,
                             self.list_dir(link, pwd, cid))

    def scan(self):
        out = []
        self.index = {}
        for si, src in enumerate(self.sources):
            link, pwd, nm0 = src['link'], src['pwd'], src['name']
            start = self._resolve_start(src)
            try:
                root = self.list_dir(link, pwd, start)
            except Exception:
                root = {}
            if not start and not (root.get('caLst') or root.get('coLst')):
                continue        # 链接都取不到就跳过这个来源，免得电视端干等
            try:
                if src.get('merge', 1):
                    items = self._mk_items(si, self._collect(link, pwd, start,
                                                             YUN139_DEPTH, nm0))
                    if items:
                        out.append({'tid': 'y2_%d_all' % si, 'name': nm0,
                                    'items': items})
                else:
                    self._scan_split(out, si, link, pwd, start, nm0,
                                     YUN139_DEPTH, root)
            except Exception:
                pass
        self.tree = out
        self.tree_ts = time.time()
        return out

    def tree_cached(self):
        now = time.time()
        if self.tree is not None and now - self.tree_ts < YUN139_TREE_TTL:
            return self.tree
        return self.scan()

    # ---------------- 对外四件套 ----------------
    def homeContent(self):
        classes = [{'type_id': c['tid'], 'type_name': c['name']} for c in self.tree_cached()]
        return {'class': classes, 'filters': {}}

    def categoryContent(self, tid):
        for c in self.tree_cached():
            if c['tid'] == tid:
                vlist = [{'vod_id': it['id'], 'vod_name': it['name'],
                          'vod_pic': it['pic'],
                          'vod_remarks': self.fmt_size(it['size'])}
                         for it in c['items']]
                return {'list': vlist, 'page': 1, 'pagecount': 1,
                        'limit': len(vlist), 'total': len(vlist)}
        return {'list': [], 'page': 1, 'pagecount': 1, 'limit': 0, 'total': 0}

    def detailContent(self, ids):
        vid = ids[0]
        it = self.index.get(vid)
        if not it:
            self.scan()
            it = self.index.get(vid)
        if not it:
            return {'list': []}
        # 音频是纯音轨 m3u8，没有 RESOLUTION，不必探测清晰度（省一次请求）
        qtxt = '音频' if it.get('audio') else self.quality(vid, it['url'])
        show = '%s[%s]' % (qtxt or '正片', self.fmt_size(it['size']))
        return {'list': [{
            'vod_id': vid,
            'vod_name': it['name'],
            'vod_pic': it['pic'],
            'type_name': it.get('dir', ''),
            'vod_year': '',
            'vod_area': '',
            'vod_actor': '',
            'vod_director': '',
            'vod_remarks': qtxt or self.fmt_size(it['size']),
            'vod_content': '移动云盘 · %s · %s' % (it.get('dir', ''), self.fmt_size(it['size'])),
            'vod_play_from': self.name,
            'vod_play_url': '%s$%s' % (show, vid),
        }]}

    def quality(self, vid, url):
        """拉一次 master 播放列表读最高档（约 2KB，很快）；失败返回空串由调用方降级"""
        if vid in self.q_cache:
            return self.q_cache[vid]
        txt = ''
        try:
            r = requests.get(url, headers={'User-Agent': UA, 'Accept': '*/*'}, timeout=10)
            tops = [int(m) for m in re.findall(r'RESOLUTION=\d+x(\d+)',
                                               r.content.decode('utf-8', 'replace'))]
            txt = ('%dP' % max(tops)) if tops else ''
        except Exception:
            txt = ''
        self.q_cache[vid] = txt
        return txt

    def playerContent(self, pid):
        it = self.index.get(pid)
        if not it or not it.get('url'):
            self.scan()             # 地址带 8 小时签名，缓存里没有就重新扫一遍
            it = self.index.get(pid)
        if it and it.get('url'):
            return {'url': it['url'], 'parse': 0, 'jx': 0, 'header': {'User-Agent': UA}}
        return {'url': '', 'parse': 0, 'jx': 0, 'header': {'User-Agent': UA}}

    @staticmethod
    def fmt_size(n):
        try:
            n = float(n)
        except Exception:
            return ''
        if n >= 1073741824:
            return '%.1f GB' % (n / 1073741824.0)
        if n >= 1048576:
            return '%.1f MB' % (n / 1048576.0)
        if n > 0:
            return '%d KB' % (n / 1024)
        return ''


if __name__ == '__main__':
    pass
