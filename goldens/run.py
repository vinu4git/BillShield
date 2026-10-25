"""Golden pairs and unit tests: bill + promise -> flags, facts and message.

pairs.json holds golden pairs: real bills from real people. unit_tests.json holds invented bills that each
pin one rule of billing.py. Both run the same way.

    python goldens/run.py          # code only: the model's reading and matching are recorded in pairs.json
    python goldens/run.py --live   # the matching and the message go through the real model (needs GROQ_API_KEY)

The reading of each bill is always the recorded one, so a run checks the code's maths and flags, not the vision
model's eyesight. Each pair ends in one outcome, the same one the app reaches:
follow_up (bare-number promise), check_reading (reading doubts, no message), all_clear or dispute.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from billshield import billing  # noqa: E402

SUITES = {"golden pairs": Path(__file__).with_name("pairs.json"),
          "unit tests": Path(__file__).with_name("unit_tests.json")}


def _str(value):
    return None if value is None else f"{billing.money(value):f}"


def run(pair: dict, live: bool) -> dict:
    """What the app does with this bill and promise, without the database."""
    promise = pair["promise"]
    if billing.is_bare_number(promise):
        return {"outcome": "follow_up"}

    lines = billing.normalise(pair["reading"])
    if live:
        from billshield import llm
        matches = llm.match_promise(lines, promise)
    else:
        matches = pair["matches"]
    billing.flag(lines, matches)

    got = {
        "flags": [{"description": l["description"], "flag_reason": l["flag_reason"],
                   "expected_amount": _str(l["expected_amount"]), "difference": _str(l["difference"])}
                  for l in lines if l["is_flagged"]],
        "facts": [f for l in lines if (f := billing.fact(l))],
    }
    if doubts := billing.reading_doubts(lines):
        return {**got, "outcome": "check_reading", "doubts": doubts}
    if not got["facts"]:
        return {**got, "outcome": "all_clear", "message": billing.all_clear(lines)}
    return {**got, "outcome": "dispute", "message": draft(got["facts"], promise) if live else None}


def draft(facts: list[str], promise: str) -> str:
    """The same no-go guard as api/index.py: one retry naming the bad number, then the facts themselves."""
    from billshield import llm
    known = " ".join(facts)
    message = llm.write_message(facts, promise)
    if message and (bad := billing.unknown_numbers(message, known, promise)):
        message = llm.write_message(facts, promise, rejected=message,
                                    bad_numbers=", ".join(f"{n:f}" for n in sorted(bad)))
    if not message or billing.unknown_numbers(message, known, promise):
        message = billing.fallback_message(facts)
    return billing.mask(message)


def compare(expect: dict, got: dict) -> list[str]:
    """Only the keys the golden names are checked."""
    return [f"{key}: expected {expect[key]!r}, got {got.get(key)!r}" for key in expect if got.get(key) != expect[key]]


def main() -> int:
    live = "--live" in sys.argv
    if live:
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env")

    failed = 0
    for suite, path in SUITES.items():
        pairs = json.loads(path.read_text())
        passed = 0
        print(f"{suite} ({path.name})")
        for n, pair in enumerate(pairs, 1):
            got = run(pair, live)
            problems = compare(pair["expect"], got)
            passed += not problems
            print(f"  {n}. {pair['name']}: {'pass' if not problems else 'FAIL'} ({got['outcome']})")
            for p in problems:
                print(f"       {p}")
            if problems and pair.get("known_failure"):
                print(f"       known failure: {pair['known_failure']}")
            if live and got.get("message"):
                print("       message: " + got["message"].replace("\n", "\n                "))
        print(f"  {passed} / {len(pairs)} pass\n")
        failed += len(pairs) - passed
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
