#!/usr/bin/env python3
"""Populate data/products.json from the Amazon Creators API.

Replaces the retired Product Advertising API 5.0 (shut down 15 May 2026).
Fetches verified titles, current prices, licensed image URLs and feature
bullets for a list of ASINs, then merges them into the data file. Curation
fields you set by hand (category, featured, slug) are preserved.

Standard library only — same as build.py, no pip install required.

Setup (never commit these):

    export CREATORS_CLIENT_ID="amzn1.application-oa2-client...."
    export CREATORS_CLIENT_SECRET="amzn1.oa2-cs.v1...."
    export CREATORS_CREDENTIAL_VERSION="3.2"    # optional, see below

Credentials are region-group scoped and must match the marketplace:

    3.1  North America              https://api.amazon.com/auth/o2/token
    3.2  Europe / Middle East / India   https://api.amazon.co.uk/auth/o2/token
    3.3  Far East                   https://api.amazon.co.jp/auth/o2/token

amazon.in belongs to the EU/ME/India group, so this site needs 3.2
credentials minted from affiliate-program.amazon.in. A 3.1 credential from
the .com account will fail to authorise against a .in marketplace.

Usage:

    python3 fetch_products.py B08XYZ1234 B07ABC5678     # add or update ASINs
    python3 fetch_products.py --from-file asins.txt      # one ASIN per line
    python3 fetch_products.py --refresh                  # re-fetch everything
    python3 fetch_products.py --refresh --dry-run        # show changes only
    python3 fetch_products.py B08XYZ1234 --raw           # dump API response

Prices go stale. Re-run --refresh on a schedule so displayed prices stay
accurate, which is what the Associates agreement expects.
"""

import argparse
import base64
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA_FILE = ROOT / "data" / "products.json"

API_HOST = "creatorsapi.amazon"
GET_ITEMS_PATH = "/catalog/v1/getItems"
MARKETPLACE = "www.amazon.in"

# Token endpoint per credential version (region group).
TOKEN_ENDPOINTS = {
    "3.1": "https://api.amazon.com/auth/o2/token",
    "3.2": "https://api.amazon.co.uk/auth/o2/token",
    "3.3": "https://api.amazon.co.jp/auth/o2/token",
    # Legacy Cognito credentials use a different scope and Authorization shape.
    "2.1": "https://creatorsapi.auth.us-east-1.amazoncognito.com/oauth2/token",
    "2.2": "https://creatorsapi.auth.eu-south-2.amazoncognito.com/oauth2/token",
    "2.3": "https://creatorsapi.auth.us-west-2.amazoncognito.com/oauth2/token",
}
DEFAULT_VERSION = "3.2"  # amazon.in

BATCH_SIZE = 10          # getItems accepts at most 10 ASINs
REQUEST_INTERVAL = 1.1   # be polite; back off further on 429

RESOURCES = [
    "ItemInfo.Title",
    "ItemInfo.Features",
    "Images.Primary.Large",
    "OffersV2.Listings.Price",
    "OffersV2.Listings.Availability",
]


def get_token(client_id, client_secret, version):
    """Exchange client credentials for a bearer token (OAuth2 client_credentials)."""
    endpoint = TOKEN_ENDPOINTS.get(version)
    if not endpoint:
        raise SystemExit(
            f"error: unknown credential version {version!r}. "
            f"Expected one of {', '.join(sorted(TOKEN_ENDPOINTS))}."
        )

    legacy = version.startswith("2.")
    scope = "creatorsapi/default" if legacy else "creatorsapi::default"
    body = {"grant_type": "client_credentials", "scope": scope}
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    if legacy:
        # Cognito wants the credentials as HTTP Basic auth.
        basic = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
        headers["Authorization"] = f"Basic {basic}"
    else:
        body["client_id"] = client_id
        body["client_secret"] = client_secret

    req = urllib.request.Request(
        endpoint,
        data=urllib.parse.urlencode(body).encode(),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        raise SystemExit(
            f"Token request failed: HTTP {exc.code}\n{detail}\n\n"
            f"Most likely: the credential version ({version}) does not match the "
            f"marketplace ({MARKETPLACE}). amazon.in needs version 3.2 credentials "
            "created in the amazon.in Associates account."
        )
    except urllib.error.URLError as exc:
        raise SystemExit(f"Could not reach {endpoint}: {exc.reason}")

    token = data.get("access_token")
    if not token:
        raise SystemExit(f"No access_token in response: {data}")
    # Authorization shape differs between v2 and v3 credentials.
    return f"Bearer {token}, Version {version}" if legacy else f"Bearer {token}"


def get_items(asins, auth_header, partner_tag, raw=False):
    """Call getItems for up to BATCH_SIZE ASINs. Returns the parsed response."""
    payload = json.dumps(
        {
            "itemIds": list(asins),
            "itemIdType": "ASIN",
            "resources": RESOURCES,
            "partnerTag": partner_tag,
            "partnerType": "Associates",
        }
    )
    req = urllib.request.Request(
        f"https://{API_HOST}{GET_ITEMS_PATH}",
        data=payload.encode("utf-8"),
        headers={
            "Authorization": auth_header,
            "Content-Type": "application/json",
            "x-marketplace": MARKETPLACE,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        if exc.code == 429:
            raise SystemExit("Rate limited (429). Wait a minute and re-run.")
        raise SystemExit(f"getItems failed: HTTP {exc.code}\n{detail}")
    except urllib.error.URLError as exc:
        raise SystemExit(f"Could not reach {API_HOST}: {exc.reason}")

    if raw:
        print(body)
    return json.loads(body)


def pick(mapping, *names):
    """Read a key case-insensitively.

    The Creators API moved to lowerCamelCase, but casing has varied between
    documentation and rollout, so accept either rather than silently
    returning nothing.
    """
    if not isinstance(mapping, dict):
        return None
    lowered = {k.lower(): v for k, v in mapping.items()}
    for name in names:
        if name.lower() in lowered:
            return lowered[name.lower()]
    return None


def parse_item(item):
    """Map one API item onto our product schema. Missing fields stay empty."""
    info = pick(item, "itemInfo") or {}
    title = (pick(pick(info, "title") or {}, "displayValue") or "").strip()

    features = [
        f.strip()
        for f in (pick(pick(info, "features") or {}, "displayValues") or [])
        if isinstance(f, str) and f.strip()
    ]

    primary = pick(pick(item, "images") or {}, "primary") or {}
    image = (pick(pick(primary, "large") or {}, "url") or "")

    price = ""
    offers = pick(item, "offersV2", "offers") or {}
    listings = pick(offers, "listings") or []
    if listings:
        money = pick(listings[0], "price") or {}
        price = (
            pick(money, "displayAmount")
            or pick(pick(money, "money") or {}, "displayAmount")
            or ""
        ).strip()

    return {
        "id": pick(item, "asin") or "",
        "title": title,
        "price": price,
        "image_url": image,
        "affiliate_url": pick(item, "detailPageURL", "detailPageUrl") or "",
        "description": features[0] if features else "",
        "features": features[:6],
    }


def merge(existing, fetched):
    """Overlay API data on a product, keeping hand-curated fields."""
    merged = dict(existing)
    for key, value in fetched.items():
        if value:  # never blank an existing value with an empty API field
            merged[key] = value
    for key in ("category", "featured", "slug"):
        if key in existing:
            merged[key] = existing[key]
    merged.setdefault("category", "")
    return merged


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("asins", nargs="*", help="ASINs to add or update")
    ap.add_argument("--from-file", help="file with one ASIN per line")
    ap.add_argument("--refresh", action="store_true",
                    help="re-fetch every ASIN already in products.json")
    ap.add_argument("--dry-run", action="store_true",
                    help="report what would change without writing")
    ap.add_argument("--raw", action="store_true",
                    help="print the raw API response (for debugging field names)")
    args = ap.parse_args()

    client_id = os.environ.get("CREATORS_CLIENT_ID", "").strip()
    client_secret = os.environ.get("CREATORS_CLIENT_SECRET", "").strip()
    version = os.environ.get("CREATORS_CREDENTIAL_VERSION", DEFAULT_VERSION).strip()
    if not client_id or not client_secret:
        sys.exit(
            "error: set CREATORS_CLIENT_ID and CREATORS_CLIENT_SECRET in your "
            "environment.\nDo not put credentials in a file inside this repository."
        )

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
    wanted = list(dict.fromkeys(a.strip().upper() for a in wanted if a.strip()))
    if not wanted:
        sys.exit("nothing to do: pass ASINs, --from-file or --refresh")

    print(f"Authenticating (credential version {version}, marketplace {MARKETPLACE}) …")
    auth_header = get_token(client_id, client_secret, version)

    print(f"Fetching {len(wanted)} ASIN(s) as {partner_tag} …")
    added = updated = missing = 0
    for start in range(0, len(wanted), BATCH_SIZE):
        batch = wanted[start:start + BATCH_SIZE]
        data = get_items(batch, auth_header, partner_tag, raw=args.raw)

        for err in (pick(data, "errors") or []):
            print(f"  ! {pick(err, 'code')}: {pick(err, 'message')}")
            missing += 1

        result = pick(data, "itemsResult") or {}
        for item in (pick(result, "items") or []):
            fetched = parse_item(item)
            asin = fetched["id"]
            if not asin:
                continue
            if asin in by_asin:
                before = dict(by_asin[asin])
                by_asin[asin].update(merge(by_asin[asin], fetched))
                if by_asin[asin] != before:
                    updated += 1
                    print(f"  ~ {asin}  {fetched['title'][:56]}  {fetched['price']}")
            else:
                new = merge({"category": "", "featured": False}, fetched)
                products.append(new)
                by_asin[asin] = new
                added += 1
                print(f"  + {asin}  {fetched['title'][:56]}  {fetched['price']}")

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
