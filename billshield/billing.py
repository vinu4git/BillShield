"""Code decides: totals, differences from the promise, masking, word counts.
Same input, same answer, every time. Every rupee in a message comes from here."""
import difflib
import re
from datetime import date
from decimal import Decimal, InvalidOperation

CENT = Decimal("0.01")
ZERO = Decimal("0.00")
NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")
KINDS = {"charge", "tax", "discount", "subtotal", "total"}
SUMS = {"subtotal", "total"}


def money(value) -> Decimal:
    try:
        return Decimal(str(value).replace(",", "").replace("₹", "").strip()).quantize(CENT)
    except (InvalidOperation, ValueError):
        return ZERO


def fmt(d: Decimal) -> str:
    return f"₹{d:,.2f}"


def parse_date(value) -> str | None:
    try:
        return date.fromisoformat(str(value)).isoformat()
    except ValueError:
        return None


def charge_key(description: str) -> str:
    """Same charge, same key, across bills: 'VAS Pack ₹199 (Oct)' -> 'vas pack oct'."""
    text = re.sub(r"\[\w+\]", " ", description.lower())  # drop [number]/[email] masks
    return " ".join(re.sub(r"[^a-z ]", " ", text).split())


def normalise(lines: list[dict]) -> list[dict]:
    """Turn the model's reading into rows shaped like line_items."""
    out = []
    for l in lines:
        description = mask(str(l.get("description", "")))
        kind = l.get("kind") if l.get("kind") in KINDS else "charge"
        try:
            quantity = Decimal(str(l.get("quantity") or 1))
        except InvalidOperation:
            quantity = Decimal(1)
        out.append({
            "description": description,
            "charge_key": charge_key(description),
            "kind": kind,
            "quantity": quantity,
            "amount": money(l.get("amount")),
        })
    return out


def computed_total(lines: list[dict]) -> Decimal:
    return sum((-l["amount"] if l["kind"] == "discount" else l["amount"]
                for l in lines if l["kind"] not in SUMS), ZERO)


def printed_total_index(lines: list[dict]) -> int | None:
    """The printed total: the last 'total' line, else the last 'subtotal'."""
    for kind in ("total", "subtotal"):
        found = [i for i, l in enumerate(lines) if l["kind"] == kind]
        if found:
            return found[-1]
    return None


def flag(lines: list[dict], matches: list[dict]) -> list[dict]:
    """Set is_flagged, flag_reason, expected_amount, difference and plain_words on each line.

    YOUR CALL: a charge the promise doesn't cover is *unexplained*, not *wrong*.
    """
    by_index = {m.get("index"): m for m in matches}
    for i, l in enumerate(lines):
        m = by_index.get(i, {})
        l.update(plain_words=m.get("plain"), is_flagged=False, flag_reason=None, expected_amount=None, difference=None)
        if l["kind"] in SUMS or l["kind"] == "discount":
            continue
        promised = m.get("promised_amount")
        if promised is not None and money(promised) != l["amount"]:
            expected = money(promised)
            l.update(is_flagged=True, flag_reason="promise_mismatch",
                     expected_amount=expected, difference=l["amount"] - expected)
        elif m.get("promised") is False:
            l.update(is_flagged=True, flag_reason="unexplained")

    i = printed_total_index(lines)
    if i is not None:
        total = computed_total(lines)
        if lines[i]["amount"] != total:
            lines[i].update(is_flagged=True, flag_reason="total_mismatch",
                            expected_amount=total, difference=lines[i]["amount"] - total)
    return lines


def fact(l: dict) -> str | None:
    """The code-written sentence for a flagged line. Works on fresh or stored rows."""
    amount, desc = money(l["amount"]), l["description"]
    expected = money(l["expected_amount"]) if l.get("expected_amount") is not None else None
    diff = abs(money(l["difference"])) if l.get("difference") is not None else None
    if l.get("flag_reason") == "total_mismatch":
        return f"The bill total is {fmt(amount)} but the lines add up to {fmt(expected)}, a difference of {fmt(diff)}."
    if l.get("flag_reason") == "promise_mismatch":
        return f"'{desc}' is charged at {fmt(amount)} but was promised at {fmt(expected)}, a difference of {fmt(diff)}."
    if l.get("flag_reason") == "unexplained":
        return f"'{desc}' for {fmt(amount)} is not part of what was promised and is unexplained."
    return None


def all_clear(lines: list[dict]) -> str:
    items = [l for l in lines if l["kind"] not in SUMS]
    added = sum((money(l["amount"]) for l in items if l["kind"] != "discount"), ZERO)
    taken = sum((money(l["amount"]) for l in items if l["kind"] == "discount"), ZERO)
    return (f"Every line checks out. Charges {fmt(added)} minus credits {fmt(taken)} "
            f"equals {fmt(added - taken)}, which matches the bill and the promise.")


def fallback_message(facts: list[str]) -> str:
    return ("Hello,\n\nI'd like to dispute the following on my bill:\n\n" + "\n".join(f"- {f}" for f in facts)
            + "\n\nPlease correct this and confirm.\n\nThank you.")


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
