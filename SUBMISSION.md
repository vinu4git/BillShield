BILLSHIELD SUBMISSION

Name / track: Vineeth Vijayan / Code
Live link: https://billshield-psi.vercel.app/
Public repo: https://github.com/vinu4git/BillShield

MOVE 1, BY HAND
it was a bill from Anand. My classmate. He had booked an indigo flight. he saw the fare and but then airline added so many additional charges. Also there was a discrepency of 200.
![Bill](image.png)
![My notes](image-1.png)
The message I wrote / did they send it / what they changed: One month back, I purchased this ticket from cleartrip and the total does not match the break up. Kindly check the same and share the details
Anand wanted it to be changed to "check the bill and let me know if there are any discrepancies."
Golden pairs (link): https://github.com/vinu4git/BillShield/tree/main/goldens (8 pairs of bill + promise → flags, facts and message; run `python goldens/run.py`, or `--live` to run them through the real model)

| # | Pair | Input (promise → bill) | Golden output | Result |
|---|---|---|---|---|
| 1 | cleartrip-flight | "Airfare ₹8,010, taxes and airport fees extra" → Cleartrip fare summary, total ₹9,907 | Total checks out. Fuel Charge ₹800 flagged **unexplained** (airline-set, not government) | **FAIL**: code treats "fuel" as a government charge and never flags it |
| 2 | airtel-addon-pack | "₹499/month, no add-on packs, GST extra" → plan ₹499 + VAS Pack ₹199 + GST | VAS Pack ₹199 flagged **unexplained**; phone number masked as [number] | pass |
| 3 | broadband-price-hike | "₹399/month for 100 Mbps" → Broadband ₹449 + GST | **Wrong**: charged ₹449.00, promised ₹399.00, difference ₹50.00 | pass |
| 4 | electricity-rate-times-units | "₹6.50 per unit" → 200 units charged ₹1,400 | **Wrong**: 200 × ₹6.50 = ₹1,300.00 (code multiplies), difference ₹100.00 | pass |
| 5 | ott-all-clear-with-discount | "₹599/month, ₹50 loyalty discount, GST extra" → ₹599 − ₹50 + GST = ₹647.82 | No flags. All-clear message, not a complaint | pass |
| 6 | total-off-by-two | "₹499/month, GST extra" → ₹499 + ₹89.82, printed total ₹590.82 | **Wrong**: total printed ₹590.82, lines add up to ₹588.82, difference ₹2.00 | pass |
| 7 | cleartrip-misread | Same bill, but the reading missed the ₹354 User Development Fee | Lines miss the printed total by ₹354 (more than ₹5): ask the user to check the reading, no message drafted | pass |
| 8 | bare-number-promise | "8010" | Follow-up: ask what the price was for before reading the bill | pass |

**7 / 8 pass.** The one failure is the real bill from Move 1, and it's the Move 3 failure mode in action.

MOVE 2, NEEDS FIRST
Need list:
1. The bill: a PDF or a photo, under 4 MB.
2. What they were promised, in their own words. Their tone is the voice of the message.
3. Provider (optional): used to track how a provider's promise changes across bills.
4. Confirmation of our reading, but only when we doubt it.
Nothing else. No account number or address: they're masked if they appear.

The follow-up from the replay, and the interface it picked:
- A promise that's only a number ("499", "₹499/-") names a price but not what it was for, so it can't be matched to a line. Interface: an inline hint under the promise box ("Tell us what that price was for, in a sentence…"). The page refocuses the box and doesn't submit.
- If the lines we read miss a printed total by more than ₹5, or the taxes add up to more than all the charges, we don't draft a message. Interface: a check-the-reading screen. The user compares our amounts with their bill and confirms, or uploads a clearer photo. Gaps under ₹1 count as the bill's rounding.

MOVE 3, THE LINE
![System Design](image-2.png)
Code decides (billshield/billing.py):
- Every total, and every difference from the promise. Money is Decimal, never float.
- Which lines get flagged, and why: total_mismatch, promise_mismatch or unexplained.
- Rate × quantity. The model copies the promised rate; code multiplies it.
- Masking emails and long numbers before saving, plus charge_key, which matches the same charge across bills.
- Words changed between the draft and what was sent.
- The no-go guard: every number in a drafted message must appear in the code-written facts or the user's promise. If one doesn't, the model gets one retry naming the bad number. If that fails too, the plain code-written facts go out instead.

AI decides (billshield/llm.py):
- Reading the bill into lines. It copies; it never adds.
- Matching each line to the promise (covered / statutory / carried_over / contradicts / not_covered), and putting each line in plain words.
- The message, in the user's tone.

My call on wrong vs unexplained, and its failure mode:
"Wrong" is something code can prove: the line's price differs from the promised price (promise_mismatch), or a printed total differs from the lines above it (total_mismatch). "Unexplained" is a charge the promise doesn't cover. We don't call it wrong; we ask the company to explain it. These are never flagged as unexplained: ₹0 lines, credits, taxes, statutory charges (by the model's reading, or by name: GST, cess, duty, FPPCA, surcharge…) and carried-over balances.
Failure mode: false negatives hidden as "statutory". A company add-on with a government-sounding name (e.g. "Regulatory Fee", "Fuel surcharge" on a plan that included it) gets waved through. And when the promise is vague, the model can mark a real extra as "covered".

MOVE 4, THE DATA

![Table Design](image-3.png)
users → bills → line_items (facts, written once, never versioned)
bills → messages (a new version on each Revise; is_latest; draft_text vs sent_text; words_changed)
messages → message_line_items → line_items  ← THE LINK: which lines a sent message disputes
Each bill keeps the promise it was checked against, so a provider's promise history comes from the bills themselves (no separate promises table).



MOVE 5, WATCH THEM

Classmate: sent? words changed? Made it simpler

Stranger: sent? words changed? He asked for revision

The surprise: LLM considers each line item that is not mentioned in the promise as unexpected. Also image clarity sometime can jumble up the item and value.
