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

### 央视频 403 的正确模型（2026-10-24 全量对照实验 · 已穷尽排除）

> ⚠️ **本节的「换 CDN host 无效」一条已被 23:55 的 V5 取证推翻** ——
> 换 host **确实有效**（存活率 17% → 75%），真因是节点质量而非"动态限流窗口"。
> 详见下方「✅ V5 真根因」。本节其余观测（签发额度独立、token 不绑 IP、
> 签名无漂移、cdn 与 vkey 绑定等）仍然成立。

此前记录的「403 = 节点热窗口限流、非 IP 绑定」**已被证伪**。全部观测汇总：

| 观测项 | 结果 | 判读 |
|---|---|---|
| 签发接口 `bkliveinfo.ysp.cctv.cn` 连发 80 次 | **80/80 全通**（57ms/次） | 签发额度**独立、从不封** |
| 全量 63 频道只签发（不碰 CDN） | **63 次签发 / 18.7s / 成功 60 失败 3** | 签发侧毫无压力 |
| **换 CDN host**（对新 token） | 无效 | 403 非 host 锁定 |
| **换全新 uuid4 + randFlag**（4 组） | **全 403** | 403 非设备身份配额 |
| **ExoPlayer 默认 UA**（无 `qqlive`） | **200，照样可播** | 403 非防盗链（**不能无 UA**，但普通浏览器 UA 即可） |
| **同一 token 连取 4 次** | **4/4 PLAY** | 403 非一次性消费 |
| **同一 token 隔 10s 重连 4 次** | **4/4 = 200** | 403 非时间过期 |
| **同一 token 隔 60s 取切片** | **200 / 2.9 MB** | 同上 |
| **cctv 版 vs drpy 版签名栈逐阶段比对** | pkt(335B)/sig/guard_time/cbc 密文/xor/b64/**cKey 全部逐字节一致** | **实现无漂移** |
| 改 URL 里的 `cdn` 值 | 403 | cdn 与 vkey 密文绑定 |
| 伪造签发时间戳 | 服务端拒签 | 时间戳新鲜度是硬校验 |
| **同一 token 每 5 分钟探一次，连探 25 分钟** | **m3u8 与末切片全部 200** | token **不会随时间退化** |
| 同一 IP 一度 24/24 全 200，隔一段时间后连签新 token 也全 403 | — | **403 是按「取流方出口 + CDN 节点」累计触发的动态状态** |

**核心结论**：403 与我方实现、请求头、UA、档位、CDN host、设备身份、token 新旧
**全都无关**；是央视频取流侧的**动态限流窗口**。

> 由此得到两条工程纪律：
> **① 限流是「取流额度」而非「签发额度」。**
> 签 63 个频道只要 63 次签发（额度独立、毫无压力），
> 但**预检本身要拉 m3u8 + 分片，每条线路 2 次取流**——真正烧额度的是预检。
> **② 签发与取流必须分离。**
> 签发交给 Actions（境外 runner、每小时刷新）；
> **取流发生在主公自己的播放器上（主公国内 IP，从未被跑批污染）**。

### 本项目最终采用的策略

1. **主流程只签发，绝不碰 CDN** → 63 频道约 **19 秒**跑完
   （此前版本需 10+ 分钟且中途集体 403 —— 预检自己烧穿了额度）；
2. **签发侧筛节点**（零取流消耗）：央视频把 token **随机分配到不同 CDN 节点**，
   `hlsliveali-cdn` 稳定、`live-dtocnc-cdn` 间歇 403（实测 12 枚样本中 3 枚全败
   且全落在它身上，另一次 24 枚又全 200 → 动态劣化，非死节点）。
   落在劣化节点就**重签**，直到命中稳定节点；
3. **每频道挂 3 条线，且分散到不同 CDN 节点** —— 三条线**完全同名、同 tvg-id**
   （与仓内 `live.m3u` 既有惯例一致），播放器会**合并为一路的多个备选源**，
   坏线自动换源。跨节点是关键：不同节点 = 各自独立的限流计数池；
4. **抽样体检**（`--sample`，默认 3 频道）只出健康报告，**不参与线路筛选**；
5. Actions 每小时在**境外 runner** 重签，覆盖 m3u 并推送 Pages。

### 用户实测的机理与对策（2026-10-24 v3）

主公实测本地 `cctv.m3u`：**cctv13 / 6 / 8 相对较好但不时断流，其它频道较差，卫视同样**，
概括为「有时能连，有时不能，连上后时断时连」。

**机理（已实测定位）**：央视频限流按「**取流方出口 + CDN 节点**」累计。
播放器（OK影视 / TVBox）加载直播列表时会对所有频道**并发测速**，
瞬间产生数十次 m3u8 拉取 —— 若这些线路全落在**同一个节点**，
该节点的计数池瞬间打满 → 整批频道 403 → 表现为「时断时连」。
cctv13 / 6 / 8 相对较好 = 抢在限流触发前被拉到。

**对策（v3 已落地）**：
1. **多线跨节点分散** —— 每频道 3 条线刻意落在**不同 CDN host**
   （不同节点 = 各自独立的计数池，摊薄并发压力）；
2. **多线完全同名 + 同 tvg-id** —— 与仓内 `live.m3u` 惯例一致，
   播放器合并为一路的多个备选源，坏线自动换源；
3. **签发与取流彻底分离** —— Actions（境外 runner）只签发，
   取流在主公自己的国内 IP 上发生（从不被跑批污染）。

> **工程铁律**：验证央视频取流**必须在境外 IP 上做**（Actions / 境外 VPS）。
> 在本机反复试取流会烧穿自己的取流额度 —— 限流窗口可达数十分钟且静置不恢复。

### ⚠️ V3 全灭的根因（2026-10-24 23:00 取证 → **23:55 已修正，见下节 V5 真根因**）

主公实测 **V3 全面沦陷、无一生还**（各频道**疑似无数据流量传输**），
而同一时段 **V2 本地文件稳定播放 10+ 分钟不断流**（cctv13 / cctv8 / 河南卫视）。

**决定性对照（同一 IP、同一 UA、同一时刻）**：

| 样本 | m3u8 | 最新分片 |
|---|---|---|
| **21:11 签发（V2 时代）** | **200** | **200 / 65536B / sync=0x47 明文 TS** ✅ |
| 19:27 / 21:20 / 22:21 / 22:27 签发 | 403 | — ❌ |
| V3 全部 180 条 | 403 | — ❌ |
| 此刻新签 5 枚 | **0/5 出生即活** | — ❌ |
| 老 token 加乱 UID 头 | 403 | — ❌（UID/guid 需匹配） |
| 老 token 不带 UID 头 | **200** | **200 / sync=0x47** ✅ |

**逐项证伪（全部实测，勿再回头试）**：

| 假设 | 实测 | 判定 |
|---|---|---|
| IP 封禁 | 换**不同出口**（服务端代理）取流仍 403 | **否决** |
| 取流侧限流窗口 | 新 vkey **签发后 0.0s 即 403**，无任何时间窗 | **否决** |
| token 一次性消费 | 老 token 连取 4 次全 200 | **否决** |
| token 过期 | 老 token（21:11 签，23:18 测）仍 200 且 PDT=当前时刻 | **否决** |
| svrtime / pid / query 参与校验 | 老 vkey 换 svrtime、换 pid、加随机 query **仍 200** | **否决** |
| 设备身份配额 | 换全新 uuid4 + 换 appVer + 全套新身份签发，仍全 403 | **否决** |
| CDN host 锁定 | 15 个 host 全扫，新 token 全 403 | **否决** |
| 防盗链 / UA | ExoPlayer 默认 UA 也 200；UID/guid 需匹配已确证 | **否决** |
| 签名实现漂移 | 与 drpy 源逐阶段比对：pkt/sig/guard_time/cbc/xor/b64/**cKey 逐字节一致** | **否决** |
| 档位映射错误 | fhd→+3 / shd→+2 / hd→+1，与 formats 表完全一致 | **否决** |
| 本机时钟偏差 | 本机 epoch 与 baidu/qq 的 Date 头误差 3 秒内 | **否决** |
| 服务端 `http_header.UID` | **UID 就是我们自己的 guid**（实测同值），带上才校验、不带不校验 | **已确证，非根因** |

**结论（23:00 版 · 已被 23:55 的 V5 取证修正，勿照搬）**：
> **央视频服务端在 21:11 之后停止授予「可用授权」**——
> 签发接口依然返回 `iretcode=0` + 结构完全正常的 playurl，
> 但服务端签发的 vkey **在 CDN 侧未获激活**（`Tengine` 边缘直接 403，
> `Via: ens-cache*,403666`）。**「签发成功」≠「可播」**。
> 老 vkey 因签于授权窗口开启期，至 23:18 仍 200 且 `#EXT-X-PROGRAM-DATE-TIME`
> 为当前时刻（实时流，非缓存冻结）。
> V2 能播、V3 全灭，差别**不在文件结构，而在签发时刻**。

### ✅ V5 真根因（2026-10-24 23:55 终局 · 推翻 23:00 的"授权窗口"结论）

23:00 的结论把 403 归因于「服务端授权窗口关闭」，属**过度归因**。
23:55 的**逆序对照实验**证明真因是 **CDN 节点质量**，且可用**换 host 直接绕开**：

**实验设计（排除"激活延迟"混淆）**
同一枚 token、同一时刻、仅换 host，且**让换 host 的一侧先测**
（若真因是"签发后需激活"，则应当是**后测**的活 —— 实测相反）：

| 侧 | 存活 | 可用率 |
|---|---|---|
| `hlsliveali-cdn`（换 host 侧） | 9/12 | **75%** |
| `live-dtocnc-cdn`（原 host 侧） | 0/12 | **0%** |

- `live-dtocnc-cdn` **两批独立样本 4/4 + 3/3 全灭**，非抽样偶然；
- 8 秒后原地重测 **6/6 全活** → token 无短时衰减，**host 才是唯一因果**；
- 换 host 后跨 6 频道实测**分片可取 23/24 = 96%**；
- **死的是节点，不是 token** —— dtocnc 签出的 token 换到 hlsliveali 即取到明文 TS。

**V3 全灭机制由此闭合**：
V3 的第 2/3 条线被**系统性送到 0% 可用的 `live-dtocnc-cdn`**
（56/60 频道），玩家拿到一堆死链 → 表现为「各频道疑似无数据流量传输」。

**V5 铁律（在 V4 之上）**：
1. **签发后立即换 host 再验活** —— 顺序不能反（先验活再换 = 只有 17% 通过率，
   白白浪费一次验活）；
2. **只换 host，path 与 query 原样保留** —— `cdn` 参数与 vkey 密文绑定，
   改 query 里的 `cdn` 值无效（实测 403）；
3. **实时性判据多路兜底** —— 首选 `#EXT-X-PROGRAM-DATE-TIME`；
   无 PDT 时退化为「分片可取 + 序号推进」。
   （CDN 偶发返回无 PDT 的简版清单，旧版一刀切判死会**误杀好线路** —— 实测 24 样本中 3 例）；
4. **备选线价值来自多枚独立 token，而非多节点** —— 全部归一到好节点，
   绝不再把线路往死节点上送；
5. **验活失败允许重签** —— `m3u8=404` = 该 token 在 CDN 边缘尚未就绪，
   换一枚新 token 即可绕开；`m3u8=403` = 节点侧问题，重签亦可能绕开。

**当前状态（2026-10-25 00:01）**：
- 全量跑批**成功产出 52 频道 / 154 条线路**（央视 63 + 卫视 73 + CGTN 18 条线路）；
- 抽样体检 **5/5 可播**；**独立复核**（脱离脚本、直接读产出文件取流）
  **14/14 = 100% 可播**；
- 8 个频道本轮验活失败被丢弃（nmgws / ynws / shanxiws2 / qhws / xzws / xjws /
  cetv1 / gxpd）—— 均为**单枚 token 边缘未就绪**，下轮重签即可补回；
- 全程耗时 201.5s / 231 次签发。

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

