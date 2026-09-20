# Demo: places in Chennai, with their coordinates

The shape most projects actually take: **you have a list of things, and the
facts you want are not printed on any page.**

No web page reliably states a latitude. So the list is your source, the
coordinates come from OpenStreetMap through a connector, and every other
column is worked out from those two numbers.

## Run it

```bash
mkdir /tmp/chennai && cd /tmp/chennai
cp <this folder>/{spider.yaml,places.csv} .
spider init
spider check
spider crawl          # reads places.csv - a tier 0 source, because you vouch for it
spider build          # geocodes, then calculates
spider export
```

There is no ordering to remember: `build` reads the sources, works out the
records, asks OpenStreetMap, then derives. Answers are cached, so building
again asks for nothing.

## What comes out

| name | lat | lon | km from centre | side | zone | rank |
| --- | --- | --- | --- | --- | --- | --- |
| Chennai Central | 13.0826 | 80.2763 | 0.61 | east | central | 1 |
| Fort St George | 13.0799 | 80.2847 | 1.55 | east | central | 2 |
| Marina Beach | 13.0533 | 80.2833 | 3.55 | south | inner | 5 |
| Guindy National Park | 13.0000 | 80.2280 | 10.29 | south | outer | 15 |

## Use it for your own project

1. **Replace `places.csv`** with your own list. Only the `map:` block needs to
   match your column headings.
2. **Or read the names off a page** instead: set `sources.mode: start_here`,
   add `seeds`, and give the `name` field an `extract:` rule. The connector
   fills the coordinates either way.
3. **Change the centre point** in `distance_from_centre_km` to wherever you
   are measuring from.

## The derivations, and why each is there

| Column | How | Why it is interesting |
| --- | --- | --- |
| `distance_from_centre_km` | `distance_km(latitude, longitude, 13.0827, 80.2707)` | the great-circle distance, written for you |
| `bearing_deg` | trigonometry on the two points | which way out of the centre it lies |
| `side_of_city` | bands on the bearing | north / east / south / west |
| `zone` | bands on the distance | central / inner / outer |
| `distance_rank` | `rank(distance_from_centre_km over place, 'asc')` | this place's position in the set |
| `further_than_typical` | `zscore(distance_from_centre_km over place)` | how unusual its distance is |
| `median_distance_km` | `median(distance_from_centre_km over place)` | the same on every row, for comparison |

The last three read a whole **column**, not one record - that is the
`over <entity>` form, and it is what makes the export useful in a notebook.

## Notes

- `sanity: [12.6, 13.6]` on the latitude is Chennai's bounding box. A place
  that geocodes somewhere else entirely goes to the review queue instead of
  into your dataset.
- OpenStreetMap's Nominatim asks for at most one request a second and a real
  User-Agent. Spider honours both, and caches every answer.
- Coordinates arrive with a source and an evidence line, like any other
  value: `spider explain "Marina Beach" latitude` shows the OSM record it
  came from.
