import requests
import sqlite3
import time
import random
import json

API = "https://en.wikipedia.org/w/api.php"

ROOT_CATEGORY = "Category:Fictional characters"

DB_FILE = "characters.db"
JSON_FILE = "characters.json"

db = sqlite3.connect(DB_FILE)

# -------------------------
# JSON export
# -------------------------


def export_json():

    rows = db.execute("""
        SELECT title, image, url
        FROM characters
        ORDER BY title
    """).fetchall()

    results = [
        {
            "name": r[0],
            "image": r[1],
            "url": r[2],
        }
        for r in rows
    ]

    with open(JSON_FILE, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    print(f"\nExported {len(results)} characters.")


# -------------------------
# Main
# -------------------------

if __name__ == "__main__":

    export_json()
