# dx-epg · 道玄自有 EPG 仓库

把公开 EPG 源全量镜像为一份标准 XMLTV 节目单，由 Cloudflare Pages 托管。

实测可用的直播源（OK影视 / 酷9 已出节目单）：

```
http://dxdszb.pages.dev/dxtv.m3u
```

## 对外端点

| 产物 | 地址 | 用途 |
|---|---|---|
| 直播源 | `http://dxdszb.pages.dev/dxtv.m3u` | OK影视 / 酷9 / 影视仓 直播列表 |
| EPG gzip | `https://dx-epg.pages.dev/epg.gz` | 与能用源同名，优先填这个 |
| EPG gzip | `https://dx-epg.pages.dev/epg.xml.gz` | 备用 |
| EPG XMLTV | `https://dx-epg.pages.dev/epg.xml` | 原始 XML |

## 原理

```
GitHub Actions（每 12h + 手动）
      python generate_epg.py
      拉取上游 XMLTV（fanmingming，失败则回退）
      体积下限 / ElementTree 解析 / sha256 / CCTV 别名
      有变化才写 epg.xml、epg.xml.gz、epg.gz
      推送 main → Cloudflare Pages
```

## 文件结构

| 文件 | 作用 |
|---|---|
| `generate_epg.py` | 镜像上游 XMLTV，写出 xml / gz，并补 CCTV-1 等 display-name |
| `.github/workflows/epg.yml` | 每 12 小时 + 手动；有变化才提交 |
| `epg.xml` / `epg.xml.gz` / `epg.gz` | Pages 托管的节目单 |
| `dxtv.m3u` | 直播列表；头为 `x-tvg-url=.../epg.gz` |

## 接入

m3u 头部（OK影视 / 酷9 认 `x-tvg-url` + `.gz`）：

```
#EXTM3U x-tvg-url="https://dx-epg.pages.dev/epg.gz"
```

频道行用 EPG 的 channel id 作 `tvg-name`：

```
#EXTINF:-1 tvg-name="CCTV1" tvg-logo="https://gitee.com/mytv-android/myTVlogo/raw/main/img/CCTV1.png" group-title="央视咪咕",CCTV-1综合
```

TVBox `api.json`：

```json
{ "epg": "https://dx-epg.pages.dev/epg.gz" }
```

换源时先删旧直播源再添加，避免缓存旧 EPG。

## 维护

- 推送 `main` 即触发 Pages 重新部署；也可在 Actions 手动 Run workflow
- 上游失效或内容未变时不提交
- 工作流使用普通 commit
- Settings → Actions → General → Workflow permissions = Read and write
