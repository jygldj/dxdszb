# -*- coding: utf-8 -*-
"""
天翼云盘（免费 10T+）直连 OK影视 —— 「我的视频 / 我的音乐」目录模式 spider
=================================================================================
一句话原理
  盒子端 spider 直接调天翼开放接口（cloud.189.cn/api/open/file/...）「Cookie 模式」（免签名）：
    1) listFiles.action              —— 列目录（folderId=-11 为根目录）
    2) getFileDownloadUrl.action     —— 拿**真实文件**的下载/播放直链（视频、音频通用）
  全程不需要中转服务 / 常开电脑 / AList，只需要一份登录 cookie 存在设备本地文件里。

【分享范围】只暴露 TY_ONLY_DIRS 里列出的顶层目录（当前 = 我的视频 / 我的音乐），
  同步盘 / 我的图片 / 我的文档 / 我的应用 / 我的备份 一律不出现在盒子里。

★ 2026-10-07 实测定论（改代码前务必先读，这几条都是踩过的坑）
  1. **接口返回的是 XML，不是 JSON**！
     content-type = application/xml，结构是 <listFiles><fileList><folder>…/<file>…</fileList>。
     旧版按 r.json() 解析 → 永远空列表（真机表现：分类空白/找不到数据）。
     → 本文件改为【正则解析 XML】，零依赖，Jython 2.7 / Python 3 都能跑。
     目录字段：id / name / parentId / fileCount / createDate
     文件字段：id / name / size / md5 / fileCata / mediaType / lastOpTime / createDate
  2. **播放直链必须用 getFileDownloadUrl.action，不要用 getNewVlcVideoPlayUrl.action**
     - vlc 接口对本账号视频返回 200 但 body 为空；对音频直接 400 PermissionDenied
       （<error><code>PermissionDenied</code>file type error or not support）
     - getFileDownloadUrl 对视频、音频都返回正常直链，实测：
         山涧小溪.mp4 → 200 video/mp4      Content-Length=70236125（真实大小，ftyp isom）
         肝之破茧.m4a → 200 octet-stream   Content-Length=5767386 （真实大小，ftyp M4A）
       → 天翼返回的是**真实文件**（UC 免费账号返回的是 15MB 会员引导替换流），非会员也不限速
  3. **直链有效期只有 ~5 分钟**：参数 expired 是毫秒时间戳，实测比 now 只多 301 秒。
     → TY_DL_TTL 必须远小于 300 秒（现设 120 秒），否则缓存里的链接会过期失效，
        真机表现为「播放一两次后就加载失败」。
  4. **直链不需要 Cookie**：不带 Cookie/Referer 直接 GET 也 200（与 UC 不同，UC 必须带）。
     但**列目录接口必须带 Cookie**，且请求头必须带 X-Requested-With: XMLHttpRequest，
     否则返回登录页 HTML。
  5. 直链首跳是 302（download.cloud.189.cn → CDN），已支持 Range（206）。
     默认 TY_RESOLVE=1 会先把 302 解析成最终 URL 再交给播放器，兼容性更好；
     若某次解析失败会自动回退用原始 302 链接（播放器一般也能跟随）。
  6. 系统固定目录 ID（实测本机）：-11 根目录 / -13 我的视频 / -14 我的音乐 / -12 我的图片
     / -15 我的文档 / -16 我的应用。**注意 -13 是视频、-14 是音乐，别写反。**

Cookie 获取（三种方式，优先级从高到低）
  1. 站点 ext.cookie：直接把整串 cookie 写进 api.json 的 ext 里
  2. 站点 ext.cookieUrl：指向一个能取到纯文本 cookie 的 URL
  3. 设备本地文件（默认）：LOCAL_COOKIE_PATHS，首个非空且含 COOKIE_LOGIN_USER 的生效
     —— 切勿把 cookie 硬编码进本文件（会随 GitHub 泄露）
"""
import re
import sys
import time
import json

try:
    import requests
except ImportError:
    requests = None

sys.path.append('..')
from base.spider import Spider

UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36')
REFERER = 'https://cloud.189.cn'
API = 'https://cloud.189.cn/api/open/file/'

# ============================ 配置区 ============================
# ★ 分享白名单：只有名字在列表里的顶层目录会出现在盒子里。
#   设为空列表 [] 则不过滤（暴露根目录下全部子目录）。
TY_ONLY_DIRS = ['我的视频', '我的音乐']

# 系统固定目录 ID 兜底（白名单里的名字在根目录没出现时，直接用这里的 ID 补上）
TY_DIR_FIDS = {'-13': '我的视频', '-14': '我的音乐'}

TY_ROOT_FID = '-11'       # 根目录
TY_DEPTH = 3              # 目录递归深度（我的音乐 → 经典音乐 就靠它收进来）
TY_TREE_TTL = 1800        # 目录树缓存（秒）
TY_DL_TTL = 120           # 播放直链缓存（秒）——必须 < 300，见顶部踩坑第 3 条
TY_RESOLVE = 1            # 1=播放前先把 302 解析成最终直链（推荐）；0=直接给 302 链接
TY_VIDEO_EXTS = ('mp4', 'mkv', 'ts', 'flv', 'm4v', 'mov', 'avi', 'rmvb',
                 'webm', 'mpg', 'mpeg', 'wmv', 'm2ts', '3gp', 'iso')
TY_AUDIO_EXTS = ('mp3', 'flac', 'wav', 'aac', 'm4a', 'ogg', 'ape', 'wma',
                 'opus', 'aiff', 'dsf', 'dff')
TY_MIN_VIDEO_SIZE = 5 * 1024 * 1024   # 滤掉碎片小文件
TY_MIN_AUDIO_SIZE = 200 * 1024        # 音频下限放低，否则整首歌会被误滤
TY_ALIAS = {}             # 目录名 -> 显示名（不配则用目录名）
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


def _unesc(s):
    """XML 实体还原（只处理天翼会用到的几个，避免依赖 html 模块）"""
    return (s or '').replace('&amp;', '&').replace('&lt;', '<') \
                    .replace('&gt;', '>').replace('&quot;', '"').replace('&apos;', "'")


def _blocks(xml, tag):
    """取 XML 中所有 <tag>...</tag> 块（正则解析，跨引擎通用）"""
    return re.findall(r'<%s>(.*?)</%s>' % (tag, tag), xml or '', re.S)


def _field(block, tag):
    m = re.search(r'<%s>(.*?)</%s>' % (tag, tag), block or '', re.S)
    return _unesc(m.group(1)) if m else ''


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
        self.cookie_weak = False
        self.last_diag = ''
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
        self.cookie_weak = False
        weak = ''
        if requests is not None:
            for u in urls:
                try:
                    r = requests.get(u, headers={'User-Agent': UA, 'Referer': REFERER}, timeout=6)
                    got = (r.text or '').strip()
                    if not got or '=' not in got:
                        continue
                    # 天翼登录凭证关键字段：COOKIE_LOGIN_USER（另有 SSON / JSESSIONID）
                    if 'COOKIE_LOGIN_USER' in got or 'SSON' in got:
                        ck = got
                        self.cookie_src = u
                        break
                    # 没有关键字段也先用着（可能是精简版 cookie），但标记 weak 以便界面提示
                    if not weak:
                        weak = got
                        self.cookie_src = u
                except Exception:
                    continue
            if not self.cookie_src and weak:
                ck = weak
                self.cookie_src = 'weak'
                self.cookie_weak = True
        self.cookie = ck.strip().strip('"\'').replace('\n', '').replace('\r', '')
        self.headers = {
            'User-Agent': UA,
            'Referer': REFERER,
            'Accept': 'application/json, text/plain, */*',
            # 天翼免签名 Cookie 模式靠这个头识别 ajax 请求，缺失会返回登录页 HTML
            'X-Requested-With': 'XMLHttpRequest',
        }
        if self.cookie:
            self.headers['Cookie'] = self.cookie

    # ---------------- 底层请求（返回 XML 文本 / JSON dict）----------------
    def _api(self, action, params=None):
        """返回 (text, js)：text=原始响应文本，js=能解析成 JSON 时的 dict（否则 None）"""
        if not self.cookie or requests is None:
            return '', None
        if params is None:
            params = {}
        try:
            r = requests.get(API + action, params=params, headers=self.headers, timeout=15)
            if r.status_code != 200:
                return '', None
            txt = r.text or ''
            js = None
            if txt.lstrip().startswith('{'):
                try:
                    js = json.loads(txt)
                except Exception:
                    js = None
            return txt, js
        except Exception:
            return '', None

    def _api_text(self, action, params=None):
        return self._api(action, params)[0]

    # ---------------- 媒体判定 / 工具 ----------------
    @staticmethod
    def is_audio(item):
        return _ext(item.get('name') or '') in TY_AUDIO_EXTS

    @staticmethod
    def is_video(item):
        return _ext(item.get('name') or '') in TY_VIDEO_EXTS

    @classmethod
    def is_media(cls, item):
        """视频或音频都收（否则「我的音乐」里的歌会被当成非视频全部跳过）"""
        return cls.is_video(item) or cls.is_audio(item)

    @classmethod
    def min_size(cls, item):
        return TY_MIN_VIDEO_SIZE if cls.is_video(item) else TY_MIN_AUDIO_SIZE

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

    # ---------------- 列目录（XML 解析 + 分页）----------------
    def list_dir(self, folder_id):
        """返回 (folders, files)，元素均为 dict：id / name / size / ..."""
        folders = []
        files = []
        page = 1
        while page <= 20:
            txt, js = self._api('listFiles.action',
                                {'folderId': folder_id, 'pageNum': page, 'pageSize': 200})
            if js is not None:
                # 万一以后改回 JSON，这里兜底
                data = js.get('data') if isinstance(js.get('data'), dict) else js
                folders += data.get('folderList') or []
                files += data.get('fileList') or []
                if len(data.get('folderList') or []) + len(data.get('fileList') or []) < 200:
                    break
            elif txt:
                for b in _blocks(txt, 'folder'):
                    folders.append({'id': _field(b, 'id'), 'name': _field(b, 'name'),
                                    'parentId': _field(b, 'parentId'),
                                    'fileCount': _field(b, 'fileCount'), 'dir': True})
                for b in _blocks(txt, 'file'):
                    files.append({'id': _field(b, 'id'), 'name': _field(b, 'name'),
                                  'size': _field(b, 'size'), 'md5': _field(b, 'md5'),
                                  'fileCata': _field(b, 'fileCata'),
                                  'mediaType': _field(b, 'mediaType'), 'dir': False})
                # 本页不足 200 项即到底（XML 无总数时按此判尾）
                if len(_blocks(txt, 'folder')) + len(_blocks(txt, 'file')) < 200:
                    break
            else:
                break
            page += 1
        return folders, files

    def collect_media(self, folder_id, depth, dirname):
        items = []
        folders, files = self.list_dir(folder_id)
        for it in folders:
            if depth > 1:
                items += self.collect_media(it.get('id'), depth - 1, it.get('name') or dirname)
        for it in files:
            if not self.is_media(it):
                continue
            try:
                sz = int(it.get('size') or 0)
            except Exception:
                sz = 0
            if sz < self.min_size(it):
                continue
            vid = 't_' + str(it.get('id'))
            name = re.sub(r'\.[^.]+$', '', it.get('name') or '')
            entry = {'id': vid, 'name': name, 'fid': str(it.get('id')),
                     'size': sz, 'dir': dirname, 'audio': self.is_audio(it)}
            items.append(entry)
            self.index[vid] = entry
        return items

    # ---------------- 目录树（带缓存 + 白名单）----------------
    def _in_whitelist(self, name):
        """分享白名单：TY_ONLY_DIRS 为空则不过滤"""
        if not TY_ONLY_DIRS:
            return True
        return name in TY_ONLY_DIRS

    def _sort_by_whitelist(self, tree):
        """让分类顺序与 TY_ONLY_DIRS 一致（我的视频在前、我的音乐在后）"""
        if not TY_ONLY_DIRS:
            return tree
        try:
            return sorted(tree, key=lambda c: TY_ONLY_DIRS.index(c['name'])
                          if c['name'] in TY_ONLY_DIRS else 999)
        except Exception:
            return tree

    def scan_tree(self):
        tree = []
        hit = set()
        folders, _files = self.list_dir(TY_ROOT_FID)
        for it in folders:
            name = it.get('name') or ''
            if not self._in_whitelist(name):
                continue
            hit.add(name)
            medias = self.collect_media(it.get('id'), TY_DEPTH, name)
            if medias:
                tree.append({'tid': 't_' + str(it.get('id')),
                             'name': TY_ALIAS.get(name, name), 'videos': medias})
        # 兜底：白名单目录没在根目录出现时，用系统固定 ID 直接补（如 -13 我的视频 / -14 我的音乐）
        for fid, name in TY_DIR_FIDS.items():
            if (not TY_ONLY_DIRS or name in TY_ONLY_DIRS) and name not in hit:
                medias = self.collect_media(fid, TY_DEPTH, name)
                if medias:
                    tree.append({'tid': 't_' + str(fid),
                                 'name': TY_ALIAS.get(name, name), 'videos': medias})
        tree = self._sort_by_whitelist(tree)
        # 自诊断：真机若显示空白，靠这段文案直接判断是 cookie / 网络 / 白名单 哪一段出问题
        # （旧版 py 的提示是「⚠未配置天翼cookie」，看到下面这些新文案 ⇒ py 已是新版）
        if not tree:
            if not folders:
                self.last_diag = '⚠cookie已读(%d字符)但列目录无返回|可能失效或网络不通' % len(self.cookie)
            else:
                names = '、'.join([(f.get('name') or '?') for f in folders[:6]])
                self.last_diag = '⚠根目录无「我的视频/我的音乐」|现有:%s' % names
        else:
            self.last_diag = ''
        self.tree = tree
        self.tree_ts = time.time()
        return tree

    def get_tree(self):
        now = time.time()
        if self.tree is not None and now - self.tree_ts < TY_TREE_TTL:
            return self.tree
        return self.scan_tree()

    # ---------------- 播放直链 ----------------
    def get_play_url(self, fid):
        now = time.time()
        if fid in self.dl_cache and now - self.dl_cache[fid][1] < TY_DL_TTL:
            return self.dl_cache[fid][0]
        # 视频/音频通用；type=2 个人云。vlc 接口对音频会报 PermissionDenied，故不用
        txt, js = self._api('getFileDownloadUrl.action', {'fileId': fid, 'type': 2})
        url = ''
        if js is not None:
            data = js.get('data') if isinstance(js.get('data'), dict) else js
            url = (data.get('fileDownloadUrl') or data.get('url') or '')
        if not url:
            m = re.search(r'<fileDownloadUrl>(.*?)</fileDownloadUrl>', txt, re.S)
            url = _unesc(m.group(1)) if m else ''
        if url and TY_RESOLVE and requests is not None:
            url = self.resolve(url) or url
        if url:
            self.dl_cache[fid] = (url, now)
        return url

    def resolve(self, url):
        """把 302 下载链接解析成最终 CDN 直链（不下载内容，只读响应头）"""
        try:
            r = requests.get(url, headers={'User-Agent': UA, 'Referer': REFERER},
                             timeout=15, stream=True, allow_redirects=True)
            final = r.url
            r.close()
            return final if final and final.startswith('http') else ''
        except Exception:
            return ''

    # ---------------- catvod 接口 ----------------
    def homeContent(self, filter):
        if requests is None:
            return {'class': [{'type_id': 'diag',
                               'type_name': '⚠本环境缺少requests模块,py无法联网'}],
                    'filters': {}}
        if not self.cookie:
            return {'class': [{'type_id': 'diag',
                               'type_name': '⚠未读到cookie|把tianyi_cookie.txt放进TVBox目录'}],
                    'filters': {}}
        try:
            tree = self.get_tree()
        except Exception as e:
            return {'class': [{'type_id': 'diag',
                               'type_name': '⚠列目录异常:%s' % (str(e)[:40])}],
                    'filters': {}}
        if not tree:
            tip = self.last_diag or '⚠目录为空'
            if self.cookie_weak:
                tip += '|cookie缺COOKIE_LOGIN_USER'
            return {'class': [{'type_id': 'diag', 'type_name': tip}], 'filters': {}}
        try:
            classes = [{'type_id': c['tid'], 'type_name': c['name']} for c in tree]
        except Exception:
            classes = []
        return {'class': classes, 'filters': {}}

    def homeVideoContent(self):
        return {'list': []}

    def categoryContent(self, tid, page, filter, ext):
        if tid in ('nocookie', 'diag'):
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
        show = ('播放' if it.get('audio') else '原画') + '[%s]' % self.fmt_size(it['size'])
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
        if not pid or not pid.startswith('t_'):
            return {'url': '', 'parse': 0, 'jx': 0}
        fid = pid[2:]
        url = self.get_play_url(fid)
        if not url:
            return {'url': '', 'parse': 0, 'jx': 0, 'header': {'User-Agent': UA}}
        # 直链本身不需要 Cookie（实测无 cookie 也 200，视频/音频皆然），带上 UA/Referer 即可
        return {'url': url, 'parse': 0, 'jx': 0,
                'header': {'User-Agent': UA, 'Referer': REFERER}}

    def destroy(self):
        return 'ok'


if __name__ == '__main__':
    pass
