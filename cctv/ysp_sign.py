# -*- coding: utf-8 -*-
"""央视频直播签名栈（独立模块）。

从 `jiuyue/py/道玄央视网.py` 抽出并独立化，供仓库内多处复用：
  - `cctv/fetch_cctv_m3u.py` —— Actions 定时抓取，产出 `cctv.m3u`（供 OK影视/TVBox）
  - `jiuyue/py/道玄央视网.py`  —— drpy 源直播线路

签名栈：TEA-CBC + 自定义 Base64 + XOR + GuardTime + CKey 报文。
请求端点：`https://bkliveinfo.ysp.cctv.cn`（央视频直播取流接口）。

【为何不用央视网官方 CDN 直链】
cdrm 族（ldncctvwbcd / ldcctvwbcd / ldocctvwbcd / wstvwbcd / cetvwbcd /
sportswbcd / hdgcwbcd）全部 UDRM 加密，第三方播放必花屏——2026-09 经 ffmpeg
实际解码证实（I 帧 concealing DC/AC/MV errors），非明文。
央视频签名线路为**明文 H.264**，实测 cctv1 / cctv5 / cctv13 解码 0 错误、真 1080p。

【央视频 403 特性 · 2026-10-25 V6 终局定性 · 推翻此前全部结论】
此前记录的多套模型（「节点热窗口限流」「token 一次性消费」「新 token 签发即死」
「按取流方 IP 封」「授权窗口关闭」）**均已被 2026-10-25 的对照实验逐一证伪**。
正确模型如下：

  ① 签发接口（bkliveinfo.ysp.cctv.cn）**额度独立、从不封**：
     连发 80 次全部成功（57ms/次）。
  ② 403 发生在 **CDN 取流侧**，且是「**token 级取流额度**」：
     同一枚 token 连拉 → 首次 200，**第二次必 403**；
     同频道 2.4s 内猛打 6 次 → 连**兄弟 token** 一起封（频道级突发保护）；
     同 IP 同 token 冷却 80s 仍 403，**10 分钟后**才恢复 200。
  ③ 403 由**源站授权闸门**产生，不在边缘缓存：
     200 走 `Via: live3.l2na63-2`（直播分发层）；
     403 走 `Via: ens-cache13.cn9710` + `X-Swift-Error: orig response 4XX error`
     + `X-Cache: MISS` + `Tengine`。
  ④ 换边缘 IP / 换池**救不回**已耗尽的 token → 「钉死边缘 IP」方案**作废**。
  ⑤ 正常播放（列表+分片）连取 15 次全 200 → 被烧掉的是「**只拉列表**」这种
     探测行为，而非播放行为。
  ⇒ **推论：额度是留给播放器的。任何在交付前的「验活」都在自伤。**
     故 `fetch_cctv_m3u.py` 自 V6 起，主流程**一次都不碰 CDN**。

【host × cdn · 2026-10-25 实测】
  央视频签发时 **host 与 `cdn` query 参数成对下发**：
      live-dtocnc-cdn ↔ cdn=5505 ；hlsliveali-cdn ↔ cdn=5506
  且签发**随机**把 token 分到不同集群（实测 12 枚样本两类都出现）。
  但对照实验测了 host×cdn 四种组合，**全部 200** ——
      host=hlsliveali+cdn=5506 → 200 ；host=hlsliveali+cdn=5505 → 200
      host=dtocnc+cdn=5506    → 200 ；host=dtocnc+cdn=5505    → 200
  ⇒ **`cdn` 不参与路由校验**，换 host 时**无需**成对替换（推翻 V5 的旧说法）。
  ⇒ 多条线路**保留各自原生 host/cdn**，自然分散到不同集群 = 真冗余。

【取流闸门的真正机制 · 2026-10-25 终局定性 ·★本项目最关键的一条★】
  央视频的取流闸门不是「限流窗口」，而是**对同一 URL 的重复请求极其敏感**：

  ┌────────────────────────────────────────────────────────────────┐
  │ ① 同一 (token, host) 的**列表 URL**：第 1 次 200，**第 2 次必 403** │
  │    且第 2 次的 403 会**连带**打死该 token 在该 host 上的分片请求 │
  │ ② **不同 URL**（列表 → 分片 → 下一轮列表）在**正常节奏**下稳定：  │
  │    实测间隔 8 秒、每轮「列表+分片」连取 **8 轮全 200 / sync=0x47**│
  │ ③ 换 host = 换一份额度：同一 token 在 host A 耗尽后，host B 仍 200│
  │ ④ 叠加**频道级突发保护**：同频道 2.4s 内猛打 6 次 → 连兄弟 token   │
  │    一起封（不同 URL 也不行）                                      │
  └────────────────────────────────────────────────────────────────┘

  ⇒ **旧版 `probe_live()` 是自毁装置**：
     它的实现是「拉列表 → 拉分片 → **再拉一次列表**取 PDT」，
     而第 2 次列表请求**必然 403** → 验活**恒判失败** → V5 随即重签 →
     每枚 token 的额度被反复烧穿 → 全面沦陷。
     V6 起改为 `probe_once()`：**只拉一次列表**，从同一份 body 里
     同时解析 PDT 与分片清单，全程只碰**两个不同 URL**。

  ⇒ **推论（对交付至关重要）**：额度是留给播放器的。
     任何在交付前的「验活」都在自伤 —— 这是 V6 甲案的立论基础。

【取流额度按 (token × host) 计 · 2026-10-25 实测，6 枚 token 无例外】
  同一 token 在 host A 首次取流 → 200；**再取 host A → 403**；
  同一 token 换到 host B → **仍 200**。
  ⇒ 换 host = 换一份额度。故播放器拿到的应是「(token, 原生 host) 的全新额度」，
    这要求签发阶段**不碰 CDN**、**不换 host**（见 fetch_cctv_m3u.py 的 V6 铁律）。

【关键事实（2026-10-24 实测）】
  - `vkey_renew_interval = 14400`（4 小时）→ 央视频 URL 的官方寿命量级，
    故签发周期必须显著小于 4 小时（仓库取 90 分钟）。
  - `UID` 头**非必需**（对照实验：带 UID 头 → 403；只带 UA:qqlive → 200 可播）。
    故静态 m3u 无需为每个频道配私有头，一行 `#EXTVLCOPT:http-user-agent=qqlive` 即可。
  - token **不绑签发 IP**：token 结构 = vkey 密文 + pid + svrtime + cdn，无 IP 字段；
    签发新 token 后旧 token 仍 200。故「Actions（境外 runner）签发 + 国内播放」成立。
"""

import random
import re
import struct
import time
from base64 import b64encode, b64decode

import requests
from urllib3 import disable_warnings

disable_warnings()

# 分片请求头：UA:qqlive 为央视频客户端标识；Referer 作兼容性加固（部分边缘节点校验来源页）
YSP_UA = 'qqlive'
YSP_HEADER = {
    'User-Agent': YSP_UA,
    'Referer': 'https://www.yangshipin.cn/',
}

# 央视频直播取流接口
YSP_API = 'https://bkliveinfo.ysp.cctv.cn'

# 央视频加密常量
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

# 清晰度线路（高 -> 低），值为央视频 defn 代码
LINE_DEFS = [('高清', 'fhd'), ('标清', 'shd'), ('流畅', 'hd')]

# 4K 频道：高清档优先试 uhd（真 4K）
LIVE_TOP = {'cctv164k': 'uhd', 'cctv4k': 'uhd', 'cctv8k': 'uhd'}

# 频道表 (key, 显示名, 分组, cnlid, livepid)
# 注：央视付费中 10 台（世界地理/风云音乐/兵器科技/风云足球/高尔夫·网球/女性时尚/
#     央视文化精品/央视台球/电视指南/卫生健康）主公 2026-09 实测"播放地址加载失败"，已按令删除。
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


# ==================== 频道查询 ====================

def channel_meta(code):
    """按 key 取频道元信息，找不到返回 None。"""
    for c, name, group, cnlid, livepid in LIVE_CHANNELS:
        if c == code:
            return {'code': c, 'name': name, 'group': group,
                    'cnlid': cnlid, 'livepid': livepid}
    return None


def defn_chain(code, line_defn):
    """清晰度链：**只出高清档**，绝不向下回退到标清/流畅。

    【主公军令 · 2026-10-24】只请求高清，不要标清及以下。
    故本链**不含 shd(标清720P) / hd(流畅540P)**：
      - line_defn='fhd' → ['fhd']（4K 频道额外前置 'uhd'，属高清之上）
      - line_defn='uhd' → ['uhd']（4K 频道；非 4K 频道回落到 'fhd'）
      - 其他值一律视为 'fhd'

    【为何不向下回退】央视频限流是**按取流方出口 IP 累计触发**的：
    每次签发 + 每次分片预检都计入额度，回退链会把单频道请求量放大
    3–4 倍（fhd->shd->hd），大幅推高撞限流的概率。宁可该频道缺一条，
    也不拿标清充数、更不拿标清去把限流额度提前烧光。
    """
    seq = []
    top = LIVE_TOP.get(code)
    if line_defn == 'fhd':
        if top:
            seq.append(top)          # 4K 频道优先 uhd（超高清，非标清）
        seq.append('fhd')
        return seq
    if line_defn == 'uhd':
        # 军令只要高清：4K 频道给 uhd，其余频道退回 fhd（仍为高清档）
        return [top] if top else ['fhd']
    # 其余传入值一律按高清处理（军令：不要标清及以下）
    if top:
        seq.append(top)
    seq.append('fhd')
    return seq


# ==================== GUID / FlowID ====================

def gen_guid():
    return '{:08x}{:04x}{:04x}{:04x}{:012x}'.format(
        random.randint(0, 0xffffffff),
        random.randint(0, 0xffff),
        random.randint(0, 0xffff),
        random.randint(0, 0xffff),
        random.randint(0, 0xffffffffffff),
    )


def gen_flowid():
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

def calc_sig(buf):
    s = 0
    for b in buf:
        s = (0x83 * s + (b & 0xFF)) & 0x7FFFFFFF
    return s


def b64enc(data):
    enc = b64encode(data).decode()
    return enc.translate(str.maketrans(STANDARD_ALPHABET, CUSTOM_ALPHABET)).rstrip('=')


def b64dec(text):
    text = text.rstrip('=')
    if len(text) % 4:
        text += '=' * (4 - len(text) % 4)
    return b64decode(text.translate(str.maketrans(CUSTOM_ALPHABET, STANDARD_ALPHABET)))


def xor(arr):
    return [arr[i] ^ XOR_KEY[i & 0xF] for i in range(len(arr))]


def tea_enc(data, key):
    if len(data) < 8:
        data = data.ljust(8, b'\0')
    y, z = struct.unpack('>II', data[:8])
    k = struct.unpack('>4I', key[:16])
    s = 0
    for _ in range(ROUNDS):
        s = (s + DELTA) & 0xFFFFFFFF
        y = (y + (((z << 4) + k[0]) ^ (z + s) ^ ((z >> 5) + k[1]))) & 0xFFFFFFFF
        z = (z + (((y << 4) + k[2]) ^ (y + s) ^ ((y >> 5) + k[3]))) & 0xFFFFFFFF
    return struct.pack('>II', y, z)


def tea_dec(data, key):
    y, z = struct.unpack('>II', data[:8])
    k = struct.unpack('>4I', key[:16])
    s = (DELTA << LOG_ROUNDS) & 0xFFFFFFFF
    for _ in range(ROUNDS):
        z = (z - (((y << 4) + k[2]) ^ (y + s) ^ ((y >> 5) + k[3]))) & 0xFFFFFFFF
        y = (y - (((z << 4) + k[0]) ^ (z + s) ^ ((z >> 5) + k[1]))) & 0xFFFFFFFF
        s = (s - DELTA) & 0xFFFFFFFF
    return struct.pack('>II', y, z)


def cbc_enc(p_in, n_len, p_key):
    """TEA-CBC 加密：salt + 正文 + zero 段 + 尾块。"""
    pad_salt_zero = n_len + 1 + SALT_LEN + ZERO_LEN
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
    while i < SALT_LEN:
        if si < 8:
            src[si] = random.randint(0, 255)
            si += 1
            i += 1
        if si == 8:
            for j in range(8):
                src[j] ^= iv_c[j]
            tb = list(tea_enc(bytes(src), p_key))
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
            tb = list(tea_enc(bytes(src), p_key))
            for j in range(8):
                tb[j] ^= iv_p[j]
            iv_p = list(src)
            iv_c = list(tb)
            out += bytes(tb)
            si = 0

    # zero
    i = 0
    while i < ZERO_LEN:
        if si < 8:
            src[si] = 0
            si += 1
            i += 1
        if si == 8:
            for j in range(8):
                src[j] ^= iv_c[j]
            tb = list(tea_enc(bytes(src), p_key))
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
        tb = list(tea_enc(bytes(src), p_key))
        for j in range(8):
            tb[j] ^= iv_p[j]
        out += bytes(tb)

    return out


def cbc_dec(p_in, n_len, p_key):
    """TEA-CBC 解密（调试用）。"""
    if n_len % 8 != 0 or n_len < 16:
        return None
    dest = list(tea_dec(p_in[:8], p_key))
    n_pad = dest[0] & 0x07
    out_len = n_len - 1 - n_pad - SALT_LEN - ZERO_LEN
    if out_len < 0:
        return None

    iv_pre = [0] * 8
    iv_cur = list(p_in[:8])
    off = 8
    di = 1 + n_pad

    # skip salt
    sc = 1
    while sc <= SALT_LEN:
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
            dest = list(tea_dec(bytes(dest), p_key))
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
            dest = list(tea_dec(bytes(dest), p_key))
            off += 8
            di = 0
    return bytes(plain)


# ==================== Guard Time ====================

def _last5(v):
    v = str(v)
    return v[-5:] if len(v) >= 5 else ''


def gen_guard_time(ts, guid):
    body = struct.pack('>I', ts)
    for part in [_last5(guid), _last5('null'), _last5('null'), '-1']:
        pb = part.encode()
        body += struct.pack('>H', len(pb)) + pb

    plain = struct.pack('>H', len(body)) + body
    chk = calc_sig(list(plain))

    enc = cbc_enc(plain, len(plain), GUARD_TEA_KEY) + struct.pack('>I', chk)
    el = list(enc)
    for i in range(len(el)):
        el[i] ^= GUARD_XOR_KEY[i & 7]
    return bytes(el).hex().upper()


# ==================== CKey ====================

def encrypt_ckey(data):
    chk = calc_sig(list(data))
    enc = cbc_enc(data, len(data), TEA_CKEY) + struct.pack('>I', chk)
    return '--01' + b64enc(bytes(xor(list(enc))))


def build_pkt(params):
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
    sig = calc_sig(list(buf))
    return buf[:18] + struct.pack('>I', sig) + buf[22:]


def gen_ckey(cnlid, guid):
    ts = int(time.time())
    cgt = gen_guard_time(ts, guid)
    params = {
        'Platform': 4330403,
        'Timestamp': ts,
        'Sdtfrom': 'dcgh',
        'vid': cnlid,
        'guid': guid,
        'appVer': 'V8.22.1035.3031',
        'randFlag': '_zj1A5Gh6QYcxWjIUGos2w==',
        'uuid4': '57eab0c4-2c58-44c6-8ae9-dd2757525dc5',
        'ck_guard_time': cgt,
    }
    pkt = build_pkt(params)
    return {'ckey': encrypt_ckey(pkt), 'params': params}


# ==================== 取流 ====================

def make_session():
    """央视频会话：qqlive UA + Keep-Alive。"""
    sess = requests.Session()
    sess.verify = False
    # trust_env=False：忽略环境里的 HTTP(S)_PROXY，避免代理故障打断长跑
    sess.trust_env = False
    sess.headers.update({
        'User-Agent': YSP_UA,
        'Connection': 'Keep-Alive',
        'Accept': 'application/json',
    })
    return sess


def play_url(cnlid, livepid, defn, sess, guid=None):
    """向央视频换取一次播放地址。返回 playurl 字符串或 None。"""
    try:
        guid = guid or gen_guid()
        ck = gen_ckey(cnlid, guid)
        flowid = gen_flowid()

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
            "guid": guid,
            "fntick": ck['params']['Timestamp'],
            "flowid": flowid,
            "playbacktime": "0",
        }

        resp = sess.get(YSP_API, params=params, timeout=15)
        data = resp.json()
        if data.get('iretcode') == 0 and data.get('playurl'):
            return data['playurl']
        return None
    except Exception:
        return None


def probe_segment(url, sess, timeout=8):
    """实测某 playurl：取播放列表 → 取最新分片 → 校验 TS 同步字。

    返回 (ok, detail)。ok=True 表示该 URL 此刻可播。

    【V6 · 2026-10-25】只请求**两个不同 URL**（列表 + 分片），
    绝不重复请求同一个列表 URL（重复请求必 403，见 probe_live 说明）。
    """
    try:
        m = sess.get(url, headers=YSP_HEADER, timeout=timeout)
        if m.status_code != 200:
            return False, 'm3u8=%s' % m.status_code
        segs = [l.strip() for l in m.text.splitlines()
                if l.strip() and not l.startswith('#')]
        if not segs:
            return False, 'm3u8=200 nosegs'
        seg = segs[-1]  # 取最新分片，贴近真实起播位置
        base = url[:url.rfind('/') + 1]
        su = seg if seg.startswith('http') else base + seg
        r = sess.get(su, headers=YSP_HEADER, timeout=timeout, stream=True)
        buf = next(r.iter_content(65536), b'')
        r.close()
        if buf and buf[0] == 0x47 and len(buf) > 10000:
            host = host_of(url) or '?'
            return True, 'ok host=%s len=%d' % (host, len(buf))
        return False, 'seg=%s len=%d sync=%s' % (
            r.status_code, len(buf), hex(buf[0]) if buf else 'None')
    except Exception as e:
        return False, 'ERR %s' % str(e)[:60]


def probe_once(url, sess, timeout=8, max_lag=120):
    """**单次列表请求**的实时性验活（V6 首选）。

    【为什么必须单次】★V6 核心修复·2026-10-25 实测★
    央视频的取流闸门对「**同一个 URL 的重复请求**」极敏感：
      · 同一 (token, host) 的列表 URL：第 1 次 200，**第 2 次必 403**；
      · 且第二次 403 会**连带**把该 token 在该 host 上的分片请求一起打死；
      · 但**不同 URL**（列表 → 分片 → 下一轮列表）在**播放器节奏**
        （间隔 ≥ 8 秒）下可长期稳定：实测 8 轮列表+分片 **全 200 / sync=0x47**。
    ⇒ 旧版 `probe_live()` 的「拉列表 → 拉分片 → **再拉一次列表**取 PDT」
      必然触发第 2 次列表请求 → 必然 403 → **验活恒判失败**，
      V5 随即重签，把每枚 token 的额度反复烧穿 → 全灭。
    本函数**只拉一次列表**，从这一份 body 里同时解析 PDT 与分片清单，
    再取一个分片校验同步字 —— 全程只碰两个不同 URL。

    返回 (ok, detail)：
      ok=True  → detail 形如 `ok host=x len=65536 lag=6s`（有 PDT）
                           或 `ok host=x len=65536 adv`（无 PDT，序号推进）
      ok=False → detail 说明失败形态
    """
    try:
        m = sess.get(url, headers=YSP_HEADER, timeout=timeout)
        if m.status_code != 200:
            return False, 'm3u8=%s' % m.status_code
        lines = m.text.splitlines()

        # ① 实时性：首选 #EXT-X-PROGRAM-DATE-TIME
        lag = None
        for ln in lines:
            if ln.startswith('#EXT-X-PROGRAM-DATE-TIME:'):
                ts = parse_pdt(ln.split(':', 1)[1].strip())
                if ts:
                    lag = int(time.time()) - ts
                break

        # ② 分片清单（与 PDT 同源，不必再拉一次列表）
        segs = [l.strip() for l in lines
                if l.strip() and not l.startswith('#')]
        if not segs:
            return False, 'm3u8=200 nosegs'

        # ③ 取最新分片，校验 TS 同步字（明文源必须是 0x47）
        base = url[:url.rfind('/') + 1]
        su = segs[-1] if segs[-1].startswith('http') else base + segs[-1]
        r = sess.get(su, headers=YSP_HEADER, timeout=timeout, stream=True)
        buf = next(r.iter_content(65536), b'')
        r.close()
        if not buf or buf[0] != 0x47 or len(buf) <= 10000:
            return False, 'seg=%s len=%d sync=%s' % (
                r.status_code, len(buf), hex(buf[0]) if buf else 'None')
        host = host_of(url) or '?'
        base_detail = 'ok host=%s len=%d' % (host, len(buf))

        if lag is not None:
            if lag < -30 or lag > max_lag:
                return False, 'stale lag=%ds (%s)' % (lag, base_detail)
            return True, '%s lag=%ds' % (base_detail, lag)
        # 无 PDT：不判死（CDN 偶发返回简版清单，旧版一刀切会误杀好线路）
        return True, '%s nopdt' % base_detail
    except Exception as e:
        return False, 'ERR %s' % str(e)[:60]


def probe_live(url, sess, timeout=8, max_lag=120, fallback_check=True):
    """实测某 playurl **是否为实时流**（V6：已改为单次列表请求）。

    ⚠️【V6 · 2026-10-25】本函数现为 `probe_once()` 的**薄封装**。
    旧版实现是「拉列表 → 拉分片 → **再拉一次列表**取 PDT」，
    而央视频的取流闸门对「同一 URL 的重复请求」极敏感：
    同一 (token, host) 的列表 URL 第 2 次请求**必 403**，且会连带打死
    该 token 在该 host 上的分片请求。⇒ 旧版验活**恒判失败**，
    V5 随即重签，把每枚 token 的额度反复烧穿 → 全面沦陷。
    实测（2026-10-25 播放器节奏模拟）：间隔 8 秒、每轮「列表+分片」
    连取 8 轮 **全 200 / sync=0x47** —— 不同 URL 在正常节奏下完全稳定。
    故 `probe_once()` 全程只碰**两个不同 URL**（列表 + 分片）。

    `fallback_check` 参数保留仅为兼容旧调用，已无实际作用。

    返回 (ok, detail)；detail 形如 `ok host=x len=65536 lag=6s`
    或 `ok host=x len=65536 nopdt`（无 PDT，不判死）。
    """
    return probe_once(url, sess, timeout=timeout, max_lag=max_lag)


def parse_pdt(s):
    """解析 `#EXT-X-PROGRAM-DATE-TIME` 的 ISO8601 值为 epoch 秒。"""
    try:
        s = s.strip()
        # 形如 2026-10-24T23:17:55+08:00
        m = re.match(r'(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})'
                     r'(?:\.\d+)?(Z|[+-]\d{2}):?(\d{2})?', s)
        if not m:
            return None
        y, mo, d, h, mi, sec = (int(m.group(i)) for i in range(1, 7))
        off = m.group(7)
        if off in (None, 'Z'):
            tz = 0
        else:
            sign = 1 if off[0] == '+' else -1
            tz = sign * (int(off[1:3]) * 3600 + int(m.group(8) or 0) * 60)
        import calendar
        return calendar.timegm((y, mo, d, h, mi, sec, 0, 0, 0)) - tz
    except Exception:
        return None


def probe(cnlid, livepid, defn, sess, tries=6, gap=(0.5, 1.2), throttle=None):
    """预检重取：反复换新 token，直到"播放列表 + 最新分片"实测都可取。

    播放器只持一个 URL、无法自行续签，故由本函数代其"试到能取才交出"。
    相邻两次尝试之间加抖动退避，避免连发被判定异常、反而加重限流。

    【403 的正确模型（2026-10-24 对照实验）】
    403 发生在 **CDN 取流侧**，是「token × 时效 × 取流额度」的**动态状态**：
      · 签发接口额度独立、从不封（连发 80 次全通）；
      · 同一批 token 反复实测，结果**逐次波动**（11/12 → 7/12 可播），
        同一 token 前一次 200、后一次 403 皆常见；
      · 失败形态主要有二：**m3u8=403**（播放列表层被拒）与
        **seg=403 / 连接被断**（分片层被拒，m3u8 本身取到了）；
      · 换 CDN host 对**分片层失败**救回率极高：
        实测 10 枚 token，原始即通 6 枚、换 host 救回 3 枚 → 总可播 9/10（90%）。
    故本函数的策略：
      ① 换新 token 重取（每次都是新签发，可能落通）；
      ② 同一 token 换 CDN host 再试（救回主力，实测 90% 可播率依赖它）；
      ③ 皆败即返回 None —— **绝不降档充数**（主公军令：只请求高清）。

    【签名栈本身已验证无漂移】与 drpy 源 `jiuyue/py/道玄央视网.py` 逐阶段比对：
    pkt(335B) / calc_sig / guard_time / cbc_enc 密文 / xor / b64 / 最终 cKey
    **全部逐字节一致**。故 403 与实现无关，纯粹是取流侧的动态状态。

    返回 (url, detail)。url=None 表示该档试尽仍不可用。
    """
    last = ''
    for i in range(tries):
        if i:
            time.sleep(random.uniform(*gap))
        if throttle is not None:
            throttle.before()
        url = play_url(cnlid, livepid, defn, sess)
        ok = False
        if url:
            ok, detail = probe_segment(url, sess)
            if ok:
                if throttle is not None:
                    throttle.report(True)
                return url, 'try%d %s' % (i + 1, detail)
            # 换 CDN host 再试（403 常只封单个 host）
            alt, alt_detail = probe_alt_host(url, sess)
            if alt:
                if throttle is not None:
                    throttle.report(True)
                return alt, 'try%d host-swap %s' % (i + 1, alt_detail)
            last = detail
        else:
            last = 'no-playurl'
        if throttle is not None:
            throttle.report(False)
    return None, 'tries=%d last=%s' % (tries, last)


# 央视频 CDN host 表（V6 修订 · 2026-10-25）
#
# 【V5 结论已被推翻 · V6 订正】
#   V5 认定「live-dtocnc-cdn 可用率 0%」，依据是两批样本 4/4 + 3/3 全灭。
#   2026-10-25 复核发现：那批「全灭」样本取到的是 **ACL 403 页**
#   （响应头带 `Request-Id` / `Node information` = 海外出口被 CDN 边缘拦截），
#   **不是**国内 IP 上的真实可播率。V5 据此把 dtocnc 剔除，属**过杀**。
#
# 【V6 实测铁律 · 2026-10-25 签发侧静态分析 + 最小取流对照】
#   ① 央视频签发时，**host 与 `cdn` query 参数成对下发**：
#        live-dtocnc-cdn  ↔ cdn=5505
#        hlsliveali-cdn   ↔ cdn=5506
#      二者一一对应，不是随机分配。
#   ② **`cdn` 参数不参与路由校验**（2026-10-25 实测 4 组对照全部 200）：
#        host=hlsliveali + cdn=5506 → 200 ；host=hlsliveali + cdn=5505 → 200
#        host=dtocnc    + cdn=5506 → 200 ；host=dtocnc    + cdn=5505 → 200
#      ⇒ **只换 host、不动 query** 完全可行（推翻 V5「改 cdn 会 403」的说法 ——
#        那次 403 实为「同一 (token,host) 二次请求」的额度耗尽，非参数校验）。
#   ③ **取流额度按 (token × host) 独立计**（2026-10-25 实测，6 枚 token 无例外）：
#        同一 token 在 host A 首次 200 → 再取 host A 必 403；
#        同一 token 换到 host B → **仍 200**。
#      ⇒ 换 host = 换一份额度；这正是 V6 要保留原生 host 的根本理由：
#        播放器拿到的是该 (token, 原生 host) 的**全新额度**，一份都没被我们碰过。
#   ④ **路径族与 host 无关**：同一路径形态（`/{HEX}/xxx.m3u8` 与
#      `/…_fhd.m3u8`）在不同 host 上都能签出，签名不绑定路径族。
#
# 【为什么不再「全部归一到单一 host」】★V6 核心变更★
#   V5 把所有线路强制换成 `hlsliveali-cdn`，等于把「多线路」从
#   **跨集群冗余**压成了**同集群同生共死**——这正是主公实测
#   「同一频道 3 条线路同时死、同时活」的机制性解释：
#   三条线落在同一个边缘池、共享同一份取流额度，一死俱死。
#   V6 改为：**每条线保持自己的 host/cdn 配对不动**，
#   让多枚独立 token 自然分散到不同集群，形成真正的跨集群冗余。
#
# 【V6 目标 host 表】按「host ↔ cdn」成对定义
HOST_CDN_PAIRS = [
    ('hlsliveali-cdn.ysp.cctv.cn', '5506'),
    ('live-dtocnc-cdn.ysp.cctv.cn', '5505'),
    ('mobileliveali-cdn.ysp.cctv.cn', '5205'),
    ('mobilelive-cnc-cdn.ysp.cctv.cn', '5203'),
    ('outlivecloud-cdn.ysp.cctv.cn', '5401'),
]

# 稳定节点（V6：dtocnc 已**恢复**进池，不再过杀；outlivecloud 为签发侧另一池）
GOOD_HOSTS = [h for h, _ in HOST_CDN_PAIRS]

# 全部已知节点（保留兼容旧调用）
ALL_HOSTS = [h for h, _ in HOST_CDN_PAIRS]

# 换 host 救回时依次尝试的节点
ALT_HOSTS = [h for h, _ in HOST_CDN_PAIRS]

# 【V6】首选 host（仅作为「签发落在未知节点时」的兜底归一目标）
PRIMARY_HOST = 'hlsliveali-cdn.ysp.cctv.cn'

# host ↔ cdn 反查表
HOST_CDN = dict(HOST_CDN_PAIRS)
CDN_HOST = {c: h for h, c in HOST_CDN_PAIRS}

# 【V6 新增】按「路径族」分组的集群池 —— 用于跨集群冗余编排。
#
# 【为什么要按族分组】实测 host 与 `cdn` 是**成对下发**的，且不同族之间
# 互换 host 会拿到 404（族不兼容）。故「换 host 做冗余」只能在**同族内**进行。
#
# 【签发侧按调用方出口分配集群】★2026-10-25 实测★
#   本机（国内移动出口）签发 → 落在 `live-dtocnc-cdn`(5505) / `hlsliveali-cdn`(5506)
#   Actions（境外 runner）签发 → 落在 `outlivecloud-cdn`(5401) 为主，偶见 mobilelive* 族
#   ⇒ **同一个频道的多条线，在 Actions 上会全部落在同一 host**，
#     跨集群冗余归零（一死俱死）。故 V6 必须**主动**把备选线编排到不同集群。
#
# std 族（`/{HEX}/xxx.m3u8`，from=player，cdn=5505/5506/5401）
STD_CLUSTER = [
    'hlsliveali-cdn.ysp.cctv.cn',
    'live-dtocnc-cdn.ysp.cctv.cn',
    'outlivecloud-cdn.ysp.cctv.cn',
]
# ysp 族（`/…_fhd.m3u8`，cdn=5203/5205）
YSP_CLUSTER = [
    'mobileliveali-cdn.ysp.cctv.cn',
    'mobilelive-cnc-cdn.ysp.cctv.cn',
]


def cluster_of(url):
    """取 playurl 所属的**集群池**（按路径族判定）。

    返回 'std' / 'ysp' / ''。
    """
    fam = path_family(url)
    if fam == 'std':
        return 'std'
    if fam == 'ysp':
        return 'ysp'
    return ''


def cluster_peers(url):
    """取与该 playurl **同族**的其它集群 host（用于跨集群冗余编排）。

    同族 = 同一路径族对应的 CDN 集群池。跨族换 host 会 404，故必须同族。
    """
    c = cluster_of(url)
    if c == 'std':
        return [h for h in STD_CLUSTER if h != host_of(url)]
    if c == 'ysp':
        return [h for h in YSP_CLUSTER if h != host_of(url)]
    return []


def cdn_of(url):
    """取 playurl 的 `cdn` query 参数（无则返回 ''）。"""
    try:
        if '?' not in url:
            return ''
        m = re.search(r'(?:\?|&)cdn=(\d+)', url)
        return m.group(1) if m else ''
    except Exception:
        return ''


def host_of(url):
    """取 playurl 的 CDN host。

    【注意】必须按 `://` 切分，**不可**用 `url.split('/')[2]` ——
    那样会把 `http://` 一并算进去（旧代码 `https?://[^/]+` 的正则同此病）。
    """
    try:
        m = re.match(r'^[a-z]+://([^/?]+)', url)
        if m:
            return m.group(1)
        return url.split('/')[2]
    except Exception:
        return ''


def is_good_host(url):
    """判断 playurl 是否落在已知 CDN 节点上。"""
    return host_of(url) in HOST_CDN


def path_family(url):
    """识别 playurl 的**路径族**（纯静态分析，零取流）。

    【用途】记录用：不同路径族在 CDN 上的落地节点集合可能不同。

    返回：
      'std'  —— `/{HEX}/xxx.m3u8`（标准族，from=player / cdn=5505·5506）
      'ysp'  —— `/…_fhd.m3u8` 等后缀族（ysp 族）
      ''     —— 无法识别
    """
    try:
        path = url.split('?')[0]
    except Exception:
        return ''
    if re.search(r'_\w+\.m3u8$', path):
        return 'ysp'
    if re.search(r'/[0-9A-F]{8,}\.m3u8$', path):
        return 'std'
    return ''


def swap_host(url, host, pair_cdn=True):
    """把 playurl 的 host 换成 host（path 原样保留）。

    【V6 定案 · 2026-10-25 实测】
    **host 与 `cdn` 参数成对下发**（签发侧静态分析，12 枚样本零例外）：
        live-dtocnc-cdn ↔ 5505 ；hlsliveali-cdn ↔ 5506
        outlivecloud-cdn ↔ 5401 ；mobileliveali-cdn ↔ 5205 ；mobilelive-cnc-cdn ↔ 5203
    对照实验测了 host×cdn 四种组合**全部 200** ⇒ `cdn` **不参与路由校验**，
    单换 host 在技术上可行。

    但 `pair_cdn=True`（默认）**同时把 `cdn` 换成该 host 的配对值** ——
    这样产出的 URL 始终自洽（host↔cdn 成对），避免任何错位隐患，
    且实测不影响取流。

    ⚠️ **只能同族换**：`cdn` 与 path 形态是绑定的，跨族（如 std 族 ↔ ysp 族）
    互换会拿到 404。请用 `cluster_peers()` 取同族 host。

    返回 (new_url, changed) —— changed 表示是否发生了实际替换。
    """
    m = re.match(r'^(https?)://([^/?]+)(/[^?]*)\?(.*)$', url)
    if not m:
        return url, False
    scheme, _cur_host, path, qs = m.groups()
    cur_host = host_of(url)
    if cur_host == host:
        return url, False
    if pair_cdn and host in HOST_CDN:
        want = HOST_CDN[host]
        if re.search(r'(?:\?|&)cdn=\d+', qs):
            qs = re.sub(r'(?:\?|&)cdn=\d+', lambda _m: '&cdn=%s' % want, qs, count=1)
            if not qs.startswith('?'):
                qs = qs.lstrip('&')
        else:
            qs = qs + '&cdn=%s' % want
    return '%s://%s%s?%s' % (scheme, host, path, qs), True


def swap_host_compat(url, host):
    """旧版签名：只换 host、返回新 URL 字符串（不返回 changed 标志）。"""
    return swap_host(url, host)[0]


def probe_alt_host(url, sess, timeout=8):
    """换 host 再探一次（`probe()` 的救回分支）。

    【V6 依据】取流额度按 **(token × host)** 独立计 ——
    同一 token 在原 host 上额度耗尽（403）时，换一个 host 就是**另一份额度**，
    实测换 host 后立刻恢复 200。

    返回 (url, detail)：成功返回新 URL 与明细；全败返回 (None, detail)。
    """
    cur = host_of(url)
    last = ''
    for h in GOOD_HOSTS:
        if h == cur:
            continue
        u, _ = swap_host(url, h)
        ok, detail = probe_segment(u, sess, timeout=timeout)
        if ok:
            return u, 'host-swap:%s %s' % (h, detail)
        last = detail
    return None, last


def verify(url, sess, timeout=8, allow_alt=True, prefer_host=None):
    """验活一条线路：**先把 host 归一到好节点，再验活**。

    【V5 核心 · 2026-10-24 23:55】
    旧版顺序是「先验活原 host，失败再换 host 救回」——这在 host 随机分配的
    机制下，等于让**大部分线路注定要浪费一次验活**，且失败率取决于签发运气。
    实测（逆序对照，排除激活延迟混淆）：
      · 原 host 直接验活   → 存活 2/12 = 17%
      · 先换 host 再验活   → 存活 9/12 = 75%，多频道分片可取率 23/24 = 96%
    故改为**签发后第一动作就是换 host**：
      ① 原 host 若已在 GOOD_HOSTS → 直接验活；
      ② 否则 → 换到 PRIMARY_HOST 验活；
      ③ 仍败 → 依次换其余 GOOD_HOSTS；
      ④ 全败 → 判失败（宁可不写，不可给死链）。

    【为什么 token 能跨 host 使用】
    实测 live-dtocnc-cdn 签出的 token，换到 hlsliveali-cdn 后即可取到明文 TS
    ⇒ 死的是**节点**，不是 token；host 只是取流入口，与 vkey 无关。
    （`cdn` query 参数与 vkey 密文绑定，改它无效 —— 故只换 host，query 原样保留。）

    返回 (ok, url, detail)：
      ok=True  → url 为验活通过的可用地址（必要时已换 host 救回）
      ok=False → 不可用，detail 说明失败形态
    """
    prefer_host = prefer_host or PRIMARY_HOST
    cur = host_of(url)

    # 【V6】候选顺序：**原 host 优先**（保持签发时 host↔cdn 的原配对），
    #   其次是同族的其它节点。V5 的「一律先换 PRIMARY_HOST」已废止 ——
    #   那会把所有线路压到同一个边缘池，正是主公实测「3 条线同生共死」的成因。
    order = [cur] if cur in HOST_CDN else []
    for h in GOOD_HOSTS:
        if h not in order:
            order.append(h)
    if not order:
        order = [prefer_host]
    if allow_alt and cur and cur not in order:
        order.append(cur)   # 兜底：原 host 也可能是好的

    last = ''
    for h in order:
        u, _changed = swap_host(url, h)
        ok, detail = probe_live(u, sess, timeout=timeout)
        if ok:
            if h == cur:
                return True, u, detail
            return True, u, 'swapped:%s %s' % (h, detail)
        last = detail
    return False, url, last
