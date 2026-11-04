import requests
import sqlite3
import time
import random
import json

API = "https://en.wikipedia.org/w/api.php"

ROOT_CATEGORY = "Category:Fictional characters"

DB_FILE = "database\\characters.db"
JSON_FILE = "database\\characters.json"

db = sqlite3.connect(DB_FILE)

# -------------------------
# JSON export
# -------------------------


def export_characters():

    rows = db.execute("""
        SELECT
            title,
            image,
            url,
            wikidata_id,
            description,
            entity_types
        FROM characters
    """).fetchall()

    characters = []

    for row in rows:
        characters.append(
            {
                "name": row[0],
                "image": row[1],
                "url": row[2],
                "wikidata_id": row[3],
                "description": row[4],
                "entity_types": row[5],
            }
        )

    with open(JSON_FILE, "w", encoding="utf-8") as f:
        json.dump(characters, f, ensure_ascii=False, indent=2)


# -------------------------
# Main
# -------------------------

if __name__ == "__main__":

    export_characters()
