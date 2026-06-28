import sys
from pathlib import Path

# Add parent directory to path so we can import app
sys.path.insert(0, str(Path(__file__).parent.parent))

from app import App, seed

# Initialize the database
try:
    seed()
except Exception as e:
    print("Seed error:", e)

# Explicitly defining a class named 'handler' ensures Vercel's strict parser finds it
class handler(App):
    pass