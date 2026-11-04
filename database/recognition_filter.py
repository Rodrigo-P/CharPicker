import json
import argparse
from pathlib import Path
from typing import Literal

from ollama import chat
from pydantic import BaseModel

MODEL = "qwen3-14b-q5"

INPUT_FILE = "database/characters_valid.json"
CACHE_FILE = "database/recognition_cache.json"

OUTPUT_KEEP = "database/characters_filtered.json"
OUTPUT_EXCLUDE = "database/characters_excluded.json"
OUTPUT_UNCERTAIN = "database/characters_uncertain.json"

BATCH_SIZE = 10


OLLAMA_OPTIONS = {"temperature": 0, "num_ctx": 8192, "num_predict": 8192}


class RecognitionResult(BaseModel):
    name: str
    decision: Literal["keep", "exclude", "uncertain"]
    modern_pop_culture_presence: Literal["high", "medium", "low", "unknown"]
    reason: str


class ClassificationResponse(BaseModel):
    results: list[RecognitionResult]


SYSTEM_PROMPT = """
Select fictional characters for an Akinator-style guessing game aimed at Brazilian players aged 18–34.

TARGET AUDIENCE:

Assume players understand English and are reasonably internet-savvy.
They are familiar with international entertainment, gaming, anime,
movies, television and mainstream online culture.

Do not restrict recognition to Brazilian mainstream media.
International characters can qualify if they have meaningful
recognition among young, English-speaking and internet-active audiences.

CORE QUESTION:

Would a reasonably knowledgeable but non-specialist young Brazilian
recognize this SPECIFIC CHARACTER through yes/no questions?

Evaluate recognition of the individual character, not just their
original work, franchise or cultural significance.

KEEP:
- Characters from popular movies, TV, animation, anime, comics and video games.
- Recognizable supporting characters.
- Characters with meaningful international or Brazilian mainstream recognition.
- Iconic literary, folkloric and mythological characters with enduring recognition.
- Older characters with strong cross-generational recognition.
- Characters with widespread recognition through internet culture.

EXCLUDE:
- Obscure characters primarily known through classical literature, theatre or mythology.
- Characters known mainly to dedicated fans or niche communities.
- Traditional theatrical stock characters without broader recognition.
- Characters primarily remembered by older generations.
- Generic character categories, archetypes and literary roles.
- Internet personalities and satirical fictional characters limited to niche communities or specific foreign political contexts.
- Fictional political figures whose recognition depends on familiarity with another country's political culture.

INDIVIDUAL CHARACTER RECOGNITION:

Evaluate every character individually.

A famous franchise does not make all its characters recognizable.
A popular or critically acclaimed series does not automatically
make all its characters mainstream.

Be particularly careful with:
- Individual incarnations of characters with multiple versions.
- Supporting characters from television series.
- Characters from older franchises without enduring recognition.
- Characters whose recognition is restricted to people familiar
  with specific episodes, storylines or adaptations.

A character should ideally be recognizable through distinctive
appearance, identity or characteristics, beyond dedicated fans.

CULTURAL AND GENERATIONAL RECOGNITION:

Consider Brazilian familiarity, international entertainment
and the target audience's online habits.

Characters can qualify through:
- Popular English-language entertainment.
- Internationally successful movies and television.
- Gaming and anime culture.
- Widely circulated memes, YouTube content and viral references.
- Enduring cross-generational cultural recognition.
- Popular adaptations of older literary or mythological characters.

Older characters do not necessarily need recent appearances.
However, historical importance, literary fame, cult status,
multiple adaptations or international fame alone are insufficient.

Folklore and mythology do not automatically imply recognition.
A character's importance in its country of origin does not
establish familiarity among young Brazilians.

Niche internet references, specialized fandoms and obscure
cultural references are insufficient.

EXAMPLES:

KEEP:
Dracula, Sherlock Holmes, Batman, Aladdin, Sweeney Todd,
Amaterasu, Bluto, Merlin, Indiana Jones,
Alice (Alice's Adventures in Wonderland), Mowgli.

EXCLUDE:
Billy Budd, Black Beauty, Blanche DuBois, Bob Cratchit,
Lady Macbeth, Banquo, Lugh, Medea, Harlequin, Brighella,
Humbaba, Don Juan, Fantômas, Fonzie, Jean Valjean,
Scarlett O'Hara.

ADDITIONAL EXCLUDE EXAMPLES:
- Seventh Doctor: Individual incarnation primarily recognized by Doctor Who fans.
- Second Doctor: Individual incarnation primarily recognized by Doctor Who fans.
- Befana: Italian folkloric figure without sufficient mainstream recognition in Brazil.
- Roxie Hart: Recognition concentrated among Chicago and musical theatre audiences.
- Robot (Lost in Space): Older television character with limited contemporary relevance.
- Rust Cohle: Recognition concentrated among True Detective viewers.
- Baron Munchausen: Historical and literary fame does not establish contemporary recognition.
- Rosie the Riveter: Primarily an American WWII-era cultural icon.
- R.O.B.: Too niche for the target audience.

DECISION STANDARD:

Be conservative, but not excessively restrictive.

When recognition is uncertain, consider:
- Cross-generational cultural familiarity.
- Popular adaptations.
- International mainstream exposure.
- Widespread online recognition.
- The character's distinctive and established identity.

Do not exclude a character simply because its original work
is old or its recent exposure is limited.

Do not assume that franchise popularity, historical importance
or niche online popularity guarantees recognition.

Exclude when there is insufficient reason to expect meaningful
recognition among young Brazilian adults.

Avoid both speculative inclusions and excessively restrictive exclusions.

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


def classify_batch(characters):

    names = [get_name(c) for c in characters]

    response = chat(
        model=MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    "Classify every character below. "
                    "Return exactly one result per character.\n"
                    + json.dumps(names, ensure_ascii=False)
                    + "\n\\no_think"
                ),
            },
        ],
        format=ClassificationResponse.model_json_schema(),
        options=OLLAMA_OPTIONS,
    )
    print("Done reason:", response.done_reason)
    print("Prompt tokens:", response.prompt_eval_count)
    print("Generated tokens:", response.eval_count)

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
                "content": f"Classify this character: {name}\n\\no_think",
            },
        ],
        format=RecognitionResult.model_json_schema(),
        options=OLLAMA_OPTIONS,
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
