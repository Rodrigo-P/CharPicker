import sqlite3
import time
import random

from datetime import date, timedelta
from pathlib import Path
from urllib.parse import quote

import requests

# -------------------------
# Configuration
# -------------------------

SOURCE_DB = Path("database/chars.db")
CACHE_DB = Path("database/popularity_cache.db")
OUTPUT_DB = Path("database/characters.db")

MIN_DAILY_VIEWS = 100
LOOKBACK_DAYS = 365

REQUEST_DELAY = 1.0
MAX_RETRIES = 6

COMMIT_INTERVAL = 500

API_BASE = "https://wikimedia.org/api/rest_v1/metrics/" "pageviews/per-article"

session = requests.Session()

session.headers.update(
    {"User-Agent": "CharPicker/1.0 (contact: your-email@example.com)"}
)


# -------------------------
# Database helpers
# -------------------------


def get_columns(db, table):

    return {row[1] for row in db.execute(f"PRAGMA table_info({table})")}


def load_cache(db):

    db.execute("""
        CREATE TABLE IF NOT EXISTS popularity (
            title TEXT PRIMARY KEY,
            avg_daily_views REAL NOT NULL,
            checked_at TEXT NOT NULL
        )
    """)

    return dict(db.execute("""
        SELECT title, avg_daily_views
        FROM popularity
    """).fetchall())


# -------------------------
# Pageview API
# -------------------------


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
                    wait = min(15 * (2**attempt), 300)

                print(
                    f"HTTP {response.status_code}: " f"{title}. Waiting {wait:.0f}s..."
                )

                time.sleep(wait + random.uniform(1, 3))
                continue

            response.raise_for_status()

            data = response.json()

            views = sum(item.get("views", 0) for item in data.get("items", []))

            actual_days = (
                date.fromisoformat(end[:8]) - date.fromisoformat(start[:8])
            ).days + 1

            return views / actual_days

        except requests.RequestException as error:

            if attempt == MAX_RETRIES - 1:
                raise

            wait = min(15 * (2**attempt), 300)

            print(f"Request failed: {error}")
            print(f"Retrying in {wait}s...")

            time.sleep(wait)

    raise RuntimeError(f"Failed: {title}")


# -------------------------
# Output database
# -------------------------


def prepare_output_database(db):

    db.execute("""
        CREATE TABLE IF NOT EXISTS characters (
            pageid INTEGER PRIMARY KEY,
            title TEXT NOT NULL,
            image TEXT NOT NULL,
            url TEXT NOT NULL,
            wikidata_id TEXT,
            description TEXT,
            entity_types TEXT,
            wikidata_processed INTEGER
        )
    """)

    columns = get_columns(db, "characters")

    for column, definition in [
        ("wikidata_id", "TEXT"),
        ("description", "TEXT"),
        ("entity_types", "TEXT"),
        ("wikidata_processed", "INTEGER"),
    ]:

        if column not in columns:

            db.execute(f"ALTER TABLE characters " f"ADD COLUMN {column} {definition}")


# -------------------------
# Bulk database writes
# -------------------------

OUTPUT_INSERT = """
    INSERT OR REPLACE INTO characters (
        pageid,
        title,
        image,
        url,
        wikidata_id,
        description,
        entity_types,
        wikidata_processed
    )
    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
"""


def save_output(db, records):

    if not records:
        return

    db.executemany(OUTPUT_INSERT, records)
    db.commit()
    records.clear()


def save_cache(db, records):

    if not records:
        return

    db.executemany(
        """
        INSERT OR REPLACE INTO popularity
        (title, avg_daily_views, checked_at)
        VALUES (?, ?, ?)
    """,
        records,
    )

    db.commit()
    records.clear()


# -------------------------
# Main processing
# -------------------------


def prepare_database():

    total_start = time.perf_counter()

    # STEP 1: Load source characters

    print("Loading source characters...")

    with sqlite3.connect(SOURCE_DB) as source:

        source.row_factory = sqlite3.Row

        source_columns = get_columns(source, "characters")

        optional_columns = [
            "wikidata_id",
            "description",
            "entity_types",
            "wikidata_processed",
        ]

        selected_columns = ["pageid", "title", "image", "url"]

        selected_columns.extend(
            column for column in optional_columns if column in source_columns
        )

        query = f"""
            SELECT {", ".join(selected_columns)}
            FROM characters
            WHERE image IS NOT NULL
              AND title IS NOT NULL
              AND url IS NOT NULL
        """

        characters = source.execute(query).fetchall()

    # STEP 2: Prepare dates

    end_date = date.today() - timedelta(days=1)

    start_date = end_date - timedelta(days=LOOKBACK_DAYS - 1)

    start = start_date.strftime("%Y%m%d") + "00"
    end = end_date.strftime("%Y%m%d") + "00"

    # STEP 3: Open databases and load cache

    with sqlite3.connect(CACHE_DB) as cache_db, sqlite3.connect(OUTPUT_DB) as output_db:

        cache_db.execute("PRAGMA journal_mode=WAL")
        output_db.execute("PRAGMA journal_mode=WAL")

        cache = load_cache(cache_db)

        prepare_output_database(output_db)

        # Clear previous selection
        output_db.execute("DELETE FROM characters")
        output_db.commit()

        # STEP 4: Calculate cache statistics

        cached_count = sum(character["title"] in cache for character in characters)

        missing_count = len(characters) - cached_count

        print(f"\nCharacters: {len(characters)}")
        print(f"Cache hits: {cached_count}")
        print(f"Cache misses: {missing_count}")
        print(
            f"Cache hit rate: " f"{cached_count / max(len(characters), 1) * 100:.1f}%"
        )

        # STEP 5: Process characters

        accepted = 0
        rejected = 0
        errors = 0
        fetched = 0

        output_records = []
        cache_records = []

        fetch_time = 0.0
        processing_start = time.perf_counter()

        today = date.today().isoformat()

        for index, character in enumerate(characters, 1):

            title = character["title"]

            # Cache lookup: no network or sleep

            if title in cache:

                views = cache[title]

            else:

                # Only uncached entries reach this block

                try:

                    request_start = time.perf_counter()

                    views = get_pageviews(title, start, end)

                    fetch_time += time.perf_counter() - request_start

                    fetched += 1

                    cache[title] = views

                    cache_records.append((title, views, today))

                    time.sleep(REQUEST_DELAY)

                except Exception as error:

                    errors += 1

                    print(f"ERROR: {title}: {error}")

                    continue

            # Apply popularity filter

            if views >= MIN_DAILY_VIEWS:

                output_records.append(
                    (
                        character["pageid"],
                        title,
                        character["image"],
                        character["url"],
                        (
                            character["wikidata_id"]
                            if "wikidata_id" in character.keys()
                            else None
                        ),
                        (
                            character["description"]
                            if "description" in character.keys()
                            else None
                        ),
                        (
                            character["entity_types"]
                            if "entity_types" in character.keys()
                            else None
                        ),
                        (
                            character["wikidata_processed"]
                            if "wikidata_processed" in character.keys()
                            else None
                        ),
                    )
                )

                accepted += 1

            else:

                rejected += 1

            # Periodic output writes

            if len(output_records) >= COMMIT_INTERVAL:

                save_output(output_db, output_records)

            # Periodic cache writes

            if len(cache_records) >= COMMIT_INTERVAL:

                save_cache(cache_db, cache_records)

            # Progress reporting

            if index % 250 == 0 or index == len(characters):

                elapsed = time.perf_counter() - processing_start

                print(
                    f"{index}/{len(characters)} | "
                    f"Accepted: {accepted} | "
                    f"Rejected: {rejected} | "
                    f"Fetched: {fetched} | "
                    f"Errors: {errors} | "
                    f"Elapsed: {elapsed:.1f}s"
                )

        # STEP 6: Save remaining records

        save_output(output_db, output_records)
        save_cache(cache_db, cache_records)

    # STEP 7: Statistics

    total_elapsed = time.perf_counter() - total_start

    print("\n" + "=" * 40)
    print("COMPLETED")
    print("=" * 40)

    print(f"Total characters: {len(characters)}")
    print(f"Cache hits: {cached_count}")
    print(f"Cache misses: {missing_count}")
    print(f"New API requests: {fetched}")
    print(f"Accepted: {accepted}")
    print(f"Rejected: {rejected}")
    print(f"Errors: {errors}")

    print(f"\nAPI request time: {fetch_time:.1f}s")
    print(f"Total runtime: {total_elapsed:.1f}s")

    print(f"Other processing time: " f"{max(0, total_elapsed - fetch_time):.1f}s")


if __name__ == "__main__":

    prepare_database()
