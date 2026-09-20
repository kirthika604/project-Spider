# Demo: every cafe in Chennai, with coordinates - from nothing

You do **not** need a list of names. Name an area and what you want in it, and
Spider asks OpenStreetMap:

```yaml
sources:
  items:
    - id: osm_cafes
      type: osm
      area: Chennai            # looked up for you and turned into a bounding box
      tags: {amenity: cafe}    # what to fetch
```

```bash
mkdir /tmp/chennai && cd /tmp/chennai
cp <this folder>/spider.yaml .
spider init && spider check && spider crawl && spider build
```

That fetched **244 real cafes** with their names, coordinates and (where
OpenStreetMap has it) cuisine. Everything else in `spider.yaml` is worked out
from the two coordinates:

| Column | How |
| --- | --- |
| `distance_from_centre_km` | `distance_km(latitude, longitude, 13.0827, 80.2707)` |
| `zone` | central / inner / outer, by distance |
| `distance_rank` | `rank(distance_from_centre_km over cafe, 'asc')` - across all 244 |

## Change it to what you want

- **A different thing**: `tags: {amenity: restaurant}`, `{shop: bakery}`,
  `{leisure: park}`, `{tourism: museum}` ... any OpenStreetMap tag. Several at
  once: `tags: ["amenity=cafe", "amenity=restaurant"]`.
- **A different place**: `area: Bengaluru`. Or a rectangle: `bbox: [s, w, n, e]`.
- **A different centre**: change the two numbers in `distance_from_centre_km`.
- **More columns**: `spider derive functions` lists everything a formula can
  use; `spider derive try "..."` tests one on your real cafes before you keep it.

## Things worth knowing

- 44 of the 244 have no name in OpenStreetMap. They are kept, and named by
  their OSM id, because a place is still a place; delete `required: true` on
  `osm_id` and add one on `name` if you only want named ones.
- OpenStreetMap is edited by volunteers, so it is tier 2 here (`tier: 2`).
  `spider explain <name> latitude` links to the exact OSM record.
- The public servers are free and shared. Spider retries and fails over
  between three of them, and caches the answer, so a rebuild asks for nothing.
  If all are busy it says so and tells you to try again.
- For very large areas prefer several small `bbox` sources over one huge query;
  the free servers time out on big ones.
