"""Code decides: totals, differences from the promise, masking, word counts.
Same input, same answer, every time. Every rupee in a message comes from here."""
import difflib
import re
from decimal import Decimal, InvalidOperation

CENT = Decimal("0.01")
NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")


def money(value) -> Decimal:
    try:
        return Decimal(str(value).replace(",", "").replace("₹", "").strip()).quantize(CENT)
    except (InvalidOperation, ValueError):
        return Decimal("0.00")


def fmt(d: Decimal) -> str:
    return f"₹{d:,.2f}"


def normalise(lines: list[dict]) -> list[dict]:
    """Turn the model's reading into rows shaped like line_items."""
    out = []
    for l in lines:
        kind = l.get("kind", "charge")
        out.append({
            "description": mask(str(l.get("description", ""))),
            "quantity": int(l.get("quantity") or 1),
            "amount": money(l.get("amount")),
            "add_deduct": kind != "discount",
            "computed": kind in ("subtotal", "total"),
        })
    return out


def find_issues(lines: list[dict], matches: list[dict]) -> list[dict]:
    """Return one fact per flagged line: {"index", "fact"}. Facts carry every number the message may use.

    YOUR CALL: a line the promise doesn't cover is flagged as *unexplained*, not *wrong*.
    """
    by_index = {m.get("index"): m for m in matches}
    facts = []

    charges = [l for l in lines if not l["computed"]]
    line_sum = sum((l["amount"] if l["add_deduct"] else -l["amount"] for l in charges), Decimal("0.00"))
    printed_sums = [i for i, l in enumerate(lines) if l["computed"]]
    if printed_sums:
        i = printed_sums[-1]  # the last printed sum on a bill is its total
        printed = lines[i]["amount"]
        if printed != line_sum:
            facts.append({"index": i, "fact": f"The bill total is {fmt(printed)} but the lines add up to "
                                              f"{fmt(line_sum)}, a difference of {fmt(abs(printed - line_sum))}."})

    for i, l in enumerate(lines):
        if l["computed"] or not l["add_deduct"]:
            continue
        m = by_index.get(i, {})
        promised = m.get("promised_amount")
        if promised is not None and money(promised) != l["amount"]:
            p = money(promised)
            facts.append({"index": i, "fact": f"'{l['description']}' is charged at {fmt(l['amount'])} but was "
                                              f"promised at {fmt(p)}, a difference of {fmt(abs(l['amount'] - p))}."})
        elif not m.get("promised", True):
            facts.append({"index": i, "fact": f"'{l['description']}' for {fmt(l['amount'])} is not part of "
                                              f"what was promised and is unexplained."})
    return facts


def all_clear(lines: list[dict]) -> str:
    charges = [l for l in lines if not l["computed"]]
    added = sum((l["amount"] for l in charges if l["add_deduct"]), Decimal("0.00"))
    taken = sum((l["amount"] for l in charges if not l["add_deduct"]), Decimal("0.00"))
    return (f"Every line checks out. Charges {fmt(added)} minus credits {fmt(taken)} "
            f"equals {fmt(added - taken)}, which matches the bill and the promise.")


def _numbers(text: str) -> set[Decimal]:
    return {money(n) for n in NUMBER.findall(text)}


def only_known_numbers(message: str, facts_text: str) -> bool:
    """NO-GO guard: the model may not state an amount code didn't compute."""
    return _numbers(message) <= _numbers(facts_text)


def mask(text: str) -> str:
    """Store the least: no account numbers, phone numbers or emails."""
    text = re.sub(r"[\w.+-]+@[\w-]+\.[\w.]+", "[email]", text)
    return re.sub(r"\+?\d(?:[\s-]?\d){7,}", "[number]", text)


def words_changed(before: str, after: str) -> int:
    a, b = before.split(), after.split()
    changed = 0
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(a=a, b=b).get_opcodes():
        if op != "equal":
            changed += max(i2 - i1, j2 - j1)
    return changed
