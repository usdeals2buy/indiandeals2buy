# indiandeals2buy.com

A static Amazon India affiliate deals site. One Python script turns
`data/products.json` into plain pre-generated HTML that GitHub Pages serves
directly. No framework, no database, no build toolchain.

## Layout

```
data/products.json            source of truth for every product
templates/*.html.template     index, product and 404 page templates
assets/style.css              the one shared stylesheet
build.py                      generates the whole site
CNAME                         indiandeals2buy.com
.nojekyll                     tells GitHub Pages to skip Jekyll
docs/                         GENERATED — this is what gets served
```

`docs/` is the published folder (GitHub Pages can serve `/docs` from the
default branch without a second branch or an Actions workflow). Everything in
it is generated; edit the templates or the data, never `docs/` directly.

## Adding products

1. Append objects to `data/products.json`:

```json
{
  "id": "B08XYZ1234",
  "slug": "wireless-earbuds-xyz",
  "title": "Wireless Earbuds XYZ",
  "category": "Electronics",
  "price": "₹1,299",
  "image_url": "https://m.media-amazon.com/images/...",
  "affiliate_url": "https://www.amazon.in/dp/B08XYZ1234?tag=onlinedealsat-21",
  "description": "Short product description.",
  "features": ["Feature 1", "Feature 2"],
  "featured": true
}
```

Only `title` and `affiliate_url` are required; anything else is optional.

- **`affiliate_url`** may include the tag as shown above, or be a plain Amazon
  link with no tag at all — both end up identical. `build.py` rewrites every
  `amazon.*` URL to carry the Associates tag (`onlinedealsat-21`), replacing any
  wrong or missing tag. Non-Amazon URLs are left untouched. To change the tag,
  edit `AFFILIATE_TAG` at the top of `build.py` — it is defined in one place.
- **`price`** is optional and safest left empty unless you keep it current.
  Products without one show "See current price on Amazon" instead, which avoids
  displaying a stale price.
- **`featured: true`** promotes a product to the image-card grid at the top of
  the index, capped at `MAX_FEATURED` (12). Everything else stays a plain text
  link so the page remains light with thousands of products.
- **`slug`** is derived from the title when omitted.

### Or pull them from the Product Advertising API

`fetch_products.py` fills in verified titles, current prices, licensed image
URLs and feature bullets, so you only supply ASINs. Standard library only.

```sh
export PAAPI_ACCESS_KEY="AKIA..."      # never commit these
export PAAPI_SECRET_KEY="..."

python3 fetch_products.py B08XYZ1234 B07ABC5678   # add or update
python3 fetch_products.py --from-file asins.txt   # one ASIN per line
python3 fetch_products.py --refresh               # re-fetch everything
python3 fetch_products.py --refresh --dry-run     # preview changes
```

`category`, `featured` and `slug` are yours — the fetcher never overwrites
them, and it will not blank an existing value with an empty API field. New
products arrive with an empty category, which the script calls out.

Using the API also settles the image-licensing question below: the URLs it
returns are the ones Amazon intends you to display.

**Re-run `--refresh` on a schedule.** Prices go stale, and the Associates
agreement expects displayed prices to be current. Anything fetched more than
a day or so ago is worth refreshing before it is published.

2. Run the build and push:

```sh
python3 build.py
git add -A && git commit -m "Add products" && git push
```

New pages are live once GitHub Pages finishes deploying (usually under a
minute). Rebuilding 6,000 products takes about two seconds.

## What build.py does

- Writes one page per product to `docs/products/{slug}.html`.
- Writes `docs/index.html` grouped by category, with a vanilla-JS text filter.
- Writes `sitemap.xml` and `robots.txt`, and copies `style.css`, `CNAME`
  and `.nojekyll` into `docs/`.
- Slugifies titles (lowercase, ASCII, hyphenated) and de-duplicates collisions
  by appending `-2`, `-3`, … Falls back to the product `id` when a title has no
  ASCII characters.
- HTML-escapes every value from the data file, so product text cannot break the
  markup.
- Deletes product pages left behind by removed or renamed products.
- Skips entries missing `title` or `affiliate_url` and reports them.

Sample summary output:

```
Built 6006 product pages + index.html into docs/
Categories: 9
Duplicate slugs renamed (2):
  duplicate-slug-product -> duplicate-slug-product-2
Skipped products (1):
  products[6005]: missing affiliate_url
```

## Deployment

GitHub repo → Settings → Pages:

- **Source:** Deploy from a branch
- **Branch:** `main`, folder `/docs`
- **Custom domain:** `indiandeals2buy.com`
- Tick **Enforce HTTPS** once the certificate is issued (can take up to 24h
  after DNS resolves).

### DNS at Squarespace

Four A records on the apex `indiandeals2buy.com`:

```
185.199.108.153
185.199.109.153
185.199.110.153
185.199.111.153
```

One CNAME record: `www` → `usdeals2buy.github.io`

These four addresses were confirmed by live DNS resolution of a
`*.github.io` host at build time. They are stable but not permanent — re-check
GitHub's "Managing a custom domain for your GitHub Pages site" documentation if
the site ever stops resolving.

## Before launch

- The seeded products link to Amazon **search results** rather than specific
  product pages, because their ASINs were never verified. Those links work and
  carry the tag, but a direct `https://www.amazon.in/dp/<ASIN>` link converts far
  better. Replace them as you confirm each ASIN.
- `image_url` is empty on the seeded products, so featured cards show a lettered
  tile instead of a broken image. Fill it in to get real product photos.
- Amazon's Associates Operating Agreement generally requires product images to
  come through the Product Advertising API rather than being hotlinked from
  `m.media-amazon.com`. The templates hotlink as specified; check this against
  the current agreement before scaling up, since image usage is the most common
  cause of Associates account issues.
