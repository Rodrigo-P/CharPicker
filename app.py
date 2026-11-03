
import json
import random
from pathlib import Path

from flask import Flask, render_template, request

app = Flask(__name__)

DATABASE_FILE = Path(__file__).parent / "characters.json"

def load_characters():
    with open(DATABASE_FILE, "r", encoding="utf-8") as file:
        characters = json.load(file)

    # Only retain entries with usable names and images
    return [
        character for character in characters
        if character.get("name")
        and character.get("image")
    ]


characters = load_characters()


@app.route("/", methods=["GET", "POST"])
def index():

    count = 5

    if request.method == "POST":
        try:
            count = int(request.form.get("count", 5))
        except (ValueError, TypeError):
            count = 5

    count = max(1, min(count, 50))

    selected = random.sample(
        characters,
        min(count, len(characters))
    )

    return render_template(
        "index.html",
        characters=selected,
        count=count,
        total=len(characters)
    )


if __name__ == "__main__":
    app.run()
