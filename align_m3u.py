#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
对照 XMLTV 全表重写 dxtv.m3u 的 tvg-id / tvg-name。
显示名（逗号后）不动。对不上的行保留原标识。
把头改成 x-tvg-url=https://dx-epg.pages.dev/epg.gz

用法（在 dxdszb 仓库根目录）：
  python align_m3u.py --xml /path/to/epg.xml --m3u dxtv.m3u
"""

from __future__ import annotations

import argparse
import re
import sys
import xml.etree.ElementTree as ET

HEADER = '#EXTM3U x-tvg-url="https://dx-epg.pages.dev/epg.gz"'
CCTV_NUM = re.compile(r"CCTV[-_ ]?(\d+)(\+)?", re.I)


def load_ids(xml_path: str) -> tuple[set[str], dict[str, str]]:
    root = ET.parse(xml_path).getroot()
    ns = ""
    if root.tag.startswith("{"):
        ns = root.tag.split("}", 1)[0] + "}"
    ids: set[str] = set()
    alias_to_id: dict[str, str] = {}
    for ch in root.findall(f"{ns}channel"):
        cid = (ch.get("id") or "").strip()
        if not cid:
            continue
        ids.add(cid)
        alias_to_id.setdefault(cid, cid)
        alias_to_id.setdefault(cid.casefold(), cid)
        for el in ch.findall(f"{ns}display-name"):
            name = (el.text or "").strip()
            if name:
                alias_to_id.setdefault(name, cid)
                alias_to_id.setdefault(name.casefold(), cid)
    return ids, alias_to_id


def norm_cctv(text: str) -> str | None:
    m = CCTV_NUM.search(text or "")
    if not m:
        return None
    n = str(int(m.group(1)))
    return f"CCTV{n}+" if m.group(2) else f"CCTV{n}"


def resolve(ids: set[str], alias: dict[str, str], *candidates: str) -> str | None:
    for raw in candidates:
        text = (raw or "").strip()
        if not text:
            continue
        if text in ids:
            return text
        if text in alias:
            return alias[text]
        folded = text.casefold()
        if folded in alias:
            return alias[folded]
        cctv = norm_cctv(text)
        if cctv and cctv in ids:
            return cctv
    return None


def attr(line: str, key: str) -> str:
    m = re.search(rf'{re.escape(key)}="([^"]*)"', line)
    return m.group(1) if m else ""


def display_name(line: str) -> str:
    if "," not in line:
        return ""
    return line.rsplit(",", 1)[-1].strip()


def set_attr(line: str, key: str, value: str) -> str:
    if re.search(rf'{re.escape(key)}="[^"]*"', line):
        return re.sub(rf'{re.escape(key)}="[^"]*"', f'{key}="{value}"', line, count=1)
    return re.sub(r"(#EXTINF:-?\d+)", rf'\1 {key}="{value}"', line, count=1)


def process(xml_path: str, m3u_path: str) -> int:
    ids, alias = load_ids(xml_path)
    with open(m3u_path, encoding="utf-8", errors="replace") as f:
        lines = f.read().splitlines()
    out: list[str] = []
    hit = miss = 0
    for i, line in enumerate(lines):
        if i == 0 and line.startswith("#EXTM3U"):
            out.append(HEADER)
            continue
        if not line.startswith("#EXTINF"):
            out.append(line)
            continue
        mapped = resolve(
            ids,
            alias,
            attr(line, "tvg-id"),
            attr(line, "tvg-name"),
            display_name(line),
        )
        if mapped:
            line = set_attr(line, "tvg-id", mapped)
            line = set_attr(line, "tvg-name", mapped)
            hit += 1
        else:
            miss += 1
        out.append(line)
    with open(m3u_path, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(out) + "\n")
    print(f"[align] 对齐 {hit} 行，保留原标识 {miss} 行，头已改为 epg.gz")
    return 0


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--xml", required=True)
    p.add_argument("--m3u", required=True)
    args = p.parse_args()
    return process(args.xml, args.m3u)


if __name__ == "__main__":
    sys.exit(main())
