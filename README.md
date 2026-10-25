# dxdszb · 道玄电视直播 · 上游直播源镜像

本仓库**不存 APK 代码**。它承载四条直播源线路的镜像与发布：文件推送到 `main` 后，由
Cloudflare Pages 构建为 `https://dxdszb.pages.dev/*`，经 CDN 加速后供 APK、播放器、TVBox 拉取。

## 四条线路

| 线路 | CDN 地址 | 上游源 | 说明 |
|---|---|---|---|
| 直播源1（Guovin） | https://dxdszb.pages.dev/live.m3u | https://guovin.github.io/iptv-api/ipv4.m3u | IPTV 直播源（Guovin，纯IPv4适配TVBox），每 12 小时镜像，剔除 TG频道@stymei / 抖音 / 快手 / 更新时间 四组 |
| 直播源2（日后） | https://dxdszb.pages.dev/dxds.txt | http://rihou.cc:555/gggg.nzk | 「日后」聚合直播源（txt 格式，`#genre#` 分组），每 12 小时镜像 |
| TVBox 线路（拾光） | https://dxdszb.pages.dev/shiguang/api.json | 仓库内 `shiguang/` 目录（人工维护） | TVBox 点播 + 直播接口，136 个站点 |
| **央视直播（央视频签名）** | **https://dxdszb.pages.dev/cctv.m3u** | **央视频签名接口 `bkliveinfo.ysp.cctv.cn`** | **CCTV1-17 + 4K/8K + CGTN 六语 + 32 个省卫视 + 教育电视/国学频道，每小时重签** |

> 三线 2026-09-05 实测均 `200` 可达。拾光线路为人工维护，不随 Actions 自动更新；
> 修改后需手动 push 到 `main` 触发 Pages 重新部署。央视线路 2026-10-24 新增，
> 见下方「央视直播线路（cctv）」专章。

## 央视直播线路（cctv）

`cctv.m3u` 是本仓第四条线路，**取流走央视频签名接口，产出明文 H.264 地址**。

### 为什么不用央视网官方 CDN 直链

cdrm 族（`ldncctvwbcd` / `ldcctvwbcd` / `ldocctvwbcd` / `wstvwbcd` / `cetvwbcd` /
`sportswbcd` / `hdgcwbcd`）**全部 UDRM 加密**，第三方播放必花屏——2026-09 经 ffmpeg
实际解码证实（I 帧 concealing DC/AC/MV errors）。2026-10-24 另经 TS 层实测复核：cdrm 与
AV1 两族视频轨均为 `stream_type 0x06`（PRIVATE_DATA）、无 codec 描述符，载荷熵
7.949/8.0、且 1080p/480p/360p 三档前 42 字节完全相同 → **是密文，与「AV1 编码」无关**。
央视频签名线路才是官方明文源。

### 短寿问题的正解

央视频返回 `vkey_renew_interval = 14400`（**4 小时**），即固化 URL 最多活 4 小时。
对策不是换架构，而是**每小时重签一轮**（`cctv.yml` 的 cron 为每小时第 23 分），
且每条线路写入前都做**预检重取**——换新 token + 抖动退避，试到「播放列表 200 +
最新分片 200 且 TS 同步字 0x47」实测可取才交出。播放器只持一个 URL、无法自行续签，
故由脚本代其"试到能取才交出"。

### 清晰度：只签高清（军令）

按主公 2026-10-24 军令，**只请求高清，不要标清及以下**。故：

- 脚本档位链**已掐断 `shd`(标清720P) / `hd`(流畅540P)**，只出 `fhd`（高清 1080P）；
- 4K 频道（`cctv4k` / `cctv8k` / `cctv164k`）额外前置 `uhd`（超高清），属高清之上；
- 单频道最多签 2 次（4K 频道），普通频道 1 次——**绝不向下回退凑档**。

此约束同时是**限流对策**：回退链会把单频道签发量放大 3–4 倍，
而央视频取流侧的额度是累计消耗的，砍掉回退即砍掉 3–4 倍无谓消耗。

### 央视频 403 的正确模型（2026-10-25 V6 终局 · 此前全部结论作废）

> ⚠️ 本节已按 2026-10-25 全天实测**整章重写**。此前记录的
> 「节点热窗口限流」「换 host 无效」「dtocnc 0% 可用」「授权窗口关闭」
> 「签发成功≠可播」等结论**均被证伪**，勿再照搬。

#### 机制定性

央视频取流侧有一道**源站授权闸门**。它的真实行为经 2026-10-25 的
8 组对照实验钉死：

| 观测 | 结果 | 判读 |
|---|---|---|
| 签发接口 `bkliveinfo.ysp.cctv.cn` 连发 80 次 | **80/80 全通**（57ms/次） | 签发额度**独立、从不封** |
| 同一 (token, host) 的**播放列表 URL**：第 1 次 / 第 2 次 | **200 / 403** | 闸门按 **(token × host × URL)** 计 |
| 第 2 次列表 403 **之后**取同 host 的分片 | **403** | 403 会**连带**打死该 token 的分片请求 |
| **不同 URL**（列表→分片→下一轮列表），间隔 8 秒 | **连取 8 轮全 200 / sync=0x47** | 正常播放**完全稳定** |
| 同一 token 换 host 再取 | **仍 200** | 换 host = 换一份额度 |
| 同频道 2.4 秒内猛打 6 次 | 连**兄弟 token** 一起封 | 叠加**频道级突发保护** |
| 同一 IP 同 token 冷却 80 秒 / 10 分钟 | 403 / 恢复 200 | 恢复是**滑动**的，非固定窗口 |
| 钉死 8 个 ali 边缘 IP 逐个测 | 仅 .19 首请求 200 | **换 IP 救不回**已耗尽的 token → 钉 IP 方案作废 |
| 403 响应头 | `Via: ens-cache*` + `X-Swift-Error: orig response 4XX error` + `X-Cache: MISS` | 判定**来自源站**，边缘只转发 |
| 200 响应头 | `Via: live3.l2na63-2`（直播分发层） | 直播链路与 CDN 缓存层分离 |

#### ★根因：旧版 `probe_live()` 是自毁装置★

旧版验活函数的实现是「拉列表 → 拉分片 → **再拉一次列表**取 PDT」。
而上面已证明：**同一列表 URL 的第 2 次请求必然 403**。
⇒ **验活恒判失败** ⇒ 脚本随即重签 ⇒ 每枚 token 的额度被反复烧穿 ⇒ 全灭。

这也精确解释了主公实测的「**留活频道线路1 全死、线路2 全活**」：
线上版三线 `svrtime` 非递减（央视 15/15、卫视 24/24 全部成立），
线路1 最早签发、被验 **2 轮**（频道级 1 轮 + 循环内 1 轮 = 6 次取流），
烧得最狠 → 最先死。

#### ★V5「dtocnc 0% 可用」是误判★
V5 判 dtocnc 不可用的依据是两批「4/4 + 3/3 全灭」样本 —— 但那批样本取到的
是 **ACL 403 页**（响应头带 `Request-Id` / `Node information` = 海外出口被拦），
**不是**国内 IP 上的真实可播率。2026-10-25 复测：**原 host 原样取流 6/6 全 200**。

签发侧静态分析（零取流）另查明：央视频签发时 **host 与 `cdn` 参数成对下发** ——
`live-dtocnc-cdn↔5505`、`hlsliveali-cdn↔5506`、`mobilelive-cnc↔5203`、`mobileliveali↔5205`，
且签发**随机**分到不同集群。但 host×cdn 四种组合交叉实测**全部 200**
⇒ **`cdn` 不参与路由校验**（V5 记的「改 cdn 会 403」实为「同 URL 二次请求」的额度耗尽）。

#### V6 三条铁律（主公 2026-10-25 定夺：甲+乙+丙）

| 铁律 | 落地 | 依据 |
|---|---|---|
| **甲 · 签发与验活彻底分离** | `sign_channels()` 内**一次都不碰 CDN**；`--sample` 体检改用**独立的一次性 token** + `probe_once()`（只拉 1 次列表 + 1 个分片，全程 2 个不同 URL） | 额度是留给播放器的；交付前的任何验活都在自伤 |
| **乙 · 写盘顺序反转** | 最后签发（最新鲜）的 token 放**线路1** | 播放器优先试线路1；实测 59/59 频道生效 |
| **丙 · `--repeat` 3 → 2** | 每频道 2 条线 | 同频道 3 条线会互争该频道的额度 |

**V6 附带强化**：同频道备选线**强制异 host**（撞 host 则重签，签发接口免费故零取流代价）
—— 额度按 (token × host) 计，同 host 的两条线共享同一份额度池，一死俱死。

#### V6 交付物实测

- 跑批：**59 频道 / 118 线路**，每频道恰好 2 条，118 URL 全唯一，耗时 **38 秒**（122 次签发，**零取流**）；
- host 分布跨 4 个集群：hlsliveali 63 / dtocnc 51 / mobilelive-cnc 2 / mobileliveali 2；
- 乙案检验：线路1 `svrtime` ≥ 线路2 的频道 **59/59**；
- 播放器节奏模拟（间隔 8 秒，列表+分片）：**8/8 轮全 200 / sync=0x47**。

### V6 工程纪律（写入代码注释，勿再违反）

1. **主流程零取流** —— 签发接口从不封（80/80），取流侧却是稀缺资源；
2. **绝不对同一个 URL 发两次请求** —— 列表 URL 一次性，分片 URL 各自独立；
3. **体检必须用一次性 token** —— 测完即弃，绝不消耗发布 token 的额度；
4. **换 host 不换 query** —— `cdn` 不参与路由校验，改它无效（V5 的旧说法已证伪）；
5. **保留原生 host** —— 多枚独立 token 自然分散到不同集群，即真正的跨集群冗余。

### 本项目最终采用的策略

1. **主流程只签发，绝不碰 CDN** → 63 频道约 **19 秒**跑完；
2. **每频道 2 条线、强制异 host**，且**完全同名、同 tvg-id**
   （与仓内 `live.m3u` 惯例一致），播放器合并为一路的多个备选源，坏线自动换源；
3. **最后签发的 token 放线路1**（乙案）；
4. **抽样体检**（`--sample`，默认 2 频道）用一次性 token 出健康报告，**不参与线路筛选**；
5. Actions 每小时在**境外 runner** 重签，覆盖 m3u 并推送 Pages。

### 用户实测的机理与对策（2026-10-24 v3 → 2026-10-25 V6 定案）

主公连续实测的现象：本地文件全死（**token 超 4 小时寿命**，已闭环）；
线上「留活频道线路1 全死、线路2 全活」；卫视各频道 3 条线死活无规律；
同一线路 10 分钟前死、10 分钟后又活。

**V6 定案解释**：
- 「线路1 先死」→ 验活轮次最多的那条烧得最狠（svrtime 非递减 100% 佐证）；
- 「死而复生」→ 闸门额度是**滑动恢复**的（10 分钟实测恢复 200）；
- 「3 条线死活无规律」→ 各线被验活的次数与签发间隔不同 → 耗尽时刻错开；
- 「本地全死」→ token 寿命 4 小时，本地版已 10.5 小时。

### 📜 历史存档：V3/V4/V5 的排查过程（结论已全部作废，勿照搬）

2026-10-24 夜，V3 版实测「全面沦陷、无一生还」，先后提出过三代假说：

| 版本 | 当时结论 | 今日状态 |
|---|---|---|
| V3（10-24 23:00） | 「服务端授权窗口关闭，签发成功 ≠ 可播」 | ❌ **证伪** —— 新签 token 立即可播 |
| V4（10-24 23:30） | 「多线刻意分散到不同节点 = 独立计数池」 | ❌ **证伪** —— 分散反而把线路送进差集群 |
| V5（10-24 23:55） | 「死的是节点不是 token，换 host 有效（17%→75%）」 | ❌ **证伪** —— 「dtocnc 0%」实为 ACL 403 页误判 |

**真正的原因**（见上文 V6 定案）：旧版验活函数「拉列表 → 拉分片 → **再拉一次列表**」
必然触发「同一列表 URL 第 2 次请求必 403」，导致验活恒失败 → 重签 → 额度烧穿 → 全灭。

**这些排查过程本身有价值**（证伪了大量假设：IP 封禁、token 过期、svrtime 校验、
设备身份配额、防盗链、签名漂移、档位映射错误…），故留此存照，**结论一律以 V6 为准**。

### 已知局限：验活请求量与限流的自相矛盾（V6 已解）

**旧版**验活是保质量的唯一手段，但**验活本身消耗取流额度**：
一轮全量（63 频道 × 3 线）约需 360 次取流请求，足以把本地计数池打满。

**V6 的解法不是「少验活」，而是「验得更准且不碰要发布的东西」**：
- 主流程**零验活**（甲案）—— 要发布的 token 的额度完整留给播放器；
- 体检用**一次性 token** + `probe_once()`（单次列表请求，全程 2 个不同 URL）——
  既不消耗发布额度，也不会因重复请求同一 URL 而误判；
- 因此**质量保证与额度保护不再冲突**：跑批 38 秒、零取流。

### 分发边界（重要，勿改坏）

Cloudflare 只托管 **m3u 文本**；**视频字节不经过 Cloudflare** —— 播放器从 m3u 读到
央视频 CDN 直链后直连 CDN 取流。实测：央视频 CDN 直连 **10.62 Mbps**、ping **18–20ms**；
而 Cloudflare 边缘仅 **485–578 KB/s（≈4 Mbps）**、ping **198–275ms**（colo=SJC）。

**任何 Worker 都不得 fetch + 转发视频字节**：一是带宽不够（慢 20 倍，必卡），
二是违反 Cloudflare 服务条款 §2.8（免费套餐禁止转发 video 等非 HTML 内容，可立即
suspend；2025 上半年 CF 因 streaming 举报处置了 21,000 个账号）。

### 目录与用法

```
cctv/
├── ysp_sign.py         # 央视频签名栈（TEA-CBC + 自定义 Base64 + GuardTime + CKey）
├── fetch_cctv_m3u.py   # 签发脚本 → cctv.m3u
└── check_sign_sync.py  # 签名栈防漂移校验（对比 jiuyue/py/道玄央视网.py）
```

```bash
python3 cctv/fetch_cctv_m3u.py                            # 全频道（只签高清），写 cctv.m3u
python3 cctv/fetch_cctv_m3u.py --sample 5                 # 抽样体检 5 个频道
python3 cctv/fetch_cctv_m3u.py --channels cctv1,cctv13    # 只签指定频道
python3 cctv/fetch_cctv_m3u.py --defn uhd                # 4K 频道改用超高清档
python3 cctv/check_sign_sync.py                           # 校验两处签名栈无漂移
```

`ysp_sign.py` 是从 `jiuyue/py/道玄央视网.py` 抽出并独立化的签名栈，两处共用同一实现
（drpy 源与定时抓取），避免签名算法两份代码走偏。

## 目录结构

```
.
├── .github/workflows/sync.yml   # GitHub Actions：每 12 小时镜像 直播源1 与 直播源2
├── .github/workflows/cctv.yml   # GitHub Actions：每小时重签央视频直播源 → cctv.m3u
├── live.m3u                              # 直播源1（库老）——上游 live.445569.xyz 的镜像
├── dxds.txt                               # 直播源2（日后）——上游 rihou.cc:555/gggg.nzk 的镜像
├── cctv.m3u                               # 央视直播（央视频签名）——每 90 分钟/小时 重签
├──  jiuyue/api.json                    #TVBox线路（九月）
├── shiguang/api.json                # TVBox 线路（拾光）——api.json + 爬虫引擎（js/py/xbpq/open/lib）
├── dxtv.m3u                              #整合直播源
├── dxtv.txt                                #整合直播源
└── README.md
```

> 曾用于服务端重排的 `scripts/classify.py` 与 `config/category_config.json` 已退役，
> 分组逻辑移回 APK 内置 `assets/category_config.json` 处理。

## 自动运行（live.m3u 与 dxds.txt）

- **触发**：`schedule` 每 12 小时（UTC `0 */12 * * *` = 北京时间 08:00 / 20:00）；
  可在 GitHub 网页手动 `Run workflow`；push 到 `main` 亦触发一次。
- **live.m3u**：拉取上游（带重试 + `#EXTM3U` 校验）→ 按 `group-title` **整组剔除**
  三组（TG频道@stymei / 抖音 / 快手）→ 写文件。上游拉取失败时保留上一份不动。
- **dxds.txt**：拉取 `http://rihou.cc:555/gggg.nzk`（带重试 + 非空校验）→ 原样写文件。
  拉取失败时保留上一份不动。
- 两条源各自成功即提交，**仅内容变化时提交**（输出无变化 → 不提交、不二次触发，故无死循环）；
  **注意：提交信息切勿加 `[skip ci]`**——Cloudflare Pages 会据此跳过构建、导致站点不更新（此前 live.m3u 曾因此不部署）。

## 与 APK / TVBox 的关系

- **APK**：`Constants.IPTV_SOURCE_URL` 指向 `https://dxdszb.pages.dev/live.m3u`，
  拉取后由 APK 内置规则重新分组（`SP.iptvSourceSkipRegroup = false` 默认关）。
  源地址寿命约 2–3 天，本仓库每 12 小时刷新一次，APK 启动即拿 12 小时内的新鲜清单。
- **播放器**：`dxds.txt` 为 txt 格式直播源，支持该格式的播放器可直接导入。
- **TVBox（拾光）**：导入 `https://dxdszb.pages.dev/shiguang/api.json`，含点播 136 站 +
  内置直播；站点代码在 `shiguang/js`、`shiguang/py` 等目录，改后手动 push 生效。


## EPG 与台标源

- **EPG（节目单）**：统一走自建 `dx-epg` 服务（`https://dx-epg.pages.dev`）。
  - m3u 播放器（TVBox / 酷9 / OK影视）认 `#EXTM3U` 头的 `x-tvg-url="https://dx-epg.pages.dev/epg.gz"`；
  - `shiguang/api.json`（拾光线路）`lives` 条目 `epg` 字段指向 `https://dx-epg.pages.dev/epg/{date}/{name}.json`。
  - 广播电台（`gbdt.txt` 直播组 / `radio.m3u`）`x-tvg-url="https://dx-epg.pages.dev/radio.gz"`，直发小型广播 EPG（约 8KB，2G 盒子友好）。
- **台标（logo）**：上游 `live.445569.xyz` 台标原指向已宕机的 `epg.112114.xyz/logo/`。
  sync.yml 将其统一改写为 gitee `myTVlogo` 裸名库
  （`https://gitee.com/mytv-android/myTVlogo/raw/main/img/{name}.png`，OK影视 实测出图），
  并**剥离 URL 编码的中文后缀**（如 `CCTV1综合.png` → `CCTV1.png`）以匹配裸名键；
  纯全名频道（东方卫视 / 江苏卫视 / 动漫等）gitee 无对应文件，仍无图标，需另建全名图库方覆盖。
- **对齐**：`sync.yml` 末尾调用仓库根 `align_m3u.py`，用 dx-epg 的 XMLTV 把 m3u 的 `tvg-id`/`tvg-name`
  对齐到 EPG（仅最佳努力，失败不阻断）。


##线路

https://dxdszb.pages.dev/live.m3u                 库老
https://dxdszb.pages.dev/dxds.txt                  日后
https://dxdszb.pages.dev/cctv.m3u                  央视直播（央视频签名，每小时重签）
https://dxdszb.pages.dev/dxtv.m3u                整合直播源
https://dxdszb.pages.dev/dxtv.txt                   整合直播源
https://dxdszb.pages.dev/jiuyue/api.json          OK影视九月接口        
https://dxdszb.pages.dev/shiguang/api.json   OK影视拾光接口
https://dxdszb.pages.dev/radio.m3u                 广播电台直播列表（含 EPG 指向）


EPG gzip                     | `https://dx-epg.pages.dev/epg.gz` | 与能用源同名，优先填这个 |
EPG gzip                     | `https://dx-epg.pages.dev/epg.xml.gz` | 备用 |
EPG XMLTV                 | `https://dx-epg.pages.dev/epg.xml` | 原始 XML |

