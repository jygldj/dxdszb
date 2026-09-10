import html as html_lib
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urljoin

import requests

sys.path.append('..')
try:
    from base.spider import Spider as BaseSpider
except ImportError:
    class BaseSpider(object):
        pass


class Spider(BaseSpider):
    site = 'https://www.i275.com'

    def __init__(self):
        try:
            super().__init__()
        except TypeError:
            pass
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': (
                'Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36 '
                '(KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36'
            ),
            'Referer': self.site + '/',
        })
        self.cateManual = {
            '最近上架': 'latest',
            '玄幻': 'xuanhuan',
            '修仙': 'xiuxian',
            '都市': 'dushi',
            '言情': 'yanqing',
            '穿越': 'chuanyue',
            '重生': 'chongsheng',
            '悬疑': 'xuanyi',
            '灵异': 'lingyi',
            '历史': 'lishi',
            '武侠': 'wuxia',
            '科幻': 'kehuan',
            '网游': 'wangyou',
            '官场': 'guanchang',
            '军事': 'junshi',
            '评书': 'pingshu',
            '儿童': 'ertong',
            '相声': 'xiangsheng',
            '广播剧': 'guangboju',
            '其他': 'qita',
        }
        self.categoryQueries = {
            'xuanhuan': '玄幻',
            'xiuxian': '修仙',
            'dushi': '都市',
            'yanqing': '言情',
            'chuanyue': '穿越',
            'chongsheng': '重生',
            'xuanyi': '悬疑',
            'lingyi': '灵异',
            'lishi': '历史',
            'wuxia': '武侠',
            'kehuan': '科幻',
            'wangyou': '网游',
            'guanchang': '官场',
            'junshi': '军事',
            'pingshu': '评书',
            'ertong': '儿童',
            'xiangsheng': '相声',
            'guangboju': '广播剧',
        }
        self._search_cache = {}
        self._cover_cache = {}
        self._cover_cache_order = []
        self._default_cover = self.site + '/uploads/bookcover.png'
        self._page_limit = 20
        self._cover_workers = 8
        self._cover_timeout = 6
        self._cover_cache_max = 2000

    def init(self, extend=''):
        pass

    def getName(self):
        return '275听书'

    def isVideoFormat(self, url):
        return bool(re.search(r'\.(?:m4a|mp3|aac|m3u8)(?:\?|$)', url or '', re.I))

    def manualVideoCheck(self):
        return False

    def homeContent(self, filter):
        return {
            'class': [
                {'type_id': value, 'type_name': name}
                for name, value in self.cateManual.items()
            ],
            'filters': {}, 'list': [], 'parse': 0, 'jx': 0,
        }

    def homeVideoContent(self):
        result = {'list': [], 'parse': 0, 'jx': 0}
        try:
            result['list'] = self._parse_books(self._get('/').text)
        except Exception as error:
            print('homeVideoContent error: %s' % error)
        return result

    def categoryContent(self, tid, pg, filter, extend):
        page = self._page_number(pg)
        limit = self._page_limit
        result = {
            'list': [], 'parse': 0, 'jx': 0, 'page': page,
            'pagecount': 1, 'limit': limit, 'total': 0,
        }
        if tid not in self.cateManual.values():
            return result
        try:
            if tid == 'latest':
                page_html = self._get('/').text
            else:
                page_html = self._get(
                    '/search.php', params={'q': self.categoryQueries.get(tid, tid)}
                ).text
            result['list'] = self._parse_books(page_html)
            result['total'] = len(result['list'])
        except Exception as error:
            print('categoryContent error: %s' % error)
        return result

    def _fetch_covers(self, book_ids):
        """只为当前页补封面：命中缓存直接用，未命中再并发轻量请求。"""
        covers = {}
        missing = []
        for book_id in book_ids:
            book_id = str(book_id)
            cached = self._cover_cache.get(book_id)
            if cached:
                covers[book_id] = cached
            else:
                missing.append(book_id)
        if not missing:
            return covers

        workers = min(self._cover_workers, len(missing))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {
                pool.submit(self._fetch_cover_one, book_id): book_id
                for book_id in missing
            }
            for future in as_completed(futures):
                book_id = futures[future]
                try:
                    pic = future.result()
                except Exception:
                    pic = ''
                if pic:
                    covers[book_id] = pic
                    self._remember_cover(book_id, pic)
        return covers

    def _fetch_cover_one(self, book_id):
        """只读详情页前部 HTML，解析封面；失败返回空串，列表用默认图兜底。"""
        url = urljoin(self.site + '/', 'book/%s.html' % book_id)
        headers = {
            'User-Agent': self.session.headers.get(
                'User-Agent',
                'Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36 '
                '(KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36',
            ),
            'Referer': self.site + '/',
        }
        try:
            response = requests.get(
                url, timeout=self._cover_timeout, headers=headers, stream=True,
            )
            if response.status_code in (403, 429):
                time.sleep(0.4)
                response = requests.get(
                    url, timeout=self._cover_timeout, headers=headers, stream=True,
                )
            response.raise_for_status()
            chunks = []
            total = 0
            for chunk in response.iter_content(chunk_size=4096):
                if not chunk:
                    break
                chunks.append(chunk)
                total += len(chunk)
                if total >= 49152:
                    break
            response.close()
            text = b''.join(chunks).decode('utf-8', errors='ignore')
            return self._extract_cover(text)
        except Exception:
            return ''

    def _extract_cover(self, page_html):
        image_match = re.search(
            r'<div class="w-32 h-44.*?<img[^>]+src=["\']([^"\']+)',
            page_html, re.S,
        )
        if not image_match:
            image_match = re.search(
                r'<img[^>]+src=["\']([^"\']+/uploads/[^"\']+)["\']',
                page_html, re.I,
            )
        if not image_match:
            return ''
        pic = urljoin(self.site, image_match.group(1).replace('&amp;', '&'))
        if 'bookcover.png' in pic:
            return ''
        return pic

    def _remember_cover(self, book_id, pic):
        book_id = str(book_id)
        if book_id in self._cover_cache:
            return
        self._cover_cache[book_id] = pic
        self._cover_cache_order.append(book_id)
        while len(self._cover_cache_order) > self._cover_cache_max:
            old_id = self._cover_cache_order.pop(0)
            self._cover_cache.pop(old_id, None)

    def detailContent(self, ids):
        result = {'list': [], 'parse': 0, 'jx': 0}
        book_id = self._book_id(ids[0] if ids else '')
        if not book_id:
            return result
        try:
            page_html = self._get('/book/%s.html' % book_id).text
            title = self._first_text(
                page_html,
                r'<h1 class="text-2xl font-bold text-gray-800">(.*?)</h1>',
            )
            pic = self._extract_cover(page_html)
            if pic:
                self._remember_cover(book_id, pic)
            author = self._field(page_html, '作者')
            speaker = self._field(page_html, '演播')
            status = self._field(page_html, '状态')
            count_match = re.search(r'正文目录\s*\((\d+)\)', page_html)
            count = count_match.group(1) if count_match else ''
            desc_match = re.search(
                r'>作品简介</h3>\s*<p[^>]*>(.*?)</p>', page_html, re.S
            )
            desc = self._clean(desc_match.group(1)) if desc_match else ''

            episodes = []
            for path_book, chapter_id, name in re.findall(
                r'href="/play/(\d+)/(\d+)\.html".*?'
                r'<span class="text-sm text-gray-700 truncate">(.*?)</span>',
                page_html, re.S,
            ):
                episodes.append((
                    self._play_name(self._clean(name)),
                    '/play/%s/%s.html' % (path_book, chapter_id),
                ))

            result['list'].append({
                'vod_id': book_id,
                'vod_name': title,
                'vod_pic': pic or self._default_cover,
                'type_name': '有声小说',
                'vod_year': '',
                'vod_area': '',
                'vod_remarks': ('%s · %s集' % (status, count)).strip(' ·'),
                'vod_actor': speaker,
                'vod_director': author,
                'vod_content': desc,
                'vod_play_from': '275听书',
                'vod_play_url': '#'.join(
                    '%s$%s' % (name, play_path) for name, play_path in episodes
                ),
            })
        except Exception as error:
            print('detailContent error: %s' % error)
        return result

    def playerContent(self, flag, id, vipFlags):
        play_url = id if str(id).startswith('http') else urljoin(self.site, str(id))
        try:
            if not self.session.cookies:
                self._get('/')
                time.sleep(1.05)
            response = self._get(play_url, headers={'Referer': self._book_referer(play_url)})
            match = re.search(
                r'\baudio\s*:\s*\[\s*\{.*?\burl\s*:\s*["\']([^"\']+)["\']',
                response.text, re.S,
            )
            if not match:
                raise ValueError('播放页没有音频地址')
            audio_url = match.group(1).replace('&amp;', '&')
            audio_referer = play_url
            if audio_url.startswith('lrts$'):
                audio_url = self._resolve_lrts(audio_url)
                audio_referer = 'https://m.lrts.me/'
            audio_url = re.sub(r'^http://', 'https://', audio_url, count=1, flags=re.I)
            if not re.match(r'^https://', audio_url, re.I):
                raise ValueError('播放地址不是有效的 HTTPS 音频链接')
            return {
                'parse': 0, 'url': audio_url, 'jx': 0,
                'header': {
                    'User-Agent': self.session.headers['User-Agent'],
                    'Referer': audio_referer,
                },
            }
        except Exception as error:
            print('playerContent error: %s' % error)
            return {
                'parse': 1, 'url': play_url, 'jx': 0,
                'header': {'User-Agent': self.session.headers['User-Agent']},
            }

    def searchContent(self, key, quick, pg='1'):
        page = self._page_number(pg)
        cache_key = (str(key), page)
        cached = self._search_cache.get(cache_key)
        if cached and time.time() - cached['cached_at'] < 60:
            return cached['result']
        result = {
            'list': [], 'parse': 0, 'jx': 0, 'page': page,
            'pagecount': 1, 'limit': 50, 'total': 0,
        }
        if page > 1:
            return result
        try:
            page_html = self._get('/search.php', params={'q': key}).text
            result['list'] = self._parse_books(page_html)
            total_match = re.search(r'的结果\s*\((\d+)\)', page_html)
            result['total'] = (
                int(total_match.group(1)) if total_match else len(result['list'])
            )
            self._search_cache[cache_key] = {
                'cached_at': time.time(), 'result': result,
            }
        except Exception as error:
            print('searchContent error: %s' % error)
            if cached:
                return cached['result']
        return result

    def localProxy(self, params):
        return [200, 'audio/mp4', {}, '']

    def _get(self, path, **kwargs):
        url = path if str(path).startswith('http') else urljoin(
            self.site + '/', str(path).lstrip('/')
        )
        response = self.session.get(url, timeout=20, **kwargs)
        if response.status_code in (403, 429):
            time.sleep(1.1)
            response = self.session.get(url, timeout=20, **kwargs)
        response.raise_for_status()
        response.encoding = 'utf-8'
        return response

    def _resolve_lrts(self, token):
        parts = str(token).split('#')
        entity_match = re.match(r'^lrts\$(\d+)$', parts[0] if parts else '')
        if (
            len(parts) < 3 or not entity_match or not parts[1].isdigit()
            or not parts[2].isdigit()
        ):
            raise ValueError('懒人听书资源标识格式错误')

        params = {
            'entityId': entity_match.group(1),
            'entityType': 3,
            'opType': 1,
            'sections': '[%s]' % parts[2],
            'type': 0,
            'id': parts[1],
            'section': parts[2],
        }
        last_message = ''
        for endpoint in ('getPlayPath', 'getListenPath'):
            response = self._get(
                'https://m.lrts.me/ajax/' + endpoint,
                params=params,
                headers={'Referer': 'https://m.lrts.me/'},
            )
            payload = response.json()
            if endpoint == 'getPlayPath':
                items = payload.get('list') or []
                audio_url = items[0].get('path', '') if items else ''
            else:
                audio_url = (payload.get('data') or {}).get('path', '')
            if payload.get('status') == 0 and re.match(r'^https?://', audio_url, re.I):
                return audio_url
            last_message = payload.get('msg') or last_message
        raise ValueError(last_message or '懒人听书音频解析失败')

    def _parse_books(self, page_html):
        videos = []
        seen = set()
        for book_id, block in re.findall(
            r'<a\s+href=["\']/book/(\d+)\.html["\'][^>]*>(.*?)</a>',
            page_html, re.S,
        ):
            image = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', block)
            if not image or book_id in seen:
                continue
            title_match = re.search(r'<h3[^>]*>(.*?)</h3>', block, re.S)
            if not title_match:
                title_match = re.search(
                    r'<div class="font-medium text-sm[^>]*>(.*?)</div>', block, re.S
                )
            title = self._clean(title_match.group(1)) if title_match else ''
            if not title:
                alt = re.search(r'<img[^>]+alt=["\']([^"\']+)["\']', block)
                title = self._clean(alt.group(1)) if alt else ''
            if not title:
                continue
            speaker_match = re.search(
                r'>演播</span>\s*([^<\r\n]+)', block, re.S
            )
            if not speaker_match:
                speaker_match = re.search(
                    r'<div class="text-xs text-gray-500[^>]*>(.*?)</div>', block, re.S
                )
            remark = self._clean(speaker_match.group(1)) if speaker_match else ''
            seen.add(book_id)
            videos.append({
                'vod_id': book_id,
                'vod_name': title,
                'vod_pic': urljoin(self.site, image.group(1)),
                'vod_remarks': remark,
            })
        return videos

    @staticmethod
    def _field(page_html, label):
        match = re.search(
            r'<p>\s*%s：\s*<span[^>]*>(.*?)</span>' % re.escape(label),
            page_html, re.S,
        )
        return Spider._clean(match.group(1)) if match else ''

    @staticmethod
    def _first_text(page_html, pattern):
        match = re.search(pattern, page_html, re.S)
        return Spider._clean(match.group(1)) if match else ''

    @staticmethod
    def _clean(value):
        value = re.sub(r'<br\s*/?>', '\n', value or '', flags=re.I)
        value = re.sub(r'<[^>]+>', '', value)
        value = html_lib.unescape(value)
        return re.sub(r'[ \t\r\f\v]+', ' ', value).strip()

    @staticmethod
    def _book_id(value):
        match = re.search(r'/book/(\d+)\.html', str(value or ''))
        if match:
            return match.group(1)
        match = re.search(r'\d+', str(value or ''))
        return match.group(0) if match else ''

    @staticmethod
    def _book_referer(play_url):
        match = re.search(r'/play/(\d+)/', play_url)
        return 'https://www.i275.com/book/%s.html' % match.group(1) if match else 'https://www.i275.com/'

    @staticmethod
    def _page_number(value):
        try:
            return max(1, int(value or 1))
        except (TypeError, ValueError):
            return 1

    @staticmethod
    def _play_name(name):
        return (name or '').replace('$', '￥').replace('#', '﹟')