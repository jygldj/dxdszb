# dxdszb · 道玄电视直播 · 上游直播源镜像

本仓库**不存 APK 代码**。它承载三条直播源线路的镜像与发布：文件推送到 `main` 后，由
Cloudflare Pages 构建为 `https://dxdszb.pages.dev/*`，经 CDN 加速后供 APK、播放器、TVBox 拉取。

## 三条线路

| 线路 | CDN 地址 | 上游源 | 说明 |
|---|---|---|---|
| 直播源1（库老） | https://dxdszb.pages.dev/live.m3u | https://live.445569.xyz/live.m3u | IPTV 直播源，每 12 小时镜像，剔除 TG频道@stymei / 抖音 / 快手 三组 |
| 直播源2（日后） | https://dxdszb.pages.dev/dxds.txt | http://rihou.cc:555/gggg.nzk | 「日后」聚合直播源（txt 格式，`#genre#` 分组），每 12 小时镜像 |
| TVBox 线路（拾光） | https://dxdszb.pages.dev/shiguang/api.json | 仓库内 `shiguang/` 目录（人工维护） | TVBox 点播 + 直播接口，136 个站点 |

> 三线 2026-09-05 实测均 `200` 可达。拾光线路为人工维护，不随 Actions 自动更新；
> 修改后需手动 push 到 `main` 触发 Pages 重新部署。

## 目录结构

```
.
├── .github/workflows/sync.yml   # GitHub Actions：每 12 小时镜像 直播源1 与 直播源2
├── live.m3u                     # 直播源1（库老）——上游 live.445569.xyz 的镜像
├── dxds.txt                     # 直播源2（日后）——上游 rihou.cc:555/gggg.nzk 的镜像
├── shiguang/                    # TVBox 线路（拾光）——api.json + 爬虫引擎（js/py/xbpq/open/lib）
├── dxtv.m3u                   #整合直播源
├── dxtv.txt                      #整合直播源
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
- 两条源各自成功即提交，仅内容变化时提交（`[skip ci]` 防死循环）。

## 与 APK / TVBox 的关系

- **APK**：`Constants.IPTV_SOURCE_URL` 指向 `https://dxdszb.pages.dev/live.m3u`，
  拉取后由 APK 内置规则重新分组（`SP.iptvSourceSkipRegroup = false` 默认关）。
  源地址寿命约 2–3 天，本仓库每 12 小时刷新一次，APK 启动即拿 12 小时内的新鲜清单。
- **播放器**：`dxds.txt` 为 txt 格式直播源，支持该格式的播放器可直接导入。
- **TVBox（拾光）**：导入 `https://dxdszb.pages.dev/shiguang/api.json`，含点播 136 站 +
  内置直播；站点代码在 `shiguang/js`、`shiguang/py` 等目录，改后手动 push 生效。


"epg": "http://cdn.1678520.xyz/epg/?ch={name}&date={date}"  实测可用
