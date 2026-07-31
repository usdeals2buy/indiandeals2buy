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
  "features": ["Feature 1", "Feature 2"]
}
```

Only `title` and `affiliate_url` are required; anything else is optional.
`slug` is optional too — it is derived from the title when omitted.

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

- Replace `YOURTAG-21` in every `affiliate_url` with the real Associates tag.
  The sample rows in `data/products.json` are placeholders with fake ASINs and
  image URLs; delete them once real products are in.
- Amazon's Associates Operating Agreement generally requires product images to
  come through the Product Advertising API rather than being hotlinked from
  `m.media-amazon.com`. The templates hotlink as specified; check this against
  the current agreement before scaling up, since image usage is the most common
  cause of Associates account issues.
