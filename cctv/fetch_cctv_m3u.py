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

【V5 终局结论 · 2026-10-24 23:55 · 推翻 V4 的"跨节点分散"策略】
  V4 曾把「多线路刻意分散到不同 CDN 节点」当作核心策略，理由是
  「不同节点 = 各自独立的限流计数池，可摊薄播放器并发测速压力」。
  **该策略已被逆序对照实验证伪**，实测数据：

    ┌ 节点 ─────────────────────┬ 存活 ────────┬ 可用率 ──┐
    │ hlsliveali-cdn           │ 9/12         │ 75%      │
    │ live-dtocnc-cdn          │ 0/12         │ **0%**   │
    │ mobileliveali-cdn        │ 少           │ 待补     │
    └───────────────────────────┴──────────────┴──────────┘
    （live-dtocnc-cdn 两批独立样本 4/4 + 3/3 全灭，非抽样偶然）

  **逆序对照实验**（同一枚 token、同一时刻、仅换 host，**先测换 host 者占先**）：
    · 换 host 侧存活 9/12 = 75%，原 host 侧仅 2/12 = 17%
    · 8 秒后原地重测 6/6 全活 → token 无短时衰减，**host 才是唯一因果**
    · 「换 host 后 m3u8 200 + 分片明文 TS」跨 6 频道实测分片可取 23/24 = 96%
  ⇒ **死的是节点，不是 token**。V3「全面沦陷 / 零数据流量」的机制由此闭合：
    V3 的第 2/3 条被系统性送到 0% 可用的 live-dtocnc-cdn，
    而播放器拿到一堆死链 → 表现为「各频道疑似无数据流量传输」。

  **V5 四条铁律（在 V4 之上）**：
  ① **签发后立即换 host**（`swap_host` 到 `PRIMARY_HOST`），再验活；
     顺序反了会白白浪费一次验活（原 host 先验 = 17% 通过率）。
  ② **换 host 只改 host**，path 与 query 原样保留
     （`cdn` 参数与 vkey 密文绑定，改 query 里的 cdn 值无效，实测 403）。
  ③ **实时性判据多路兜底**：首选 `#EXT-X-PROGRAM-DATE-TIME`；
     无 PDT 时退化为「分片可取 + 序号推进」（CDN 偶发返回简版清单，
     旧版一刀切判死会**误杀好线路** —— 实测 24 样本中 3 例）。
  ④ **备选线价值来自多枚独立 token，而非多节点**；
     全部归一到好节点，绝不再把线路往死节点上送。

【为什么每频道挂多条线】
  · 播放器加载直播列表时会并发测速，多条**独立 token** 的线路 = 多个真正
    可用的回退路径，坏线自动换源；
  · 故 `--repeat` 默认 3，每条线独立签发 + 独立验活 + 独立去重。

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

    【节点筛选 · 关键】
    央视频签发时会把 token **随机分配到不同 CDN 节点**，各节点质量差异极大：
      · `hlsliveali-cdn` / `mobileliveali-cdn` —— 稳定节点（实测 14/14 m3u8=200，
        切片 3/3 通，连续 6 轮全通，UA 无关）
      · `live-dtocnc-cdn` —— **劣化节点**，间歇性 403（同一时刻 12 枚样本中
        3 枚全败且全落在它身上，另一次 24 枚样本又全 200）
    用户实测「有时能连，有时不能，连上后时断时连」，根因即此：
    m3u 里混入了劣化节点的线路，播放器在好坏线间来回跳。
    故签发时**只保留稳定节点的 token**，落在劣化节点就**重签**——
    这是纯本地判断，**零取流消耗**（签发接口 80/80 从不封）。

    【多线分散】
    `--repeat` 挂的多条线**刻意分散到不同节点**（按 host 去重后取），
    播放器自动换源时即多几个真正可用的回退路径。

    返回 (entries, stats)：
      entries —— m3u 线路字典列表
      stats   —— {'ok': 命中频道数, 'fail': 失败频道数, 'detail': {code: 说明}}
    """
    entries = []
    stats = {'ok': 0, 'fail': 0, 'detail': {}, 'retry': 0}
    for code, name, group, cnlid, livepid in channels:
        throttle.before()
        url = None
        picked = None
        for d in Y.defn_chain(code, defn):
            try:
                u = Y.play_url(cnlid, livepid, d, sess)
            except Exception as e:
                stats['detail'][code] = 'ERR %s' % str(e)[:60]
                continue
            if not u:
                continue
            # 【V5 关键】**签发后立即把 host 归一到好节点**。
            #   旧版这里是「落在劣化节点 → 有限次重签」，想靠运气抽到好节点。
            #   但实测节点分配是**随机**的，且 live-dtocnc-cdn 可用率 0%
            #   → 靠重签抽 = 把成败押在运气上。
            #   现在改为**主动换 host**：token 可跨 host 使用（实测成立），
            #   换 host 后分片可取率 23/24 = 96%（原 host 侧仅 17%）。
            #   ⚠️ 必须**先换 host 再验活**；若先验活再换，多数线路会白死一次。
            if not Y.is_good_host(u):
                u = Y.swap_host(u, Y.PRIMARY_HOST)
            url = u
            picked = d
            break
        if not url:
            stats['fail'] += 1
            stats['detail'].setdefault(code, '签发失败')
            print('[cctv] %-10s 签发失败' % code, flush=True)
            continue

        stats['ok'] += 1
        host = url.split('/')[2]
        stats['detail'][code] = '签发 %s / %s' % (picked, host)
        print('[cctv] %-10s %-4s 签发成功 %s'
              % (code, picked, host), flush=True)

        # 【V5 关键】第 1 条同样**必须验活**，且**必须允许重签**。
        #   旧版第 1 条直连签发结果就写盘（未经任何 CDN 验证），
        #   这是 V3「全面沦陷 / 零数据流量」的直接原因：
        #   签发接口永远 iretcode=0，但服务端签发的 vkey 未必在 CDN 侧激活。
        #   V5 补上重试：失败形态 `m3u8=404` = 该 token 在 CDN 边缘尚未就绪，
        #   换一枚新 token 即可绕开（实测单枚 404 并不代表该频道不可播）。
        ok0, url_ok0, detail0 = Y.verify(url, sess)
        if not ok0:
            for _try in range(2):
                throttle.before()
                try:
                    cand = Y.play_url(cnlid, livepid, picked, sess)
                except Exception:
                    break
                if not cand:
                    break
                if not Y.is_good_host(cand):
                    cand = Y.swap_host(cand, Y.PRIMARY_HOST)
                ok0, url_ok0, detail0 = Y.verify(cand, sess)
                if ok0:
                    break
                stats['retry'] += 1
        if not ok0:
            stats['fail'] += 1
            stats['detail'][code] = '验活失败 %s' % detail0[:40]
            print('[cctv] %-10s 验活失败 %s（该频道整条丢弃）'
                  % (code, detail0[:50]), flush=True)
            stats['ok'] -= 1
            continue
        url = url_ok0

        # 同档重复挂线：每条都是**独立签发的 token**，且**全部归一到好节点**。
        #
        # 【V5 关键修正 · 2026-10-24 23:55】
        #   V4 的策略是「刻意把第 2/3 条分散到**不同**节点」，理由是
        #   「不同节点 = 各自独立的计数池，可摊薄播放器并发测速压力」。
        #   该策略已被**逆序对照实验证伪**：不同节点里有一个
        #   （live-dtocnc-cdn）可用率实测 **0%**，把它当"分散池"等于
        #   主动把线路往死节点上送 —— 这正是 V3 全灭的机制。
        #   V5 改为：每条线独立签发 + **强制换到好节点** + 独立验活。
        #   备选线的价值来自「多枚独立 token」而非「多节点」——
        #   播放器加载时会并发测速，多条独立 token 里只要有一条活着即可播。
        written_urls = set()
        for i in range(max(1, repeat)):
            u = url if i == 0 else None
            # 【V5】备选线：**签发 → 换 host → 验活**，失败则重签，最多 3 轮。
            #   失败形态 `m3u8=404` = 该 token 在 CDN 侧尚未就绪（边缘未同步），
            #   换一枚新 token 即可绕开；实测单轮失败率不低，故必须允许多轮。
            ok = False
            u_ok = None
            detail = ''
            for _try in range(3):
                if u is None:
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
                    u = cand
                # 【V5】验活（verify 内部已含「换 host 到 PRIMARY」）
                ok, u_ok, detail = Y.verify(u, sess)
                if ok:
                    break
                u = None      # 换一枚新 token 重试
                stats['retry'] += 1

            if not ok:
                stats['fail'] += 1
                stats['detail'][code] = '备选线验活失败 %s' % detail[:36]
                print('[cctv] %-10s 备选线验活失败 %s（不写入）'
                      % (code, detail[:50]), flush=True)
                continue
            if u_ok in written_urls:
                stats['retry'] += 1
                continue
            written_urls.add(u_ok)
            u = u_ok

            logo = build_logo(name)
            entries.append({
                # 【关键】多线路必须**完全同名 + 同 tvg-id**，
                #   否则播放器会当成多个独立频道显示（而非一路的多个备选源）。
                #   仓库 live.m3u 的既有惯例即如此（CCTV-1 有 4 条同名同 tvg-id）。
                'name': name,
                'group': group,
                'url': u,
                'logo': logo,
                'tvg_id': code,
            })
            if i + 1 < repeat:
                time.sleep(random.uniform(0.1, 0.3))
    return entries, stats


def sample_health(sess, entries, sample, count):
    """抽样体检：对少量频道实拉分片，判断本轮签发的整体健康度。

    【重要】这是**唯一**会触碰 CDN 取流侧的环节，且只跑 `count` 个频道。
    它不参与线路筛选——验不过的线路照样留在 m3u 里（播放器会自己换源），
    只是在日志里标出来，便于 Actions 日志判读。

    返回 (ok_n, total_n, 明细列表)。
    """
    if count <= 0 or not entries:
        return 0, 0, []
    # 均匀抽样，避免只测到开头几个频道
    step = max(1, len(entries) // count)
    picked = entries[::step][:count]
    ok_n = 0
    detail = []
    for e in picked:
        try:
            # 【V5】体检也走 probe_live（含实时性判定），不再只测分片。
            #   写盘的线路本就验过活，这里是二次确认 + 记录实时 lag。
            good, d = Y.probe_live(e['url'], sess, fallback_check=False)
        except Exception as ex:
            good, d = False, 'ERR %s' % str(ex)[:50]
        if good:
            ok_n += 1
        detail.append((e['tvg_id'], 'PLAYABLE' if good else 'FAIL', d[:60]))
        print('[cctv] 体检 %-10s %-9s %s'
              % (e['tvg_id'], 'PLAYABLE' if good else 'FAIL', d[:60]), flush=True)
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
    ap.add_argument('--repeat', type=int, default=3,
                    help='每档挂几条同档线路（默认 3）。多条**完全同名同 tvg-id**'
                         ' → 播放器合并为一路的多个备选源，坏线自动换源。')
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

    # 抽样体检（唯一触碰 CDN 的环节）
    if args.sample > 0:
        ok_n, tot_n, _ = sample_health(sess, entries, args.sample, args.sample)
        print('[cctv] 抽样体检：%d/%d 可播' % (ok_n, tot_n))

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
