#!/usr/bin/env python3
"""Send a test notification — tap it to open the listing link."""

from urllib.request import urlopen, Request

TOPIC = "amsterdam-apts-josephm"

message = (
    "2 new listings this run:\n"
    "\n"
    "1. Cozy Studio - Indische Buurt\n"
    "   EUR1,650/month | Aug 10 - Feb 10\n"
    "   ~1.2 km | Bathroom: private | Kitchen: private\n"
    "   Student-friendly: True | Priority: high\n"
    "   https://www.pararius.com/apartments/amsterdam\n"
    "\n"
    "2. Room Near UvA - Plantage\n"
    "   EUR1,200/month | Aug 1 - Feb 5\n"
    "   ~0.5 km | Bathroom: shared | Kitchen: private\n"
    "   Student-friendly: True | Priority: medium\n"
    "   https://kamernet.nl/en/apartments"
)

req = Request(
    f"https://ntfy.sh/{TOPIC}",
    data=message.encode(),
    headers={
        "Title": "Test: 2 new Amsterdam apartments",
        "Priority": "high",
        "Tags": "house",
    },
    method="POST",
)

print(f"Sending test notification to: {TOPIC}")
with urlopen(req) as r:
    if r.status == 200:
        print("Sent! Check your phone — tap the notification to open the listing.")
    else:
        print(f"Unexpected response: {r.status}")
