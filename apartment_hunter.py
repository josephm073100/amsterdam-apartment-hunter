#!/usr/bin/env python3
"""
Amsterdam Apartment Hunter - Finds student-friendly apartments near Roeterseiland
Sources: Pararius, Kamernet, HousingAnywhere, DUWO, SSH, ROOM.nl
"""

import json
import os
import time
import hashlib
import re
import gzip
from datetime import datetime
from urllib.request import urlopen, Request
from urllib.parse import urlencode

# ─── CONFIG ──────────────────────────────────────────────────────────────────

BUDGET_MIN = 0
BUDGET_MAX = 1750
MAX_DISTANCE_KM = 5
NOTIFICATION_URL = os.environ.get('NTFY_TOPIC') or 'amsterdam-apts-josephm'
DB_FILE = 'seen_apartments.json'

MOVE_IN_START  = "2026-08-15"
CLASSES_END    = "2027-01-30"
LATEST_CHECKOUT = "2027-02-05"

TARGET_NEIGHBORHOODS = [
    'Indische Buurt', 'Zeeburg', 'Watergraafsmeer', 'Oosterparkbuurt',
    'Amsterdam-Oost', 'Plantage', 'Oud-Oost', 'Oost',
    'Roeterseiland', 'Weesperbuurt', 'Dapperbuurt',
    'Oostelijk Havengebied', 'IJburg', 'Diemen',
    'Jordaan', 'De Pijp', 'Grachtengordel', 'Centrum',
    'Amsterdam',  # generic fallback
]

BROWSER_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.9,nl;q=0.8',
    'Accept-Encoding': 'gzip, deflate',
    'Connection': 'keep-alive',
}

# ─── HELPERS ─────────────────────────────────────────────────────────────────

def fetch_html(url, extra_headers=None):
    headers = dict(BROWSER_HEADERS)
    if extra_headers:
        headers.update(extra_headers)
    req = Request(url, headers=headers)
    with urlopen(req, timeout=30) as r:
        raw = r.read()
    try:
        return gzip.decompress(raw).decode('utf-8', errors='ignore')
    except Exception:
        return raw.decode('utf-8', errors='ignore')

def fetch_json(url, extra_headers=None):
    headers = dict(BROWSER_HEADERS)
    headers['Accept'] = 'application/json'
    if extra_headers:
        headers.update(extra_headers)
    req = Request(url, headers=headers)
    with urlopen(req, timeout=20) as r:
        raw = r.read()
    try:
        return json.loads(gzip.decompress(raw))
    except Exception:
        return json.loads(raw)

def parse_price(text):
    text = str(text).replace(',', '').replace('.', '').replace(' ', '')
    nums = re.findall(r'\d{3,}', text)
    return int(nums[0]) if nums else 0

def load_seen_apartments():
    if os.path.exists(DB_FILE):
        try:
            with open(DB_FILE, 'r') as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def save_seen_apartments(data):
    with open(DB_FILE, 'w') as f:
        json.dump(data, f)

# ─── SCRAPERS ────────────────────────────────────────────────────────────────

def scrape_pararius():
    """Scrape Pararius with BeautifulSoup. May be blocked by Cloudflare on cloud IPs."""
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        print("[Pararius] BeautifulSoup not installed, skipping")
        return []

    apartments = []
    base = "https://www.pararius.com"

    for page in range(1, 4):
        try:
            url = f"{base}/apartments/amsterdam/0-1750" + (f"/page-{page}" if page > 1 else "")
            html = fetch_html(url)
            soup = BeautifulSoup(html, 'html.parser')

            listings = soup.select('li.search-list__item--listing, section.listing-search-item')
            if not listings:
                print(f"[Pararius] No listings on page {page} (likely blocked by Cloudflare)")
                break

            for item in listings:
                try:
                    link = (
                        item.find('a', class_=re.compile(r'listing-search-item__link')) or
                        item.find('a', href=re.compile(r'/apartment/'))
                    )
                    if not link:
                        continue
                    href = link.get('href', '')
                    if href.startswith('/'):
                        href = f"{base}{href}"
                    title = link.get_text(strip=True) or href

                    price_el = item.find(class_=re.compile(r'price'))
                    price = parse_price(price_el.get_text() if price_el else '')

                    loc_el = item.find(class_=re.compile(r'sub-title|location|city'))
                    location = loc_el.get_text(strip=True) if loc_el else ''
                    neighborhood = location.split(',')[0].strip() if location else 'Amsterdam'

                    desc_el = item.find(class_=re.compile(r'description|features'))
                    description = desc_el.get_text(' ', strip=True) if desc_el else ''

                    if title and 0 < price <= BUDGET_MAX:
                        apartments.append({
                            'title': title, 'price': price,
                            'neighborhood': neighborhood,
                            'url': href, 'description': description,
                        })
                except Exception:
                    continue

            time.sleep(2)
        except Exception as e:
            print(f"[Pararius] Error page {page}: {e}")
            break

    print(f"[Pararius] Found {len(apartments)} listings")
    return apartments


def scrape_kamernet():
    """Kamernet — try their search page HTML."""
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        return []

    apartments = []
    try:
        url = "https://kamernet.nl/en/for-rent/apartments-amsterdam?maxRent=1750&radius=10"
        html = fetch_html(url, {'Referer': 'https://kamernet.nl/'})
        soup = BeautifulSoup(html, 'html.parser')

        cards = soup.select('[class*="listing"], [class*="tile"], [class*="property"], article')
        print(f"[Kamernet] Found {len(cards)} raw cards")

        for card in cards:
            try:
                link = card.find('a', href=re.compile(r'/en/for-rent/'))
                if not link:
                    continue
                href = link.get('href', '')
                if href.startswith('/'):
                    href = 'https://kamernet.nl' + href
                title_el = card.find(['h2', 'h3', 'h4'])
                title = title_el.get_text(strip=True) if title_el else ''
                price_match = re.search(r'€\s*([\d,.]+)', card.get_text())
                price = parse_price(price_match.group(1)) if price_match else 0
                loc_el = card.find(string=re.compile(r'Amsterdam'))
                neighborhood = loc_el.strip().split(',')[0] if loc_el else 'Amsterdam'
                if title and 0 < price <= BUDGET_MAX:
                    apartments.append({
                        'title': title, 'price': price,
                        'neighborhood': neighborhood,
                        'url': href, 'description': card.get_text(' ', strip=True)[:300],
                    })
            except Exception:
                continue
    except Exception as e:
        print(f"[Kamernet] Error: {e}")

    print(f"[Kamernet] Found {len(apartments)} listings")
    return apartments


def scrape_funda():
    """Funda.nl — largest Dutch real estate site, heavy SEO = server-side HTML."""
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        return []

    apartments = []
    try:
        url = "https://www.funda.nl/en/zoeken/huur/?selected_area=%5B%22amsterdam%22%5D&price=%22-1750%22&object_type=%5B%22apartment%22%5D"
        html = fetch_html(url)
        soup = BeautifulSoup(html, 'html.parser')

        # Funda embeds search results as JSON in a script tag
        for script in soup.find_all('script'):
            text = script.string or ''
            if 'searchresult' in text.lower() or 'listings' in text.lower():
                try:
                    # Find JSON blob
                    match = re.search(r'\{.*"price".*\}', text, re.DOTALL)
                    if match:
                        data = json.loads(match.group())
                        # parse if found
                        break
                except Exception:
                    pass

        # Card-based HTML parsing
        cards = soup.select('[data-test-id="search-result-item"], [class*="search-result"], [class*="listing-result"]')
        print(f"[Funda] Found {len(cards)} raw cards")

        for card in cards:
            try:
                link = card.find('a', href=re.compile(r'/huur/|/en/rent/'))
                if not link:
                    continue
                href = link.get('href', '')
                if href.startswith('/'):
                    href = 'https://www.funda.nl' + href
                title_el = card.find(['h2', 'h3', 'h4', '[class*="title"]'])
                title = title_el.get_text(strip=True) if title_el else ''
                price_match = re.search(r'€\s*([\d,.]+)', card.get_text())
                price = parse_price(price_match.group(1)) if price_match else 0
                loc_text = card.get_text()
                neighborhood = 'Amsterdam'
                for n in TARGET_NEIGHBORHOODS:
                    if n.lower() in loc_text.lower():
                        neighborhood = n
                        break
                if title and 0 < price <= BUDGET_MAX:
                    apartments.append({
                        'title': title, 'price': price,
                        'neighborhood': neighborhood,
                        'url': href, 'description': card.get_text(' ', strip=True)[:300],
                    })
            except Exception:
                continue
    except Exception as e:
        print(f"[Funda] Error: {e}")

    print(f"[Funda] Found {len(apartments)} listings")
    return apartments


def scrape_duwo():
    """DUWO — major Amsterdam student housing provider. Less aggressive blocking."""
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        return []

    apartments = []
    try:
        url = "https://www.duwo.nl/en/housing/find-a-room/"
        html = fetch_html(url)
        soup = BeautifulSoup(html, 'html.parser')

        # DUWO typically lists rooms in a table or card layout
        cards = soup.select('.housing-offer, .room-offer, .listing, article, [class*="offer"], [class*="room"]')
        print(f"[DUWO] Found {len(cards)} raw cards")

        for card in cards:
            try:
                link = card.find('a', href=True)
                href = link['href'] if link else ''
                if href and not href.startswith('http'):
                    href = 'https://www.duwo.nl' + href

                title_el = card.find(['h2', 'h3', 'h4', 'h5'])
                title = title_el.get_text(strip=True) if title_el else ''

                price_el = card.find(string=re.compile(r'[€]\s*\d+|\d+\s*[€]|EUR\s*\d+'))
                price = parse_price(price_el) if price_el else 0

                loc_el = card.find(string=re.compile(r'Amsterdam|Oost|Centrum|Noord|Zuid|West'))
                neighborhood = loc_el.strip() if loc_el else 'Amsterdam'

                if title and price > 0:
                    apartments.append({
                        'title': f"[DUWO] {title}", 'price': price,
                        'neighborhood': neighborhood,
                        'url': href or 'https://www.duwo.nl',
                        'description': card.get_text(' ', strip=True)[:300],
                    })
            except Exception:
                continue

    except Exception as e:
        print(f"[DUWO] Error: {e}")

    print(f"[DUWO] Found {len(apartments)} listings")
    return apartments


def scrape_room_nl():
    """ROOM.nl — national student housing platform with Amsterdam listings."""
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        return []

    apartments = []
    try:
        # ROOM.nl has an offer search page
        url = "https://www.room.nl/en/offerings/to-rent/detail/?city=Amsterdam&priceMax=1750"
        html = fetch_html(url)
        soup = BeautifulSoup(html, 'html.parser')

        cards = soup.select('.offer, .listing-item, [class*="offer"], [class*="listing"]')
        print(f"[ROOM.nl] Found {len(cards)} raw cards")

        for card in cards:
            try:
                link = card.find('a', href=True)
                href = link['href'] if link else ''
                if href and not href.startswith('http'):
                    href = 'https://www.room.nl' + href

                title_el = card.find(['h2', 'h3', 'h4'])
                title = title_el.get_text(strip=True) if title_el else ''

                price_el = card.find(string=re.compile(r'€\s*\d+|\d+\s*/\s*month'))
                price = parse_price(price_el) if price_el else 0

                if title and price > 0:
                    apartments.append({
                        'title': f"[ROOM] {title}", 'price': price,
                        'neighborhood': 'Amsterdam',
                        'url': href or 'https://www.room.nl',
                        'description': card.get_text(' ', strip=True)[:300],
                    })
            except Exception:
                continue

    except Exception as e:
        print(f"[ROOM.nl] Error: {e}")

    print(f"[ROOM.nl] Found {len(apartments)} listings")
    return apartments


def scrape_housinganywhere():
    """HousingAnywhere — student-focused international platform."""
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        return []

    apartments = []
    try:
        url = (
            "https://housinganywhere.com/s/Amsterdam--Netherlands"
            "?minPrice=0&maxPrice=1750&roomType=apartment,studio,private-room&sort=newest"
        )
        html = fetch_html(url)
        soup = BeautifulSoup(html, 'html.parser')

        # Try JSON-LD structured data first
        for script in soup.find_all('script', type='application/ld+json'):
            try:
                data = json.loads(script.string or '{}')
                items = data if isinstance(data, list) else data.get('itemListElement', [])
                for item in items:
                    offer = item.get('item', item)
                    name = offer.get('name', '')
                    url_val = offer.get('url', '')
                    offers = offer.get('offers', {})
                    price = int(float(str(offers.get('price', 0)).replace(',', '')))
                    if name and 0 < price <= BUDGET_MAX:
                        apartments.append({
                            'title': name, 'price': price,
                            'neighborhood': offer.get('address', {}).get('addressLocality', 'Amsterdam'),
                            'url': url_val or url,
                            'description': offer.get('description', ''),
                        })
            except Exception:
                continue

        # HTML card fallback
        if not apartments:
            cards = soup.select('[data-testid*="listing"], [class*="ListingCard"], [class*="listing-card"]')
            for card in cards:
                try:
                    link = card.find('a', href=True)
                    href = link['href'] if link else ''
                    if href and not href.startswith('http'):
                        href = 'https://housinganywhere.com' + href
                    title_el = card.find(['h2', 'h3', 'h4'])
                    title = title_el.get_text(strip=True) if title_el else ''
                    price_match = re.search(r'€\s*(\d[\d,.]*)', card.get_text())
                    price = parse_price(price_match.group(1)) if price_match else 0
                    if title and 0 < price <= BUDGET_MAX:
                        apartments.append({
                            'title': title, 'price': price,
                            'neighborhood': 'Amsterdam',
                            'url': href, 'description': '',
                        })
                except Exception:
                    continue

    except Exception as e:
        print(f"[HousingAnywhere] Error: {e}")

    print(f"[HousingAnywhere] Found {len(apartments)} listings")
    return apartments


# ─── FILTERING & SCORING ─────────────────────────────────────────────────────

def check_student_friendly(description, title):
    text = (description + " " + title).lower()
    if any(p in text for p in ['students allowed', 'student housing', 'student-friendly',
                                'suitable for students', 'allows students', 'international students',
                                'expats welcome', 'no age limit']):
        return {'is_student': True, 'priority': 'high'}
    if 'no students' not in text and 'not suitable for students' not in text:
        return {'is_student': None, 'priority': 'medium'}
    return {'is_student': False, 'priority': 'low'}


def check_amenities(description, title):
    text = (description + " " + title).lower()
    private_bath = any(p in text for p in [
        'private bathroom', 'private bath', 'private shower', 'en suite', 'ensuite',
        'own bathroom', 'own shower', 'eigen badkamer', 'eigen douche', 'eigen toilet',
    ])
    shared_bath = any(p in text for p in [
        'shared bathroom', 'shared bath', 'shared shower', 'communal bathroom', 'gedeelde badkamer',
    ])
    private_kitchen = any(p in text for p in [
        'private kitchen', 'own kitchen', 'kitchenette', 'studio',
        'eigen keuken', 'eigen kookgelegenheid',
    ])
    shared_kitchen = any(p in text for p in [
        'shared kitchen', 'communal kitchen', 'gedeelde keuken',
    ])
    bath = 'private' if private_bath else ('shared' if shared_bath else 'unknown')
    kitchen = 'private' if private_kitchen else ('shared' if shared_kitchen else 'unknown')
    boost = (2 if private_bath else (-1 if shared_bath else 0)) + (1 if private_kitchen else 0)
    return {'bathroom': bath, 'kitchen': kitchen, 'priority_boost': boost}


def check_date_availability(available_from, available_until):
    try:
        from_date = datetime.strptime(str(available_from)[:10], '%Y-%m-%d').date()
        until_date = datetime.strptime(str(available_until)[:10], '%Y-%m-%d').date()
        move_in = datetime.strptime(MOVE_IN_START, '%Y-%m-%d').date()
        end = datetime.strptime(CLASSES_END, '%Y-%m-%d').date()
        if from_date > move_in:
            return False, f"Available {from_date.strftime('%b %d')} - too late"
        if until_date < end:
            return False, f"Ends {until_date.strftime('%b %d')} - before classes end"
        return True, f"{from_date.strftime('%b %d')} - {until_date.strftime('%b %d')}"
    except Exception as e:
        return True, "Dates not specified"  # let through if unparseable


def calculate_distance(neighborhood):
    distances = {
        'Roeterseiland': 0.0, 'Plantage': 0.5, 'Weesperbuurt': 0.8,
        'Indische Buurt': 1.2, 'Dapperbuurt': 1.8, 'Oosterparkbuurt': 1.5,
        'Oost': 2.0, 'Oud-Oost': 2.2, 'Amsterdam-Oost': 2.0,
        'Zeeburg': 2.5, 'Watergraafsmeer': 3.0, 'Jordaan': 3.5,
        'De Pijp': 3.0, 'Centrum': 2.0, 'Grachtengordel': 2.5,
        'Oostelijk Havengebied': 3.2, 'IJburg': 5.0, 'Diemen': 4.5,
        'Amsterdam': 2.5,
    }
    for key, val in distances.items():
        if key.lower() in neighborhood.lower():
            return val
    return None


def is_within_target_area(neighborhood):
    dist = calculate_distance(neighborhood)
    return dist is not None and dist <= MAX_DISTANCE_KM


# ─── NOTIFICATIONS ────────────────────────────────────────────────────────────

def send_batch_notification(apartments):
    """Send one summary notification for all new apartments found this run."""
    if not apartments:
        return
    count = len(apartments)
    title = f"{count} new Amsterdam apt{'s' if count > 1 else ''} found"
    lines = [f"{count} new listing{'s' if count > 1 else ''} this run:\n"]
    for i, apt in enumerate(apartments, 1):
        dist = apt.get('estimated_distance')
        dist_str = f"~{dist:.1f} km" if dist is not None else "dist unknown"
        lines.append(f"{i}. {apt['title']} - {apt['neighborhood']}")
        lines.append(f"   EUR{apt['price']}/month | {apt.get('date_range', 'Check listing')}")
        lines.append(f"   {dist_str} | Bath: {apt.get('bathroom','?')} | Kitchen: {apt.get('kitchen','?')}")
        lines.append(f"   Student: {apt['student_status']} | Priority: {apt['priority']}")
        if apt.get('url'):
            lines.append(f"   {apt['url']}")
        lines.append("")
    message = "\n".join(lines).strip()
    has_high = any(a.get('priority') == 'high' for a in apartments)
    _ntfy_send(title, message, priority="high" if has_high else "default")


def send_heartbeat(new_count, total_seen, sources_summary):
    """Status notification every run — confirms the bot is alive."""
    title = f"Bot ran: {new_count} new apt{'s' if new_count != 1 else ''} found"
    message = (
        f"Run at {datetime.now().strftime('%Y-%m-%d %H:%M')} UTC\n"
        f"New listings this run: {new_count}\n"
        f"Total seen so far: {total_seen}\n"
        f"Sources: {sources_summary}"
    )
    _ntfy_send(title, message, priority="default")


def _ntfy_send(title, message, priority="default"):
    try:
        # HTTP headers must be ASCII-safe — strip/replace any non-ASCII characters
        safe_title = title.encode('ascii', 'replace').decode('ascii')
        req = Request(
            f"https://ntfy.sh/{NOTIFICATION_URL}",
            data=message.encode('utf-8'),
            headers={"Title": safe_title, "Priority": priority, "Tags": "house"},
            method="POST",
        )
        with urlopen(req, timeout=15) as r:
            if r.status == 200:
                print(f"✅ Notification sent: {title}")
            else:
                print(f"⚠️  ntfy status {r.status}")
    except Exception as e:
        print(f"❌ Notification failed: {e}")


# ─── PROCESSING ──────────────────────────────────────────────────────────────

def process_apartment(item, source):
    try:
        apt = {
            'id': hashlib.md5(f"{item.get('url','')}{item.get('title','')}".encode()).hexdigest(),
            'title': item['title'],
            'price': item['price'],
            'neighborhood': item['neighborhood'],
            'url': item.get('url', ''),
            'source': source,
            'found_at': datetime.now().isoformat(),
            'estimated_distance': calculate_distance(item['neighborhood']),
        }
        if apt['price'] < BUDGET_MIN or apt['price'] > BUDGET_MAX:
            return None
        if not is_within_target_area(apt['neighborhood']):
            return None

        student = check_student_friendly(item.get('description', ''), item['title'])
        apt['student_status'] = student['is_student']
        apt['priority'] = student['priority']

        amenity = check_amenities(item.get('description', ''), item['title'])
        apt['bathroom'] = amenity['bathroom']
        apt['kitchen'] = amenity['kitchen']

        boost = amenity['priority_boost']
        order = ['low', 'medium', 'high']
        idx = order.index(apt['priority'])
        apt['priority'] = order[max(0, min(2, idx + (1 if boost > 0 else (-1 if boost < 0 else 0))))]

        if 'available_from' in item and 'available_until' in item:
            valid, date_range = check_date_availability(item['available_from'], item['available_until'])
            apt['date_range'] = date_range
            if not valid:
                return None
        else:
            apt['date_range'] = "Dates not specified"

        return apt
    except Exception as e:
        print(f"Error processing: {e}")
        return None


# ─── MAIN ─────────────────────────────────────────────────────────────────────

def main():
    print(f"\n🔍 Amsterdam Apartment Hunter — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} UTC")
    print(f"Budget: €{BUDGET_MIN}–{BUDGET_MAX} | Max: {MAX_DISTANCE_KM}km from Roeterseiland")
    print(f"ntfy topic: {NOTIFICATION_URL}")
    print("-" * 60)

    seen = load_seen_apartments()
    new_apartments = []

    print("\n📡 Scraping...")
    sources = [
        (scrape_pararius,       'Pararius'),
        (scrape_kamernet,       'Kamernet'),
        (scrape_funda,          'Funda'),
        (scrape_housinganywhere,'HousingAnywhere'),
    ]

    all_raw = []
    source_counts = {}
    for fn, name in sources:
        try:
            results = fn()
            source_counts[name] = len(results)
            all_raw.extend([(r, name) for r in results])
        except Exception as e:
            print(f"[{name}] Uncaught error: {e}")
            source_counts[name] = 0
        time.sleep(1)

    print(f"\n✅ Raw total: {len(all_raw)}")

    for item, source in all_raw:
        apt = process_apartment(item, source)
        if not apt or apt['id'] in seen:
            continue
        seen[apt['id']] = apt
        new_apartments.append(apt)

    save_seen_apartments(seen)

    if new_apartments:
        send_batch_notification(new_apartments)

    sources_str = " | ".join(f"{k}:{v}" for k, v in source_counts.items())
    send_heartbeat(len(new_apartments), len(seen), sources_str)

    print(f"\n📊 New: {len(new_apartments)} | Total seen: {len(seen)}")
    if new_apartments:
        for apt in new_apartments:
            print(f"  [{apt['source']}] {apt['title']}: €{apt['price']} ({apt['priority']})")

    return len(new_apartments) > 0


if __name__ == '__main__':
    try:
        main()
        exit(0)
    except Exception as e:
        print(f"\n❌ Fatal: {e}")
        import traceback
        traceback.print_exc()
        exit(1)
