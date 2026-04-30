#!/usr/bin/env python3
"""
Amsterdam Apartment Hunter - Finds student-friendly apartments near Roeterseiland
Checks Pararius and Kamernet, filters by budget, student status, and transit distance
"""

import json
import os
import time
import hashlib
from datetime import datetime
from urllib.request import urlopen
from urllib.parse import urlencode, quote
import re

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
    'Roeterseiland', 'Weesperbuurt', 'Creatiecijn'
]

def load_seen_apartments():
    """Load previously seen apartment IDs from local file"""
    if os.path.exists(DB_FILE):
        try:
            with open(DB_FILE, 'r') as f:
                return json.load(f)
        except:
            return {}
    return {}

def save_seen_apartments(data):
    """Save seen apartment IDs to local file"""
    with open(DB_FILE, 'w') as f:
        json.dump(data, f)

def scrape_pararius():
    """Scrape Pararius.com for Amsterdam apartments"""
    apartments = []
    
    try:
        # Search URL for Amsterdam apartments with filters
        url = (
            "https://www.pararius.com/apartments/amsterdam"
            "?priceRange=[0,1750]"
            "&sortBy=date_published"
        )
        
        # Note: Pararius is JavaScript-heavy, so we'll search via their listing pages
        # For a production tool, you might want to use Selenium, but we'll parse HTML
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
        
        # This is a simplified version - in production you'd use BeautifulSoup/Selenium
        # For now, we're demonstrating the structure
        print("[Pararius] Skipping JS-heavy site (requires Selenium in production)")
        
    except Exception as e:
        print(f"[Pararius] Error: {e}")
    
    return apartments

def scrape_kamernet():
    """Scrape Kamernet.nl for Amsterdam apartments"""
    apartments = []
    
    try:
        # Kamernet search URL
        params = {
            'search_type': 'apartments',
            'location': 'Amsterdam',
            'priceMax': '1750',
            'sort': 'date_desc'
        }
        
        url = f"https://kamernet.nl/en/apartments?{'&'.join([f'{k}={v}' for k,v in params.items()])}"
        
        print("[Kamernet] Skipping JS-heavy site (requires Selenium in production)")
        
    except Exception as e:
        print(f"[Kamernet] Error: {e}")
    
    return apartments

def check_student_friendly(description, listing_title):
    """Determine if apartment is student-friendly"""
    if not description:
        description = ""
    
    text = (description + " " + listing_title).lower()
    
    # High priority: explicitly allows students
    if any(phrase in text for phrase in ['students allowed', 'student housing', 'student-friendly', 
                                           'suitable for students', 'allows students', 'no age limit']):
        return {'is_student': True, 'priority': 'high'}
    
    # Medium priority: no mention of restrictions
    if 'no students' not in text and 'not suitable for students' not in text:
        return {'is_student': None, 'priority': 'medium'}
    
    return {'is_student': False, 'priority': 'low'}

def check_date_availability(available_from, available_until):
    """
    Validate that apartment covers Aug 15, 2026 - Feb 5, 2027
    Flexible on exact dates, but must cover the essential period
    """
    try:
        # Parse dates
        if isinstance(available_from, str):
            from_date = datetime.strptime(available_from, '%Y-%m-%d').date()
        else:
            from_date = available_from
        
        if isinstance(available_until, str):
            until_date = datetime.strptime(available_until, '%Y-%m-%d').date()
        else:
            until_date = available_until
        
        # Required dates
        move_in_start = datetime.strptime(MOVE_IN_START, '%Y-%m-%d').date()
        classes_end = datetime.strptime(CLASSES_END, '%Y-%m-%d').date()
        latest_checkout = datetime.strptime(LATEST_CHECKOUT, '%Y-%m-%d').date()
        
        # Check if apartment availability covers the needed period
        # Allow up to 2 weeks flexibility on move-in (could arrive earlier/later)
        # Must cover through Jan 30 at minimum
        
        # Must be available by early August (move-in start)
        if from_date > move_in_start:
            return False, f"Available {from_date.strftime('%b %d')} - too late for move-in"
        
        # Must be available through at least late January
        if until_date < classes_end:
            return False, f"Ends {until_date.strftime('%b %d')} - before classes end"
        
        return True, f"{from_date.strftime('%b %d')} - {until_date.strftime('%b %d')}"
    
    except Exception as e:
        # If we can't parse dates, be conservative and skip
        print(f"Warning: Could not parse dates - {e}")
        return False, "Unknown dates"

def calculate_distance_estimate(neighborhood):
    """Estimate distance from Roeterseiland based on neighborhood"""
    # Distance estimates in km (approximate)
    distances = {
        'Indische Buurt': 1.2,
        'Plantage': 0.5,
        'Weesperbuurt': 0.8,
        'Roeterseiland': 0.0,
        'Zeeburg': 2.5,
        'Watergraafsmeer': 3.0,
        'Oosterparkbuurt': 1.5,
        'Oost': 2.0,
        'Oud-Oost': 2.2,
        'Amsterdam-Oost': 2.0
    }
    
    for key, val in distances.items():
        if key.lower() in neighborhood.lower():
            return val
    
    return None

def is_within_target_area(neighborhood, price):
    """Check if apartment is in a target neighborhood and reasonable distance"""
    # Bias towards cheap + close, but allow slightly farther if cheap
    distance = calculate_distance_estimate(neighborhood)
    
    if distance is None:
        return False
    
    # Strict distance check
    if distance > MAX_DISTANCE_KM:
        return False
    
    return True

def send_notification(apartment_data):
    """Send push notification via ntfy.sh"""
    try:
        title = f"🏠 New: {apartment_data['title']}"
        
        message = f"""
Price: €{apartment_data['price']}/month
Available: {apartment_data.get('date_range', 'Check listing')}
Neighborhood: {apartment_data['neighborhood']}
Distance: ~{apartment_data['estimated_distance']:.1f} km from Roeterseiland
Student-friendly: {apartment_data['student_status']}
Priority: {apartment_data['priority']}

Link: {apartment_data['url']}
"""
        
        # Send via ntfy.sh
        cmd = f'curl -d "{message}" https://ntfy.sh/{NOTIFICATION_URL}'
        os.system(cmd)
        
        print(f"✅ Notification sent: {apartment_data['title']}")
    except Exception as e:
        print(f"❌ Failed to send notification: {e}")

def process_apartment(item, source):
    """Process a single apartment listing"""
    try:
        apartment = {
            'id': hashlib.md5(f"{item['url']}{item['title']}".encode()).hexdigest(),
            'title': item['title'],
            'price': item['price'],
            'neighborhood': item['neighborhood'],
            'url': item['url'],
            'source': source,
            'found_at': datetime.now().isoformat(),
            'estimated_distance': calculate_distance_estimate(item['neighborhood']),
        }
        
        # Check student status
        student_info = check_student_friendly(item.get('description', ''), item['title'])
        apartment['student_status'] = student_info['is_student']
        apartment['priority'] = student_info['priority']
        
        # Validate criteria
        if apartment['price'] < BUDGET_MIN or apartment['price'] > BUDGET_MAX:
            return None
        
        if not is_within_target_area(apartment['neighborhood'], apartment['price']):
            return None
        
        # Check dates - CRITICAL filter
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

def main():
    """Main function - run the scraper and check for new apartments"""
    print(f"\n🔍 Amsterdam Apartment Hunter - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Budget: €{BUDGET_MIN}-{BUDGET_MAX}")
    print(f"Max distance: {MAX_DISTANCE_KM}km from Roeterseiland")
    print("-" * 60)
    
    seen = load_seen_apartments()
    new_apartments = []
    
    # Scrape sources
    print("\n📡 Scraping apartment listings...")
    print("[NOTE] In production, this script would use Selenium or playwright")
    print("[NOTE] to handle JavaScript-heavy sites like Pararius and Kamernet")
    
    # For demonstration, we'll show the structure
    all_apartments = []
    
    # Would add Pararius listings
    pararius_apts = scrape_pararius()
    all_apartments.extend([(apt, 'Pararius') for apt in pararius_apts])
    
    # Would add Kamernet listings  
    kamernet_apts = scrape_kamernet()
    all_apartments.extend([(apt, 'Kamernet') for apt in kamernet_apts])
    
    # Process each apartment
    print(f"\n✅ Found {len(all_apartments)} listings")
    
    for apartment_data, source in all_apartments:
        apartment = process_apartment(apartment_data, source)
        
        if not apartment:
            continue
        
        # Check if we've seen this before
        if apartment['id'] in seen:
            continue
        
        # Mark as seen
        seen[apartment['id']] = apartment
        new_apartments.append(apartment)
        
        # Send notification
        send_notification(apartment)
        
        # Rate limit notifications (5 second delay between each)
        time.sleep(5)
    
    # Save state
    save_seen_apartments(seen)
    
    # Summary
    print(f"\n📊 Summary:")
    print(f"New apartments found: {len(new_apartments)}")
    print(f"Total apartments seen so far: {len(seen)}")
    
    if new_apartments:
        print(f"\n🎯 New apartments:")
        for apt in new_apartments:
            print(f"  - {apt['title']}: €{apt['price']} ({apt['priority']} priority)")
    
    return len(new_apartments) > 0

if __name__ == '__main__':
    try:
        success = main()
        exit(0 if success else 1)
    except Exception as e:
        print(f"\n❌ Fatal error: {e}")
        import traceback
        traceback.print_exc()
        exit(1)
