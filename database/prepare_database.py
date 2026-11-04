import sqlite3
import time
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import quote

import requests

SOURCE_DB = Path("chars.db")
CACHE_DB = Path("popularity_cache.db")
OUTPUT_DB = Path("characters.db")

MIN_DAILY_VIEWS = 100
LOOKBACK_DAYS = 365

REQUEST_DELAY = 0.5
MAX_RETRIES = 6
COMMIT_INTERVAL = 500

API_BASE = "https://wikimedia.org/api/rest_v1/metrics/" "pageviews/per-article"

session = requests.Session()
session.headers.update(
    {"User-Agent": "CharPicker/1.0 (contact: your-email@example.com)"}
)


def load_cache():

    with sqlite3.connect(CACHE_DB) as db:
        return dict(db.execute("""
            SELECT title, avg_daily_views
            FROM popularity
        """).fetchall())


def get_pageviews(title, start, end):

    encoded_title = quote(title.replace(" ", "_"), safe="")

    url = (
        f"{API_BASE}/en.wikipedia.org/"
        f"all-access/user/{encoded_title}/daily/"
        f"{start}/{end}"
    )

    for attempt in range(MAX_RETRIES):

        try:
            response = session.get(url, timeout=30)

            if response.status_code == 404:
                return 0.0

            if response.status_code in (429, 500, 502, 503, 504):
                retry_after = response.headers.get("Retry-After")

                try:
                    wait = float(retry_after)
                except (TypeError, ValueError):
                    wait = min(5 * (2**attempt), 120)

                time.sleep(wait)
                continue

            response.raise_for_status()

            data = response.json()

            views = sum(item.get("views", 0) for item in data.get("items", []))

            return views / LOOKBACK_DAYS

        except requests.RequestException:

            if attempt == MAX_RETRIES - 1:
                raise

            time.sleep(min(5 * (2**attempt), 120))

    raise RuntimeError(f"Failed: {title}")


def prepare_database():

    # Load all source characters
    with sqlite3.connect(SOURCE_DB) as source:
        source.row_factory = sqlite3.Row

        characters = source.execute("""
            SELECT pageid, title, image, url
            FROM characters
            WHERE image IS NOT NULL
              AND title IS NOT NULL
              AND url IS NOT NULL
        """).fetchall()

    # Load popularity cache once
    cache = load_cache()

    end_date = date.today() - timedelta(days=1)
    start_date = end_date - timedelta(days=LOOKBACK_DAYS - 1)

    start = start_date.strftime("%Y%m%d") + "00"
    end = end_date.strftime("%Y%m%d") + "00"

    print(f"Characters: {len(characters)}")
    print(f"Cached: {len(cache)}")

    # Keep connections open
    with sqlite3.connect(CACHE_DB) as cache_db, sqlite3.connect(OUTPUT_DB) as output_db:

        cache_db.execute("""
            CREATE TABLE IF NOT EXISTS popularity (
                title TEXT PRIMARY KEY,
                avg_daily_views REAL NOT NULL,
                checked_at TEXT NOT NULL
            )
        """)

        output_db.execute("""
            CREATE TABLE IF NOT EXISTS characters (
                pageid INTEGER PRIMARY KEY,
                title TEXT NOT NULL,
                image TEXT NOT NULL,
                url TEXT NOT NULL
            )
        """)

        output_db.execute("DELETE FROM characters")

        accepted = 0
        rejected = 0
        errors = 0

        inserts = []
        cache_inserts = []

        for index, character in enumerate(characters, 1):

            title = character["title"]

            if title in cache:
                views = cache[title]

            else:
                try:
                    views = get_pageviews(title, start, end)

                    cache[title] = views

                    cache_inserts.append((title, views, date.today().isoformat()))

                    time.sleep(REQUEST_DELAY)

                except Exception as error:
                    errors += 1
                    print(f"ERROR: {title}: {error}")
                    continue

            if views >= MIN_DAILY_VIEWS:

                inserts.append(
                    (character["pageid"], title, character["image"], character["url"])
                )

                accepted += 1

            else:
                rejected += 1

            # Batch writes
            if len(inserts) >= COMMIT_INTERVAL:
                output_db.executemany(
                    """
                    INSERT OR REPLACE INTO characters
                    VALUES (?, ?, ?, ?)
                """,
                    inserts,
                )

                inserts.clear()

            if len(cache_inserts) >= COMMIT_INTERVAL:
                cache_db.executemany(
                    """
                    INSERT OR REPLACE INTO popularity
                    VALUES (?, ?, ?)
                """,
                    cache_inserts,
                )

                cache_inserts.clear()

            if index % 1000 == 0:
                print(
                    f"{index}/{len(characters)} | "
                    f"Accepted: {accepted} | "
                    f"Rejected: {rejected} | "
                    f"Errors: {errors}"
                )

        # Flush remaining records
        if inserts:
            output_db.executemany(
                """
                INSERT OR REPLACE INTO characters
                VALUES (?, ?, ?, ?)
            """,
                inserts,
            )

        if cache_inserts:
            cache_db.executemany(
                """
                INSERT OR REPLACE INTO popularity
                VALUES (?, ?, ?)
            """,
                cache_inserts,
            )

    print("\nCompleted!")
    print(f"Accepted: {accepted}")
    print(f"Rejected: {rejected}")
    print(f"Errors: {errors}")


if __name__ == "__main__":
    prepare_database()
