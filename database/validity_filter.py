import json
import argparse
import re
from pathlib import Path
from typing import Literal

from ollama import chat
from pydantic import BaseModel

MODEL = "qwen3-14b-q5"

INPUT_FILE = "database/characters.json"
CACHE_FILE = "database/validity_cache.json"

OUTPUT_VALID = "database/characters_valid.json"
OUTPUT_INVALID = "database/characters_invalid.json"

BATCH_SIZE = 15

OLLAMA_OPTIONS = {"temperature": 0, "num_ctx": 8192, "num_predict": 4096}


class EntityResult(BaseModel):
    name: str
    is_character: bool
    reason: str


class ClassificationResponse(BaseModel):
    results: list[EntityResult]


SYSTEM_PROMPT = """
Determine whether each Wikipedia article describes one specific individual fictional character.

The objective is to identify fictional individuals, NOT to evaluate their popularity, recognition, importance or cultural relevance.

VALID:
- Specific fictional individuals from any medium.
- Literary and mythological individuals.
- Supporting characters.
- Fictional animals and creatures.
- Named traditional or stock characters when they represent an established individual fictional identity.
- Characters from obscure, old or niche fictional works.

INVALID:
- Real people, authors and actors.
- Books, novels, series, movies and franchises.
- Places, organizations, businesses and events.
- Cultural symbols and abstract concepts.
- Character categories, archetypes and generic roles.
- Groups or classes of characters rather than individuals.
- Articles describing fictional works rather than their characters.
- Generic species or creature types without an individual identity.

INDIVIDUAL IDENTITY:

The entity must represent a particular fictional individual, not merely a type of person, creature, role or recurring character.

A character does not need a unique personal name if it has an established individual identity.

Traditional characters require special attention:
- Harlequin: VALID (established individual traditional character).
- Pierrot: VALID (established individual traditional character).
- Bond girl: INVALID (generic category).
- Final girl: INVALID (narrative archetype).
- Superhero: INVALID (generic role).
- Disney Princess: INVALID (character category).

Do not automatically exclude traditional characters merely because multiple works portray different versions of them.

VISUAL REPRESENTATION:

For characters originating in literary works, require at least one visual adaptation outside theatre, such as:
- Movies or television.
- Animation or anime.
- Comics or graphic novels.
- Video games.

Exclude literary characters known only through written works or theatrical performances.

The adaptation must provide an identifiable visual depiction suitable for a guessing-game picture.

The adaptation does NOT need to be popular, recent or recognizable.
Popularity and recognizability belong to a separate filter.

Characters originating in cinema, television, animation, anime, comics or video games do not need additional adaptations.

MYTHOLOGICAL CHARACTERS:

Individual mythological gods, heroes, monsters and creatures are VALID even when they originate in ancient mythology.

However, generic mythological creature types, abstract concepts and symbolic representations are INVALID unless they represent a specific established individual.

EVIDENCE:

Use the supplied Wikidata description and entity types as evidence.

Do not assume an entity is fictional merely because its name sounds like a character.

If the available information is insufficient to establish that the article describes an individual fictional character, classify it as invalid.

Ignore popularity, age, recognition and modern relevance.

A character can be obscure, ancient or culturally unfamiliar and still be valid.

Reasons must be concise, ideally one sentence and no more than 20 words.
Do not provide lengthy explanations.
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
                    "\\no_think\n"
                    "Classify every candidate:\n"
                    + json.dumps(candidates, ensure_ascii=False)
                ),
            },
        ],
        format=ClassificationResponse.model_json_schema(),
        options=OLLAMA_OPTIONS,
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

    missing = [
        c
        for c in characters
        if normalize_name(get_name(c)) not in {normalize_name(name) for name in results}
    ]

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
                "content": ("\\no_think\n" + json.dumps(candidate, ensure_ascii=False)),
            },
        ],
        format=EntityResult.model_json_schema(),
        options=OLLAMA_OPTIONS,
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
