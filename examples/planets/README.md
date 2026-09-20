# Demo: the solar system, and the numbers you work out from it

A dataset where most columns are not on any page. Two sites state a planet's
**mass** and **mean radius**; everything else is calculated.

| Site | Port | Tier | What it gives |
| --- | --- | --- | --- |
| Observatory | 8021 | 1 | all six planets, measured values in scientific notation |
| University | 8022 | 2 | the inner four, the same facts rounded differently |

## Run it

```bash
./serve_demo_sites.sh          # leave running
```

```bash
mkdir /tmp/planets && cd /tmp/planets
cp <this folder>/{spider.yaml,gold.csv,moons.csv} .
spider init && spider ref load moons.csv
spider check && spider crawl && spider build
spider report --gold gold.csv
```

## What it exercises

| Derived column | Formula | Shows |
| --- | --- | --- |
| `volume_m3` | `(4 / 3) * pi * radius_m ** 3` | a constant and a power |
| `density_kg_m3` | `mass_kg / volume_m3` | **a derivation that reads another derivation** |
| `surface_gravity_ms2` | `6.674e-11 * mass_kg / radius_m ** 2` | arithmetic on scientific notation |
| `escape_velocity_kms` | `sqrt(2 * 6.674e-11 * mass_kg / radius_m) / 1000` | `sqrt` |
| `orbital_period_years` | `convert(orbital_period_days, 'day', 'year')` | unit conversion |
| `log_mass` | `log10(mass_kg)` | a maths function |
| `size_class` | bands on the radius | `method: lookup` |
| `moon_count` | `count(moon via has_moon)` | an aggregate over a relation |

`density_kg_m3` is the interesting one: it needs `volume_m3`, which is itself
calculated, so the engine has to order them. Get that wrong and density is
empty.

## The test that matters

`gold.csv` holds **published NASA figures** - not values from these formulas -
so the check is against physics rather than against itself:

```
Accuracy test against 19 known facts
  found        19/19 (100.0% coverage)
  correct      19/19 (100.0%)
  high confidence (>= 0.8): 17/17 (100.0%)  target 90%
```

Earth comes out at 5513 kg/m³ (published 5514), 9.82 m/s² (9.81) and
11.19 km/s (11.19), from nothing but a mass and a radius read off a web page.

## Also worth looking at

- **Agreement.** Both sites give Earth's mass, written differently. They are
  read as one value, so it scores 0.95 rather than the 0.80 a single tier 1
  source would earn - and `spider report` shows the university's 19 confirmed
  values under `values_agreed`, even though it does not own a single row.
- **Scientific notation** survives the whole chain: `5.972e24` is stored as
  `5.972e+24`, not as an integer with nineteen invented digits.
