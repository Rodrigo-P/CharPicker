import json
import argparse
import re
from pathlib import Path
from typing import Literal

from ollama import chat
from pydantic import BaseModel

MODEL = "deepseek-r1:14b"
INPUT_FILE = "database/characters.json"
CACHE_FILE = "database/validity_cache.json"

OUTPUT_VALID = "database/characters_valid.json"
OUTPUT_INVALID = "database/characters_invalid.json"

BATCH_SIZE = 20


class EntityResult(BaseModel):
    name: str
    is_character: bool
    reason: str


class ClassificationResponse(BaseModel):
    results: list[EntityResult]


SYSTEM_PROMPT = """
Determine whether each Wikipedia article describes one specific individual fictional character.

VALID:
- A specific fictional individual from any medium.
- Literary and mythological individuals.
- Supporting characters.
- Fictional animals and creatures.
- Named stock characters, if they represent a specific established fictional individual.

INVALID:
- Real people, authors and actors.
- Books, novels, series, movies and franchises.
- Places, organizations, businesses and events.
- Cultural symbols and concepts.
- Character categories, archetypes and generic roles.
- Groups or classes of characters rather than individuals.
- Articles about fictional works rather than their characters.

VISUAL REPRESENTATION:

For characters originating in literary works, require at least one visual adaptation outside theatre, such as:
- Movies or television.
- Animation or anime.
- Comics or graphic novels.
- Video games.

Exclude literary characters known only through written works or theatrical performances.

The adaptation must provide an identifiable visual depiction suitable for use as a guessing-game picture.

The adaptation does not need to be popular or recognizable.
Popularity and recognizability belong to the separate recognition filter.


IMPORTANT DISTINCTION:

The article must describe an individual character, not a type of character or a category of fictional roles.

Examples:
- Sherlock Holmes: VALID (specific fictional individual).
- Harlequin: VALID (specific traditional character).
- Bond girl: INVALID (generic category of characters).
- Final girl: INVALID (character archetype).
- Superhero: INVALID (generic character category).
- Disney Princess: INVALID (character category).

Use Wikidata descriptions and entity types as evidence.

Ignore popularity, age and modern relevance.
A character can be obscure or ancient and still be valid.

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


def normalize_name(name):
    return re.sub(r"[^a-z0-9]", "", name.lower())


def get_candidate_data(character):
    entity_types = character.get("entity_types", [])

    if isinstance(entity_types, str):
        try:
            entity_types = json.loads(entity_types)
        except json.JSONDecodeError:
            entity_types = []

    return {
        "name": get_name(character),
        "description": character.get("description"),
        "entity_types": entity_types,
    }


def classify_batch(characters):

    candidates = [get_candidate_data(c) for c in characters]
    names = [c["name"] for c in candidates]

    response = chat(
        model=MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    "Classify every candidate:\n"
                    + json.dumps(candidates, ensure_ascii=False)
                ),
            },
        ],
        format=ClassificationResponse.model_json_schema(),
        options={"temperature": 0},
    )

    parsed = ClassificationResponse.model_validate_json(response.message.content)

    lookup = {normalize_name(n): n for n in names}
    results = {}

    for result in parsed.results:
        key = normalize_name(result.name)

        if key in lookup:
            name = lookup[key]
            data = result.model_dump()
            data["name"] = name
            results[name] = data

    missing = [c for c in characters if get_name(c) not in results]

    for character in missing:
        name = get_name(character)
        print(f"Retrying individually: {name}")

        result = classify_single(character)

        results[name] = result

    return results


def classify_single(character):

    candidate = get_candidate_data(character)
    name = candidate["name"]

    response = chat(
        model=MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": json.dumps(candidate, ensure_ascii=False),
            },
        ],
        format=EntityResult.model_json_schema(),
        options={"temperature": 0},
    )

    result = EntityResult.model_validate_json(response.message.content)

    data = result.model_dump()
    data["name"] = name

    return data


def save_selection(characters, cache):

    valid = []
    invalid = []
    unclassified = []

    for character in characters:
        name = get_name(character)
        result = cache.get(name)

        if not result:
            unclassified.append(character)

        elif result["is_character"]:
            valid.append(character)

        else:
            invalid.append(
                {
                    "character": character,
                    "classification": result,
                }
            )

    save_json(OUTPUT_VALID, valid)
    save_json(OUTPUT_INVALID, invalid)

    print("\nValidity selection:")
    print(f"Valid: {len(valid)}")
    print(f"Invalid: {len(invalid)}")
    print(f"Unclassified: {len(unclassified)}")


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

    print("\nValidity filtering finished.")


if __name__ == "__main__":
    main()
