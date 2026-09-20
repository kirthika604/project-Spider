"""Every function a derived column may use, in plain words.

`spider derive functions` reads this, and a test fails if a function exists in
the evaluator without an entry here - so what the help promises and what the
evaluator accepts cannot drift apart.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Function:
    name: str
    group: str
    signature: str
    what: str
    example: str
    result: str = ""          # what the example gives, when it can be shown
    given: tuple = ()         # what the example assumes: (("price", 250),)


G_ARITH, G_MATHS, G_CHOOSE = "Arithmetic and rounding", "Maths", "Choosing and comparing"
G_TEXT, G_DATES, G_UNITS = "Text", "Dates", "Units and numbers in text"
G_GEO, G_SEASON = "Geography and angles", "Calendars"
G_STATS, G_REL = "Statistics across a column", "Across related records"

CATALOGUE: list[Function] = [
    # ------------------------------------------------------ arithmetic
    Function("round", G_ARITH, "round(value, digits)", "Round to a number of decimal places.",
             "round(price * 1.18, 2)", "47.2", (("price", 40),)),
    Function("floor", G_ARITH, "floor(value)", "Round down to a whole number.", "floor(3.7)", "3"),
    Function("ceil", G_ARITH, "ceil(value)", "Round up to a whole number.", "ceil(3.2)", "4"),
    Function("abs", G_ARITH, "abs(value)", "Drop the minus sign.", "abs(-4)", "4"),
    Function("sign", G_ARITH, "sign(value)", "-1, 0 or 1 depending on the sign.", "sign(-9)", "-1"),
    Function("min", G_ARITH, "min(a, b, ...)", "The smallest of the values given.", "min(price, 100)"),
    Function("max", G_ARITH, "max(a, b, ...)", "The largest of the values given.", "max(price, 100)"),
    Function("sum", G_ARITH, "sum(a, b, ...)", "Add the values given.", "sum(price, tax, shipping)"),
    Function("avg", G_ARITH, "avg(a, b, ...)", "The average of the values given.", "avg(q1, q2, q3)"),
    Function("clamp", G_ARITH, "clamp(value, low, high)", "Keep a value between two limits.",
             "clamp(score, 0, 100)", "100", (("score", 130),)),
    Function("midpoint", G_ARITH, "midpoint(low, high)", "The middle of two numbers, e.g. a range.",
             "midpoint(altitude_m.min, altitude_m.max)"),
    # ---------------------------------------------------------- maths
    Function("sqrt", G_MATHS, "sqrt(value)", "Square root.", "sqrt(16)", "4"),
    Function("pow", G_MATHS, "pow(value, power)", "A number to a power (or use `**`).", "pow(2, 10)", "1024"),
    Function("log", G_MATHS, "log(value, base)", "Natural log, or log to a base.", "log(100, 10)", "2"),
    Function("log10", G_MATHS, "log10(value)", "Log to base 10.", "log10(1000)", "3"),
    Function("exp", G_MATHS, "exp(value)", "e to a power.", "exp(1)"),
    Function("pi", G_MATHS, "pi", "The constant pi.", "pi * radius ** 2"),
    Function("e", G_MATHS, "e", "The constant e.", "e ** 2"),
    Function("tau", G_MATHS, "tau", "2 pi.", "tau * radius"),
    # ------------------------------------------------ choosing / comparing
    Function("if", G_CHOOSE, "if(condition, then, else)", "Pick one of two values.",
             "if(rating >= 4, 'good', 'poor')", "good", (("rating", 5),)),
    Function("band", G_CHOOSE, "band(value, cutoffs, labels)",
             "Put a number into a labelled band. One more label than cutoffs.",
             "band(price, [100, 500], ['low', 'mid', 'high'])", "mid", (("price", 250),)),
    Function("coalesce", G_CHOOSE, "coalesce(a, b, ...)", "The first value that is not empty.",
             "coalesce(nickname, name)", "Asha", (("nickname", None), ("name", "Asha"))),
    Function("is_empty", G_CHOOSE, "is_empty(value)", "True when there is nothing in it.",
             "is_empty(nickname)", "True", (("nickname", None),)),
    # ------------------------------------------------------------ text
    Function("contains", G_TEXT, "contains(text, piece)", "Whether a text has a piece in it (any case).",
             "contains(availability, 'in stock')", "True", (("availability", "In stock (22 available)"),)),
    Function("startswith", G_TEXT, "startswith(text, prefix)", "Whether a text starts with a prefix.",
             "startswith(code, 'TN')"),
    Function("endswith", G_TEXT, "endswith(text, suffix)", "Whether a text ends with a suffix.",
             "endswith(email, '.gov.in')"),
    Function("word", G_TEXT, "word(text, n)", "The nth word; 1 is the first, -1 the last.",
             "word('star-rating Three', 2)", "Three"),
    Function("substr", G_TEXT, "substr(text, start, length)", "A piece of a text; start counts from 1.",
             "substr(code, 1, 3)", "TN-", (("code", "TN-123"),)),
    Function("replace", G_TEXT, "replace(text, old, new)", "Swap one piece of text for another.",
             "replace(price_text, ',', '')"),
    Function("trim", G_TEXT, "trim(text)", "Remove spaces at both ends.", "trim(name)"),
    Function("lower", G_TEXT, "lower(text)", "Lower case.", "lower(city)"),
    Function("upper", G_TEXT, "upper(text)", "Upper case.", "upper(code)"),
    Function("concat", G_TEXT, "concat(a, b, ...)", "Join values with '; ' between them.",
             "concat(street, city)"),
    Function("len", G_TEXT, "len(text)", "How many characters.", "len(name)"),
    # ------------------------------------------------------------ dates
    Function("years_between", G_DATES, "years_between(start, end)", "Years from one date to another, as a decimal.",
             "years_between(born, '2026-09-20')", "36.35", (("born", "1990-05-15"),)),
    Function("days_between", G_DATES, "days_between(start, end)", "Whole days from one date to another.",
             "days_between('2026-01-01', '2026-09-20')", "262"),
    Function("year_of", G_DATES, "year_of(date)", "The year.", "year_of('20 Sep 2026')", "2026"),
    Function("month_of", G_DATES, "month_of(date)", "The month number, 1 to 12.", "month_of('20 Sep 2026')", "9"),
    Function("day_of", G_DATES, "day_of(date)", "The day of the month.", "day_of('2026-09-20')", "20"),
    Function("weekday_of", G_DATES, "weekday_of(date)", "The name of the day.", "weekday_of('2026-09-20')", "Sunday"),
    # ------------------------------------------------ units and numbers
    Function("convert", G_UNITS, "convert(value, from, to)", "Change a number from one unit to another.",
             "convert(altitude_m, 'm', 'ft')", "9842.52", (("altitude_m", 3000),)),
    Function("number", G_UNITS, "number(text)", "The number inside a text.", "number('£51.77')", "51.77"),
    # -------------------------------------------------------- geography
    Function("distance_km", G_GEO, "distance_km(lat1, lon1, lat2, lon2)",
             "Straight-line distance over the earth's surface, in km.",
             "distance_km(latitude, longitude, 13.0827, 80.2707)"),
    Function("sin", G_GEO, "sin(radians)", "Sine.", "sin(pi / 2)", "1"),
    Function("cos", G_GEO, "cos(radians)", "Cosine.", "cos(0)", "1"),
    Function("tan", G_GEO, "tan(radians)", "Tangent.", "tan(0)", "0"),
    Function("asin", G_GEO, "asin(value)", "Inverse sine.", "asin(1)"),
    Function("acos", G_GEO, "acos(value)", "Inverse cosine.", "acos(1)", "0"),
    Function("atan", G_GEO, "atan(value)", "Inverse tangent.", "atan(1)"),
    Function("atan2", G_GEO, "atan2(y, x)", "The angle of a point, in radians.", "atan2(1, 1)"),
    Function("hypot", G_GEO, "hypot(a, b)", "The length of the hypotenuse.", "hypot(3, 4)", "5"),
    Function("radians", G_GEO, "radians(degrees)", "Degrees to radians.", "radians(180)"),
    Function("degrees", G_GEO, "degrees(radians)", "Radians to degrees.", "degrees(pi)", "180"),
    # -------------------------------------------------------- calendars
    Function("season_of", G_SEASON, "season_of(month, calendar)",
             "The season a month falls in. With no calendar it uses `season_scheme` "
             "from spider.yaml (northern, southern or india).",
             "season_of(month, 'india')", "monsoon", (("month", "July"),)),
    Function("place_parent", G_SEASON, "place_parent(name)",
             "What a place belongs to, from the places you loaded with `spider ref load`.",
             "place_parent(district)"),
    # ------------------------------------------------ across a column
    Function("mean", G_STATS, "mean(field over entity)", "The average of a column.", "mean(price over book)"),
    Function("median", G_STATS, "median(field over entity)", "The middle value of a column.", "median(price over book)"),
    Function("stdev", G_STATS, "stdev(field over entity)", "How spread out a column is.", "stdev(price over book)"),
    Function("variance", G_STATS, "variance(field over entity)", "The square of the spread.", "variance(price over book)"),
    Function("total", G_STATS, "total(field over entity)", "The sum of a column.", "total(sales over shop)"),
    Function("spread", G_STATS, "spread(field over entity)", "Largest minus smallest.", "spread(price over book)"),
    Function("smallest", G_STATS, "smallest(field over entity)", "The smallest value in a column.", "smallest(price over book)"),
    Function("largest", G_STATS, "largest(field over entity)", "The largest value in a column.", "largest(price over book)"),
    Function("records", G_STATS, "records(field over entity)", "How many values a column has.", "records(price over book)"),
    Function("percentile", G_STATS, "percentile(field over entity, p)", "The value below which p% of records fall.",
             "percentile(price over book, 90)"),
    Function("zscore", G_STATS, "zscore(field over entity)",
             "How many standard deviations THIS record is from the average.", "zscore(price over book)"),
    Function("normalize", G_STATS, "normalize(field over entity)",
             "Where this record sits between the smallest and largest, 0 to 1.", "normalize(price over book)"),
    Function("rank", G_STATS, "rank(field over entity, 'asc')", "This record's place in the column; 1 is the largest.",
             "rank(price over book)"),
    Function("share", G_STATS, "share(field over entity)", "This record's fraction of the column's total.",
             "share(sales over shop)"),
    Function("count_distinct", G_STATS, "count_distinct(field over entity)", "How many different values a column has.",
             "count_distinct(city over shop)"),
    Function("mode", G_STATS, "mode(field over entity)", "The most common value in a column.", "mode(city over shop)"),
    Function("correlation", G_STATS, "correlation(a over entity, b over entity)",
             "How strongly two columns move together, -1 to 1.",
             "correlation(price over book, rating over book)"),
    # ---------------------------------------------- related records
    Function("count", G_REL, "count(entity via relation)", "How many linked records there are.",
             "count(moon via has_moon)"),
]

BY_NAME = {f.name: f for f in CATALOGUE}
GROUPS = [G_ARITH, G_MATHS, G_CHOOSE, G_TEXT, G_DATES, G_UNITS, G_GEO, G_SEASON,
          G_STATS, G_REL]


def find(word: str) -> list[Function]:
    word = (word or "").strip().lower()
    if not word:
        return list(CATALOGUE)
    exact = [f for f in CATALOGUE if f.name == word]
    if exact:
        return exact
    return [f for f in CATALOGUE if word in f.name or word in f.what.lower()
            or word in f.group.lower()]


COOKBOOK = [
    ("A percentage", "price / total(price over book) * 100"),
    ("A ratio between two columns", "sales / staff"),
    ("A label from a number", "band(price, [100, 500], ['low', 'mid', 'high'])"),
    ("A yes/no flag", "if(zscore(price over book) < -1 and rating >= 4, 'bargain', 'no')"),
    ("How far from a point", "distance_km(latitude, longitude, 13.0827, 80.2707)"),
    ("An age from a date", "years_between(born, '2026-09-20')"),
    ("A number hiding in text", "number(price_text)"),
    ("A fallback when one is missing", "coalesce(price_offer, price_list)"),
    ("Where a record sits among the rest", "rank(price over book) / records(price over book)"),
]
