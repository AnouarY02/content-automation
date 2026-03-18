"""Quick script to trigger a campaign generation via the API."""
import requests
import sys

url = "http://127.0.0.1:8000/api/campaigns/start"
payload = {"app_id": "app_cce58eb7", "platform": "tiktok"}

try:
    r = requests.post(url, json=payload, timeout=10)
    print(f"Status: {r.status_code}")
    print(r.text[:500])
except Exception as e:
    print(f"Error: {e}", file=sys.stderr)
    sys.exit(1)
