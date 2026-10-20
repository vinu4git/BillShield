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
ROUNDING = Decimal("1.00")  # bills round to the rupee without printing a rounding line
MISREAD = Decimal("5.00")   # a gap bigger than this is far more likely our misreading than their mistake
# Set by the government or regulator, not the company's plan. Backs up the model, which sometimes misses them.
STATUTORY = re.compile(r"\b(?:surcharge|fppca|fppa|fpppa|fuel|duty|cess|gst|cgst|sgst|igst|tax|true ?up|"
                       r"regulatory|round(?:ing|ed)? ?off)\b")


def money(value) -> Decimal:
    try:
        return Decimal(str(value).replace(",", "").replace("₹", "").strip()).quantize(CENT)
    except (InvalidOperation, ValueError):
        return ZERO


def fmt(d: Decimal) -> str:
    return f"{'−' if d < 0 else ''}₹{abs(d):,.2f}"


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
    """Turn the model's reading into rows shaped like line_items. Amounts are signed: credits are negative."""
    out = []
    for l in lines:
        description = mask(str(l.get("description", "")))
        kind = l.get("kind") if l.get("kind") in KINDS else "charge"
        try:
            quantity = Decimal(str(l.get("quantity") or 1))
        except InvalidOperation:
            quantity = Decimal(1)
        amount = money(l.get("amount"))
        if kind == "discount" and amount > 0:
            amount = -amount  # "Discount 50.00" takes 50 off
        out.append({
            "description": description,
            "charge_key": charge_key(description),
            "kind": kind,
            "quantity": quantity,
            "amount": amount,
        })
    return out


def computed_total(lines: list[dict]) -> Decimal:
    return sum((money(l["amount"]) for l in lines if l["kind"] not in SUMS), ZERO)


def printed_total_index(lines: list[dict]) -> int | None:
    """The printed total: the last 'total' line, else the last 'subtotal'."""
    for kind in ("total", "subtotal"):
        found = [i for i, l in enumerate(lines) if l["kind"] == kind]
        if found:
            return found[-1]
    return None


def check_sums(lines: list[dict]) -> None:
    """Check every printed subtotal and total against the lines above it.

    A printed sum is right if it is within a rupee of any of: the lines since the previous sum
    (a section), the previous printed sum plus those lines (a running total), or every line so far.
    """
    section, so_far, previous = ZERO, ZERO, None
    for l in lines:
        amount = money(l["amount"])
        if l["kind"] not in SUMS:
            section += amount
            so_far += amount
            continue
        accepted = {section, so_far} | ({previous + section} if previous is not None else set())
        if all(abs(amount - a) >= ROUNDING for a in accepted):
            expected = so_far if l["kind"] == "total" else section
            l.update(is_flagged=True, flag_reason="total_mismatch", expected_amount=expected, difference=amount - expected)
        section, previous = ZERO, amount


def promised_price(l: dict, m: dict) -> Decimal | None:
    """What the promise says this line should cost. A rate is multiplied here, never by the model."""
    if m.get("promised_rate") is not None:
        return (money(m["promised_rate"]) * Decimal(str(l["quantity"]))).quantize(CENT)
    if m.get("promised_amount") is not None:
        return money(m["promised_amount"])
    return None


def flag(lines: list[dict], matches: list[dict]) -> list[dict]:
    """Set is_flagged, flag_reason, expected_amount, difference and plain_words on each line.

    YOUR CALL: a charge the promise doesn't cover is *unexplained*, not *wrong*.
    Never flagged as unexplained: ₹0 lines, credits, taxes, statutory charges (by the model's reading
    or by name, see STATUTORY) and carried-over balances, unless the model reads the promise as
    ruling them out.
    """
    by_index = {m.get("index"): m for m in matches}
    for i, l in enumerate(lines):
        m = by_index.get(i, {})
        l.update(plain_words=m.get("plain"), is_flagged=False, flag_reason=None, expected_amount=None, difference=None)
        amount = money(l["amount"])
        if l["kind"] in SUMS or amount <= 0:
            continue
        status = m.get("status", "covered")
        expected = promised_price(l, m)
        if expected is not None and expected != amount:
            l.update(is_flagged=True, flag_reason="promise_mismatch", expected_amount=expected, difference=amount - expected)
        elif status == "contradicts" or (status == "not_covered" and l["kind"] != "tax"
                                         and not STATUTORY.search(l["description"].lower())):
            l.update(is_flagged=True, flag_reason="unexplained")
    check_sums(lines)
    return lines


def reading_doubts(lines: list[dict]) -> list[str]:
    """Signs we misread the bill. Any of these and the user checks the reading before anything is disputed."""
    doubts = []
    for l in lines:
        if l.get("flag_reason") == "total_mismatch" and abs(money(l["difference"])) > MISREAD:
            doubts.append(f"'{l['description']}' is printed as {fmt(money(l['amount']))} but the lines we read "
                          f"add up to {fmt(money(l['expected_amount']))}.")
    charges = sum((money(l["amount"]) for l in lines if l["kind"] == "charge" and money(l["amount"]) > 0), ZERO)
    taxes = sum((money(l["amount"]) for l in lines if l["kind"] == "tax"), ZERO)
    if taxes > charges:
        doubts.append(f"The taxes we read ({fmt(taxes)}) are more than all the charges together ({fmt(charges)}).")
    return doubts


def fact(l: dict) -> str | None:
    """The code-written sentence for a flagged line. Works on fresh or stored rows."""
    amount, desc = money(l["amount"]), l["description"]
    expected = money(l["expected_amount"]) if l.get("expected_amount") is not None else None
    diff = abs(money(l["difference"])) if l.get("difference") is not None else None
    if l.get("flag_reason") == "total_mismatch":
        return f"'{desc}' is printed as {fmt(amount)} but the lines add up to {fmt(expected)}, a difference of {fmt(diff)}."
    if l.get("flag_reason") == "promise_mismatch":
        quantity = Decimal(str(l.get("quantity") or 1)).normalize()
        promised = f"the promised rate for {quantity:f} units comes to {fmt(expected)}" if quantity != 1 \
            else f"was promised at {fmt(expected)}"
        return f"'{desc}' is charged at {fmt(amount)} but {promised}, a difference of {fmt(diff)}."
    if l.get("flag_reason") == "unexplained":
        return f"'{desc}' for {fmt(amount)} is not part of what was promised and is unexplained."
    return None


def all_clear(lines: list[dict]) -> str:
    items = [money(l["amount"]) for l in lines if l["kind"] not in SUMS]
    added = sum((a for a in items if a > 0), ZERO)
    taken = -sum((a for a in items if a < 0), ZERO)
    return (f"Every line checks out. Charges {fmt(added)} minus credits {fmt(taken)} "
            f"equals {fmt(added - taken)}, which matches the bill and the promise.")


def fallback_message(facts: list[str]) -> str:
    return ("Hello,\n\nI'd like to dispute the following on my bill:\n\n" + "\n".join(f"- {f}" for f in facts)
            + "\n\nPlease correct this and confirm.\n\nThank you.")


def _numbers(text: str) -> set[Decimal]:
    return {money(n) for n in NUMBER.findall(text)}


def unknown_numbers(message: str, facts_text: str, promise: str = "") -> set[Decimal]:
    """NO-GO guard: the amounts in a message that code didn't compute and the user didn't write."""
    return _numbers(message) - _numbers(facts_text) - _numbers(promise)


def is_bare_number(promise: str) -> bool:
    """'2000' or '₹499/-' says a price but not what it was for, so nothing can be matched against it."""
    return re.fullmatch(r"\s*(?:rs\.?|inr|₹)?\s*[\d,]+(?:\.\d+)?\s*(?:/-)?\s*", promise, re.I) is not None


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
