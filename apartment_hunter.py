#!/usr/bin/env python3
"""
Amsterdam Apartment Hunter - Finds student-friendly apartments near Roeterseiland
Checks Pararius, Kamernet, and HousingAnywhere, filters by budget, student status, and transit distance
"""

import json
import os
import time
import hashlib
import re
import gzip
from datetime import datetime
from urllib.request import urlopen, Request
from urllib.parse import urlencode, quote

# Configuration
BUDGET_MIN = 0
BUDGET_MAX = 1750  # in euros
ROETERSEILAND_LAT = 52.3645
ROETERSEILAND_LNG = 4.9107
MAX_DISTANCE_KM = 5  # approximation for 30 min transit/bike
NOTIFICATION_URL = os.environ.get('NTFY_TOPIC', 'your-topic-here')  # Set via GitHub secret
DB_FILE = 'seen_apartments.json'

# Date range for study abroad
MOVE_IN_START = "2026-08-15"  # ~2 weeks before classes
MOVE_IN_END = "2026-08-31"    # Or earlier if you find something
CLASSES_END = "2027-01-30"    # Last class date
LATEST_CHECKOUT = "2027-02-05"  # A few days after to prepare return

# Neighborhoods close to Roeterseiland within transit distance
TARGET_NEIGHBORHOODS = [
    'Indische Buurt', 'Zeeburg', 'Watergraafsmeer', 'Oosterparkbuurt',
    'Amsterdam-Oost', 'Plantage', 'Oud-Oost', 'Oost',
    'Roeterseiland', 'Weesperbuurt', 'Creatiecijn', 'Dapperbuurt',
    'Oostelijk Havengebied', 'IJburg', 'Diemen',
]

BROWSER_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.9,nl;q=0.8',
    'Accept-Encoding': 'gzip, deflate',
    'Connection': 'keep-alive',
    'Upgrade-Insecure-Requests': '1',
    'Cache-Control': 'no-cache',
}


def fetch_html(url, extra_headers=None):
    """Fetch a URL and return decoded HTML string."""
    headers = dict(BROWSER_HEADERS)
    if extra_headers:
        headers.update(extra_headers)
    req = Request(url, headers=headers)
    with urlopen(req, timeout=30) as r:
        raw = r.read()
    # Handle gzip encoding
    try:
        return gzip.decompress(raw).decode('utf-8', errors='ignore')
    except Exception:
        return raw.decode('utf-8', errors='ignore')


def parse_price(text):
    """Extract integer price from strings like '€ 1,250 /mnd' or 'EUR1250'."""
    text = text.replace(',', '').replace('.', '')
    nums = re.findall(r'\d{3,}', text)
    return int(nums[0]) if nums else 0


def load_seen_apartments():
    """Load previously seen apartment IDs from local file"""
    if os.path.exists(DB_FILE):
        try:
            with open(DB_FILE, 'r') as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_seen_apartments(data):
    """Save seen apartment IDs to local file"""
    with open(DB_FILE, 'w') as f:
        json.dump(data, f)


# ─── SCRAPERS ────────────────────────────────────────────────────────────────

def scrape_pararius():
    """Scrape Pararius.com for Amsterdam apartments under €1750."""
    from bs4 import BeautifulSoup
    apartments = []
    base = "https://www.pararius.com"

    # Pararius paginates with /page-N; scrape first 3 pages
    for page in range(1, 4):
        try:
            url = f"{base}/apartments/amsterdam/0-1750" + (f"/page-{page}" if page > 1 else "")
            print(f"[Pararius] Fetching page {page}...")
            html = fetch_html(url)
            soup = BeautifulSoup(html, 'html.parser')

            listings = soup.select(
                'li.search-list__item--listing, '
                'section.listing-search-item'
            )

            if not listings:
                # Try alternate selectors
                listings = soup.find_all(attrs={'data-listing-id': True})

            if not listings:
                print(f"[Pararius] No listings found on page {page} (may be blocked or end of results)")
                break

            for item in listings:
                try:
                    # URL + title
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

                    # Price
                    price_el = item.find(class_=re.compile(r'price'))
                    price = parse_price(price_el.get_text() if price_el else '')

                    # Location
                    loc_el = item.find(class_=re.compile(r'sub-title|location|city'))
                    location = loc_el.get_text(strip=True) if loc_el else ''
                    neighborhood = location.split(',')[0].strip() if location else 'Amsterdam'

                    # Description snippets
                    desc_el = item.find(class_=re.compile(r'description|features|tag'))
                    description = desc_el.get_text(' ', strip=True) if desc_el else ''

                    if title and 0 < price <= BUDGET_MAX:
                        apartments.append({
                            'title': title,
                            'price': price,
                            'neighborhood': neighborhood,
                            'url': href,
                            'description': description,
                        })
                except Exception:
                    continue

            time.sleep(2)  # be polite between pages

        except Exception as e:
            print(f"[Pararius] Error on page {page}: {e}")
            break

    print(f"[Pararius] Total: {len(apartments)} listings")
    return apartments


def scrape_kamernet():
    """Scrape Kamernet via their JSON search API."""
    apartments = []
    try:
        api_url = (
            "https://kamernet.nl/api/v1/search/listings"
            "?locationids=1&radius=10&maxrent=1750&listingtype=7"  # listingtype 7 = apartments
            "&pageno=1&pagesize=40&sortby=date_desc"
        )
        headers = dict(BROWSER_HEADERS)
        headers.update({
            'Accept': 'application/json',
            'X-Requested-With': 'XMLHttpRequest',
            'Referer': 'https://kamernet.nl/en/for-rent/apartments-amsterdam',
        })
        req = Request(api_url, headers=headers)
        with urlopen(req, timeout=20) as r:
            raw = r.read()
        try:
            data = json.loads(gzip.decompress(raw))
        except Exception:
            data = json.loads(raw)

        listings = data.get('listings') or data.get('data') or []
        print(f"[Kamernet] API returned {len(listings)} listings")

        for item in listings:
            try:
                price = int(item.get('rent', 0) or item.get('price', 0))
                if not price or price > BUDGET_MAX:
                    continue
                title = item.get('title') or item.get('listingTitle') or 'Kamernet listing'
                city_district = item.get('cityDistrictName') or item.get('neighborhood') or ''
                neighborhood = city_district.split(',')[0].strip() if city_district else 'Amsterdam'
                listing_id = item.get('listingId') or item.get('id') or ''
                url = f"https://kamernet.nl/en/for-rent/room-amsterdam/{listing_id}" if listing_id else 'https://kamernet.nl'
                description = item.get('description') or item.get('descriptionTranslated') or ''
                available_from = item.get('availableFrom') or ''
                available_until = item.get('availableUntil') or ''

                apt = {
                    'title': title,
                    'price': price,
                    'neighborhood': neighborhood,
                    'url': url,
                    'description': description,
                }
                if available_from:
                    apt['available_from'] = available_from[:10]  # YYYY-MM-DD
                if available_until:
                    apt['available_until'] = available_until[:10]
                apartments.append(apt)
            except Exception:
                continue

    except Exception as e:
        print(f"[Kamernet] Error: {e}")

    print(f"[Kamernet] Total: {len(apartments)} listings")
    return apartments


def scrape_housinganywhere():
    """Scrape HousingAnywhere — student-focused, often has English listings."""
    from bs4 import BeautifulSoup
    apartments = []
    base = "https://housinganywhere.com"

    try:
        url = (
            f"{base}/s/Amsterdam--Netherlands"
            "?minPrice=0&maxPrice=1750&roomType=apartment,studio,private-room"
            "&sort=newest"
        )
        print("[HousingAnywhere] Fetching listings...")
        html = fetch_html(url)
        soup = BeautifulSoup(html, 'html.parser')

        # HousingAnywhere embeds listing data in JSON script tags
        scripts = soup.find_all('script', type='application/ld+json')
        for script in scripts:
            try:
                data = json.loads(script.string or '{}')
                items = []
                if isinstance(data, list):
                    items = data
                elif data.get('@type') == 'ItemList':
                    items = data.get('itemListElement', [])

                for item in items:
                    try:
                        offer = item.get('item', item)
                        name = offer.get('name', '')
                        url_val = offer.get('url', '')
                        offers = offer.get('offers', {})
                        price = int(float(str(offers.get('price', 0)).replace(',', '')))
                        address = offer.get('address', {})
                        neighborhood = address.get('addressLocality', 'Amsterdam')

                        if name and 0 < price <= BUDGET_MAX:
                            apartments.append({
                                'title': name,
                                'price': price,
                                'neighborhood': neighborhood,
                                'url': url_val or url,
                                'description': offer.get('description', ''),
                            })
                    except Exception:
                        continue
            except Exception:
                continue

        # Also try card-based HTML parsing as fallback
        if not apartments:
            cards = soup.select('[data-testid*="listing"], [class*="ListingCard"], [class*="listing-card"]')
            for card in cards:
                try:
                    link = card.find('a', href=True)
                    href = link['href'] if link else ''
                    if href and not href.startswith('http'):
                        href = base + href
                    title_el = card.find(['h2', 'h3', 'h4'])
                    title = title_el.get_text(strip=True) if title_el else ''
                    price_el = card.find(string=re.compile(r'€\s*\d+'))
                    price = parse_price(price_el) if price_el else 0
                    if title and 0 < price <= BUDGET_MAX:
                        apartments.append({
                            'title': title,
                            'price': price,
                            'neighborhood': 'Amsterdam',
                            'url': href,
                            'description': '',
                        })
                except Exception:
                    continue

    except Exception as e:
        print(f"[HousingAnywhere] Error: {e}")

    print(f"[HousingAnywhere] Total: {len(apartments)} listings")
    return apartments


# ─── FILTERING & SCORING ─────────────────────────────────────────────────────

def check_student_friendly(description, listing_title):
    """Determine if apartment is student-friendly"""
    if not description:
        description = ""
    text = (description + " " + listing_title).lower()
    if any(phrase in text for phrase in [
        'students allowed', 'student housing', 'student-friendly',
        'suitable for students', 'allows students', 'no age limit',
        'international students', 'expats welcome',
    ]):
        return {'is_student': True, 'priority': 'high'}
    if 'no students' not in text and 'not suitable for students' not in text:
        return {'is_student': None, 'priority': 'medium'}
    return {'is_student': False, 'priority': 'low'}


def check_amenities(description, listing_title):
    """
    Check for private bathroom and kitchen.
    Private bath/shower: preferred, boosts priority.
    Private kitchen: nice to have, further boosts priority.
    """
    if not description:
        description = ""
    text = (description + " " + listing_title).lower()

    private_bath = any(phrase in text for phrase in [
        'private bathroom', 'private bath', 'private shower', 'en suite', 'ensuite',
        'own bathroom', 'own bath', 'own shower', 'private toilet', 'private facilities',
        'eigen badkamer', 'eigen douche', 'eigen toilet',
    ])
    shared_bath = any(phrase in text for phrase in [
        'shared bathroom', 'shared bath', 'shared shower', 'shared facilities',
        'communal bathroom', 'gedeelde badkamer',
    ])
    private_kitchen = any(phrase in text for phrase in [
        'private kitchen', 'own kitchen', 'kitchenette', 'studio',
        'eigen keuken', 'eigen kookgelegenheid',
    ])
    shared_kitchen = any(phrase in text for phrase in [
        'shared kitchen', 'communal kitchen', 'gedeelde keuken',
    ])

    bath_status = 'private' if private_bath else ('shared' if shared_bath else 'unknown')
    kitchen_status = 'private' if private_kitchen else ('shared' if shared_kitchen else 'unknown')

    priority_boost = 0
    if private_bath:
        priority_boost += 2
    elif shared_bath:
        priority_boost -= 1
    if private_kitchen:
        priority_boost += 1

    return {
        'bathroom': bath_status,
        'kitchen': kitchen_status,
        'priority_boost': priority_boost,
    }


def check_date_availability(available_from, available_until):
    """Validate that apartment covers Aug 15, 2026 - Feb 5, 2027"""
    try:
        if isinstance(available_from, str):
            from_date = datetime.strptime(available_from[:10], '%Y-%m-%d').date()
        else:
            from_date = available_from
        if isinstance(available_until, str):
            until_date = datetime.strptime(available_until[:10], '%Y-%m-%d').date()
        else:
            until_date = available_until

        move_in_start = datetime.strptime(MOVE_IN_START, '%Y-%m-%d').date()
        classes_end = datetime.strptime(CLASSES_END, '%Y-%m-%d').date()

        if from_date > move_in_start:
            return False, f"Available {from_date.strftime('%b %d')} - too late for move-in"
        if until_date < classes_end:
            return False, f"Ends {until_date.strftime('%b %d')} - before classes end"

        return True, f"{from_date.strftime('%b %d')} - {until_date.strftime('%b %d')}"
    except Exception as e:
        print(f"Warning: Could not parse dates - {e}")
        return True, "Dates not specified"  # allow through if we can't parse


def calculate_distance_estimate(neighborhood):
    """Estimate distance from Roeterseiland based on neighborhood"""
    distances = {
        'Roeterseiland': 0.0,
        'Plantage': 0.5,
        'Weesperbuurt': 0.8,
        'Indische Buurt': 1.2,
        'Oosterparkbuurt': 1.5,
        'Oost': 2.0,
        'Oud-Oost': 2.2,
        'Amsterdam-Oost': 2.0,
        'Dapperbuurt': 1.8,
        'Zeeburg': 2.5,
        'Watergraafsmeer': 3.0,
        'Oostelijk Havengebied': 3.2,
        'IJburg': 5.0,
        'Diemen': 4.5,
        'Jordaan': 3.5,
        'De Pijp': 3.0,
        'Oud-Zuid': 4.0,
        'Amsterdam': 2.5,  # generic fallback
    }
    for key, val in distances.items():
        if key.lower() in neighborhood.lower():
            return val
    return None


def is_within_target_area(neighborhood):
    """Check if apartment is in a target neighborhood and within distance."""
    distance = calculate_distance_estimate(neighborhood)
    if distance is None:
        return False
    return distance <= MAX_DISTANCE_KM


# ─── NOTIFICATION ─────────────────────────────────────────────────────────────

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
        bath = apt.get('bathroom', 'unknown')
        kitchen = apt.get('kitchen', 'unknown')
        url = apt.get('url', '')

        lines.append(f"{i}. {apt['title']} - {apt['neighborhood']}")
        lines.append(f"   EUR{apt['price']}/month | {apt.get('date_range', 'Check listing')}")
        lines.append(f"   {dist_str} | Bathroom: {bath} | Kitchen: {kitchen}")
        lines.append(f"   Student-friendly: {apt['student_status']} | Priority: {apt['priority']}")
        if url:
            lines.append(f"   {url}")
        lines.append("")

    message = "\n".join(lines).strip()
    has_high = any(a.get('priority') == 'high' for a in apartments)

    try:
        req = Request(
            f"https://ntfy.sh/{NOTIFICATION_URL}",
            data=message.encode(),
            headers={
                "Title": title,
                "Priority": "high" if has_high else "default",
                "Tags": "house",
            },
            method="POST",
        )
        with urlopen(req) as r:
            if r.status == 200:
                print(f"✅ Batch notification sent: {count} apartment(s)")
            else:
                print(f"⚠️  Unexpected ntfy status {r.status}")
    except Exception as e:
        print(f"❌ Failed to send notification: {e}")


# ─── PROCESSING ──────────────────────────────────────────────────────────────

def process_apartment(item, source):
    """Validate and score a single apartment listing."""
    try:
        apartment = {
            'id': hashlib.md5(f"{item.get('url','')}{item.get('title','')}".encode()).hexdigest(),
            'title': item['title'],
            'price': item['price'],
            'neighborhood': item['neighborhood'],
            'url': item.get('url', ''),
            'source': source,
            'found_at': datetime.now().isoformat(),
            'estimated_distance': calculate_distance_estimate(item['neighborhood']),
        }

        # Price filter
        if apartment['price'] < BUDGET_MIN or apartment['price'] > BUDGET_MAX:
            return None

        # Distance filter
        if not is_within_target_area(apartment['neighborhood']):
            return None

        # Student status
        student_info = check_student_friendly(item.get('description', ''), item['title'])
        apartment['student_status'] = student_info['is_student']
        apartment['priority'] = student_info['priority']

        # Amenities
        amenity_info = check_amenities(item.get('description', ''), item['title'])
        apartment['bathroom'] = amenity_info['bathroom']
        apartment['kitchen'] = amenity_info['kitchen']

        # Adjust priority based on amenities
        boost = amenity_info['priority_boost']
        priority_order = ['low', 'medium', 'high']
        current_idx = priority_order.index(apartment['priority'])
        new_idx = max(0, min(2, current_idx + (1 if boost > 0 else (-1 if boost < 0 else 0))))
        apartment['priority'] = priority_order[new_idx]

        # Date filter (only if dates provided)
        if 'available_from' in item and 'available_until' in item:
            date_valid, date_range = check_date_availability(item['available_from'], item['available_until'])
            apartment['date_range'] = date_range
            if not date_valid:
                return None
        else:
            apartment['date_range'] = "Dates not specified"

        return apartment

    except Exception as e:
        print(f"Error processing apartment: {e}")
        return None


# ─── MAIN ─────────────────────────────────────────────────────────────────────

def main():
    print(f"\n🔍 Amsterdam Apartment Hunter - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Budget: €{BUDGET_MIN}-{BUDGET_MAX} | Max distance: {MAX_DISTANCE_KM}km from Roeterseiland")
    print("-" * 60)

    seen = load_seen_apartments()
    new_apartments = []

    print("\n📡 Scraping apartment listings...")

    all_apartments = []

    pararius_apts = scrape_pararius()
    all_apartments.extend([(apt, 'Pararius') for apt in pararius_apts])
    time.sleep(2)

    kamernet_apts = scrape_kamernet()
    all_apartments.extend([(apt, 'Kamernet') for apt in kamernet_apts])
    time.sleep(2)

    ha_apts = scrape_housinganywhere()
    all_apartments.extend([(apt, 'HousingAnywhere') for apt in ha_apts])

    print(f"\n✅ Raw listings fetched: {len(all_apartments)}")

    for apartment_data, source in all_apartments:
        apartment = process_apartment(apartment_data, source)
        if not apartment:
            continue
        if apartment['id'] in seen:
            continue
        seen[apartment['id']] = apartment
        new_apartments.append(apartment)

    save_seen_apartments(seen)

    if new_apartments:
        send_batch_notification(new_apartments)

    print(f"\n📊 Summary:")
    print(f"  New apartments found: {len(new_apartments)}")
    print(f"  Total seen so far:    {len(seen)}")

    if new_apartments:
        print(f"\n🎯 New apartments:")
        for apt in new_apartments:
            print(f"  - [{apt['source']}] {apt['title']}: €{apt['price']} ({apt['priority']} priority)")

    return len(new_apartments) > 0


if __name__ == '__main__':
    try:
        main()
        exit(0)
    except Exception as e:
        print(f"\n❌ Fatal error: {e}")
        import traceback
        traceback.print_exc()
        exit(1)
