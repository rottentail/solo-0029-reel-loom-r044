from fractions import Fraction
from .errors import require
from .values import number

TICKS_PER_SECOND = 1_000_000


def ticks(value, field="time"):
    number(value, field)
    return round(Fraction(str(value)) * TICKS_PER_SECOND)


def seconds(value):
    return float(Fraction(value, TICKS_PER_SECOND))


def rational(value):
    try:
        result = Fraction(str(value))
    except (ValueError, ZeroDivisionError) as exc:
        from .errors import LoomError
        raise LoomError("invalid_rate", "Frame rate must be rational") from exc
    require(0 < result <= 120, "invalid_rate", "Frame rate must be 0..120")
    return result


def range_ticks(start, end, duration):
    a = ticks(start, "in")
    b = ticks(end, "out")
    require(a < b, "empty_slice", "A slice must have positive length")
    require(b <= duration, "slice_bounds", "Slice extends beyond source media", duration=seconds(duration))
    return a, b


def decimal(value):
    return f"{seconds(value):.6f}"


def intersect(a, b, c, d):
    start, end = max(a, c), min(b, d)
    return (start, end) if start < end else None


def project_interval(start, end, slices):
    result = []
    for index, item in enumerate(slices):
        overlap = intersect(start, end, item["in_tick"], item["out_tick"])
        if overlap:
            a, b = overlap
            offset = item["at_tick"] - item["in_tick"]
            result.append({"slice": index, "start_tick": a+offset, "end_tick": b+offset})
    return result


def stamp(value, separator=","):
    ms = round(Fraction(value, 1000))
    seconds_total, millis = divmod(ms, 1000)
    minutes_total, second = divmod(seconds_total, 60)
    hour, minute = divmod(minutes_total, 60)
    return f"{hour:02}:{minute:02}:{second:02}{separator}{millis:03}"
