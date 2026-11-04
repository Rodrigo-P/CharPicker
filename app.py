import sqlite3
from pathlib import Path

from flask import Flask, render_template, request

app = Flask(__name__)

# Database location
DATABASE_FILE = Path(__file__).parent / "fictional_characters.db"


def get_db_connection():
    """Create a database connection for the current operation."""
    connection = sqlite3.connect(DATABASE_FILE, timeout=10)
    connection.row_factory = sqlite3.Row
    return connection


def get_character_count():
    """Return the number of characters in the database."""
    with get_db_connection() as db:
        result = db.execute("""
            SELECT COUNT(*)
            FROM characters
            WHERE image IS NOT NULL
              AND title IS NOT NULL
        """).fetchone()

    return result[0]


def get_random_characters(count=5):
    """Retrieve random characters directly from SQLite."""

    with get_db_connection() as db:
        rows = db.execute(
            """
            SELECT
                title AS name,
                image,
                url
            FROM characters
            WHERE image IS NOT NULL
              AND title IS NOT NULL
              AND url IS NOT NULL
            ORDER BY RANDOM()
            LIMIT ?
        """,
            (count,),
        ).fetchall()

    return [dict(row) for row in rows]


@app.route("/", methods=["GET", "POST"])
def index():

    # Default selection size
    count = 5

    if request.method == "POST":
        try:
            count = int(request.form.get("count", 5))
        except (ValueError, TypeError):
            count = 5

    # Restrict selection size
    if count not in (5, 10, 20, 50):
        count = 5

    # Retrieve characters
    selected = get_random_characters(count)

    # Database statistics
    total = get_character_count()

    return render_template("index.html", characters=selected, count=count, total=total)


if __name__ == "__main__":
    app.run(debug=True)
