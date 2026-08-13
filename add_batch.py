#!/usr/bin/env python3
"""Add many products at once from a list of Amazon URLs.

Each line is a URL, optionally followed by pipe-separated extras:

    URL | Category | Price | Image URL

Only the URL is required. Amazon product URLs usually carry the product
name in the path, so a sensible title is derived from the URL itself —
no scraping, just parsing what you paste. Override any derived title
afterwards by editing data/products.json or re-running add_product.py.

Example input file (products.txt):

    # Electronics
    https://www.amazon.in/boAt-Airdopes-141-Playback-Resistance/dp/B0F18RKVS4 | Electronics | ₹1,299
    https://www.amazon.in/dp/B09XYZ1234 | Books
    https://www.amazon.in/Prestige-Iris-750-Watt-Grinder/dp/B00LZP24AS | Home & Kitchen | ₹2,749 | https://m.media-amazon.com/images/I/71abc.jpg

Usage:

    python3 add_batch.py products.txt
    python3 add_batch.py products.txt --dry-run
    python3 add_batch.py products.txt --featured-first 6

Re-running updates existing ASINs rather than duplicating them.
Then run: python3 build.py
"""

import argparse
import json
import re
import sys
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA_FILE = ROOT / "data" / "products.json"

sys.path.insert(0, str(ROOT))
from add_product import extract_asin  # noqa: E402  (single source of truth)

# Path segments that are never part of a product name.
NOISE = {"dp", "gp", "product", "ref", "b", "s", "d", "www.amazon.in", "amazon.in"}


def title_from_url(url):
    """Derive a readable title from the slug in an Amazon product URL.

    https://www.amazon.in/boAt-Airdopes-141-Bluetooth/dp/B0F18RKVS4/ref=sr_1_3
      -> "boAt Airdopes 141 Bluetooth"

    Returns "" for short-form URLs like /dp/ASIN that carry no name.
    """
    path = urllib.parse.urlsplit(url).path
    for segment in path.split("/"):
        segment = urllib.parse.unquote(segment).strip()
        if not segment or segment.lower() in NOISE:
            continue
        # Skip ASINs and ref tokens.
        if re.fullmatch(r"[A-Z0-9]{10}", segment, re.IGNORECASE):
            continue
        if segment.lower().startswith(("ref=", "sr_", "pd_", "psc")):
            continue
        if "-" not in segment and len(segment) < 12:
            continue
        words = [w for w in segment.split("-") if w and not w.isdigit() or w.isdigit()]
        title = " ".join(words).strip()
        # Amazon slugs are truncated mid-phrase; trim a dangling short word.
        if len(title) > 3:
            return title
    return ""


def parse_line(line):
    """Split 'URL | Category | Price | Image' into its parts."""
    parts = [p.strip() for p in line.split("|")]
    while len(parts) < 4:
        parts.append("")
    return parts[0], parts[1], parts[2], parts[3]


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("file", help="text file of Amazon URLs, one per line")
    ap.add_argument("--dry-run", action="store_true",
                    help="show what would be added without writing")
    ap.add_argument("--featured-first", type=int, default=0, metavar="N",
                    help="mark the first N new products as featured")
    args = ap.parse_args()

    lines = [
        ln.strip()
        for ln in Path(args.file).read_text(encoding="utf-8").splitlines()
        if ln.strip() and not ln.strip().startswith("#")
    ]
    if not lines:
        sys.exit(f"error: no usable lines in {args.file}")

    products = json.loads(DATA_FILE.read_text(encoding="utf-8")) if DATA_FILE.exists() else []
    by_asin = {p.get("id"): p for p in products if isinstance(p, dict) and p.get("id")}

    added = updated = skipped = 0
    for number, line in enumerate(lines, 1):
        url, category, price, image = parse_line(line)
        asin = extract_asin(url)
        if not asin:
            print(f"  ! line {number}: no ASIN found in {url[:60]!r}")
            skipped += 1
            continue

        title = title_from_url(url) or f"Product {asin}"
        entry = {
            "id": asin,
            "title": title,
            "category": category,
            "price": price,
            "image_url": image,
            # build.py appends the Associates tag.
            "affiliate_url": f"https://www.amazon.in/dp/{asin}",
            "description": "",
            "features": [],
            "featured": False,
        }

        if asin in by_asin:
            existing = by_asin[asin]
            for key, value in entry.items():
                if value and key not in ("featured",):
                    existing[key] = value
            updated += 1
            print(f"  ~ {asin}  {existing['title'][:52]}")
        else:
            if args.featured_first and added < args.featured_first:
                entry["featured"] = True
            products.append(entry)
            by_asin[asin] = entry
            added += 1
            flag = " [featured]" if entry["featured"] else ""
            print(f"  + {asin}  {title[:52]}{flag}")
            if not category:
                print(f"      (no category — set one before publishing)")

    print(f"\nAdded {added}, updated {updated}, skipped {skipped}.")
    if args.dry_run:
        print("Dry run — data/products.json was not written.")
        return
    DATA_FILE.write_text(
        json.dumps(products, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"Wrote data/products.json ({len(products)} products).")
    print("\nReview the derived titles, then: python3 build.py")


if __name__ == "__main__":
    main()
