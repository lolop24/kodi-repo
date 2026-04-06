# -*- coding: utf-8 -*-

import hashlib
import json
import os
import re
import threading
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from difflib import SequenceMatcher
from urllib.parse import quote_plus, urlencode, urlparse

import requests
import xbmc
import xbmcaddon
import xbmcvfs
from bs4 import BeautifulSoup

ADDON = xbmcaddon.Addon()

CSFD_BASE = 'https://www.csfd.cz'
SC_ADDON_ID = 'plugin.video.stream-cinema'

HEADERS = {
    'User-Agent': 'curl/8.0',
    'Accept-Language': 'cs,sk;q=0.9,en;q=0.5',
}

SOURCE_CONFIG = {
    'newstream': {
        'label_id': 30002,
        'path': '/FMovies/newstream',
    },
    'latest': {
        'label_id': 30003,
        'path_template': '/FMovies/latestd?limit={limit}',
    },
}

DETAIL_CACHE_TTL = 3 * 24 * 60 * 60
SEARCH_CACHE_TTL = 30 * 24 * 60 * 60
MAX_WORKERS = 6
DETAIL_CACHE_NAME = 'detail_cache.json'
SEARCH_CACHE_NAME = 'search_cache.json'
DETAIL_CACHE_LOCK = threading.Lock()
SEARCH_CACHE_LOCK = threading.Lock()


def log(message, level=xbmc.LOGINFO):
    xbmc.log('[Kolop] %s' % message, level)


def get_source_label(source_key):
    return ADDON.getLocalizedString(SOURCE_CONFIG[source_key]['label_id'])


def _cache_dir():
    path = xbmcvfs.translatePath(ADDON.getAddonInfo('profile'))
    if not xbmcvfs.exists(path):
        xbmcvfs.mkdirs(path)
    return path


def _cache_path(name):
    return os.path.join(_cache_dir(), name)


def _load_json(name):
    path = _cache_path(name)
    if not xbmcvfs.exists(path):
        return {}
    try:
        with open(path, 'r', encoding='utf-8') as handle:
            return json.load(handle)
    except Exception as exc:
        log('Cache load failed for %s: %s' % (name, exc), xbmc.LOGWARNING)
        return {}


def _save_json(name, data):
    path = _cache_path(name)
    try:
        with open(path, 'w', encoding='utf-8') as handle:
            json.dump(data, handle, ensure_ascii=False, indent=1)
    except Exception as exc:
        log('Cache save failed for %s: %s' % (name, exc), xbmc.LOGWARNING)


def _md5(value):
    return hashlib.md5(value.encode('utf-8')).hexdigest()


def _hexlify(value):
    return value.encode('utf-8').hex()


def _safe_int(value):
    if value in (None, ''):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    cleaned = re.sub(r'[^0-9]', '', str(value))
    return int(cleaned) if cleaned else None


def _safe_float(value):
    if value in (None, ''):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    cleaned = str(value).replace('%', '').replace(',', '.').strip()
    try:
        return float(cleaned)
    except ValueError:
        return None


def _format_votes(value):
    votes_int = _safe_int(value)
    if votes_int is None:
        return ''
    return '{:,}'.format(votes_int).replace(',', ' ')


def _join_value(value, separator):
    if isinstance(value, list):
        return separator.join([str(item) for item in value if item])
    if value is None:
        return ''
    return str(value)


def _parse_date(value):
    if not value:
        return 0
    for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%d'):
        try:
            return int(datetime.strptime(value, fmt).timestamp())
        except ValueError:
            continue
    return 0


def _normalize_text(value):
    if not value:
        return ''
    normalized = unicodedata.normalize('NFKD', value)
    normalized = ''.join(ch for ch in normalized if not unicodedata.combining(ch))
    normalized = normalized.lower()
    normalized = re.sub(r'[^a-z0-9]+', ' ', normalized)
    return normalized.strip()


def _first_year(value):
    if not value:
        return ''
    match = re.search(r'(19|20)\d{2}', str(value))
    return match.group(0) if match else ''


def _normalize_rating(value):
    if value in (None, ''):
        return ('', None)
    text = str(value).strip()
    if not text.endswith('%'):
        number = _safe_float(text)
        if number is not None:
            text = '%s%%' % int(round(number))
    number = _safe_float(text)
    return (text, number)


def _build_session():
    session = requests.Session()
    session.headers.update(HEADERS)
    return session


def _is_robot_page(response):
    text = response.text or ''
    if 'anubis_challenge' in text:
        return True
    return 'Uji' in text and 'nejste robot' in text


def _solve_anubis(session, response, redir_url):
    started = time.time()
    soup = BeautifulSoup(response.text, 'html.parser')
    challenge_node = soup.select_one('#anubis_challenge')
    if not challenge_node:
        return False

    try:
        payload = json.loads(challenge_node.get_text(strip=True))
    except Exception as exc:
        log('Failed to parse Anubis challenge: %s' % exc, xbmc.LOGWARNING)
        return False

    challenge = payload.get('challenge', {})
    rules = payload.get('rules', {})
    random_data = challenge.get('randomData', '')
    difficulty = int(rules.get('difficulty', 0))
    challenge_id = challenge.get('id', '')
    if not random_data or not challenge_id or difficulty <= 0:
        return False

    nonce = 0
    full_bytes = difficulty // 2
    odd_half_byte = difficulty % 2
    while True:
        digest = hashlib.sha256((random_data + str(nonce)).encode('utf-8')).digest()
        valid = all(part == 0 for part in digest[:full_bytes])
        if valid and odd_half_byte and (digest[full_bytes] >> 4) != 0:
            valid = False
        if valid:
            response_hash = digest.hex()
            break
        nonce += 1

    parsed = urlparse(response.url)
    pass_url = '%s://%s/.within.website/x/cmd/anubis/api/pass-challenge?%s' % (
        parsed.scheme,
        parsed.netloc,
        urlencode({
            'id': challenge_id,
            'response': response_hash,
            'nonce': nonce,
            'redir': redir_url,
            'elapsedTime': int((time.time() - started) * 1000),
        })
    )

    try:
        session.get(pass_url, allow_redirects=False, timeout=15)
        return True
    except requests.RequestException as exc:
        log('Anubis pass request failed: %s' % exc, xbmc.LOGWARNING)
        return False


def _session_get(url, timeout=15):
    session = _build_session()
    try:
        response = session.get(url, timeout=timeout)
        if _is_robot_page(response) and _solve_anubis(session, response, url):
            response = session.get(url, timeout=timeout)
        response.raise_for_status()
        return response
    finally:
        session.close()


def _extract_json_ld(soup):
    for script in soup.select('script[type="application/ld+json"]'):
        raw = script.string or script.get_text()
        if not raw:
            continue
        try:
            payload = json.loads(raw)
        except Exception:
            continue
        nodes = payload if isinstance(payload, list) else [payload]
        while nodes:
            node = nodes.pop(0)
            if isinstance(node, dict):
                if '@graph' in node and isinstance(node['@graph'], list):
                    nodes.extend(node['@graph'])
                if node.get('@type') in ('Movie', 'TVSeries', 'TVEpisode', 'CreativeWork'):
                    return node
    return {}


def _extract_titles(soup):
    original_title = ''
    english_title = ''
    english_countries = {'usa', 'velka britanie', 'kanada', 'australie', 'irsko', 'novy zeland'}

    names_ul = soup.select_one('ul.film-names')
    if not names_ul:
        return original_title, english_title

    for li in names_ul.select('li'):
        flag = li.select_one('img.flag[title]')
        country = _normalize_text(flag['title']) if flag and flag.get('title') else ''
        info_span = li.select_one('span.info')
        info_hint = _normalize_text(info_span.get_text(' ', strip=True)) if info_span else ''
        for tag in li.select('span, img'):
            tag.decompose()
        title = re.sub(r'\s*\((vice|mene)\)\s*$', '', li.get_text(' ', strip=True)).strip()
        if not title:
            continue
        if not original_title:
            original_title = title
        if country in english_countries or 'anglick' in info_hint:
            english_title = title

    return original_title, english_title


def _extract_reviews(soup):
    reviews = []
    for article in soup.select('section.box-reviews article[data-film-review]'):
        review = {'user': '?', 'stars': 0, 'text': ''}
        user = article.select_one('a.user-title-name')
        if user:
            review['user'] = user.get_text(strip=True)
        stars = article.select_one('span.stars')
        if stars:
            for css_class in stars.get('class', []):
                match = re.match(r'stars-(\d+)', css_class)
                if match:
                    review['stars'] = int(match.group(1))
                    break
        text = article.select_one('span.comment[data-film-review-content]')
        if text:
            review['text'] = text.get_text(' ', strip=True)
        if review['text']:
            reviews.append(review)
    return reviews


def _best_poster(soup, ld_data):
    image = soup.select_one('.film-posters img, .film-main-posters img, figure.article-img img')
    if image:
        srcset = image.get('srcset', '')
        if srcset:
            parts = [part.strip() for part in srcset.split(',') if part.strip()]
            if parts:
                url = parts[-1].split(' ')[0].strip()
                if url:
                    return 'https:' + url if url.startswith('//') else url
        src = image.get('src', '')
        if src:
            return 'https:' + src if src.startswith('//') else src

    og_image = soup.select_one('meta[property="og:image"]')
    if og_image and og_image.get('content'):
        return og_image['content'].strip()

    if isinstance(ld_data, dict) and ld_data.get('image'):
        return ld_data['image']

    return ''


def _parse_detail(html, csfd_url):
    soup = BeautifulSoup(html, 'html.parser')
    ld_data = _extract_json_ld(soup)
    original_title, english_title = _extract_titles(soup)

    rating_text = ''
    for selector in ('.film-rating-average', '.rating-average', '.film-header-average-rating'):
        element = soup.select_one(selector)
        if element:
            rating_text = element.get_text(' ', strip=True)
            if rating_text:
                break
    rating, rating_num = _normalize_rating(rating_text)

    votes_int = None
    aggregate = ld_data.get('aggregateRating', {}) if isinstance(ld_data, dict) else {}
    if isinstance(aggregate, dict):
        votes_int = _safe_int(aggregate.get('ratingCount'))
    if votes_int is None:
        votes_el = soup.select_one('.rating-total')
        if votes_el:
            votes_int = _safe_int(votes_el.get_text(' ', strip=True))

    genres = ''
    genres_el = soup.select_one('.genres')
    if genres_el:
        genres = genres_el.get_text(' / ', strip=True)
    elif isinstance(ld_data, dict) and ld_data.get('genre'):
        genres = _join_value(ld_data.get('genre'), ' / ')

    origin = ''
    origin_el = soup.select_one('.origin')
    if origin_el:
        origin = origin_el.get_text(' ', strip=True)

    title = ''
    title_el = soup.select_one('h1')
    if title_el:
        title = title_el.get_text(' ', strip=True)
    if not title and isinstance(ld_data, dict):
        title = ld_data.get('name', '')

    plot = ''
    for selector in ('div.plots-item', 'div.plot-full p', 'div.plot-preview p'):
        element = soup.select_one(selector)
        if element:
            plot = element.get_text(' ', strip=True)
            if len(plot) > 20:
                break
    if not plot:
        og_desc = soup.select_one('meta[property="og:description"]')
        if og_desc and og_desc.get('content'):
            plot = og_desc['content'].strip()

    country = origin.split(',')[0].strip() if origin else ''
    year = _first_year(origin)
    if not year and isinstance(ld_data, dict) and ld_data.get('datePublished'):
        year = _first_year(ld_data.get('datePublished'))

    return {
        'csfd_url': csfd_url,
        'title': title,
        'rating': rating,
        'rating_num': rating_num,
        'votes': _format_votes(votes_int),
        'votes_int': votes_int,
        'genres': genres,
        'country': country,
        'year': year,
        'poster': _best_poster(soup, ld_data),
        'plot': plot,
        'reviews': _extract_reviews(soup),
        'original_title': original_title,
        'english_title': english_title,
    }


def _fetch_detail_from_url(csfd_url):
    if not csfd_url:
        return {}

    cache_key = _md5(csfd_url)
    now = time.time()
    with DETAIL_CACHE_LOCK:
        cache = _load_json(DETAIL_CACHE_NAME)
        entry = cache.get(cache_key)
    if entry and (now - entry.get('ts', 0)) < DETAIL_CACHE_TTL:
        return entry

    try:
        response = _session_get(csfd_url, timeout=15)
        detail = _parse_detail(response.text, response.url)
        detail['ts'] = now
        with DETAIL_CACHE_LOCK:
            cache = _load_json(DETAIL_CACHE_NAME)
            cache[cache_key] = detail
            _save_json(DETAIL_CACHE_NAME, cache)
        return detail
    except requests.RequestException as exc:
        log('CSFD detail fetch failed for %s: %s' % (csfd_url, exc), xbmc.LOGWARNING)
        return entry or {}


def fetch_single_reviews(csfd_url):
    detail = _fetch_detail_from_url(csfd_url)
    return detail.get('reviews', []) if detail else []


def _build_search_candidates(item):
    candidates = []
    for value in (item.get('title', ''), item.get('original_title', ''), item.get('sc_original_title', '')):
        value = value.strip()
        if value and value not in candidates:
            candidates.append(value)
    return candidates


def _search_score(query, candidate_title, query_year, candidate_year):
    left = _normalize_text(query)
    right = _normalize_text(candidate_title)
    if not left or not right:
        return 0.0

    score = SequenceMatcher(None, left, right).ratio()
    if left == right:
        score += 1.0
    elif left in right or right in left:
        score += 0.4

    if query_year and candidate_year:
        if str(query_year) == str(candidate_year):
            score += 0.5
        else:
            score -= min(abs(int(query_year) - int(candidate_year)) * 0.05, 0.25)

    return score


def _search_csfd(query, year=''):
    cache_key = _md5('%s|%s' % (_normalize_text(query), year))
    now = time.time()
    with SEARCH_CACHE_LOCK:
        cache = _load_json(SEARCH_CACHE_NAME)
        entry = cache.get(cache_key)
    if entry and (now - entry.get('ts', 0)) < SEARCH_CACHE_TTL:
        return entry.get('result')

    result = None
    try:
        url = '%s/hledat/?q=%s' % (CSFD_BASE, quote_plus(query))
        response = _session_get(url, timeout=15)
        soup = BeautifulSoup(response.text, 'html.parser')
        best_score = 0.0
        for article in soup.select('article.article'):
            title_el = article.select_one('a.film-title-name')
            if not title_el:
                continue
            href = title_el.get('href', '')
            if '/film/' not in href:
                continue
            candidate_title = title_el.get_text(strip=True)
            info_el = article.select_one('.film-title-info .info')
            candidate_year = _first_year(info_el.get_text(' ', strip=True)) if info_el else ''
            score = _search_score(query, candidate_title, year, candidate_year)
            if score > best_score:
                best_score = score
                result = {
                    'csfd_url': CSFD_BASE + href if href.startswith('/') else href,
                    'title': candidate_title,
                    'year': candidate_year,
                }
        if best_score < 0.95:
            result = None
    except requests.RequestException as exc:
        log('CSFD search failed for %s: %s' % (query, exc), xbmc.LOGWARNING)

    with SEARCH_CACHE_LOCK:
        cache = _load_json(SEARCH_CACHE_NAME)
        cache[cache_key] = {'result': result, 'ts': now}
        _save_json(SEARCH_CACHE_NAME, cache)
    return result


def _match_to_csfd(item):
    unique_ids = item.get('unique_ids') or {}
    csfd_id = unique_ids.get('csfd')
    if csfd_id:
        detail = _fetch_detail_from_url('%s/film/%s/' % (CSFD_BASE, csfd_id))
        if detail:
            return detail

    for query in _build_search_candidates(item):
        match = _search_csfd(query, item.get('year', ''))
        if not match:
            continue
        detail = _fetch_detail_from_url(match.get('csfd_url', ''))
        if detail:
            return detail

    return {}


def _jsonrpc(method, params):
    payload = json.dumps({
        'jsonrpc': '2.0',
        'id': 1,
        'method': method,
        'params': params,
    })
    raw = xbmc.executeJSONRPC(payload)
    try:
        return json.loads(raw)
    except Exception as exc:
        log('Invalid JSON-RPC response: %s' % exc, xbmc.LOGERROR)
        return {}


def _get_limit():
    try:
        value = int(ADDON.getSetting('item_limit') or '30')
    except ValueError:
        value = 30
    return max(10, min(60, value))


def _get_source_path(source_key):
    config = SOURCE_CONFIG[source_key]
    if 'path' in config:
        return config['path']
    return config['path_template'].format(limit=_get_limit())


def _build_sc_directory_url(source_key):
    return 'plugin://%s/?url=%s&widget=1' % (SC_ADDON_ID, _hexlify(_get_source_path(source_key)))


def _normalize_item(raw_item):
    unique_ids = raw_item.get('uniqueid') or {}
    art = raw_item.get('art') or {}
    return {
        'title': (raw_item.get('title') or raw_item.get('label') or '').strip(),
        'original_title': (raw_item.get('originaltitle') or '').strip(),
        'sc_original_title': (raw_item.get('originaltitle') or '').strip(),
        'year': str(raw_item.get('year') or '').strip(),
        'sc_url': raw_item.get('file', ''),
        'is_folder': raw_item.get('filetype', 'directory') != 'file',
        'unique_ids': {str(key): str(value) for key, value in unique_ids.items()},
        'sc_plot': raw_item.get('plot', ''),
        'sc_genre': _join_value(raw_item.get('genre'), ' / '),
        'sc_country': _join_value(raw_item.get('country'), ', '),
        'sc_poster': art.get('poster') or art.get('thumb') or art.get('icon') or '',
        'sc_fanart': art.get('fanart') or art.get('banner') or '',
        'sc_rating': _safe_float(raw_item.get('rating')),
        'sc_votes': _safe_int(raw_item.get('votes')),
        'dateadded': raw_item.get('dateadded', ''),
        'dateadded_ts': _parse_date(raw_item.get('dateadded', '')),
        'reviews': [],
        'csfd_rating': '',
        'csfd_rating_num': None,
        'csfd_votes': '',
        'csfd_votes_int': None,
        'genres': '',
        'country': '',
        'plot': '',
        'poster': '',
        'fanart': '',
        'csfd_url': '',
        'position': '',
    }


def fetch_sc_items(source_key):
    response = _jsonrpc('Files.GetDirectory', {
        'directory': _build_sc_directory_url(source_key),
        'media': 'video',
        'properties': [
            'title',
            'originaltitle',
            'year',
            'rating',
            'votes',
            'plot',
            'genre',
            'country',
            'art',
            'dateadded',
            'resume',
            'uniqueid',
        ],
    })
    if 'error' in response:
        log('Files.GetDirectory failed: %s' % response['error'], xbmc.LOGERROR)
        return []
    items = [_normalize_item(item) for item in response.get('result', {}).get('files', [])]
    return items[:_get_limit()]


def _merge_item(item, detail):
    if not detail:
        item['genres'] = item.get('sc_genre', '')
        item['country'] = item.get('sc_country', '')
        item['plot'] = item.get('sc_plot', '')
        item['poster'] = item.get('sc_poster', '')
        item['fanart'] = item.get('sc_fanart', '')
        return item

    item['csfd_url'] = detail.get('csfd_url', '')
    item['csfd_rating'] = detail.get('rating', '')
    item['csfd_rating_num'] = detail.get('rating_num')
    item['csfd_votes'] = detail.get('votes', '')
    item['csfd_votes_int'] = detail.get('votes_int')
    item['genres'] = detail.get('genres') or item.get('sc_genre', '')
    item['country'] = detail.get('country') or item.get('sc_country', '')
    item['plot'] = detail.get('plot') or item.get('sc_plot', '')
    item['poster'] = detail.get('poster') or item.get('sc_poster', '')
    item['fanart'] = detail.get('poster') or item.get('sc_fanart', '')
    item['reviews'] = detail.get('reviews', [])
    item['original_title'] = detail.get('english_title') or detail.get('original_title') or item.get('original_title', '')
    if detail.get('year'):
        item['year'] = detail['year']
    return item


def _sort_key(item):
    return (
        0 if item.get('csfd_rating_num') is not None else 1,
        -(item.get('csfd_rating_num') or -1),
        -(item.get('csfd_votes_int') or -1),
        -item.get('dateadded_ts', 0),
        _normalize_text(item.get('title', '')),
    )


def build_source_items(source_key, progress_cb=None):
    source_items = fetch_sc_items(source_key)
    if not source_items:
        return []

    enriched = list(source_items)
    with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, len(source_items))) as pool:
        futures = {pool.submit(_match_to_csfd, item): index for index, item in enumerate(source_items)}
        canceled = False
        done = 0
        for future in as_completed(futures):
            index = futures[future]
            try:
                detail = future.result()
            except Exception as exc:
                log('Worker failed for %s: %s' % (source_items[index].get('title', ''), exc), xbmc.LOGWARNING)
                detail = {}
            enriched[index] = _merge_item(dict(source_items[index]), detail)
            done += 1
            if progress_cb and progress_cb(done, len(source_items)):
                canceled = True
                break
        if canceled:
            for future in futures:
                future.cancel()

    enriched.sort(key=_sort_key)
    for index, item in enumerate(enriched, start=1):
        item['position'] = str(index)
    return enriched
