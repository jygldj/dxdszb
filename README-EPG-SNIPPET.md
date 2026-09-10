# dxdszb 需要手工改的两处

## 1. shiguang/api.json

顶层（约第 7 行）把：

```
"epg": "http://cdn.1678520.xyz/epg/?ch={name}&date={date}",
```

改成：

```
"epg": "https://dx-epg.pages.dev/epg/{date}/{name}.json",
```

`lives` 里「道玄直播」条目把：

```
"epg": "https://dx-iptv.pages.dev/epg.xml",
```

改成：

```
"epg": "https://dx-epg.pages.dev/epg/{date}/{name}.json",
```

## 2. README.md 末尾

把：

```
"epg": "http://cdn.1678520.xyz/epg/?ch={name}&date={date}"  实测可用
```

改成：

```
TVBox EPG：https://dx-epg.pages.dev/epg/{date}/{name}.json
酷9 / OK影视：m3u 头 x-tvg-url="https://dx-epg.pages.dev/epg.gz"
```

## 3. 对齐 dxtv.m3u

把 `align_m3u.py` 拷到 dxdszb 仓库根，先从 dx-epg 拿一份 `epg.xml`，然后：

```
python align_m3u.py --xml epg.xml --m3u dxtv.m3u
```

显示名不动；对不上 EPG 的台保留原 `tvg-name`。
