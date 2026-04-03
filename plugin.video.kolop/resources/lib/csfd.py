# -*- coding: utf-8 -*-
"""
ČSFD scraper v8.0 – ranking + detail pages.

Filter token: JSON → base64 → ROT13 (ČSFD SimpleCrypt).
SC matching is done on-click in sc_bridge.py.
Widget mode: return stale cache instantly, refresh in background.
"""

import os
import re
import json
import time
import codecs
import hashlib
import base64
import threading
import xbmc
import xbmcvfs
import xbmcgui
import xbmcaddon
import requests
from bs4 import BeautifulSoup
from concurrent.futures import ThreadPoolExecutor, as_completed

CSFD_BASE = 'https://www.csfd.cz'

HEADERS = {
    'User-Agent': 'curl/8.0',
    'Accept-Language': 'cs,sk;q=0.9,en;q=0.5',
}


TYPE_FILM = 'film'
TYPE_SERIES = 'serial'
TYPE_SERIE = 'serie'

# ČSFD form type IDs
_CSFD_TYPE = {
    TYPE_FILM: 1,
    TYPE_SERIES: 3,
    TYPE_SERIE: 10,
}

MAX_WORKERS = 6
ITEMS_PER_PAGE = 30
RANKING_CACHE_TTL = 7200      # 2 hours
DETAIL_CACHE_TTL = 259200     # 3 days
RANKING_CACHE_VER = 6         # bump to invalidate old cached rankings


def log(msg, level=xbmc.LOGINFO):
    xbmc.log('[CSFD] %s' % msg, level)


# ═══════════════════════════════════════════════════════════════
# FILTER URL BUILDER (SimpleCrypt: JSON → base64 → ROT13)
# ═══════════════════════════════════════════════════════════════

CSFD_RANKING_BASE = CSFD_BASE + '/zebricky/vlastni-vyber/?filter='


def _encode_filter(obj):
    """Encode filter object to ČSFD SimpleCrypt token."""
    raw = json.dumps(obj, separators=(',', ':')).encode('utf-8')
    b64 = base64.b64encode(raw).decode('ascii').rstrip('=')
    return codecs.encode(b64, 'rot_13')


def build_filter_url(year, media_type=TYPE_FILM):
    """Build ČSFD ranking filter URL for any type and year."""
    year = int(year)
    csfd_type = _CSFD_TYPE.get(media_type, 1)
    obj = {
        'type': csfd_type,
        'origin': None,
        'genre': [],
        'year_from': year,
        'year_to': None,
        'actor': [],
        'director': [],
    }
    token = _encode_filter(obj)
    log('Filter: type=%s(%d) year=%d' % (media_type, csfd_type, year))
    return CSFD_RANKING_BASE + token



# ═══════════════════════════════════════════════════════════════
# CACHE
# ═══════════════════════════════════════════════════════════════

def _cache_dir():
    addon = xbmcaddon.Addon()
    d = xbmcvfs.translatePath(addon.getAddonInfo('profile'))
    if not xbmcvfs.exists(d):
        xbmcvfs.mkdirs(d)
    return d


def _load_json(filename):
    path = os.path.join(_cache_dir(), filename)
    try:
        if xbmcvfs.exists(path):
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
    except Exception as e:
        log('Cache load err (%s): %s' % (filename, e), xbmc.LOGWARNING)
    return {}


def _save_json(filename, data):
    path = os.path.join(_cache_dir(), filename)
    try:
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
    except Exception as e:
        log('Cache save err (%s): %s' % (filename, e), xbmc.LOGWARNING)


def _md5(s):
    return hashlib.md5(s.encode('utf-8')).hexdigest()


# ═══════════════════════════════════════════════════════════════
# RANKING PAGE
# ═══════════════════════════════════════════════════════════════

def fetch_ranking(media_type, year_from):
    """Fetch full ranking (cached 2h). Returns list of item dicts."""
    cache_key = '%s_%s' % (media_type, year_from)
    cache = _load_json('ranking_cache.json')
    now = time.time()

    entry = cache.get(cache_key)
    if entry and entry.get('v') == RANKING_CACHE_VER and (now - entry.get('ts', 0)) < RANKING_CACHE_TTL:
        log('Ranking cache hit: %s (%d items)' % (cache_key, len(entry.get('items', []))))
        return entry.get('items', [])

    url = build_filter_url(year_from, media_type)
    mt = 'F' if media_type == TYPE_FILM else 'S'

    log('Fetching ranking: %s (year_from=%s)' % (media_type, year_from))
    log('URL: %s' % url)

    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
    except requests.RequestException as e:
        log('Ranking fetch failed: %s' % e, xbmc.LOGERROR)
        if entry:
            return entry.get('items', [])
        return []

    items = _parse_ranking(resp.text, mt)
    cache[cache_key] = {'items': items, 'ts': now, 'v': RANKING_CACHE_VER}
    _save_json('ranking_cache.json', cache)
    log('Ranking fetched: %s (%d items)' % (cache_key, len(items)))
    return items


# Lock to prevent multiple background refreshes for the same key
_bg_refresh_lock = threading.Lock()
_bg_refresh_active = set()


def _bg_refresh_ranking(media_type, year_from, cache_key):
    """Background thread: refresh ranking and save to cache."""
    try:
        log('BG refresh start: %s' % cache_key)
        url = build_filter_url(year_from, media_type)
        mt = 'F' if media_type == TYPE_FILM else 'S'
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        items = _parse_ranking(resp.text, mt)
        cache = _load_json('ranking_cache.json')
        cache[cache_key] = {'items': items, 'ts': time.time(), 'v': RANKING_CACHE_VER}
        _save_json('ranking_cache.json', cache)
        log('BG refresh done: %s (%d items)' % (cache_key, len(items)))
    except Exception as e:
        log('BG refresh failed: %s – %s' % (cache_key, e), xbmc.LOGWARNING)
    finally:
        with _bg_refresh_lock:
            _bg_refresh_active.discard(cache_key)


def fetch_ranking_cached(media_type, year_from):
    """Return ranking instantly from cache (even stale). Trigger background refresh if needed."""
    cache_key = '%s_%s' % (media_type, year_from)
    cache = _load_json('ranking_cache.json')
    now = time.time()

    entry = cache.get(cache_key)
    if entry and entry.get('v') == RANKING_CACHE_VER:
        items = entry.get('items', [])
        age = now - entry.get('ts', 0)
        if age < RANKING_CACHE_TTL:
            log('Widget cache hit: %s (%d items)' % (cache_key, len(items)))
            return items
        # Stale cache — return it but trigger background refresh
        log('Widget stale cache: %s (%d items, age=%ds)' % (cache_key, len(items), int(age)))
        with _bg_refresh_lock:
            if cache_key not in _bg_refresh_active:
                _bg_refresh_active.add(cache_key)
                t = threading.Thread(target=_bg_refresh_ranking,
                                     args=(media_type, year_from, cache_key),
                                     daemon=True)
                t.start()
        return items

    # No cache at all — must fetch synchronously
    log('Widget no cache: %s, fetching sync' % cache_key)
    return fetch_ranking(media_type, year_from)


def _parse_ranking(html, media_type):
    soup = BeautifulSoup(html, 'html.parser')
    articles = soup.select('article.article')
    items = []
    for art in articles:
        item = _parse_article(art, media_type)
        if item:
            items.append(item)
    return items


def _parse_article(art, media_type):
    title_el = art.select_one('a.film-title-name')
    if not title_el:
        return None
    title = title_el.get_text(strip=True)
    if not title:
        return None

    href = title_el.get('href', '')
    csfd_url = CSFD_BASE + href if href.startswith('/') else href

    item = {'title': title, 'csfd_url': csfd_url, 'media_type': media_type}

    pos = art.select_one('.film-title-user')
    if pos:
        item['position'] = pos.get_text(strip=True).rstrip('.')

    year_el = art.select_one('.film-title-info .info')
    if year_el:
        m = re.search(r'(\d{4})', year_el.get_text())
        if m:
            item['year'] = m.group(1)

    rating_el = art.select_one('.rating-average')
    if rating_el:
        item['rating'] = rating_el.get_text(strip=True)

    votes_el = art.select_one('.rating-total')
    if votes_el:
        m = re.search(r'([\d\s\xa0]+)', votes_el.get_text())
        if m:
            item['votes'] = m.group(1).strip()

    genres_el = art.select_one('p.film-origins-genres')
    if genres_el:
        text = genres_el.get_text(strip=True)
        item['origins_genres'] = text
        parts = text.split(',', 1)
        if len(parts) >= 1:
            item['country'] = parts[0].strip()
        if len(parts) >= 2:
            item['genres'] = parts[1].strip()

    img_el = art.select_one('figure.article-img img')
    if img_el:
        poster = _best_poster(img_el)
        if poster:
            item['poster_url'] = poster

    return item


def _best_poster(img_el):
    srcset = img_el.get('srcset', '')
    if srcset:
        parts = [p.strip() for p in srcset.split(',') if p.strip()]
        if parts:
            url = parts[-1].split(' ')[0].strip()
            if url:
                return 'https:' + url if url.startswith('//') else url
    src = img_el.get('src', '')
    if src and 'pmgstatic' in src:
        return 'https:' + src if src.startswith('//') else src
    return None


# ═══════════════════════════════════════════════════════════════
# DETAIL PAGE (plot + reviews, cached 3 days)
# ═══════════════════════════════════════════════════════════════

def _parse_detail(html):
    soup = BeautifulSoup(html, 'html.parser')
    result = {'plot': '', 'reviews': [], 'original_title': '', 'english_title': ''}

    # Original title + English title from ul.film-names
    _EN_COUNTRIES = {'usa', 'velká británie', 'kanada', 'austrálie', 'irsko', 'nový zéland'}
    names_ul = soup.select_one('ul.film-names')
    if names_ul:
        for li in names_ul.select('li'):
            # Detect country via img.flag[title] (new format ~2026-03)
            flag_img = li.select_one('img.flag[title]')
            country = flag_img['title'].strip().lower() if flag_img else ''

            # Old format: span.info with "anglický název" etc.
            info_span = li.select_one('span.info')
            lang_hint = info_span.get_text(strip=True).lower() if info_span else ''

            # Get clean title (remove spans and img)
            for tag in li.select('span, img'):
                tag.decompose()
            title_text = li.get_text(strip=True)
            title_text = re.sub(r'\s*\((více|méně)\)\s*$', '', title_text).strip()

            if not title_text:
                continue

            # First li = original title
            if not result['original_title']:
                result['original_title'] = title_text

            # English title: new format (flag country) or old format (span.info)
            if country in _EN_COUNTRIES or 'anglick' in lang_hint:
                result['english_title'] = title_text

    for sel in ['div.plots-item', 'div.plot-full p', 'div.plot-preview p']:
        el = soup.select_one(sel)
        if el:
            text = el.get_text(separator=' ', strip=True)
            if text and len(text) > 20:
                result['plot'] = text
                break

    if not result['plot']:
        og = soup.select_one('meta[property="og:description"]')
        if og:
            c = og.get('content', '').strip()
            if c and len(c) > 20 and 'Recenze' not in c[:30]:
                result['plot'] = c

    for art in soup.select('section.box-reviews article[data-film-review]'):
        review = {}
        u = art.select_one('a.user-title-name')
        review['user'] = u.get_text(strip=True) if u else '?'
        review['stars'] = 0
        se = art.select_one('span.stars')
        if se:
            for cls in se.get('class', []):
                m = re.match(r'stars-(\d+)', cls)
                if m:
                    review['stars'] = int(m.group(1))
        c = art.select_one('span.comment[data-film-review-content]')
        review['text'] = c.get_text(separator=' ', strip=True) if c else ''
        if review['text']:
            result['reviews'].append(review)

    return result


def _fetch_one_detail(csfd_url):
    """Fetch detail page for one item. Each call uses its own connection."""
    try:
        resp = requests.get(csfd_url, headers=HEADERS, timeout=10)
        resp.raise_for_status()
        return _parse_detail(resp.text)
    except requests.RequestException as e:
        log('Detail failed: %s' % e, xbmc.LOGWARNING)
        return {'plot': '', 'reviews': [], 'original_title': '', 'english_title': ''}


def _L(string_id):
    """Localized string helper (csfd.py has no ADDON global)."""
    return xbmcaddon.Addon().getLocalizedString(string_id)


def fetch_details_for_page(items, silent=False):
    """Fetch plot + reviews for page items. Parallel, cached 3 days.

    silent=True: no progress dialog, use stale cache (for home screen widgets).
    """
    cache = _load_json('detail_cache.json')
    now = time.time()

    to_fetch = []
    for item in items:
        url = item.get('csfd_url', '')
        if not url:
            continue
        key = _md5(url)
        entry = cache.get(key)
        has_titles = 'original_title' in entry and 'english_title' in entry if entry else False
        fresh = entry and (now - entry.get('ts', 0)) < DETAIL_CACHE_TTL
        if entry and has_titles and (fresh or silent):
            # Use cache (fresh always, stale only in silent/widget mode)
            item['plot'] = entry.get('plot', '')
            item['reviews'] = entry.get('reviews', [])
            item['original_title'] = entry.get('original_title', '')
            item['english_title'] = entry.get('english_title', '')
        else:
            if silent and entry:
                # Stale cache without titles — still use what we have
                item['plot'] = entry.get('plot', '')
                item['reviews'] = entry.get('reviews', [])
                item['original_title'] = entry.get('original_title', '')
                item['english_title'] = entry.get('english_title', '')
            to_fetch.append(item)

    if not to_fetch:
        log('All %d details from cache' % len(items))
        return items

    if silent and not to_fetch:
        log('Widget: all %d details from stale cache' % len(items))
        return items

    # In silent/widget mode, skip HTTP for items that already got stale cache data
    if silent:
        to_fetch = [it for it in to_fetch
                    if not it.get('plot') and not it.get('original_title')]
        if not to_fetch:
            log('Widget: all details from stale cache (partial)')
            return items

    log('Fetching %d/%d details (silent=%s)' % (len(to_fetch), len(items), silent))

    progress = None
    if not silent:
        progress = xbmcgui.DialogProgress()
        # 30012 = "Loading plots and reviews... (%d/%d)"
        progress.create(_L(30001), _L(30012) % (0, len(to_fetch)))

    done = 0
    updated = False

    def _work(item):
        return item, _fetch_one_detail(item['csfd_url'])

    try:
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
            futs = {pool.submit(_work, it): it for it in to_fetch}
            for f in as_completed(futs):
                if progress and progress.iscanceled():
                    for ff in futs:
                        ff.cancel()
                    break
                try:
                    item, det = f.result()
                    item['plot'] = det.get('plot', '')
                    item['reviews'] = det.get('reviews', [])
                    item['original_title'] = det.get('original_title', '')
                    item['english_title'] = det.get('english_title', '')
                    cache[_md5(item['csfd_url'])] = {
                        'plot': item['plot'],
                        'reviews': item['reviews'],
                        'original_title': item['original_title'],
                        'english_title': item['english_title'],
                        'ts': now,
                    }
                    updated = True
                except Exception as e:
                    log('Detail worker err: %s' % e, xbmc.LOGWARNING)
                done += 1
                if progress:
                    pct = int(100 * done / len(to_fetch))
                    progress.update(pct, _L(30012) % (done, len(to_fetch)))
    finally:
        if progress:
            progress.close()

    if updated:
        _save_json('detail_cache.json', cache)

    return items


def fetch_single_reviews(csfd_url):
    """Fetch reviews for one film (context menu)."""
    cache = _load_json('detail_cache.json')
    now = time.time()
    key = _md5(csfd_url)
    entry = cache.get(key)

    if entry and (now - entry.get('ts', 0)) < DETAIL_CACHE_TTL:
        if entry.get('reviews'):
            return entry['reviews']

    det = _fetch_one_detail(csfd_url)
    if not entry:
        entry = {}
    entry.update({
        'reviews': det.get('reviews', []),
        'plot': det.get('plot', entry.get('plot', '')),
        'ts': now,
    })
    cache[key] = entry
    _save_json('detail_cache.json', cache)
    return det.get('reviews', [])
