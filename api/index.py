import sys
from pathlib import Path

# Add parent directory to path so we can import app
sys.path.insert(0, str(Path(__file__).parent.parent))

# Import App and seed from app.py
from app import App, seed

# Initialize the database (creates tables and initial data in /tmp) 
# before Vercel starts serving requests.
try:
    seed()
except Exception as e:
    print("Seed error:", e)

# Vercel's @vercel/python requires the application to be exported as 'handler'
handler = App