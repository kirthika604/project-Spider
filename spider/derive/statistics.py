"""Statistics across a column, for analysts and researchers.

`count(moon via has_moon)` looks along a relation. These look down a column:
every accepted value of one field, across every record of an entity, so a
derivation can say where this record sits in the dataset as a whole.

    spread_vs_mean:
      on: plant
      formula: "zscore(altitude_m over plant)"
"""

from __future__ import annotations

import bisect
import math
import statistics


class Column:
    """One column of one entity type, read from the database once.

    A statistic over a column used to re-read the whole column for every row it
    was asked about - 4,000 reads of 2,000 values to fill three columns. Now
    the column is loaded once per derivation and every summary is memoised, so
    the cost grows with the number of rows, not with its square.
    """

    def __init__(self, conn, entity_type: str, field_name: str):
        rows = conn.execute(
            "SELECT a.entity_id, a.value_num FROM attributes a "
            "JOIN entities e ON e.id = a.entity_id "
            "WHERE e.type = ? AND a.name = ? AND a.status = 'accepted' "
            "AND a.value_num IS NOT NULL ORDER BY a.entity_id, a.confidence DESC",
            (entity_type, field_name)).fetchall()
        self.values = [float(r["value_num"]) for r in rows]
        self.by_entity: dict = {}
        for row in rows:                       # the most confident value per record
            self.by_entity.setdefault(row["entity_id"], float(row["value_num"]))
        self._memo: dict = {}

    def memo(self, key, compute):
        if key not in self._memo:
            self._memo[key] = compute()
        return self._memo[key]

    @property
    def ordered(self) -> list:
        return self.memo("ordered", lambda: sorted(self.values))


class Columns:
    """The columns a derivation has asked about so far."""

    def __init__(self, conn):
        self.conn = conn
        self._columns: dict = {}

    def get(self, entity_type: str, field_name: str) -> Column:
        key = (str(entity_type), str(field_name))
        if key not in self._columns:
            self._columns[key] = Column(self.conn, *key)
        return self._columns[key]

    def texts(self, entity_type: str, field_name: str) -> list:
        key = ("texts", str(entity_type), str(field_name))
        if key not in self._columns:
            rows = self.conn.execute(
                "SELECT a.value FROM attributes a JOIN entities e ON e.id = a.entity_id "
                "WHERE e.type = ? AND a.name = ? AND a.status = 'accepted' "
                "AND a.value IS NOT NULL", (str(entity_type), str(field_name))).fetchall()
            self._columns[key] = [str(r["value"]) for r in rows]
        return self._columns[key]


def build(conn, entity_id: int, columns: Columns | None = None) -> dict:
    """The statistics functions, bound to this record and this project."""
    columns = columns or Columns(conn)

    def col(field_name, entity_type) -> Column:
        return columns.get(entity_type, field_name)

    def summary(name, function, least=1):
        def run(field_name, entity_type, *extra):
            column = col(field_name, entity_type)
            if len(column.values) < least:
                return None

            def compute():
                try:
                    return round(float(function(column.values, *extra)), 6)
                except (statistics.StatisticsError, ValueError, ZeroDivisionError):
                    return None
            return column.memo((name, extra), compute)
        return run

    def percentile(field_name, entity_type, which=50):
        """The value below which that percentage of records fall."""
        values = col(field_name, entity_type).ordered
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
        column = col(field_name, entity_type)
        mine = column.by_entity.get(entity_id)
        if mine is None or len(column.values) < 2:
            return None
        mean = column.memo("mean", lambda: statistics.fmean(column.values))
        spread = column.memo("pstdev", lambda: statistics.pstdev(column.values))
        return 0.0 if not spread else round((mine - mean) / spread, 4)

    def normalize(field_name, entity_type):
        """Where this record falls between the smallest and largest, 0 to 1."""
        column = col(field_name, entity_type)
        mine = column.by_entity.get(entity_id)
        if mine is None or not column.values:
            return None
        low, high = column.ordered[0], column.ordered[-1]
        return 0.0 if high == low else round((mine - low) / (high - low), 4)

    def rank(field_name, entity_type, order="desc"):
        """This record's place in the column, 1 being the largest."""
        column = col(field_name, entity_type)
        mine = column.by_entity.get(entity_id)
        if mine is None or not column.values:
            return None
        ordered = column.ordered
        if str(order).lower() == "asc":
            return bisect.bisect_left(ordered, mine) + 1
        return len(ordered) - bisect.bisect_right(ordered, mine) + 1

    def share(field_name, entity_type):
        """This record's value as a fraction of the column's total."""
        column = col(field_name, entity_type)
        mine = column.by_entity.get(entity_id)
        total = column.memo("total", lambda: sum(column.values))
        return None if mine is None or not total else round(mine / total, 6)

    def count_distinct(field_name, entity_type):
        texts = columns.texts(entity_type, field_name)
        return len({v.strip().lower() for v in texts})

    def mode(field_name, entity_type):
        texts = columns.texts(entity_type, field_name)
        if not texts:
            return None
        try:
            return statistics.mode(texts)
        except statistics.StatisticsError:
            return None

    def correlation(first, entity_type, second, second_entity=None):
        """How strongly two columns move together, -1 to 1.

        Written `correlation(a over e, b over e)`, so both field names arrive
        as names rather than as one record's value.
        """
        del second_entity
        left, right = col(first, entity_type), col(second, entity_type)

        def compute():
            shared = [k for k in left.by_entity if k in right.by_entity]
            if len(shared) < 2:
                return None
            try:
                return round(statistics.correlation(
                    [left.by_entity[k] for k in shared],
                    [right.by_entity[k] for k in shared]), 4)
            except (statistics.StatisticsError, ValueError, ZeroDivisionError):
                return None
        return left.memo(("correlation", str(second)), compute)

    return {
        "mean": summary("mean", statistics.fmean),
        "median": summary("median", statistics.median),
        "stdev": summary("stdev", statistics.pstdev, 2),
        "variance": summary("variance", statistics.pvariance, 2),
        "total": summary("total", sum),
        "spread": summary("spread", lambda v: max(v) - min(v)),
        "smallest": summary("smallest", min), "largest": summary("largest", max),
        "records": summary("records", len),
        "percentile": percentile, "zscore": zscore, "normalize": normalize,
        "rank": rank, "share": share, "count_distinct": count_distinct,
        "mode": mode, "correlation": correlation,
    }


COLUMN_FUNCTIONS = ("mean", "median", "stdev", "variance", "total", "spread",
                    "smallest", "largest", "records", "percentile", "zscore",
                    "normalize", "rank", "share", "count_distinct", "mode",
                    "correlation")
