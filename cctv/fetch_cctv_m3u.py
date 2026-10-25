#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""签发央视频直播源，产出 `cctv.m3u`（供 OK影视 / TVBox / 酷9 导入）。

【定位】
仓库 `jygldj/dxdszb` 的第四条线路。取流走**央视频签名接口**
（`bkliveinfo.ysp.cctv.cn`，TEA-CBC + GuardTime + CKey），产出**明文 H.264**
直播地址，直接写进 m3u。

【为何不用央视网官方 CDN 直链】
cdrm 族（ldncctvwbcd / ldcctvwbcd / ldocctvwbcd / wstvwbcd / cetvwbcd /
sportswbcd / hdgcwbcd）全部 UDRM 加密，第三方播放必花屏——2026-09 经 ffmpeg
实际解码证实（I 帧 concealing DC/AC/MV errors），非明文。
2026-10-24 另经 TS 层实测复核：cdrm / AV1 两族视频轨均为
`stream_type 0x06`（PRIVATE_DATA）、无 codec 描述符，载荷熵 7.949/8.0、
三档分辨率前 42 字节完全相同 → **密文**，与"AV1 编码"无关。
央视频签名线路才是官方明文源。

【短寿问题的正解】
央视频返回 `vkey_renew_interval = 14400`（4 小时），故固化 URL 最多活 4 小时。
对策：**Actions 每小时重签一轮**（远小于 4 小时），覆盖到 m3u 并推送到 Pages。
播放器只持一个 URL、无法自行续签，故由 Actions 代其周期性续签。

【核心设计：只签发，不验流】★本脚本最重要的一条★
央视频有**两套完全独立的配额**（2026-10-24 实测，二者互不相干）：

  ① **签发接口** `bkliveinfo.ysp.cctv.cn`
     —— 连发 80 次 **80/80 全通**（57ms/次），从不封、不限流。
  ② **CDN 取流侧**（`hlsliveali-cdn.ysp.cctv.cn` 等）
     —— **按出口 IP 累计量触发限流**，403 后需等待恢复。

此前版本对每个频道做「预检重取」（拉 m3u8 + 拉分片，校验 TS 同步字 0x47），
63 频道 × 重试 × 多线 = **数百次 CDN 请求**——正是预检**自己**烧穿了取流额度，
导致跑批后半段集体 403、耗时暴涨、成功率骤降。

故本脚本改为：
  · **主流程只签发**，不碰 CDN —— 63 频道仅 63 次签发接口调用，约 20–40 秒；
  · **抽样体检**（`--sample`，默认 3 个频道）才去 CDN 验一条，用于输出健康
    报告，判断本轮是否整体异常；验流通道与主流程**物理隔离**，不参与筛选；
  · 签发失败的频道**照常保留其余成功频道**，绝不因个别失败拖垮整轮。

【403 的真实性质（2026-10-24 23:00 终局取证 · 已穷尽排除）】
  签发接口**永远**返回 iretcode=0 + 结构完全正常的 playurl，
  但服务端签发的 vkey **可能未在 CDN 侧激活** → m3u8 直接 403
  （`Tengine` 边缘拒绝，`Via: ens-cache*,403666`）。
  实测铁证：
    · **21:11 签发的 token，至 23:18 仍 200**，
      分片 sync=0x47 明文 TS，且 `#EXT-X-PROGRAM-DATE-TIME` = 当前时刻（实时流）；
    · **22:21 之后签发的全部 403**，签发后 **0.0s 即死**（无任何时间窗口）；
    · 老 vkey 换 svrtime / 换 pid / 加随机 query **仍 200**
      → svrtime、pid、query 均不参与校验，**生死只由 vkey 本身决定**；
    · 老 vkey 带**乱** UID 头 → 403，不带 → 200
      → UID/guid 需匹配（且服务端 http_header.UID **就是我们的 guid** 本身）。
  **已逐一实测证伪的假设（勿再回头试）**：
      - 非 IP 封禁（换**不同出口**取流仍 403）
      - 非取流侧限流窗口（新 vkey 签发后 0.0s 即死）
      - 非 token 一次性消费（老 token 连取 4 次全 200）
      - 非 token 过期（老 token 2 小时后仍实时可播）
      - 非设备身份配额（换全新 uuid4 + 换 appVer + 全套新身份，仍全 403）
      - 非 CDN host 锁定（15 个 host 全扫，新 token 全 403）
      - 非防盗链 / 非 UA（ExoPlayer 默认 UA 亦 200）
      - 非我方实现漂移（与 drpy 版逐阶段比对，**cKey 逐字节一致**）
      - 非档位映射错误（fhd→+3 / shd→+2 / hd→+1，与 formats 表一致）
      - 非本机时钟偏差（本机 epoch 与 baidu/qq 的 Date 头误差 <3s）
  **唯一模型**：服务端自 21:11 起停止授予可用授权（授权窗口关闭，需冷却）。
  ⇒ **「签发成功」≠「可播」**。这正是 V3 全灭（播放器表现为零数据流量）的根因。

【V4 铁律（仍有效）】
  ① **不验活，绝不写盘**：每条线须 `m3u8 200` + `分片 sync=0x47` +
     `实时性判定通过` 三项全过才进 m3u；
  ② **同一 vkey 不得复用**：换不到新节点时另签一枚独立 token，
     绝不 `u = u or url`（V3 曾产出 180 条中 60 条与第 1 条逐字节相同的重复 URL）；
  ③ **验不过就整条丢弃**，宁可少一个频道，也不写死链；
  ④ 授权窗口关闭时 `--min-channels` 生效 → **拒绝覆盖旧文件**，
     保住上一版可用成果。

【V5 终局结论 · 2026-10-24 23:55 · ⚠ 已于 2026-10-25 被 V6 推翻，仅存档】
  V4 曾把「多线路刻意分散到不同 CDN 节点」当作核心策略，理由是
  「不同节点 = 各自独立的限流计数池，可摊薄播放器并发测速压力」。
  **该策略已被逆序对照实验证伪**，实测数据：

    ┌ 节点 ─────────────────────┬ 存活 ────────┬ 可用率 ──┐
    │ hlsliveali-cdn           │ 9/12         │ 75%      │
    │ live-dtocnc-cdn          │ 0/12         │ **0%**   │
    │ mobileliveali-cdn        │ 少           │ 待补     │
    └───────────────────────────┴──────────────┴──────────┘
    （live-dtocnc-cdn 两批独立样本 4/4 + 3/3 全灭，非抽样偶然）

  ⚠️ **订正（V6）**：上表的「dtocnc 0%」是**误判** —— 那批「全灭」样本取到的
  是 **ACL 403 页**（响应头带 Request-Id / Node information = 海外出口被 CDN
  拦截），**不是**主公国内 IP 上的真实可播率。2026-10-25 复测：
  原 host 原样取流 **6/6 全 200**（含 dtocnc/cdn=5505 那枚）。dtocnc 已恢复入池。

  **V5 四条铁律（⚠ 前两条已被 V6 取代，保留供对照）**：
  ① ~~签发后立即换 host~~ → **V6 改为保留原生 host**（换 host 会造成
     host↔cdn 配对错位，且会消灭跨集群冗余）；
  ② ~~换 host 只改 host，path 与 query 原样保留~~ → **V6 改为 host 与 cdn
     成对替换**（只改一边必然错位）；
  ③ **实时性判据多路兜底**（仍有效）：首选 `#EXT-X-PROGRAM-DATE-TIME`；
     无 PDT 时退化为「分片可取 + 序号推进」（CDN 偶发返回简版清单，
     旧版一刀切判死会**误杀好线路** —— 实测 24 样本中 3 例）。
  ④ **备选线价值来自多枚独立 token**（仍有效）；且**必须分散到不同集群**
     （V6 明确：保留原生 host/cdn 即自然实现）。

【为什么每频道挂多条线】
  · 播放器加载直播列表时会并发测速，多条**独立 token** 的线路 = 多个真正
    可用的回退路径，坏线自动换源；
  · 故 `--repeat` 默认 3，每条线独立签发 + 独立验活 + 独立去重。

【V6 终局模型 · 2026-10-25 · 甲乙丙三案 · 本次落刀】
  2026-10-25 全天实测（主公连续实测 + 末将 6 组对照实验）把 403 机制彻底定性，
  **推翻了 V5 的「节点质量」结论**，也推翻了「窗口期」与「IP 决定生死」两说：

  ┌──────────────────────────────────────────────────────────────┐
  │ 403 产生在**源站授权闸门**，不在边缘缓存（X-Cache: MISS、      │
  │ X-Swift-Error: orig response 4XX error、Via: ens-cache*）      │
  │ 额度按 **token** 计，且叠加 **频道级突发保护**：                │
  │   · 同一 token 连拉 → 首次 200，之后必 403/417                │
  │   · 同频道 2.4s 内猛打 6 次 → 连**兄弟 token** 一起封          │
  │   · 同 IP 同 token 冷却 80s 仍 403，**10 分钟后**才恢复 200    │
  │   · 正常播放（列表+分片）连取 15 次全 200                      │
  │     → 被烧掉的是「只拉列表」这种**探测**行为                    │
  │ ★ 换边缘 IP / 换池**救不回**已耗尽的 token（钉 IP 方案作废）  │
  └──────────────────────────────────────────────────────────────┘

  **根因**：V5 的 `sign_channels()` 对每条线调 `Y.verify()`（列表+分片+再列表
  = 3 次取流），线路1 更被验 2 轮（频道级 1 轮 + 循环内 1 轮 = 6 次）。
  实测「线路顺序 = 签发顺序」（三线 svrtime 非递减 100%：央视 15/15、卫视 24/24），
  线路1 验得最多、烧得最狠 → **正是主公实测「留活频道线路1 全死、线路2 全活」的机制**。

  **V6 三案（主公 2026-10-25 亲自定夺：甲+乙+丙）**：
  ① **甲案（签发与验活彻底分离）**
     `sign_channels()` 内**一次都不碰 CDN**；要发布的 token 的取流额度
     **完整留给播放器**。`--sample` 体检改用**独立的一次性 token**，
     测完即弃，绝不消耗发布 token 的额度、也绝不写进 m3u。
  ② **乙案（写盘顺序反转）**
     播放器**优先尝试线路1**，故把**最后签发**（最新鲜）的 token 放线路1。
  ③ **丙案（--repeat 由 3 降为 2）**
     同频道 3 条线会互相争抢该频道的额度，2 条为冗余与争抢的平衡点。

  【V6 附带订正 · host × cdn 配对】
  签发侧静态分析实测（零取流，12 枚样本）：央视频签发时 **host 与 `cdn`
  query 参数成对下发** ——
      live-dtocnc-cdn ↔ cdn=5505 ；hlsliveali-cdn ↔ cdn=5506
  且签发**随机**把 token 分到这两个集群之一。
  ⇒ V5 把所有线路强制换到 `hlsliveali-cdn`（V5 误以为 dtocnc 0% 可用，
    实际那批「全灭」样本取到的是 **ACL 403 页**= 海外出口被拦，不是国内可播率）
    等于把「多线路」从**跨集群冗余**压成**同集群同生共死** ——
    这正是主公实测「同一频道 3 条线同时死、同时活」的机制性解释。
  ⇒ V6 **保留每条线的原生 host + 原生 cdn**，让多枚独立 token 自然分散到不同集群。

【分发边界（重要）】
本脚本产出的是 **m3u 文本**，由 Cloudflare Pages 静态托管（`dxdszb.pages.dev/cctv.m3u`）。
**视频字节不经过 Cloudflare** —— 播放器从 m3u 读到央视频 CDN 直链后直连 CDN 取流。
实测：央视频 CDN 直连 10.62 Mbps、ping 18–20ms；而 Cloudflare 边缘仅
485–578 KB/s（≈4 Mbps）、ping 198–275ms（colo=SJC）。
**任何 Worker 都不得 fetch + 转发视频字节** —— 一是带宽不够（慢 20 倍，必卡），
二是违反 Cloudflare 服务条款 §2.8（免费套餐禁止转发 video 等非 HTML 内容，
可立即 suspend；2025 上半年 CF 因 streaming 举报处置了 21,000 个账号）。

【用法】
    python3 fetch_cctv_m3u.py                          # 全频道签发，写 cctv.m3u
    python3 fetch_cctv_m3u.py --channels cctv1,cctv13  # 只签指定频道
    python3 fetch_cctv_m3u.py --repeat 1               # 每档挂 1 条线（默认 2）
    python3 fetch_cctv_m3u.py --sample 5               # 抽样体检 5 个频道
    python3 fetch_cctv_m3u.py --out cctv.m3u           # 指定输出

【退出码】
    0 = 成功产出；1 = 无可用线路（保留旧文件，由 workflow 决定是否提交）
"""

import argparse
import os
import random
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import ysp_sign as Y

# EPG：统一走自建 dx-epg（与仓内 live.m3u / radio.m3u 同款）
EPG_URL = 'https://dx-epg.pages.dev/epg.gz'

# 台标：gitee myTVlogo 裸名库（上游 epg.112114.xyz 已宕机）
LOGO_BASE = 'https://gitee.com/mytv-android/myTVlogo/raw/main/img/{}.png'

# 默认输出：**仓库根**的 cctv.m3u（相对本脚本目录的上一级）。
# 【V5 修正】原为 'cctv.m3u'（相对当前工作目录）——在 cctv/ 目录里跑批时
#   会把产物写进 cctv/cctv.m3u，而仓库根的 cctv.m3u 纹丝不动，
#   导致「跑批成功但线上文件没更新」的静默失败。改为锚定仓库根。
OUTPUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                      'cctv.m3u')


def build_logo(name):
    """频道显示名 -> 台标 URL。

    myTVlogo 为**裸名库**（键为纯字母数字，如 `CCTV1.png`），
    故取显示名开头的英文/数字段作键：
      'CCTV-1 综合' -> 'CCTV1'
      '北京卫视'    -> ''（纯中文，gitee 无对应文件）
    """
    import re
    m = re.match(r'^([A-Za-z0-9\-]+)', (name or '').strip())
    if not m:
        return ''
    key = re.sub(r'[^A-Za-z0-9]', '', m.group(1))
    if not key:
        return ''
    return LOGO_BASE.format(key)


class Throttle:
    """签发节流。

    【为什么需要】
    签发接口虽从不封，但连发过快仍可能被上游识别为异常流量，故保底一个
    最小间隔（默认 0.3s，63 频道约 20 秒跑完）。签发接口实测 80/80 全通，
    故**不需要**熔断退避——签发失败几乎只源于单频道参数问题，
    重试无意义，直接记失败、继续下一频道。
    """

    def __init__(self, interval=0.3):
        self.interval = interval
        self.count = 0
        self._last = 0.0

    def before(self):
        now = time.time()
        elapsed = now - self._last
        if elapsed < self.interval:
            time.sleep(self.interval - elapsed)
        self._last = time.time()
        self.count += 1


def sign_channels(sess, channels, defn, repeat, throttle):
    """为频道列表**只签发**（不碰 CDN）。

    【V6 核心 · 2026-10-25 · 甲乙丙三案落地】
    V5 曾在这里对每条线调 `Y.verify()`（拉列表 + 拉分片 + 再拉列表 = 3 次取流），
    线路1 更被验 2 轮（频道级 1 轮 + 循环内 1 轮 = 6 次）。
    实测（2026-10-25 终局取证）证明：**这些取流正是把播放器的额度烧穿的原因** ——
      · 同一 token 连拉：首次 200，之后必 403；
      · 同频道 2.4s 内猛打 6 次 → 连**兄弟 token** 一起封（频道级突发保护）；
      · 同 IP 同 token 冷却 80s 仍 403，**10 分钟后**才恢复 200。
    且实测「线路顺序 = 签发顺序」（三线 svrtime 非递减 100%：央视 15/15、卫视 24/24），
    故验得最多的线路1 死得最早 —— 正是主公实测「留活频道线路1 全死、线路2 全活」的机制。

    ⇒ **V6 铁律：主流程一次都不碰 CDN。**
      · `sign_channels()` 内**不再有任何 verify/probe 调用**；
      · 要发布的 token 的取流额度**完整留给播放器**；
      · `--sample` 体检改用**独立的一次性 token**（测完即弃），绝不测要发布的 token。

    【V6 变更 2 · 保留原生 host↔cdn 配对（不再归一）】
    2026-10-25 签发侧静态分析实测：央视频签发时 **host 与 `cdn` 参数成对下发**：
        live-dtocnc-cdn ↔ cdn=5505 ；hlsliveali-cdn ↔ cdn=5506
    且签发会**随机**把 token 分到这两个集群之一（实测 12 枚样本两类都出现）。
    V5 把所有线路强制换成 `hlsliveali-cdn`，等于把「多线路」从
    **跨集群冗余**压成**同集群同生共死** —— 这正是主公实测
    「同一频道 3 条线同时死、同时活」的机制性解释。
    ⇒ V6 保留每条线的**原生 host + 原生 cdn**，让多枚独立 token 自然分散到不同集群。

    【V6 变更 3 · 写盘顺序反转（乙案）】
    播放器加载直播列表时**优先尝试线路1**。V5 的线路1 是**最早签发**的那枚
    （svrtime 最小），在共享额度下最不占优。V6 改为：**最后签发的 token 放线路1**，
    让播放器第一口就咬到最新鲜的一枚。

    返回 (entries, stats)：
      entries —— m3u 线路字典列表（已按 V6 顺序排好）
      stats   —— {'ok': 命中频道数, 'fail': 失败频道数, 'detail': {code: 说明}}
    """
    entries = []
    stats = {'ok': 0, 'fail': 0, 'detail': {}, 'retry': 0}

    for code, name, group, cnlid, livepid in channels:
        # 本频道的候选线路（每条独立签发、互不复用）
        picked_urls = []
        picked = None
        for d in Y.defn_chain(code, defn):
            throttle.before()
            try:
                u = Y.play_url(cnlid, livepid, d, sess)
            except Exception as e:
                stats['detail'][code] = 'ERR %s' % str(e)[:60]
                continue
            if not u:
                continue
            # 【V6】**保留原生 host + 原生 cdn**，不做任何换手。
            #   host↔cdn 是成对下发的，改一边会造成错位（实测 404/403）；
            #   且归一到单一 host 会消灭跨集群冗余。
            picked_urls.append(u)
            picked = d
            break

        if not picked_urls:
            stats['fail'] += 1
            stats['detail'].setdefault(code, '签发失败')
            print('[cctv] %-10s 签发失败' % code, flush=True)
            continue

        stats['ok'] += 1
        host = Y.host_of(picked_urls[0])
        stats['detail'][code] = '签发 %s / %s' % (picked, host)
        print('[cctv] %-10s %-4s 签发成功 %s (cdn=%s)'
              % (code, picked, host, Y.cdn_of(picked_urls[0])), flush=True)

        # 同档重复挂线：每条都是**独立签发的 token**，保留各自原生 host/cdn。
        # 【V6】不再验活（一次都不碰 CDN），失败与否交给播放器实测。
        written_urls = set()
        used_hosts = set()
        channel_entries = []
        for i in range(max(1, repeat)):
            if i == 0:
                u = picked_urls[0]
            else:
                # 【V6 强化】同频道的备选线**必须落在不同的边缘集群**。
                #   依据：额度按 (token × host) 计 —— 同 host 的两条线共享同一份
                #   额度池，一死俱死（正是主公实测「同一频道 3 条线同时死、同时活」）。
                #
                #   ★签发侧按调用方出口分配集群（2026-10-25 实测）：
                #     本机 → dtocnc(5505)/hlsliveali(5506)；
                #     Actions 境外 runner → **outlivecloud(5401) 为主**，
                #     同一频道的多条线会**全部落在同一 host** → 冗余归零。
                #   故策略是：先**重签**（零取流代价，签发接口 80/80 从不封）；
                #   若重签多次仍同 host，则**主动换到同族另一集群** ——
                #   实测换 host 安全（`cdn` 不参与路由校验），
                #   且换 host = 换一份**全新额度**（该 (token,新host) 从未被碰过）。
                u = None
                fallback = None
                for _try in range(6):
                    throttle.before()
                    try:
                        cand = Y.play_url(cnlid, livepid, picked, sess)
                    except Exception:
                        cand = None
                    if not cand:
                        break
                    if cand in written_urls:
                        stats['retry'] += 1
                        continue
                    h = Y.host_of(cand)
                    if h in used_hosts:
                        stats['retry'] += 1
                        if fallback is None:
                            fallback = cand     # 记一个同 host 的候选，作回退
                        continue              # 同 host，换一枚
                    u = cand
                    break
                if u is None and fallback is not None:
                    # 重签耗尽仍同 host → 主动换到同族另一集群
                    peers = Y.cluster_peers(fallback)
                    for h in peers:
                        cand, _ch = Y.swap_host(fallback, h)
                        if cand not in written_urls:
                            u = cand
                            break
                if u is None:
                    u = fallback
                if u is None:
                    stats['retry'] += 1
                    continue
            if u in written_urls:
                stats['retry'] += 1
                continue
            written_urls.add(u)
            used_hosts.add(Y.host_of(u))

            channel_entries.append({
                # 【关键】多线路必须**完全同名 + 同 tvg-id**，
                #   否则播放器会当成多个独立频道显示（而非一路的多个备选源）。
                #   仓库 live.m3u 的既有惯例即如此（CCTV-1 有 4 条同名同 tvg-id）。
                'name': name,
                'group': group,
                'url': u,
                'logo': build_logo(name),
                'tvg_id': code,
            })
            if i + 1 < repeat:
                time.sleep(random.uniform(0.1, 0.3))

        if not channel_entries:
            stats['fail'] += 1
            stats['detail'][code] = '无可用线路'
            continue

        # 【V6 乙案】**写盘顺序反转**：最后签发的 token 放线路1。
        #   播放器优先试线路1 → 让它咬最新鲜的那枚。
        channel_entries.reverse()
        entries.extend(channel_entries)

    return entries, stats


def sample_health(sess, channels, defn, throttle, count):
    """抽样体检：**用一次性 token** 实测本轮整体健康度。

    【V6 铁律 · 2026-10-25】
    旧版体检直接拿**要发布的 token** 去 probe —— 那等于在交付前
    把播放器的取流额度烧掉（实测：同一 token 连拉第二次即 403）。
    V6 改为：
      · 为体检**单独签一批一次性 token**（与发布的 token 完全无关）；
      · 用 `probe_once()`（**只拉一次列表** + 一个分片，全程两个不同 URL）——
        旧版 `probe_live` 会重复请求同一列表 URL，**必 403**；
      · 测完即弃，绝不写进 m3u，也绝不消耗发布 token 的额度；
      · 体检结果**只用于日志判读**，不影响写盘（验不过的频道照样保留，
        由播放器自己换源）。

    返回 (ok_n, total_n, 明细列表)。
    """
    if count <= 0 or not channels:
        return 0, 0, []
    # 均匀抽样，避免只测到开头几个频道
    step = max(1, len(channels) // count)
    picked = channels[::step][:count]
    ok_n = 0
    detail = []
    for code, name, group, cnlid, livepid in picked:
        throttle.before()
        try:
            u = Y.play_url(cnlid, livepid, defn, sess)   # 一次性 token，测完即弃
        except Exception:
            u = None
        if not u:
            detail.append((code, 'FAIL', '签发失败'))
            print('[cctv] 体检 %-10s %-9s 签发失败' % (code, 'FAIL'), flush=True)
            continue
        try:
            # 【V6】体检用 `probe_once()`：**只拉一次列表** + 一个分片。
            #   绝不能用会重复请求同一列表 URL 的旧版 probe_live ——
            #   实测第 2 次列表请求必 403，会连带打死分片请求。
            good, d = Y.probe_once(u, sess)
        except Exception as ex:
            good, d = False, 'ERR %s' % str(ex)[:50]
        if good:
            ok_n += 1
        detail.append((code, 'PLAYABLE' if good else 'FAIL', d[:60]))
        print('[cctv] 体检 %-10s %-9s %s'
              % (code, 'PLAYABLE' if good else 'FAIL', d[:60]), flush=True)
    return ok_n, len(picked), detail


def write_m3u(entries, path):
    """写 m3u 文本。

    每条线路写 `#EXTVLCOPT:http-user-agent=qqlive` —— 央视频分片请求需
    `User-Agent: qqlive`；实测 `UID` 头非必需，故静态 m3u 无需逐频道配私有头。
    """
    lines = ['#EXTM3U x-tvg-url="%s"' % EPG_URL]
    for e in entries:
        attrs = 'tvg-id="%s"' % e['tvg_id']
        if e['logo']:
            attrs += ' tvg-name="%s" tvg-logo="%s"' % (e['name'], e['logo'])
        else:
            attrs += ' tvg-name="%s"' % e['name']
        lines.append('#EXTINF:-1 group-title="%s" %s,%s'
                     % (e['group'], attrs, e['name']))
        lines.append('#EXTVLCOPT:http-user-agent=qqlive')
        lines.append(e['url'])
    with open(path, 'w', encoding='utf-8', newline='\n') as f:
        f.write('\n'.join(lines) + '\n')
    return len(entries)


def main():
    ap = argparse.ArgumentParser(description='签发央视频直播源 → cctv.m3u')
    ap.add_argument('--channels', default='',
                    help='只签指定频道（逗号分隔，如 cctv1,cctv13）')
    ap.add_argument('--defn', default='fhd',
                    choices=['fhd', 'uhd'],
                    help='清晰度档位：fhd=高清1080P（默认）/ uhd=超高清（仅4K频道有效）。'
                         '按主公军令「只请求高清，不要标清及以下」，本脚本不提供标清/流畅档。')
    ap.add_argument('--repeat', type=int, default=2,
                    help='每档挂几条同档线路（默认 2 · V6 由 3 降为 2）。多条'
                         '**完全同名同 tvg-id** → 播放器合并为一路的多个备选源，'
                         '坏线自动换源。【V6 丙案】同频道 3 条线会互相争抢该频道的'
                         '取流额度（实测频道级突发保护会连兄弟 token 一起封），'
                         '故由 3 降为 2。')
    ap.add_argument('--interval', type=float, default=0.3,
                    help='两次签发之间最小间隔秒数（默认 0.3）')
    ap.add_argument('--sample', type=int, default=3,
                    help='抽样体检的频道数（默认 3；0 = 不体检。这是唯一会碰 CDN 的环节）')
    ap.add_argument('--min-channels', type=int, default=10,
                    help='低于此成功频道数则判定本次签发异常，不覆盖旧文件')
    ap.add_argument('--out', default=OUTPUT, help='输出文件（默认 cctv.m3u）')
    args = ap.parse_args()

    # 频道筛选
    if args.channels:
        want = set(x.strip() for x in args.channels.split(',') if x.strip())
        channels = [c for c in Y.LIVE_CHANNELS if c[0] in want]
        missing = want - set(c[0] for c in channels)
        if missing:
            print('[cctv] 未知频道: %s' % ', '.join(sorted(missing)))
        if not channels:
            print('[cctv] 无匹配频道，退出')
            return 1
    else:
        channels = list(Y.LIVE_CHANNELS)

    print('[cctv] 开始：%d 频道 / 档位 %s / 每档 %d 条线 / 签发间隔 %.1fs'
          % (len(channels), args.defn, args.repeat, args.interval))
    print('[cctv] 策略：只签发（不碰 CDN），抽样体检 %d 个频道' % args.sample)
    t0 = time.time()

    sess = Y.make_session()
    throttle = Throttle(interval=args.interval)
    entries, stats = sign_channels(sess, channels, args.defn, args.repeat, throttle)

    t_sign = time.time() - t0
    print('')
    print('[cctv] 签发完成：成功 %d / 失败 %d，累计签发 %d 次，耗时 %.1fs'
          % (stats['ok'], stats['fail'], throttle.count, t_sign))

    # 抽样体检（唯一触碰 CDN 的环节 —— **用一次性 token**，不碰要发布的 token）
    if args.sample > 0:
        ok_n, tot_n, _ = sample_health(sess, channels, args.defn, throttle, args.sample)
        print('[cctv] 抽样体检：%d/%d 可播（一次性 token，测完即弃）' % (ok_n, tot_n))

    print('')
    if not entries:
        print('[cctv] 无可用线路，保留旧文件不动')
        return 1

    # 成功频道过少说明签发接口异常（而非限流），此时写出的半份 m3u 会让主公
    # 看到大量不可播频道 —— 宁可不写，保留旧文件。
    if stats['ok'] < args.min_channels:
        print('[cctv] 成功频道数 %d < 阈值 %d，判定签发异常，保留旧文件不动'
              % (stats['ok'], args.min_channels))
        return 1

    n = write_m3u(entries, args.out)
    size = os.path.getsize(args.out)
    print('[cctv] 已写入 %s：%d 条线路 / %d 字节 / 总耗时 %.1fs'
          % (args.out, n, size, time.time() - t0))
    return 0


if __name__ == '__main__':
    sys.exit(main())
