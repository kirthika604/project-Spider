"""Statistics across a column, for analysts and researchers.

`count(moon via has_moon)` looks along a relation. These look down a column:
every accepted value of one field, across every record of an entity, so a
derivation can say where this record sits in the dataset as a whole.

    spread_vs_mean:
      on: plant
      formula: "zscore(altitude_m over plant)"
"""

from __future__ import annotations

import math
import statistics


def column_values(conn, entity_type: str, field_name: str) -> list[float]:
    """Every accepted number in one column, in record order."""
    rows = conn.execute(
        "SELECT a.value_num FROM attributes a JOIN entities e ON e.id = a.entity_id "
        "WHERE e.type = ? AND a.name = ? AND a.status = 'accepted' "
        "AND a.value_num IS NOT NULL ORDER BY a.entity_id",
        (entity_type, field_name)).fetchall()
    return [float(r["value_num"]) for r in rows]


def column_texts(conn, entity_type: str, field_name: str) -> list[str]:
    rows = conn.execute(
        "SELECT a.value FROM attributes a JOIN entities e ON e.id = a.entity_id "
        "WHERE e.type = ? AND a.name = ? AND a.status = 'accepted' "
        "AND a.value IS NOT NULL", (entity_type, field_name)).fetchall()
    return [str(r["value"]) for r in rows]


def this_value(conn, entity_id: int, field_name: str):
    row = conn.execute(
        "SELECT value_num FROM attributes WHERE entity_id = ? AND name = ? "
        "AND status = 'accepted' AND value_num IS NOT NULL "
        "ORDER BY confidence DESC LIMIT 1", (entity_id, field_name)).fetchone()
    return float(row["value_num"]) if row else None


def build(conn, entity_id: int) -> dict:
    """The statistics functions, bound to this record and this project."""

    def numbers(field_name, entity_type):
        return column_values(conn, str(entity_type), str(field_name))

    def _guard(values, least: int = 1):
        return values if len(values) >= least else None

    def stat(function, least=1):
        def run(field_name, entity_type, *extra):
            values = _guard(numbers(field_name, entity_type), least)
            if values is None:
                return None
            try:
                return round(float(function(values, *extra)), 6)
            except (statistics.StatisticsError, ValueError, ZeroDivisionError):
                return None
        return run

    def percentile(field_name, entity_type, which=50):
        """The value below which that percentage of records fall."""
        values = sorted(numbers(field_name, entity_type))
        if not values:
            return None
        share = max(0.0, min(100.0, float(which))) / 100
        position = share * (len(values) - 1)
        low, high = math.floor(position), math.ceil(position)
        if low == high:
            return round(values[int(position)], 6)
        weight = position - low
        return round(values[low] * (1 - weight) + values[high] * weight, 6)

    def zscore(field_name, entity_type):
        """How many standard deviations this record sits from the mean."""
        values = numbers(field_name, entity_type)
        mine = this_value(conn, entity_id, str(field_name))
        if mine is None or len(values) < 2:
            return None
        spread = statistics.pstdev(values)
        if not spread:
            return 0.0
        return round((mine - statistics.fmean(values)) / spread, 4)

    def normalize(field_name, entity_type):
        """Where this record falls between the smallest and largest, 0 to 1."""
        values = numbers(field_name, entity_type)
        mine = this_value(conn, entity_id, str(field_name))
        if mine is None or not values:
            return None
        low, high = min(values), max(values)
        if high == low:
            return 0.0
        return round((mine - low) / (high - low), 4)

    def rank(field_name, entity_type, order="desc"):
        """This record's place in the column, 1 being the largest."""
        values = sorted(numbers(field_name, entity_type),
                        reverse=str(order).lower() != "asc")
        mine = this_value(conn, entity_id, str(field_name))
        if mine is None or not values:
            return None
        return values.index(mine) + 1 if mine in values else None

    def share(field_name, entity_type):
        """This record's value as a fraction of the column's total."""
        values = numbers(field_name, entity_type)
        mine = this_value(conn, entity_id, str(field_name))
        total = sum(values)
        if mine is None or not total:
            return None
        return round(mine / total, 6)

    def count_distinct(field_name, entity_type):
        return len({v.strip().lower()
                    for v in column_texts(conn, str(entity_type), str(field_name))})

    def mode(field_name, entity_type):
        texts = column_texts(conn, str(entity_type), str(field_name))
        if not texts:
            return None
        try:
            return statistics.mode(texts)
        except statistics.StatisticsError:
            return None

    def correlation(first, entity_type, second, second_entity=None):
        """How strongly two columns move together, -1 to 1.

        Written `correlation(mass_kg over planet, radius_m over planet)`, so
        both field names arrive as names rather than as one record's value.
        """
        del second_entity
        rows = conn.execute(
            "SELECT a.entity_id, a.name, a.value_num FROM attributes a "
            "JOIN entities e ON e.id = a.entity_id WHERE e.type = ? "
            "AND a.name IN (?, ?) AND a.status = 'accepted' "
            "AND a.value_num IS NOT NULL", (str(entity_type), str(first),
                                            str(second))).fetchall()
        paired: dict = {}
        for row in rows:
            paired.setdefault(row["entity_id"], {})[row["name"]] = row["value_num"]
        xs = [v[str(first)] for v in paired.values()
              if str(first) in v and str(second) in v]
        ys = [v[str(second)] for v in paired.values()
              if str(first) in v and str(second) in v]
        if len(xs) < 2:
            return None
        try:
            return round(statistics.correlation(xs, ys), 4)
        except (statistics.StatisticsError, ValueError, ZeroDivisionError):
            return None

    return {
        "mean": stat(statistics.fmean), "median": stat(statistics.median),
        "stdev": stat(statistics.pstdev, 2), "variance": stat(statistics.pvariance, 2),
        "total": stat(sum), "spread": stat(lambda v: max(v) - min(v)),
        "smallest": stat(min), "largest": stat(max), "records": stat(len),
        "percentile": percentile, "zscore": zscore, "normalize": normalize,
        "rank": rank, "share": share, "count_distinct": count_distinct,
        "mode": mode, "correlation": correlation,
    }


COLUMN_FUNCTIONS = ("mean", "median", "stdev", "variance", "total", "spread",
                    "smallest", "largest", "records", "percentile", "zscore",
                    "normalize", "rank", "share", "count_distinct", "mode",
                    "correlation")
