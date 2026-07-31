#!/usr/bin/env python3
"""Populate data/products.json from the Amazon Product Advertising API 5.0.

Fetches verified titles, current prices, licensed image URLs and feature
bullets for a list of ASINs, then merges them into the data file. Curation
fields you set by hand (category, featured, slug) are preserved.

Standard library only — same as build.py, no pip install required.

Setup (never commit these):

    export PAAPI_ACCESS_KEY="AKIA..."
    export PAAPI_SECRET_KEY="..."

Usage:

    python3 fetch_products.py B08XYZ1234 B07ABC5678     # add or update ASINs
    python3 fetch_products.py --from-file asins.txt      # one ASIN per line
    python3 fetch_products.py --refresh                  # re-fetch everything
    python3 fetch_products.py --refresh --dry-run        # show changes only

Prices go stale. Re-run --refresh on a schedule so displayed prices stay
accurate, which is what the Associates agreement expects.
"""

import argparse
import datetime
import hashlib
import hmac
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA_FILE = ROOT / "data" / "products.json"

# amazon.in lives in the eu-west-1 PA-API region.
HOST = "webservices.amazon.in"
REGION = "eu-west-1"
MARKETPLACE = "www.amazon.in"
SERVICE = "ProductAdvertisingAPI"
PATH = "/paapi5/getitems"
TARGET = "com.amazon.paapi5.v1.ProductAdvertisingAPIv1.GetItems"

# GetItems accepts at most 10 ASINs per call.
BATCH_SIZE = 10
# PA-API starts new accounts at 1 request/second and grants more with sales.
REQUEST_INTERVAL = 1.1

RESOURCES = [
    "ItemInfo.Title",
    "ItemInfo.Features",
    "ItemInfo.ProductInfo",
    "Offers.Listings.Price",
    "Offers.Listings.Availability.Message",
    "Images.Primary.Large",
    "BrowseNodeInfo.BrowseNodes",
]


def _sign(key, msg):
    return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()


def sign_request(payload, access_key, secret_key):
    """Build SigV4 headers for a PA-API 5.0 POST.

    PA-API signs a fixed set of headers; the signed list and the headers
    actually sent must match exactly or the service returns 403.
    """
    now = datetime.datetime.now(datetime.timezone.utc)
    amz_date = now.strftime("%Y%m%dT%H%M%SZ")
    date_stamp = now.strftime("%Y%m%d")

    headers = {
        "content-encoding": "amz-1.0",
        "content-type": "application/json; charset=utf-8",
        "host": HOST,
        "x-amz-date": amz_date,
        "x-amz-target": TARGET,
    }
    signed_headers = ";".join(sorted(headers))
    canonical_headers = "".join(f"{k}:{headers[k]}\n" for k in sorted(headers))
    payload_hash = hashlib.sha256(payload.encode("utf-8")).hexdigest()

    canonical_request = "\n".join(
        ["POST", PATH, "", canonical_headers, signed_headers, payload_hash]
    )
    scope = f"{date_stamp}/{REGION}/{SERVICE}/aws4_request"
    to_sign = "\n".join(
        [
            "AWS4-HMAC-SHA256",
            amz_date,
            scope,
            hashlib.sha256(canonical_request.encode("utf-8")).hexdigest(),
        ]
    )

    k_date = _sign(f"AWS4{secret_key}".encode("utf-8"), date_stamp)
    k_region = _sign(k_date, REGION)
    k_service = _sign(k_region, SERVICE)
    k_signing = _sign(k_service, "aws4_request")
    signature = hmac.new(k_signing, to_sign.encode("utf-8"), hashlib.sha256).hexdigest()

    headers["Authorization"] = (
        f"AWS4-HMAC-SHA256 Credential={access_key}/{scope}, "
        f"SignedHeaders={signed_headers}, Signature={signature}"
    )
    return headers


def get_items(asins, access_key, secret_key, partner_tag):
    """Call GetItems for up to BATCH_SIZE ASINs. Returns the parsed response."""
    payload = json.dumps(
        {
            "ItemIds": list(asins),
            "ItemIdType": "ASIN",
            "Resources": RESOURCES,
            "PartnerTag": partner_tag,
            "PartnerType": "Associates",
            "Marketplace": MARKETPLACE,
        }
    )
    headers = sign_request(payload, access_key, secret_key)
    req = urllib.request.Request(
        f"https://{HOST}{PATH}", data=payload.encode("utf-8"), headers=headers, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        raise SystemExit(
            f"PA-API returned HTTP {exc.code}.\n{body}\n\n"
            "Common causes: credentials wrong, the account is not yet approved "
            "for PA-API, or the tag does not belong to the amazon.in marketplace."
        )
    except urllib.error.URLError as exc:
        raise SystemExit(f"Could not reach {HOST}: {exc.reason}")


def parse_item(item):
    """Map one PA-API item onto our product schema. Missing fields stay empty."""
    info = item.get("ItemInfo") or {}
    title = ((info.get("Title") or {}).get("DisplayValue") or "").strip()

    features = [
        f.strip()
        for f in ((info.get("Features") or {}).get("DisplayValues") or [])
        if f and f.strip()
    ]

    image = (
        ((item.get("Images") or {}).get("Primary") or {}).get("Large") or {}
    ).get("URL", "")

    # Only take a price from a listing that is actually buyable.
    price = ""
    listings = (item.get("Offers") or {}).get("Listings") or []
    if listings:
        price = ((listings[0].get("Price") or {}).get("DisplayAmount") or "").strip()

    # PA-API has no short description; the first feature bullet reads best.
    description = features[0] if features else ""

    return {
        "id": item.get("ASIN", ""),
        "title": title,
        "price": price,
        "image_url": image,
        "affiliate_url": item.get("DetailPageURL", ""),
        "description": description,
        "features": features[:6],
    }


def merge(existing, fetched):
    """Overlay API data on a product, keeping hand-curated fields."""
    merged = dict(existing)
    for key, value in fetched.items():
        # Never blank out something we already have with an empty API field.
        if value:
            merged[key] = value
    # Curation fields belong to you, not the API.
    for key in ("category", "featured", "slug"):
        if key in existing:
            merged[key] = existing[key]
    merged.setdefault("category", "")
    return merged


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("asins", nargs="*", help="ASINs to add or update")
    ap.add_argument("--from-file", help="file with one ASIN per line")
    ap.add_argument("--refresh", action="store_true",
                    help="re-fetch every ASIN already in products.json")
    ap.add_argument("--dry-run", action="store_true",
                    help="report what would change without writing")
    args = ap.parse_args()

    access_key = os.environ.get("PAAPI_ACCESS_KEY", "").strip()
    secret_key = os.environ.get("PAAPI_SECRET_KEY", "").strip()
    if not access_key or not secret_key:
        sys.exit(
            "error: set PAAPI_ACCESS_KEY and PAAPI_SECRET_KEY in your environment.\n"
            "Do not put credentials in a file inside this repository."
        )

    # Single source of truth for the tag: build.py.
    sys.path.insert(0, str(ROOT))
    from build import AFFILIATE_TAG as partner_tag

    products = json.loads(DATA_FILE.read_text(encoding="utf-8")) if DATA_FILE.exists() else []
    by_asin = {p.get("id"): p for p in products if isinstance(p, dict) and p.get("id")}

    wanted = list(args.asins)
    if args.from_file:
        wanted += [
            line.strip()
            for line in Path(args.from_file).read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.startswith("#")
        ]
    if args.refresh:
        wanted += list(by_asin)
    # De-duplicate, preserving order.
    wanted = list(dict.fromkeys(a.strip().upper() for a in wanted if a.strip()))

    if not wanted:
        sys.exit("nothing to do: pass ASINs, --from-file or --refresh")

    print(f"Fetching {len(wanted)} ASIN(s) from PA-API as {partner_tag} …")
    added = updated = missing = 0
    for start in range(0, len(wanted), BATCH_SIZE):
        batch = wanted[start:start + BATCH_SIZE]
        data = get_items(batch, access_key, secret_key, partner_tag)

        for err in data.get("Errors", []) or []:
            print(f"  ! {err.get('Code')}: {err.get('Message')}")
            missing += 1

        for item in (data.get("ItemsResult") or {}).get("Items", []) or []:
            fetched = parse_item(item)
            asin = fetched["id"]
            if asin in by_asin:
                before = dict(by_asin[asin])
                by_asin[asin].update(merge(by_asin[asin], fetched))
                if by_asin[asin] != before:
                    updated += 1
                    print(f"  ~ {asin}  {fetched['title'][:58]}  {fetched['price']}")
            else:
                new = merge({"category": "", "featured": False}, fetched)
                products.append(new)
                by_asin[asin] = new
                added += 1
                print(f"  + {asin}  {fetched['title'][:58]}  {fetched['price']}")

        if start + BATCH_SIZE < len(wanted):
            time.sleep(REQUEST_INTERVAL)

    print(f"\nAdded {added}, updated {updated}, not found {missing}.")
    if args.dry_run:
        print("Dry run — data/products.json was not written.")
        return
    DATA_FILE.write_text(
        json.dumps(products, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"Wrote {DATA_FILE.relative_to(ROOT)} ({len(products)} products).")
    if added:
        print("New products have an empty category — set it before publishing.")
    print("Next: python3 build.py")


if __name__ == "__main__":
    main()
