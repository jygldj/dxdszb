# -*- coding: utf-8 -*-
"""
UC 网盘（2T 永久）直连 OK影视 —— 目录模式 spider
=================================================================
原理（与移动云盘方案同源，但 UC 没有免登录外链）：
  盒子端 spider 直接调 UC 主 API（pc-api.uc.cn/1/clouddrive），
  列目录 + 拿原始下载直链，全程无需任何中转服务 / 常开设备。
  代价：必须内置登录 cookie（存设备本地文件，不硬编码）。

两种模式（改下方 UC_MODE）：
  self  —— 列【自己网盘】某目录（默认根目录）下的视频，自动按子目录分类。
          播放 = 直接 file/download 自己文件的直链，无需分享 / 转存。最干净（推荐）。
  share —— 列【分享链接】目录，播放前会把文件转存到你网盘再下载。

部署：与 我的节目.py 并列放在 py/ 目录；OK影视 新增一个站点，api 指向本文件。

Cookie 获取（self / share 都需要）：
  1. 浏览器（手机/PC）登录 https://drive.uc.cn
  2. 开发者工具 → Network，点任意请求，复制 Request Headers 里的 Cookie 整串
     （必须含 __pus 字段，否则无效）
  3. 存成【一行纯文本】到设备 TVBox 目录下的 uc_cookie.txt
     （或用站点 ext.cookie / ext.cookieUrl 传入；切勿写死在本文件）
"""

import os
import re
import sys
import time
import json
import base64
from urllib.parse import quote

import requests

sys.path.append('..')
from base.spider import Spider

UA = ('Mozilla/5.0 (Linux; Android 8.0.0; SM-G955U Build/R16NW) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/116.0.0.0 Mobile Safari/537.36')
REFERER = 'https://drive.uc.cn'

API = 'https://pc-api.uc.cn/1/clouddrive/'
PR = ('pr=UCBrowser&fr=pc&sys=darwin&ve=1.8.6&ut='
      'Nk27FcCv6q1eo6rXz8QHR/nIG6qLA3jh7KdL+agFgcOvww==')

# ============================ 配置区 ============================
UC_MODE = 'self'          # 'self' 自己网盘目录 | 'share' 分享链接
UC_ROOT_FID = '0'         # self 模式起始目录 fid（'0' = 根目录，把影视文件夹放根目录即可）
UC_SHARE_URL = ''         # share 模式：https://drive.uc.cn/s/xxxx
UC_SHARE_PWD = ''         # share 模式：提取码（无则留空）
UC_DEPTH = 3              # 目录递归深度
UC_TREE_TTL = 1800        # 目录树缓存（秒）
UC_DL_TTL = 1800          # 下载直链缓存（秒）
UC_VIDEO_EXTS = ('mp4', 'mkv', 'ts', 'flv', 'm4v', 'mov', 'avi', 'rmvb',
                 'webm', 'mpg', 'mpeg', 'wmv', 'm2ts', '3gp', 'iso')
UC_ALIAS = {}             # 目录名 -> 显示名（不配则用目录名）
LOCAL_COOKIE_PATHS = [
    'http://127.0.0.1:9978/file/TVBox/uc_cookie.txt',
    'http://127.0.0.1:9978/file/Documents/uc_cookie.txt',
    'http://127.0.0.1:9978/file/Download/uc_cookie.txt',
    'http://127.0.0.1:9978/file/uc_cookie.txt',
]
# ================================================================


def _ext(name):
    m = re.search(r'\.([^.]+)$', name or '')
    return (m.group(1).lower() if m else '')


def _b64d(s):
    s = s.replace(' ', '+')
    s = s.replace('-', '+').replace('_', '/')
    s += '=' * (-len(s) % 4)
    return base64.b64decode(s.encode()).decode()


class Spider(Spider):
    def getName(self):
        return 'UC网盘'

    # ---------------- cookie ----------------
    def init(self, extend=''):
        self.cookie = ''
        self.cookie_url = ''
        self.cookie_src = ''
        self.tree = None
        self.tree_ts = 0
        self.dl_cache = {}
        self.index = {}
        self.save_dir_id = None
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
                if got and '__pus' in got:
                    ck = got
                    self.cookie_src = u
                    break
            except Exception:
                continue
        self.cookie = ck.strip().strip('"\'').replace('\n', '').replace('\r', '')
        self.headers = {
            'User-Agent': UA,
            'Referer': REFERER,
            'Content-Type': 'application/json',
        }
        if self.cookie:
            self.headers['Cookie'] = self.cookie

    def _api(self, url, params=None, data=None, method='GET'):
        if not self.cookie:
            return None
        if params is None:
            params = {}
        try:
            if method == 'GET':
                r = requests.get(API + url, params=params, headers=self.headers, timeout=15)
            else:
                body = json.dumps(data or {}, ensure_ascii=False)
                r = requests.post(API + url, data=body.encode('utf-8'),
                                  headers=self.headers, timeout=15)
            if r.status_code == 200:
                return r.json()
        except Exception:
            pass
        return None

    # ---------------- 视频判定 / 工具 ----------------
    @staticmethod
    def is_video(item):
        oc = (item.get('obj_category') or '').lower()
        if oc == 'video':
            return True
        return _ext(item.get('file_name') or '') in UC_VIDEO_EXTS

    @staticmethod
    def _is_dir(item):
        d = item.get('dir')
        return d is True or d == 'true' or d == 1

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

    # ---------------- 目录列举（self 模式）----------------
    def list_dir(self, fid):
        js = self._api('file/sort?%s&pdir_fid=%s&_page=1&_size=200&_sort=file_type:asc,file_name:asc'
                      % (PR, fid), method='GET')
        if not js or js.get('data') is None:
            return []
        return js['data'].get('list') or []

    def collect_videos(self, fid, depth, dirname):
        items = []
        for it in self.list_dir(fid):
            if self._is_dir(it):
                if depth > 1:
                    items += self.collect_videos(it.get('fid'), depth - 1,
                                                 it.get('file_name') or dirname)
            elif self.is_video(it):
                sz = int(it.get('size') or 0)
                if sz < 5 * 1024 * 1024:
                    continue
                vid = 'u_' + str(it.get('fid'))
                entry = {'id': vid, 'name': re.sub(r'\.[^.]+$', '', it.get('file_name') or ''),
                         'fid': str(it.get('fid')), 'size': sz, 'dir': dirname}
                items.append(entry)
                self.index[vid] = entry
        return items

    # ---------------- 目录列举（share 模式）----------------
    def get_share_data(self):
        if not UC_SHARE_URL:
            return None
        m = re.search(r'drive\.uc\.cn/s/([^?]+)', UC_SHARE_URL)
        if m:
            return {'shareId': m.group(1), 'pwd': UC_SHARE_PWD}
        return None

    def get_share_token(self, sd):
        js = self._api('share/sharepage/token?%s' % PR,
                       data={'pwd_id': sd['shareId'], 'passcode': sd['pwd'] or ''}, method='POST')
        try:
            if js and js.get('data') and js['data'].get('stoken'):
                return js['data']['stoken']
        except Exception:
            pass
        return None

    def list_share_dir(self, sd, pdir_fid, stoken):
        js = self._api('share/sharepage/detail?%s&pwd_id=%s&stoken=%s&pdir_fid=%s'
                      '&force=0&_page=1&_size=200&_sort=file_type:asc,file_name:asc'
                      % (PR, sd['shareId'], quote(stoken), pdir_fid), method='GET')
        if not js or js.get('data') is None:
            return []
        return js['data'].get('list') or []

    def collect_share_videos(self, sd, fid, depth, stoken, dirname):
        items = []
        for it in self.list_share_dir(sd, fid, stoken):
            if self._is_dir(it):
                if depth > 1:
                    items += self.collect_share_videos(sd, it.get('fid'), depth - 1, stoken,
                                                      it.get('file_name') or dirname)
            elif self.is_video(it):
                sz = int(it.get('size') or 0)
                if sz < 5 * 1024 * 1024:
                    continue
                vid = 'u_' + str(it.get('fid'))
                entry = {'id': vid, 'name': re.sub(r'\.[^.]+$', '', it.get('file_name') or ''),
                         'fid': str(it.get('fid')), 'size': sz, 'dir': dirname}
                items.append(entry)
                self.index[vid] = entry
        return items

    # ---------------- 目录树（带缓存）----------------
    def scan_tree(self):
        tree = []
        if UC_MODE == 'self':
            for it in self.list_dir(UC_ROOT_FID):
                if self._is_dir(it):
                    name = it.get('file_name') or ''
                    vids = self.collect_videos(it.get('fid'), UC_DEPTH, name)
                    if vids:
                        tree.append({'tid': 'u_' + str(it.get('fid')),
                                     'name': UC_ALIAS.get(name, name), 'videos': vids})
        else:
            sd = self.get_share_data()
            if sd:
                stoken = self.get_share_token(sd)
                if stoken:
                    for it in self.list_share_dir(sd, '0', stoken):
                        if self._is_dir(it):
                            name = it.get('file_name') or ''
                            vids = self.collect_share_videos(sd, it.get('fid'), UC_DEPTH,
                                                           stoken, name)
                            if vids:
                                tree.append({'tid': 'u_' + str(it.get('fid')),
                                             'name': UC_ALIAS.get(name, name), 'videos': vids})
        self.tree = tree
        self.tree_ts = time.time()
        return tree

    def get_tree(self):
        now = time.time()
        if self.tree is not None and now - self.tree_ts < UC_TREE_TTL:
            return self.tree
        return self.scan_tree()

    # ---------------- 播放直链 ----------------
    def ensure_save_dir(self):
        js = self._api('file/sort?%s&pdir_fid=0&_page=1&_size=200'
                      '&_sort=file_type:asc,updated_at:desc' % PR, method='GET')
        if js and js.get('data'):
            for it in js['data'].get('list') or []:
                if it.get('file_name') == 'TV' and self._is_dir(it):
                    self.save_dir_id = str(it.get('fid'))
                    return
        js = self._api('file?%s' % PR,
                       data={'pdir_fid': '0', 'file_name': 'TV',
                             'dir_path': '', 'dir_init_lock': 'false'}, method='POST')
        if js and js.get('data') and js['data'].get('fid'):
            self.save_dir_id = str(js['data']['fid'])

    def save_to_my_drive(self, sd, fid, stoken):
        if self.save_dir_id is None:
            self.ensure_save_dir()
        if not self.save_dir_id:
            return None
        js = self._api('share/sharepage/save?%s' % PR, data={
            'fid_list': [fid], 'fid_token_list': [fid], 'to_pdir_fid': self.save_dir_id,
            'pwd_id': sd['shareId'], 'stoken': stoken, 'pdir_fid': '0', 'scene': 'link'},
            method='POST')
        if not js or js.get('data') is None:
            return None
        task_id = js['data'].get('task_id')
        if not task_id:
            return None
        for _ in range(4):
            t = self._api('task?%s&task_id=%s&retry_index=0' % (PR, task_id), method='GET')
            try:
                top = t['data']['save_as']['save_as_top_fids']
                if top:
                    return top[0]
            except Exception:
                pass
            time.sleep(1)
        return None

    def get_download_url(self, fid):
        now = time.time()
        if fid in self.dl_cache and now - self.dl_cache[fid][1] < UC_DL_TTL:
            return self.dl_cache[fid][0]
        if UC_MODE == 'self':
            real_fid = fid
        else:
            sd = self.get_share_data()
            stoken = self.get_share_token(sd) if sd else None
            real_fid = self.save_to_my_drive(sd, fid, stoken) if sd else None
            if not real_fid:
                return ''
        js = self._api('file/download?%s&uc_param_str=' % PR,
                       data={'fids': [real_fid]}, method='POST')
        url = ''
        try:
            if js and js.get('data'):
                url = (js['data'][0].get('download_url') or '') if isinstance(js['data'], list) \
                    else (js['data'].get('download_url') or '')
        except Exception:
            url = ''
        if url:
            self.dl_cache[fid] = (url, now)
        return url

    # ---------------- catvod 接口 ----------------
    def homeContent(self, filter):
        if not self.cookie:
            return {'class': [{'type_id': 'nocookie', 'type_name': '⚠未配置UC cookie'}],
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
            'vod_content': 'UC网盘 · %s · %s' % (it.get('dir', ''), self.fmt_size(it['size'])),
            'vod_play_from': 'UC原画',
            'vod_play_url': '%s$%s' % (show, vid),
        }]}

    def searchContent(self, key, quick, page='1'):
        return {'list': []}

    def playerContent(self, flag, pid, vipFlags):
        if not pid.startswith('u_'):
            return {'url': '', 'parse': 0, 'jx': 0}
        fid = pid[2:]
        url = self.get_download_url(fid)
        if not url:
            return {'url': '', 'parse': 0, 'jx': 0, 'header': {'User-Agent': UA}}
        return {'url': url, 'parse': 0, 'jx': 0,
                'header': {'User-Agent': UA, 'Referer': REFERER, 'Cookie': self.cookie}}

    def destroy(self):
        return 'ok'


if __name__ == '__main__':
    pass
