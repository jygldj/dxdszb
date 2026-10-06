# -*- coding: utf-8 -*-
"""
天翼云盘（免费 10T+，非会员不限速、视频直链返回真实文件）直连 OK影视 —— 目录模式 spider
=================================================================================
原理（与移动云盘方案同源，且比 UC 更优）：
  盒子端 spider 直接调天翼「免签名 Cookie 模式」接口（cloud.189.cn/api/open/...），
  列目录 + 拿视频播放直链，全程无需任何中转服务 / 常开设备 / AList。
  - 天翼开放平台接口大多要签名，但 /api/open/file/ 这套“Cookie 模式”接口免签名，
    只需登录 cookie（源自浏览器自动化扫码，存设备本地文件，不硬编码）。
  - 关键接口（均为 GET + 参数 + Cookie，免签名）：
      listFiles.action                —— 列目录（folderId=-11 为根目录）
      getNewVlcVideoPlayUrl.action   —— 视频播放直链（type=2 个人云）

为什么选天翼而不是 UC：
  UC 免费账号的视频直链会被服务端替换成“开通会员引导视频”（实测 67MB 文件只返回 14MB 换片）；
  天翼官方媒体实测“非会员与会员下载同速、均标明不限速”，社区反馈仅为晚高峰跨网限速/单线程慢，
  视频直链返回的是【真实文件】，慢但能播，不会被换片。容量免费 10T+。

部署：与 我的节目.py / uc.py 并列放在 py/ 目录；OK影视 新增一个站点，api 指向本文件。

Cookie 获取：
  1. 浏览器（手机/PC）登录 https://cloud.189.cn （或 https://m.cloud.189.cn）
  2. 开发者工具 → Network，点任意请求，复制 Request Headers 里的 Cookie 整串
  3. 存成【一行纯文本】到设备 TVBox 目录下的 tianyi_cookie.txt
     （或用站点 ext.cookie / ext.cookieUrl 传入；切勿写死在本文件）
"""

import os
import re
import sys
import time
import json
import requests

sys.path.append('..')
from base.spider import Spider

# 天翼接口默认就是“Cookie 免签名”模式，关键请求头 X-Requested-With 必须带，否则返回登录页 HTML
UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36')
REFERER = 'https://cloud.189.cn'
API = 'https://cloud.189.cn/api/open/file/'

# ============================ 配置区 ============================
TY_ROOT_FID = '-11'   # 起始目录：-11=根目录，-10=私密空间，-14=我的视频（把影视文件夹放根目录即可）
TY_DEPTH = 3          # 目录递归深度
TY_TREE_TTL = 1800    # 目录树缓存（秒）
TY_DL_TTL = 1800      # 播放直链缓存（秒）
TY_VIDEO_EXTS = ('mp4', 'mkv', 'ts', 'flv', 'm4v', 'mov', 'avi', 'rmvb',
                'webm', 'mpg', 'mpeg', 'wmv', 'm2ts', '3gp', 'iso', 'mp3', 'flac', 'm4a', 'wav')
TY_ALIAS = {}         # 目录名 -> 显示名（不配则用目录名）
LOCAL_COOKIE_PATHS = [
    'http://127.0.0.1:9978/file/TVBox/tianyi_cookie.txt',
    'http://127.0.0.1:9978/file/Documents/tianyi_cookie.txt',
    'http://127.0.0.1:9978/file/Download/tianyi_cookie.txt',
    'http://127.0.0.1:9978/file/tianyi_cookie.txt',
]
# ================================================================


def _ext(name):
    m = re.search(r'\.([^.]+)$', name or '')
    return (m.group(1).lower() if m else '')


class Spider(Spider):
    def getName(self):
        return '天翼云盘'

    # ---------------- cookie ----------------
    def init(self, extend=''):
        self.cookie = ''
        self.cookie_url = ''
        self.cookie_src = ''
        self.tree = None
        self.tree_ts = 0
        self.dl_cache = {}
        self.index = {}
        try:
            if isinstance(extend, dict):
                self.cookie = (extend.get('cookie') or '').strip()
                self.cookie_url = (extend.get('cookieUrl') or extend.get('cookie_url') or '').strip()
        except Exception:
            pass
        self.load_cookie()

    def load_cookie(self):
        ck = self.cookie
        urls = []
        if self.cookie_url:
            urls.append(self.cookie_url)
        for u in LOCAL_COOKIE_PATHS:
            if u not in urls:
                urls.append(u)
        self.cookie_src = ''
        for u in urls:
            try:
                r = requests.get(u, headers={'User-Agent': UA, 'Referer': REFERER}, timeout=6)
                got = r.text.strip()
                # 天翼 cookie 关键字段较多（lt / LOGIN_USER / COOKIE_LOGIN_USER 等），非空即认为有效
                if got and '=' in got:
                    ck = got
                    self.cookie_src = u
                    break
            except Exception:
                continue
        self.cookie = ck.strip().strip('"\'').replace('\n', '').replace('\r', '')
        self.headers = {
            'User-Agent': UA,
            'Referer': REFERER,
            'Accept': 'application/json, text/plain, */*',
            'X-Requested-With': 'XMLHttpRequest',
        }
        if self.cookie:
            self.headers['Cookie'] = self.cookie

    def _api(self, action, params=None):
        if not self.cookie:
            return None
        if params is None:
            params = {}
        try:
            r = requests.get(API + action, params=params, headers=self.headers, timeout=15)
            if r.status_code == 200 and r.text:
                ct = r.headers.get('content-type', '')
                # 若未带正确 cookie，天翼会返回登录页 HTML；这里只解析 JSON
                if 'json' in ct or r.text.lstrip().startswith('{'):
                    return r.json()
        except Exception:
            pass
        return None

    # ---------------- 视频判定 / 工具 ----------------
    @staticmethod
    def is_video(item):
        ft = str(item.get('fileType') or item.get('mediaType') or '')
        if ft in ('3', 'video', 'VIDEO'):
            return True
        return _ext(item.get('fileName') or item.get('name') or '') in TY_VIDEO_EXTS

    @staticmethod
    def _is_dir(item):
        # folderList 项本身即目录；fileList 项用 isFolder 标识
        if item.get('isFolder') is True or item.get('isFolder') == 'true':
            return True
        if 'folderId' in item and 'fileId' not in item:
            return True
        return False

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

    # ---------------- 列目录（含分页）----------------
    def list_dir(self, folder_id):
        all_folders = []
        all_files = []
        page = 1
        while True:
            params = {'folderId': folder_id, 'pageNum': page, 'pageSize': 200}
            js = self._api('listFiles.action', params)
            if not js:
                break
            data = js.get('data') if isinstance(js, dict) else None
            data = data if isinstance(data, dict) else js
            folders = data.get('folderList') or []
            files = data.get('fileList') or []
            all_folders += folders
            all_files += files
            # 分页：本页不足 pageSize 即到底
            if len(folders) + len(files) < 200:
                break
            page += 1
            if page > 50:
                break
        return all_folders, all_files

    def collect_videos(self, folder_id, depth, dirname):
        items = []
        folders, files = self.list_dir(folder_id)
        for it in folders:
            if depth > 1:
                items += self.collect_videos(it.get('folderId'), depth - 1,
                                             it.get('fileName') or it.get('name') or dirname)
        for it in files:
            if not self.is_video(it):
                continue
            sz = int(it.get('size') or 0)
            if sz < 5 * 1024 * 1024:
                continue
            fid = str(it.get('fileId'))
            vid = 't_' + fid
            name = re.sub(r'\.[^.]+$', '', it.get('fileName') or it.get('name') or '')
            entry = {'id': vid, 'name': name, 'fid': fid, 'size': sz, 'dir': dirname}
            items.append(entry)
            self.index[vid] = entry
        return items

    # ---------------- 目录树（带缓存）----------------
    def scan_tree(self):
        tree = []
        folders, files = self.list_dir(TY_ROOT_FID)
        for it in folders:
            name = it.get('fileName') or it.get('name') or ''
            vids = self.collect_videos(it.get('folderId'), TY_DEPTH, name)
            if vids:
                tree.append({'tid': 't_' + str(it.get('folderId')),
                             'name': TY_ALIAS.get(name, name), 'videos': vids})
        self.tree = tree
        self.tree_ts = time.time()
        return tree

    def get_tree(self):
        now = time.time()
        if self.tree is not None and now - self.tree_ts < TY_TREE_TTL:
            return self.tree
        return self.scan_tree()

    # ---------------- 视频播放直链 ----------------
    def get_play_url(self, fid):
        now = time.time()
        if fid in self.dl_cache and now - self.dl_cache[fid][1] < TY_DL_TTL:
            return self.dl_cache[fid][0]
        params = {'fileId': fid, 'type': 2}  # type=2 个人云
        js = self._api('getNewVlcVideoPlayUrl.action', params)
        url = ''
        if isinstance(js, dict):
            data = js.get('data')
            if isinstance(data, dict):
                # 多种返回字段兜底
                url = (data.get('url') or data.get('fileUrl') or data.get('playUrl')
                       or data.get('fileDownloadUrl') or '')
            if not url:
                url = js.get('url') or js.get('fileUrl') or ''
        if url:
            self.dl_cache[fid] = (url, now)
        return url

    # ---------------- catvod 接口 ----------------
    def homeContent(self, filter):
        if not self.cookie:
            return {'class': [{'type_id': 'nocookie', 'type_name': '⚠未配置天翼cookie'}],
                    'filters': {}}
        try:
            classes = [{'type_id': c['tid'], 'type_name': c['name']} for c in self.get_tree()]
        except Exception:
            classes = []
        return {'class': classes, 'filters': {}}

    def homeVideoContent(self):
        return {'list': []}

    def categoryContent(self, tid, page, filter, ext):
        if tid == 'nocookie':
            return {'list': [], 'page': 1, 'pagecount': 1, 'limit': 0, 'total': 0}
        tree = self.get_tree()
        for c in tree:
            if c['tid'] == tid:
                vlist = [{'vod_id': v['id'], 'vod_name': v['name'],
                          'vod_remarks': self.fmt_size(v['size'])} for v in c['videos']]
                return {'list': vlist, 'page': 1, 'pagecount': 1,
                        'limit': len(vlist), 'total': len(vlist)}
        return {'list': [], 'page': 1, 'pagecount': 1, 'limit': 0, 'total': 0}

    def detailContent(self, ids):
        vid = ids[0]
        it = self.index.get(vid)
        if not it:
            self.get_tree()
            it = self.index.get(vid)
        if not it:
            return {'list': []}
        show = '原画[%s]' % self.fmt_size(it['size'])
        return {'list': [{
            'vod_id': vid,
            'vod_name': it['name'],
            'type_name': it.get('dir', ''),
            'vod_remarks': self.fmt_size(it['size']),
            'vod_content': '天翼云盘 · %s · %s' % (it.get('dir', ''), self.fmt_size(it['size'])),
            'vod_play_from': '天翼原画',
            'vod_play_url': '%s$%s' % (show, vid),
        }]}

    def searchContent(self, key, quick, page='1'):
        return {'list': []}

    def playerContent(self, flag, pid, vipFlags):
        if not pid.startswith('t_'):
            return {'url': '', 'parse': 0, 'jx': 0}
        fid = pid[2:]
        url = self.get_play_url(fid)
        if not url:
            return {'url': '', 'parse': 0, 'jx': 0, 'header': {'User-Agent': UA}}
        # 天翼播放直链校验登录态，需带 Cookie + Referer
        return {'url': url, 'parse': 0, 'jx': 0,
                'header': {'User-Agent': UA, 'Referer': REFERER, 'Cookie': self.cookie}}

    def destroy(self):
        return 'ok'


if __name__ == '__main__':
    pass
