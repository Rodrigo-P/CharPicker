import requests
import sqlite3
import time
import random
import json

API = "https://en.wikipedia.org/w/api.php"

ROOT_CATEGORY = "Category:Fictional characters"

DB_FILE = "database/fictional_characters.db"

# Image filters
MIN_WIDTH = 200
MIN_HEIGHT = 200
MAX_ASPECT_RATIO = 2.0
MIN_FILE_SIZE = 10_000

ALLOWED_MIME_TYPES = {"image/jpeg", "image/png", "image/webp"}

# Request configuration
REQUEST_DELAY = 1.0
MAX_RETRIES = 6
BATCH_SIZE = 50

session = requests.Session()
session.headers.update(
    {
        "User-Agent": (
            "FictionalCharacterCollector/1.0 " "(contact: your-email@example.com)"
        )
    }
)


# -------------------------
# Database setup
# -------------------------

db = sqlite3.connect(DB_FILE)

db.execute("""
CREATE TABLE IF NOT EXISTS categories (
    title TEXT PRIMARY KEY,
    processed INTEGER DEFAULT 0
)
""")

db.execute("""
CREATE TABLE IF NOT EXISTS characters (
    pageid INTEGER PRIMARY KEY,
    title TEXT,
    image TEXT,
    url TEXT,
    width INTEGER,
    height INTEGER,
    mime TEXT,
    size INTEGER
)
""")

# Migrate older databases
existing_columns = {row[1] for row in db.execute("PRAGMA table_info(characters)")}

for column, definition in [
    ("width", "INTEGER"),
    ("height", "INTEGER"),
    ("mime", "TEXT"),
    ("size", "INTEGER"),
]:
    if column not in existing_columns:
        db.execute(f"ALTER TABLE characters ADD COLUMN {column} {definition}")

db.execute(
    """
INSERT OR IGNORE INTO categories (title)
VALUES (?)
""",
    (ROOT_CATEGORY,),
)

db.commit()


# -------------------------
# API helper with backoff
# -------------------------


def api_request(params):

    params = {**params, "format": "json", "maxlag": 5}

    for attempt in range(MAX_RETRIES):

        try:
            response = session.get(API, params=params, timeout=30)

            if response.status_code in (429, 503):

                retry_after = response.headers.get("Retry-After")

                wait = float(retry_after) if retry_after else 5 * (2**attempt)

                print(f"Rate limited. Waiting {wait}s...")
                time.sleep(wait + random.uniform(0, 1))
                continue

            response.raise_for_status()

            data = response.json()

            if "error" in data:

                error_code = data["error"].get("code")

                if error_code in ("maxlag", "ratelimited"):

                    wait = 5 * (2**attempt)

                    print(f"API throttling. Waiting {wait}s...")

                    time.sleep(wait)
                    continue

                raise RuntimeError(data["error"])

            time.sleep(REQUEST_DELAY + random.uniform(0, 0.3))

            return data

        except requests.RequestException as error:

            if attempt == MAX_RETRIES - 1:
                raise

            wait = 5 * (2**attempt)

            print(f"Request error: {error}. Waiting {wait}s...")

            time.sleep(wait)

    raise RuntimeError("Maximum retries exceeded")


# -------------------------
# Category members
# -------------------------


def get_members(category, member_type):

    params = {
        "action": "query",
        "list": "categorymembers",
        "cmtitle": category,
        "cmtype": member_type,
        "cmlimit": "500",
    }

    if member_type == "page":
        params["cmnamespace"] = "0"

    members = []

    while True:

        data = api_request(params)

        members.extend(data["query"]["categorymembers"])

        if "continue" not in data:
            break

        params.update(data["continue"])

    return members


# -------------------------
# Image filtering
# -------------------------


def is_good_image(metadata):

    width = metadata.get("width", 0)
    height = metadata.get("height", 0)
    mime = metadata.get("mime", "")
    size = metadata.get("size", 0)

    if width < MIN_WIDTH or height < MIN_HEIGHT:
        return False

    ratio = max(width / height, height / width)

    if ratio > MAX_ASPECT_RATIO:
        return False

    if mime not in ALLOWED_MIME_TYPES:
        return False

    if size < MIN_FILE_SIZE:
        return False

    return True


# -------------------------
# Batch image retrieval
# -------------------------


def get_article_images(pages):

    found = []

    stats = {
        "articles": len(pages),
        "with_filename": 0,
        "with_thumbnail": 0,
        "with_metadata": 0,
        "passed_filters": 0,
        "missing_metadata": 0,
    }

    for i in range(0, len(pages), BATCH_SIZE):

        batch = pages[i : i + BATCH_SIZE]

        pageids = "|".join(str(page["pageid"]) for page in batch)

        # Step 1: Retrieve article images
        data = api_request(
            {
                "action": "query",
                "pageids": pageids,
                "prop": "pageimages",
                "piprop": "name|thumbnail",
                "pithumbsize": "500",
            }
        )

        image_pages = {}

        for page in data.get("query", {}).get("pages", {}).values():

            filename = page.get("pageimage")
            thumbnail = page.get("thumbnail", {}).get("source")

            if filename:
                stats["with_filename"] += 1

            if thumbnail:
                stats["with_thumbnail"] += 1

            if not filename or not thumbnail:
                continue

            image_pages[page["pageid"]] = {
                "title": page["title"],
                "filename": filename,
                "thumbnail": thumbnail,
            }

        if not image_pages:
            continue

        # STEP 2: Retrieve image metadata in batches

        filenames = ["File:" + item["filename"] for item in image_pages.values()]

        metadata_by_filename = {}

        for j in range(0, len(filenames), 50):

            file_batch = filenames[j : j + 50]

            metadata_data = api_request(
                {
                    "action": "query",
                    "titles": "|".join(file_batch),
                    "prop": "imageinfo",
                    "iiprop": "size|mime",
                    "redirects": 1,
                }
            )

            query = metadata_data.get("query", {})

            # Apply API title normalization and redirects
            normalized = {
                entry["from"]: entry["to"] for entry in query.get("normalized", [])
            }

            normalized.update(
                {entry["from"]: entry["to"] for entry in query.get("redirects", [])}
            )

            for file_page in query.get("pages", {}).values():

                info_list = file_page.get("imageinfo", [])

                if not info_list:
                    continue

                info = info_list[0]

                metadata = {
                    "width": info.get("width", 0),
                    "height": info.get("height", 0),
                    "mime": info.get("mime", ""),
                    "size": info.get("size", 0),
                }

                metadata_by_filename[file_page["title"]] = metadata

                # Also store the original requested title
                for requested in file_batch:
                    resolved = normalized.get(requested, requested)

                    if resolved == file_page["title"]:
                        metadata_by_filename[requested] = metadata

        # Step 3: Apply filters
        for pageid, item in image_pages.items():

            filename = "File:" + item["filename"]

            metadata = metadata_by_filename.get(filename)

            if not metadata:
                stats["missing_metadata"] += 1
                continue

            stats["with_metadata"] += 1

            if not is_good_image(metadata):
                continue

            stats["passed_filters"] += 1

            found.append(
                {
                    "pageid": pageid,
                    "title": item["title"],
                    "image": item["thumbnail"],
                    "url": ("https://en.wikipedia.org/?curid=" + str(pageid)),
                    "width": metadata["width"],
                    "height": metadata["height"],
                    "mime": metadata["mime"],
                    "size": metadata["size"],
                }
            )

    print("\nIMAGE DIAGNOSTICS")
    for key, value in stats.items():
        print(f"{key}: {value}")

    print("\nAccepted examples:")
    for character in found[:5]:
        print(character["title"], character["width"], character["height"])

    return found


# -------------------------
# Recursive crawler
# -------------------------


def crawl():

    while True:

        row = db.execute("""
            SELECT title
            FROM categories
            WHERE processed = 0
            LIMIT 1
        """).fetchone()

        if row is None:
            break

        category = row[0]

        print("\nProcessing:", category)

        # Find subcategories
        subcategories = get_members(category, "subcat")

        for subcat in subcategories:

            db.execute(
                """
                INSERT OR IGNORE INTO categories (title)
                VALUES (?)
            """,
                (subcat["title"],),
            )

        # Find articles
        pages = get_members(category, "page")

        characters = get_article_images(pages)

        db.executemany(
            """
            INSERT OR IGNORE INTO characters
            (pageid, title, image, url, width, height, mime, size)
            VALUES (
                :pageid, :title, :image, :url,
                :width, :height, :mime, :size
            )
        """,
            characters,
        )

        db.execute(
            """
            UPDATE categories
            SET processed = 1
            WHERE title = ?
        """,
            (category,),
        )

        db.commit()

        print(
            f"Subcategories: {len(subcategories)}"
            f" | Articles: {len(pages)}"
            f" | Accepted images: {len(characters)}"
        )

    print("\nCrawl completed!")


# -------------------------
# Main
# -------------------------

if __name__ == "__main__":

    try:
        crawl()

    finally:
        db.close()
