#!/usr/bin/env python3
"""Add or update one product in data/products.json from an Amazon URL.

For use until the Creators API becomes available (it requires 10 qualifying
sales in the last 30 days). Paste a product URL, fill in the details, and
this writes a correctly-formed entry — no hand-editing JSON, no malformed
files, no duplicate ASINs.

Usage:

    python3 add_product.py https://www.amazon.in/dp/B0F18RKVS4
        …prompts for the rest

    python3 add_product.py B0F18RKVS4 \
        --title "boAt Airdopes 141" \
        --price "₹1,299" \
        --image "https://m.media-amazon.com/images/I/71abc.jpg" \
        --category Electronics \
        --feature "42H playback" --feature "IPX4 water resistant" \
        --featured

Re-running with an existing ASIN updates that product instead of adding a
duplicate. Then run: python3 build.py
"""

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA_FILE = ROOT / "data" / "products.json"

# /dp/ASIN, /gp/product/ASIN, /product/ASIN, ?asin=ASIN, or a bare ASIN.
ASIN_PATTERNS = [
    r"/dp/([A-Z0-9]{10})",
    r"/gp/product/([A-Z0-9]{10})",
    r"/product/([A-Z0-9]{10})",
    r"[?&]asin=([A-Z0-9]{10})",
    r"^([A-Z0-9]{10})$",
]


def extract_asin(text):
    """Pull the ASIN out of an Amazon URL, or accept a bare ASIN."""
    candidate = text.strip()
    for pattern in ASIN_PATTERNS:
        match = re.search(pattern, candidate, re.IGNORECASE)
        if match:
            return match.group(1).upper()
    return None


def ask(prompt, current=""):
    """Prompt, showing any existing value as the default.

    Falls straight through to the default when stdin is not a terminal, so
    the script stays usable from another script or a CI job instead of
    hanging on a prompt nobody can answer.
    """
    if not sys.stdin.isatty():
        return current
    suffix = f" [{current}]" if current else ""
    try:
        answer = input(f"{prompt}{suffix}: ").strip()
    except (EOFError, KeyboardInterrupt):
        sys.exit("\naborted")
    return answer or current


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("url", help="Amazon product URL or bare ASIN")
    ap.add_argument("--title")
    ap.add_argument("--price", help='e.g. "₹1,299" — leave unset to show '
                                    '"See current price on Amazon"')
    ap.add_argument("--image", help="product image URL")
    ap.add_argument("--category")
    ap.add_argument("--description")
    ap.add_argument("--feature", action="append", default=[],
                    help="repeat for each bullet point")
    ap.add_argument("--featured", action="store_true",
                    help="show as an image card at the top of the index")
    args = ap.parse_args()

    asin = extract_asin(args.url)
    if not asin:
        sys.exit(
            f"error: could not find an ASIN in {args.url!r}\n"
            "Expected something like https://www.amazon.in/dp/B0F18RKVS4"
        )

    products = json.loads(DATA_FILE.read_text(encoding="utf-8")) if DATA_FILE.exists() else []
    existing = next(
        (p for p in products if isinstance(p, dict) and p.get("id") == asin), None
    )
    if existing:
        print(f"ASIN {asin} is already listed — updating it.\n")
    else:
        print(f"Adding ASIN {asin}.\n")
    base = existing or {}

    title = args.title or ask("Title", base.get("title", ""))
    if not title:
        sys.exit("error: a title is required")

    category = args.category or ask("Category", base.get("category", ""))
    price = args.price if args.price is not None else ask(
        "Price (blank = 'See current price on Amazon')", base.get("price", "")
    )
    image = args.image if args.image is not None else ask(
        "Image URL (right-click the Amazon photo -> Copy image address)",
        base.get("image_url", ""),
    )
    description = args.description or ask("Short description", base.get("description", ""))

    features = list(args.feature) or list(base.get("features") or [])
    if not args.feature and not features and sys.stdin.isatty():
        print("Feature bullets — one per line, blank line to finish:")
        while True:
            line = ask("  •")
            if not line:
                break
            features.append(line)

    product = {
        "id": asin,
        "title": title,
        "category": category,
        "price": price,
        "image_url": image,
        # build.py appends the Associates tag, so a plain link is correct here.
        "affiliate_url": f"https://www.amazon.in/dp/{asin}",
        "description": description,
        "features": features,
        "featured": args.featured or bool(base.get("featured")),
    }
    if existing:
        existing.update(product)
    else:
        products.append(product)

    DATA_FILE.write_text(
        json.dumps(products, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"\n{'Updated' if existing else 'Added'} {asin}: {title}")
    print(f"{DATA_FILE.relative_to(ROOT)} now has {len(products)} products.")
    if not price:
        print("No price set — the page will say 'See current price on Amazon'.")
    if not image:
        print("No image set — this product will show a lettered tile.")
    print("\nNext: python3 build.py")


if __name__ == "__main__":
    main()
