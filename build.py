#!/usr/bin/env python3
"""Build indiandeals2buy.com: generates static HTML in docs/ from data/products.json.

Usage:  python build.py

GitHub Pages is configured to serve the docs/ folder, so everything the site
needs (index.html, products/, style.css, sitemap.xml, robots.txt, 404.html,
CNAME, .nojekyll) is written or copied there.
"""

import html
import json
import re
import shutil
import sys
import unicodedata
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

ROOT = Path(__file__).resolve().parent
DATA_FILE = ROOT / "data" / "products.json"
TEMPLATE_DIR = ROOT / "templates"
OUTPUT_DIR = ROOT / "docs"
SITE_URL = "https://indiandeals2buy.com"

# Shown next to the logo on every page. Kept merchant-neutral so it still reads
# correctly as stores beyond Amazon are added.
SITE_TAGLINE = "Hand-picked deals from India’s top stores"

# Amazon Associates tag. Every amazon.* link is rewritten to carry it, so the
# data file can hold plain product URLs and still earn correctly.
AFFILIATE_TAG = "onlinedealsat-21"

# Google AdSense publisher ID, e.g. "ca-pub-1234567890123456". Leave empty to
# build the site with no ad code at all. When set, every page gets the AdSense
# loader (which is also what Auto ads and site verification use) and docs/ads.txt
# is generated — AdSense will not serve ads without that file.
ADSENSE_CLIENT = ""

# Published on the contact page and in the privacy policy. AdSense reviewers
# look for a reachable address here — make sure this mailbox actually works.
CONTACT_EMAIL = "contact@indiandeals2buy.com"

# Shown at the top of the privacy policy; bump it whenever the policy changes.
POLICY_UPDATED = "7 August 2026"

# Standalone content pages, built from templates/pages/<slug>.html into docs/.
# (slug, footer label, page title, meta description)
PAGES = [
    (
        "about",
        "About",
        "About",
        "Who picks the deals on IndianDeals2Buy, why prices move, and how the site makes money.",
    ),
    (
        "contact",
        "Contact",
        "Contact",
        "How to reach IndianDeals2Buy about corrections, broken links, suggestions or removal requests.",
    ),
    (
        "privacy",
        "Privacy",
        "Privacy Policy",
        "What IndianDeals2Buy collects, the cookies Google and Amazon set, and how to opt out of personalised ads.",
    ),
]

# Products shown as image cards at the top of the index. Everything else stays
# a plain text link so the page stays light with thousands of products.
MAX_FEATURED = 12

REQUIRED_FIELDS = ("title", "affiliate_url")


def apply_affiliate_tag(url):
    """Force our Associates tag onto an Amazon URL. Returns (url, was_changed).

    Non-Amazon URLs are left untouched — this should never silently rewrite a
    link to somewhere it does not belong.
    """
    parts = urlsplit(url)
    if not parts.netloc or "amazon." not in parts.netloc.lower():
        return url, False
    params = [
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if k.lower() != "tag"
    ]
    params.append(("tag", AFFILIATE_TAG))
    tagged = urlunsplit(
        (parts.scheme, parts.netloc, parts.path, urlencode(params), parts.fragment)
    )
    return tagged, tagged != url


def slugify(text):
    """Lowercase, ASCII-fold, replace runs of non-alphanumerics with hyphens.

    Returns "" when nothing usable survives (e.g. a title written entirely in
    Devanagari); callers fall back to the product id.
    """
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def adsense_head():
    """The AdSense loader <script>, or "" when no publisher ID is configured."""
    if not ADSENSE_CLIENT:
        return ""
    if not re.fullmatch(r"ca-pub-\d{16}", ADSENSE_CLIENT):
        sys.exit(
            f"error: ADSENSE_CLIENT must look like ca-pub-1234567890123456, "
            f"got {ADSENSE_CLIENT!r}"
        )
    return (
        '<script async src="https://pagead2.googlesyndication.com/pagead/js/'
        f'adsbygoogle.js?client={ADSENSE_CLIENT}" crossorigin="anonymous"></script>\n'
    )


def footer_nav(prefix=""):
    """Footer links to the standalone pages.

    `prefix` makes the hrefs resolve from whatever directory the page lives in —
    product pages sit one level down and need "../".
    """
    links = [f'<a href="{prefix}index.html">All deals</a>'] + [
        f'<a href="{prefix}{slug}.html">{label}</a>' for slug, label, _, _ in PAGES
    ]
    return f'  <nav class="footer-nav" aria-label="Site">{"".join(links)}</nav>\n'


def render(template, context):
    """Fill {{placeholder}} slots. Values must already be HTML-escaped."""
    return re.sub(
        r"\{\{(\w+)\}\}",
        lambda m: context.get(m.group(1), ""),
        template,
    )


def build():
    if not DATA_FILE.exists():
        sys.exit(f"error: {DATA_FILE} not found")

    try:
        products = json.loads(DATA_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        sys.exit(f"error: {DATA_FILE} is not valid JSON: {exc}")
    if not isinstance(products, list):
        sys.exit("error: products.json must contain a JSON array of products")

    product_tpl = (TEMPLATE_DIR / "product.html.template").read_text(encoding="utf-8")
    index_tpl = (TEMPLATE_DIR / "index.html.template").read_text(encoding="utf-8")
    page_tpl = (TEMPLATE_DIR / "page.html.template").read_text(encoding="utf-8")
    notfound_path = TEMPLATE_DIR / "404.html.template"
    notfound_tpl = notfound_path.read_text(encoding="utf-8") if notfound_path.exists() else ""

    products_dir = OUTPUT_DIR / "products"
    products_dir.mkdir(parents=True, exist_ok=True)

    ads_head = adsense_head()
    nav_root = footer_nav()

    seen_slugs = set()
    skipped = []       # (index, reason)
    renamed_slugs = [] # (original, final)
    entries = []       # (category, title, price, slug) for the index
    featured = []      # products to render as cards at the top of the index
    thumbs = {}        # slug -> escaped image URL, for index list thumbnails
    retagged = 0       # affiliate URLs rewritten to carry AFFILIATE_TAG

    for i, product in enumerate(products):
        if not isinstance(product, dict):
            skipped.append((i, "not a JSON object"))
            continue
        missing = [f for f in REQUIRED_FIELDS if not str(product.get(f, "")).strip()]
        if missing:
            skipped.append((i, f"missing {', '.join(missing)}"))
            continue

        title = str(product["title"]).strip()
        # Prefer an explicit slug, then the title; fall back to the ASIN so a
        # non-Latin title still gets a meaningful, stable filename.
        slug = (
            slugify(str(product.get("slug", "")).strip())
            or slugify(title)
            or slugify(str(product.get("id", "")).strip())
            or "product"
        )
        if slug in seen_slugs:
            base, n = slug, 2
            while slug in seen_slugs:
                slug = f"{base}-{n}"
                n += 1
            renamed_slugs.append((base, slug))
        seen_slugs.add(slug)

        category = str(product.get("category", "")).strip() or "Other"
        price = str(product.get("price", "")).strip()
        description = str(product.get("description", "")).strip()
        features = product.get("features") or []

        features_html = "\n".join(
            f"        <li>{html.escape(str(f))}</li>" for f in features if str(f).strip()
        )
        meta_description = description or f"{title} — best price on Amazon India."
        if len(meta_description) > 160:
            meta_description = meta_description[:157].rstrip() + "…"

        affiliate_url, changed = apply_affiliate_tag(str(product["affiliate_url"]).strip())
        if changed:
            retagged += 1

        # Products without an image skip the image column entirely rather than
        # leaving a broken <img> and a large blank gap in the layout.
        image_url = html.escape(str(product.get("image_url", "")).strip(), quote=True)
        if image_url:
            image_block = (
                '    <div class="product-image">\n'
                f'      <img src="{image_url}" alt="{html.escape(title)}" '
                'loading="lazy" referrerpolicy="no-referrer">\n'
                '    </div>\n'
            )
            og_image_tag = f'<meta property="og:image" content="{image_url}">\n'
        else:
            image_block = ""
            og_image_tag = ""

        # An unverified price is worse than no price — Amazon requires displayed
        # prices to be current, so products without one point at Amazon instead.
        if price:
            price_block = f'      <p class="price">{html.escape(price)}</p>\n'
        else:
            price_block = '      <p class="price-note">See current price on Amazon</p>\n'

        page = render(product_tpl, {
            "title": html.escape(title),
            "category": html.escape(category),
            "price": html.escape(price),
            "description": html.escape(description),
            "features_html": features_html,
            "price_block": price_block,
            "image_block": image_block,
            "og_image_tag": og_image_tag,
            "affiliate_url": html.escape(affiliate_url, quote=True),
            "meta_description": html.escape(meta_description),
            "canonical_url": f"{SITE_URL}/products/{slug}.html",
            "adsense_head": ads_head,
            "footer_nav": footer_nav("../"),
        })
        (products_dir / f"{slug}.html").write_text(page, encoding="utf-8")
        entries.append((category, title, price, slug))
        if image_url:
            thumbs[slug] = image_url
        if product.get("featured") and len(featured) < MAX_FEATURED:
            featured.append((title, category, price, slug, image_url, description))

    # Index: category sections, alphabetical, products alphabetical within each.
    by_category = {}
    for category, title, price, slug in entries:
        by_category.setdefault(category, []).append((title, price, slug))

    sections = []
    for category in sorted(by_category, key=str.lower):
        items = []
        for title, price, slug in sorted(by_category[category], key=lambda p: p[0].lower()):
            price_span = f' <span class="li-price">{html.escape(price)}</span>' if price else ""
            # Thumbnails are lazy-loaded, so thousands of rows cost nothing until
            # they scroll into view. Once any product has an image, rows without
            # one get an empty spacer so titles stay aligned in a half-filled
            # data file; with no images at all, no thumb column is rendered.
            thumb = thumbs.get(slug, "")
            if thumb:
                thumb_html = (
                    f'<img class="li-thumb" src="{thumb}" alt="" loading="lazy" '
                    f'decoding="async" referrerpolicy="no-referrer">'
                )
            elif thumbs:
                thumb_html = '<span class="li-thumb li-thumb-empty"></span>'
            else:
                thumb_html = ""
            items.append(
                f'      <li><a href="products/{slug}.html">{thumb_html}'
                f'<span class="li-title">{html.escape(title)}</span></a>{price_span}</li>'
            )
        sections.append(
            f'  <section class="category-section" id="cat-{slugify(category)}">\n'
            f'    <h2 class="section-heading">{html.escape(category)}</h2>\n'
            f'    <ul class="product-list">\n' + "\n".join(items) + "\n"
            f'    </ul>\n'
            f'  </section>'
        )

    # Featured cards. Products with no image get a lettered tile instead of a
    # broken <img>, so a half-filled data file still looks deliberate.
    cards = []
    for title, category, price, slug, image_url, description in featured:
        if image_url:
            media = (
                f'<div class="card-media"><img src="{image_url}" '
                f'alt="{html.escape(title)}" loading="lazy" '
                f'referrerpolicy="no-referrer"></div>'
            )
        else:
            initial = html.escape(title[:1].upper() or "?")
            media = f'<div class="card-media card-media-empty"><span>{initial}</span></div>'
        price_html = f'<span class="card-price">{html.escape(price)}</span>' if price else ""
        cards.append(
            f'      <a class="card" href="products/{slug}.html">\n'
            f'        {media}\n'
            f'        <div class="card-body">\n'
            f'          <span class="card-cat">{html.escape(category)}</span>\n'
            f'          <h3 class="card-title">{html.escape(title)}</h3>\n'
            f'          {price_html}\n'
            f'        </div>\n'
            f'      </a>'
        )
    featured_section = ""
    if cards:
        featured_section = (
            '  <section class="featured">\n'
            '    <h2 class="section-heading">Featured picks</h2>\n'
            '    <div class="card-grid">\n' + "\n".join(cards) + "\n"
            '    </div>\n'
            '  </section>'
        )

    chips = "\n".join(
        f'      <a class="chip" href="#cat-{slugify(c)}">{html.escape(c)} '
        f'<span>{len(by_category[c])}</span></a>'
        for c in sorted(by_category, key=str.lower)
    )

    # The hero used to promise "no tracking scripts" and a zero-tracker count.
    # Ad code makes that untrue, so the claim is only made when there is none.
    if ADSENSE_CLIENT:
        hero_sub = (
            "Every product here is picked by hand and links straight to the "
            "store — no clutter, no clickbait."
        )
        stat_third = '<div class="stat"><strong>Direct</strong><span>store links</span></div>'
    else:
        hero_sub = (
            "Every product here is picked by hand and links straight to the "
            "store — no clutter, no clickbait, no tracking scripts."
        )
        stat_third = '<div class="stat"><strong>0</strong><span>trackers</span></div>'

    index_page = render(index_tpl, {
        "product_count": f"{len(entries):,}",
        "category_count": str(len(by_category)),
        "featured_section": featured_section,
        "category_chips": chips,
        "category_sections": "\n".join(sections),
        "adsense_head": ads_head,
        "footer_nav": nav_root,
        "tagline": html.escape(SITE_TAGLINE),
        "hero_sub": hero_sub,
        "stat_third": stat_third,
    })
    (OUTPUT_DIR / "index.html").write_text(index_page, encoding="utf-8")

    # Standalone content pages (about / contact / privacy). AdSense expects
    # these to exist and to be reachable, hence the footer nav on every page.
    for slug, _label, page_title, page_desc in PAGES:
        body_path = TEMPLATE_DIR / "pages" / f"{slug}.html"
        if not body_path.exists():
            sys.exit(f"error: {body_path} not found")
        body = render(body_path.read_text(encoding="utf-8"), {
            "contact_email": html.escape(CONTACT_EMAIL),
            "last_updated": html.escape(POLICY_UPDATED),
        })
        (OUTPUT_DIR / f"{slug}.html").write_text(
            render(page_tpl, {
                "title": html.escape(page_title),
                "meta_description": html.escape(page_desc),
                "canonical_url": f"{SITE_URL}/{slug}.html",
                "content": body,
                "adsense_head": ads_head,
                "footer_nav": nav_root,
                "tagline": html.escape(SITE_TAGLINE),
            }),
            encoding="utf-8",
        )

    # sitemap.xml + robots.txt so several thousand product pages get crawled.
    urls = (
        [f"  <url><loc>{SITE_URL}/</loc></url>"]
        + [f"  <url><loc>{SITE_URL}/{slug}.html</loc></url>" for slug, _, _, _ in PAGES]
        + [
            f"  <url><loc>{SITE_URL}/products/{slug}.html</loc></url>"
            for _, _, _, slug in sorted(entries, key=lambda e: e[3])
        ]
    )
    (OUTPUT_DIR / "sitemap.xml").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        + "\n".join(urls)
        + "\n</urlset>\n",
        encoding="utf-8",
    )
    (OUTPUT_DIR / "robots.txt").write_text(
        f"User-agent: *\nAllow: /\n\nSitemap: {SITE_URL}/sitemap.xml\n", encoding="utf-8"
    )

    # GitHub Pages serves this for any unknown path.
    if notfound_tpl:
        (OUTPUT_DIR / "404.html").write_text(
            render(notfound_tpl, {
                "adsense_head": ads_head,
                # Served from any depth, so its links must be absolute.
                "footer_nav": footer_nav("/"),
            }),
            encoding="utf-8",
        )

    # AdSense refuses to serve ads on a domain without a matching ads.txt at the
    # site root, so it is generated from the same publisher ID.
    ads_txt = OUTPUT_DIR / "ads.txt"
    if ADSENSE_CLIENT:
        publisher = ADSENSE_CLIENT.replace("ca-pub-", "pub-")
        ads_txt.write_text(
            f"google.com, {publisher}, DIRECT, f08c47fec0942fa0\n", encoding="utf-8"
        )
    elif ads_txt.exists():
        ads_txt.unlink()

    # Static files served alongside the generated pages.
    shutil.copy(ROOT / "assets" / "style.css", OUTPUT_DIR / "style.css")
    # Written without a trailing newline to match what GitHub's custom-domain UI
    # commits, so the file does not flip-flop between builds and UI edits.
    cname_src = ROOT / "CNAME"
    if cname_src.exists():
        (OUTPUT_DIR / "CNAME").write_text(
            cname_src.read_text(encoding="utf-8").strip(), encoding="utf-8"
        )
    if (ROOT / ".nojekyll").exists():
        shutil.copy(ROOT / ".nojekyll", OUTPUT_DIR / ".nojekyll")

    # Remove product pages left over from deleted/renamed products.
    stale = [p for p in products_dir.glob("*.html") if p.stem not in seen_slugs]
    for path in stale:
        path.unlink()

    print(f"Built {len(entries)} product pages + index.html into {OUTPUT_DIR.relative_to(ROOT)}/")
    print(f"Categories: {len(by_category)}   Featured cards: {len(featured)}")
    print(f"Affiliate tag: {AFFILIATE_TAG} ({retagged} URL(s) rewritten to carry it)")
    print(f"Content pages: {', '.join(slug + '.html' for slug, _, _, _ in PAGES)}")
    print(f"AdSense: {ADSENSE_CLIENT + ' (+ ads.txt)' if ADSENSE_CLIENT else 'not configured'}")
    if renamed_slugs:
        print(f"Duplicate slugs renamed ({len(renamed_slugs)}):")
        for original, final in renamed_slugs:
            print(f"  {original} -> {final}")
    if skipped:
        print(f"Skipped products ({len(skipped)}):")
        for index, reason in skipped:
            print(f"  products[{index}]: {reason}")
    if stale:
        print(f"Removed {len(stale)} stale page(s): {', '.join(p.name for p in stale)}")


if __name__ == "__main__":
    build()
