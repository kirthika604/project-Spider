# Demo: a real website that has nothing to do with plants

`books.toscrape.com` is a public sandbox built for scraper practice, so it is
fair game. This project crawls its catalogue: 100 books with price, rating,
stock and category.

```bash
mkdir /tmp/books && cd /tmp/books
cp <this folder>/spider.yaml .
spider init && spider check && spider crawl && spider build
```

It is a good test of the parts a plants demo never touches:

| The page has | So the project uses |
| --- | --- |
| a rating that is a CSS **class**, `star-rating Three`, not text | `extract: ["p.star-rating@class"]` - read an attribute |
| the price written `£51.77` | `unit: gbp` - stays in pounds; it is **not** converted to anything |
| list pages that also mention prices | `upc` is `required`, so a page without one is not a book |
| a UPC as identity, a title as the name | `label: title` |
| a rating word, not a number | `method: rules` turning `Three` into `3` |

Derived columns, all written by the user:

```yaml
derived:
  rating:            # "star-rating Three" -> 3
    method: rules
    rules: [{if: "contains(rating_class, 'One')", then: 1}, ...]
  price_usd:
    formula: "price_gbp * 1.27"      # the rate is YOUR choice - Spider has none
  value_for_money:
    formula: "rating / price_gbp * 10"
```

Then try your own without editing the file by hand:

```bash
spider derive try "rating / price_gbp * 10 * (1 + zscore(rating over book))"
spider derive add bargain "if(zscore(price_gbp over book) < -1 and rating >= 4, 'yes', 'no')" --on book
```

Change `seeds` to crawl more listing pages (there are 50; each has 20 books).
The site allows it, but keep `delay_seconds` polite on any site that is not a
sandbox.
