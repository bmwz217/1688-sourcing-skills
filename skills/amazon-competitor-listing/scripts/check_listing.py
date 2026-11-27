#!/usr/bin/env python3
"""Check final English listing text. See references/validation.md for the schema."""

import argparse
from collections import Counter
import json
from pathlib import Path
import re
import sys


TITLE_EXEMPT_WORDS = frozenset(
    'a an the as at by for from in into of on over per to under upon via with within without '
    'across after against along around before behind below beneath beside between beyond '
    'concerning despite down during except inside near off onto outside past since through '
    'throughout toward until up and but if nor or so than that though unless when while yet'.split()
)


def measure(text):
    return {"characters": len(text), "utf8_bytes": len(text.encode("utf-8"))}


def tokenize(text):
    return re.findall(r"[a-z0-9]+", text.lower())


def analyze(data, *, title_only=False, profile="bags"):
    errors = []
    warnings = []
    counts = {}
    foreground = []

    def check_length(label, rule, *, unchanged=False):
        if rule is None:
            return
        try:
            maximum = rule["maximum"]
            unit = rule["unit"]
            exclusive = rule.get("exclusive", False)
            if (type(maximum) is not int or maximum < 1
                    or unit not in ("characters", "utf8_bytes")
                    or type(exclusive) is not bool):
                raise ValueError
        except (AttributeError, KeyError, TypeError, ValueError):
            errors.append(f"{label}: invalid length limit")
            return
        minimum = (maximum * 4 + 4) // 5
        length = counts[label][unit]
        if length < minimum:
            message = f"{label}: below {minimum} {unit}"
            if unchanged:
                warnings.append(message + " (unchanged field in a title-only edit)")
            else:
                errors.append(message)
        if length > maximum or (exclusive and length == maximum):
            boundary = "must be fewer than" if exclusive else "exceeds"
            errors.append(f"{label}: {boundary} {maximum} {unit}")

    def check_text(value, label):
        if not isinstance(value, str) or not value.strip():
            errors.append(f"{label}: expected nonempty text")
            return None
        counts[label] = measure(value)
        if value != value.strip():
            errors.append(f"{label}: remove leading/trailing whitespace")
        return value

    if not isinstance(data, dict):
        return {"valid": False, "errors": ["JSON root must be an object"],
                "warnings": [], "counts": {}}
    if "titles" in data:
        errors.append("titles: use one shared parent title in the title field")

    limits = data.get("length_limits", {})
    if not isinstance(limits, dict):
        errors.append("length_limits: expected an object")
        limits = {}
    defaults = {
        "title": {"maximum": 75, "unit": "characters"},
        "highlight": {"maximum": 125, "unit": "characters"},
        "search_terms": {"maximum": 250, "unit": "utf8_bytes", "exclusive": True},
    }

    def rule_for(field):
        return limits.get(field, defaults.get(field))

    title = check_text(data.get("title"), "title")
    if title is not None:
        check_length("title", rule_for("title"))
        foreground.append(title)
        if re.search(r"\[colou?r\]", title, re.IGNORECASE):
            errors.append("title: remove the color placeholder from the shared parent title")
        if re.search(r"[!$?_{}^¬¦]", title):
            errors.append("title: contains a prohibited Amazon title character")
        repeats = sorted(word for word, count in Counter(tokenize(title)).items()
                         if count > 2 and word not in TITLE_EXEMPT_WORDS)
        if repeats:
            errors.append("title: words used more than twice: " + ", ".join(repeats))

    if profile == "bags" or "highlight" in data:
        highlight = check_text(data.get("highlight"), "highlight")
        if highlight is not None:
            check_length("highlight", rule_for("highlight"), unchanged=title_only)
            foreground.append(highlight)

    description = check_text(data.get("description"), "description")
    if description is not None:
        check_length("description", rule_for("description"), unchanged=title_only)
        foreground.append(description)

    bullets = data.get("bullets")
    if not isinstance(bullets, list):
        errors.append("bullets: expected a list of exactly 5 items")
    else:
        if len(bullets) != 5:
            errors.append("bullets: expected exactly 5 items")
        valid_bullets = []
        for index, bullet in enumerate(bullets, 1):
            label = f"bullet_{index}"
            value = check_text(bullet, label)
            if value is not None:
                check_length(label, rule_for("bullet"), unchanged=title_only)
                valid_bullets.append(value)
                foreground.append(value)
        if len(valid_bullets) == 5:
            counts["bullets_total"] = measure("".join(valid_bullets))
            check_length("bullets_total", rule_for("bullets_total"), unchanged=title_only)

    backend = check_text(data.get("search_terms"), "search_terms")
    duplicates = []
    overlap = []
    if backend is not None:
        size = counts["search_terms"]["utf8_bytes"]
        check_length("search_terms", rule_for("search_terms"), unchanged=title_only)
        if profile == "bags" and size < 235:
            warnings.append("search_terms: below 235-byte bag writing target; do not add irrelevant words")
        if re.fullmatch(r"[a-z0-9]+(?: [a-z0-9]+)*", backend) is None:
            errors.append("search_terms: use lowercase ASCII words/numbers and single spaces only")
        tokens = tokenize(backend)
        duplicates = sorted(word for word, count in Counter(tokens).items() if count > 1)
        if duplicates:
            errors.append("search_terms: repeated words: " + ", ".join(duplicates))
        overlap = sorted(set(tokens) & set(tokenize(" ".join(foreground))))
        if overlap:
            errors.append("search_terms: words already in foreground: " + ", ".join(overlap))
        stopwords = sorted(set(tokens) & {"for", "with", "and"})
        if stopwords:
            errors.append("search_terms: remove filler words: " + ", ".join(stopwords))
        probable_asins = sorted({word for word in tokens if re.fullmatch(r"b0[a-z0-9]{8}", word)})
        if probable_asins:
            errors.append("search_terms: remove ASINs: " + ", ".join(probable_asins))

    return {"valid": not errors, "errors": errors, "warnings": warnings,
            "counts": counts, "backend_duplicates": duplicates,
            "foreground_overlap": overlap}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="UTF-8 JSON containing final English copy")
    parser.add_argument("--profile", choices=("bags", "home-decor"), default="bags",
                        help="validation profile, defaults to the existing bag workflow")
    parser.add_argument("--title-only", action="store_true",
                        help="warn on a short unchanged highlight in a title-only edit")
    args = parser.parse_args()
    try:
        data = json.loads(args.input.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, ValueError) as exc:
        print(json.dumps({"valid": False, "errors": [str(exc)]}, ensure_ascii=True))
        return 2
    result = analyze(data, title_only=args.title_only, profile=args.profile)
    print(json.dumps(result, ensure_ascii=True, indent=2))
    return 0 if result["valid"] else 1


if __name__ == "__main__":
    sys.exit(main())
