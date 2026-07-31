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

ROOT = Path(__file__).resolve().parent
DATA_FILE = ROOT / "data" / "products.json"
TEMPLATE_DIR = ROOT / "templates"
OUTPUT_DIR = ROOT / "docs"
SITE_URL = "https://indiandeals2buy.com"

REQUIRED_FIELDS = ("title", "affiliate_url")


def slugify(text):
    """Lowercase, ASCII-fold, replace runs of non-alphanumerics with hyphens.

    Returns "" when nothing usable survives (e.g. a title written entirely in
    Devanagari); callers fall back to the product id.
    """
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


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
    notfound_path = TEMPLATE_DIR / "404.html.template"
    notfound_tpl = notfound_path.read_text(encoding="utf-8") if notfound_path.exists() else ""

    products_dir = OUTPUT_DIR / "products"
    products_dir.mkdir(parents=True, exist_ok=True)

    seen_slugs = set()
    skipped = []       # (index, reason)
    renamed_slugs = [] # (original, final)
    entries = []       # (category, title, price, slug) for the index

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

        page = render(product_tpl, {
            "title": html.escape(title),
            "category": html.escape(category),
            "price": html.escape(price),
            "description": html.escape(description),
            "features_html": features_html,
            "image_block": image_block,
            "og_image_tag": og_image_tag,
            "affiliate_url": html.escape(str(product["affiliate_url"]).strip(), quote=True),
            "meta_description": html.escape(meta_description),
            "canonical_url": f"{SITE_URL}/products/{slug}.html",
        })
        (products_dir / f"{slug}.html").write_text(page, encoding="utf-8")
        entries.append((category, title, price, slug))

    # Index: category sections, alphabetical, products alphabetical within each.
    by_category = {}
    for category, title, price, slug in entries:
        by_category.setdefault(category, []).append((title, price, slug))

    sections = []
    for category in sorted(by_category, key=str.lower):
        items = []
        for title, price, slug in sorted(by_category[category], key=lambda p: p[0].lower()):
            price_span = f' <span class="li-price">{html.escape(price)}</span>' if price else ""
            items.append(
                f'      <li><a href="products/{slug}.html">{html.escape(title)}</a>{price_span}</li>'
            )
        sections.append(
            f'  <section class="category-section">\n'
            f'    <h2>{html.escape(category)}</h2>\n'
            f'    <ul class="product-list">\n' + "\n".join(items) + "\n"
            f'    </ul>\n'
            f'  </section>'
        )

    index_page = render(index_tpl, {
        "product_count": f"{len(entries):,}",
        "category_count": str(len(by_category)),
        "category_sections": "\n".join(sections),
    })
    (OUTPUT_DIR / "index.html").write_text(index_page, encoding="utf-8")

    # sitemap.xml + robots.txt so several thousand product pages get crawled.
    urls = [f"  <url><loc>{SITE_URL}/</loc></url>"] + [
        f"  <url><loc>{SITE_URL}/products/{slug}.html</loc></url>"
        for _, _, _, slug in sorted(entries, key=lambda e: e[3])
    ]
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
        (OUTPUT_DIR / "404.html").write_text(notfound_tpl, encoding="utf-8")

    # Static files served alongside the generated pages.
    shutil.copy(ROOT / "assets" / "style.css", OUTPUT_DIR / "style.css")
    for name in ("CNAME", ".nojekyll"):
        src = ROOT / name
        if src.exists():
            shutil.copy(src, OUTPUT_DIR / name)

    # Remove product pages left over from deleted/renamed products.
    stale = [p for p in products_dir.glob("*.html") if p.stem not in seen_slugs]
    for path in stale:
        path.unlink()

    print(f"Built {len(entries)} product pages + index.html into {OUTPUT_DIR.relative_to(ROOT)}/")
    print(f"Categories: {len(by_category)}")
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
