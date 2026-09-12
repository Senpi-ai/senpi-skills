#!/usr/bin/env python3
"""Second person is for the trader's own book. For someone else's wallet the desk speaks in the third
person — English second person and third person plural share every verb form, so the switch is a
pronoun rewrite. The product persona ("your quant") stays addressed to the reader, who hired it."""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import re

KEEP = ("YOUR QUANT — LIVE", "Your quant", "your quant", "hire my quant", "your own book", "under **your** name", "under your name", "your name",
        "you could run", "you can run", "you run under", "by the time you see them", "you see them", "Say *hire", "you hired", "you want", "Want me", "Want the",
        "Want their", "Want to", "your say-so", "on your side", "you already hold", "run for you", "run this for you", "tell you", "your agents")
# object case first: "paid you", "sits with you", "against you" → them. English second person hides the case; third person does not.
OBJECT = re.compile(r"\b(against|with|behind|ahead of|beside|from|of|by|to|at|on|for|between|around|near|over|under|toward|towards|without|into|onto|off|past|"
                    r"paid|pays|paying|pay|cost|costs|costing|tells|tell|telling|told|shows|show|showing|showed|lets|let|letting|gives|give|gave|giving|"
                    r"hurt|hurts|hurting|keeps|keep|kept|keeping|puts|put|putting|leaves|leave|left|leaving|makes|make|made|making|gets|get|got|getting|"
                    r"beat|beats|beating|saves|save|saved|saving|earns|earn|earned|earning|owes|owe|owed|protects|protect|protected|protecting|"
                    r"stops|stop|stopped|stopping|warns|warn|warned|reminds|remind|reminded) you\b")
PAIRS = (
    (r"\bYou're\b", "They're"), (r"\byou're\b", "they're"), (r"\bYou've\b", "They've"), (r"\byou've\b", "they've"),
    (r"\bYou'd\b", "They'd"), (r"\byou'd\b", "they'd"), (r"\bYou'll\b", "They'll"), (r"\byou'll\b", "they'll"),
    (r"\bYourself\b", "Themselves"), (r"\byourself\b", "themselves"), (r"\bYours\b", "Theirs"), (r"\byours\b", "theirs"),
    (r"\bYour\b", "Their"), (r"\byour\b", "their"), (r"\bYou\b", "They"), (r"\byou\b", "they"),
)


def third_person(md, short_addr=None):
    keep = {}
    for i, k in enumerate(KEEP):
        tok = f"\x00{i}\x00"
        keep[tok] = k
        md = md.replace(k, tok)
    md = OBJECT.sub(r"\1 them", md)
    for pat, rep in PAIRS:
        md = re.sub(pat, rep, md)
    for tok, k in keep.items():
        md = md.replace(tok, k)
    if short_addr:
        md = md.replace(f"# Their desk — `{short_addr}`", f"# The desk for `{short_addr}`")
    md = md.replace("| Coin | They | Cohort | Read |", "| Coin | Trader | Cohort | Read |").replace("| Coin | They | Smart money | Read |", "| Coin | Trader | Smart money | Read |")
    md = re.sub(r"(?m)^They: ", "This trader: ", md)
    md = md.replace("They vs smart money", f"`{short_addr}` vs smart money" if short_addr else "This trader vs smart money")
    md = md.replace("What they've been doing", "What they've been doing").replace("The market they're trading in", "The market they're trading in")
    return md
