import json
import argparse
from pathlib import Path
from typing import Literal

from ollama import chat
from pydantic import BaseModel

MODEL = "deepseek-r1:14b"

INPUT_FILE = "database/characters_valid.json"
CACHE_FILE = "database/recognition_cache.json"

OUTPUT_KEEP = "database/characters_filtered.json"
OUTPUT_EXCLUDE = "database/characters_excluded.json"
OUTPUT_UNCERTAIN = "database/characters_uncertain.json"

BATCH_SIZE = 15


class RecognitionResult(BaseModel):
    name: str
    decision: Literal["keep", "exclude", "uncertain"]
    modern_pop_culture_presence: Literal["high", "medium", "low", "unknown"]
    reason: str


class ClassificationResponse(BaseModel):
    results: list[RecognitionResult]


SYSTEM_PROMPT = """
Select fictional characters for an Akinator-style guessing game aimed at Brazilian players aged 18–34.

Would a young Brazilian player reasonably recognize or identify this character through yes/no questions?

KEEP:
- Characters from popular movies, TV, animation, anime, comics and video games.
- Recognizable supporting characters.
- Older characters that remain relevant through modern entertainment or popular culture.
- Iconic literary and mythological characters with meaningful contemporary recognition.

EXCLUDE:
- Obscure characters primarily known through classical literature, theatre or mythology.
- Characters with little recognition among younger Brazilians.
- Traditional theatrical stock characters without contemporary mainstream recognition.
- Characters primarily remembered by older generations.
- Niche characters known mainly to dedicated fans.
- Literary archetypes or generic character categories rather than independently recognizable individuals.
- Internet personalities and satirical fictional characters whose recognition is limited to niche online communities or specific foreign political contexts.
- Fictional political figures whose appeal depends on familiarity with another country's political culture.


OLDER CHARACTERS:

Older characters require additional scrutiny.

Modern entertainment exposure is strong evidence, but not mandatory. Historical fame or multiple adaptations alone are not sufficient.

FAME IS NOT ENOUGH:

Popularity within a dedicated fanbase does not imply broad public recognition.
A character should be recognizable beyond the people who follow its specific franchise, fandom or community.
Do not confuse a character being well-known by name with being recognizable as an individual through distinctive traits, appearance or identity.

Historical importance, literary fame, cult status or international recognition do not guarantee recognition among younger Brazilians.

A character should ideally be recognizable without requiring knowledge of its original work.

Prefer characters with recognizable identities in contemporary mainstream entertainment.

Examples:

KEEP:
Dracula, Sherlock Holmes, Batman, Aladdin, Sweeney Todd, Amaterasu, Bluto.

EXCLUDE:
Billy Budd, Black Beauty, Blanche DuBois, Bob Cratchit, Lady Macbeth, Banquo, Lugh, Medea, Harlequin, Brighella, Humbaba, Don Juan, Fantômas, Fonzie, Jean Valjean, Scarlett O'Hara.

CULTURAL AND GENERATIONAL RELEVANCE:

Consider the target audience's cultural background.
Characters and cultural icons strongly associated with older generations or specific foreign historical contexts should generally be excluded unless they remain recognizable among younger Brazilians.

Historical or international fame does not imply recognition in Brazil.

Example:
- Rosie the Riveter: EXCLUDE (primarily an American WWII-era cultural icon with limited relevance to younger Brazilians).
- R.O.B. is too niche for the target audience.

Prioritize Brazilian mainstream familiarity and worldwide popular entertainment over historical or academic importance.
Exclude characters whose recognition depends primarily on familiarity with foreign historical, political or cultural contexts that are not widely understood in Brazil.

Do not invent popularity.

Return exactly one result per candidate.
Preserve names exactly.
Return JSON matching the supplied schema.
"""


def load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path, data):
    Path(path).parent.mkdir(parents=True, exist_ok=True)

    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def get_name(entry):
    if isinstance(entry, str):
        return entry

    for key in ("name", "title", "character"):
        if key in entry:
            return str(entry[key])

    raise ValueError(f"Unknown character format: {entry}")


def classify_batch(characters):

    names = [get_name(c) for c in characters]

    response = chat(
        model=MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    "Classify every character below:\n"
                    + json.dumps(names, ensure_ascii=False)
                ),
            },
        ],
        format=ClassificationResponse.model_json_schema(),
        options={"temperature": 0},
    )

    parsed = ClassificationResponse.model_validate_json(response.message.content)

    lookup = {n.casefold(): n for n in names}
    results = {}

    for result in parsed.results:

        original = lookup.get(result.name.casefold())

        if original:
            data = result.model_dump()
            data["name"] = original
            results[original] = data

    missing = [c for c in characters if get_name(c) not in results]

    for character in missing:
        name = get_name(character)
        print(f"Retrying individually: {name}")
        results[name] = classify_single(character)

    return results


def classify_single(character):

    name = get_name(character)

    response = chat(
        model=MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": f"Classify this character: {name}",
            },
        ],
        format=RecognitionResult.model_json_schema(),
        options={"temperature": 0},
    )

    result = RecognitionResult.model_validate_json(response.message.content)

    data = result.model_dump()
    data["name"] = name

    return data


def save_selection(characters, cache):

    kept = []
    excluded = []
    uncertain = []

    for character in characters:

        name = get_name(character)
        result = cache.get(name)

        if not result:
            uncertain.append(
                {
                    "character": character,
                    "reason": "Not classified",
                }
            )

        elif result["decision"] == "keep":
            kept.append(
                {
                    "name": name,
                    "image": character.get("image"),
                    "url": character.get("url"),
                }
            )

        elif result["decision"] == "exclude":
            excluded.append(
                {
                    "character": character,
                    "classification": result,
                }
            )

        else:
            uncertain.append(
                {
                    "character": character,
                    "classification": result,
                }
            )

    save_json(OUTPUT_KEEP, kept)
    save_json(OUTPUT_EXCLUDE, excluded)
    save_json(OUTPUT_UNCERTAIN, uncertain)

    print("\nRecognition selection:")
    print(f"Kept: {len(kept)}")
    print(f"Excluded: {len(excluded)}")
    print(f"Uncertain: {len(uncertain)}")


def main():

    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default=INPUT_FILE)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--batch-size", type=int, default=BATCH_SIZE)

    args = parser.parse_args()

    characters = load_json(args.input)

    if args.limit:
        characters = characters[: args.limit]

    cache = load_json(CACHE_FILE) if Path(CACHE_FILE).exists() else {}

    pending = [c for c in characters if get_name(c) not in cache]

    print(f"Total: {len(characters)}")
    print(f"Cached: {len(characters) - len(pending)}")
    print(f"Remaining: {len(pending)}")

    save_selection(characters, cache)

    for start in range(0, len(pending), args.batch_size):

        batch = pending[start : start + args.batch_size]

        try:
            print(f"\nBatch {start // args.batch_size + 1}")

            results = classify_batch(batch)
            cache.update(results)

            save_json(CACHE_FILE, cache)
            save_selection(characters, cache)

        except Exception as error:
            print(f"ERROR: {error}")
            print("Previous results preserved.")
            break

    print("\nRecognition filtering finished.")


if __name__ == "__main__":
    main()
