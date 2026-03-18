"""Direct pipeline test — bypasses the API server."""
import sys
import os

# Ensure project root is on path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from workflows.campaign_pipeline import run_pipeline

print("Starting campaign pipeline directly...")
result = run_pipeline(
    app_id="app_cce58eb7",
    platform="tiktok",
    on_progress=lambda msg: print(f"  > {msg}"),
)
print(f"\nDone! Campaign ID: {result.get('id', '?')}")
print(f"Viral score: {result.get('viral_score', {}).get('composite_score', 'N/A')}")
print(f"Scores: {result.get('viral_score', {}).get('scores', {})}")
