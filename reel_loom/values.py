import hashlib
import json
import math
import re
import time
import uuid
from .errors import require


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def identifier(prefix):
    return prefix + "_" + uuid.uuid4().hex


def now():
    return time.time()


def object_body(value, allowed=None, required=()):
    require(isinstance(value, dict), "invalid_body", "An object is required")
    if allowed is not None:
        unknown = set(value) - set(allowed)
        require(not unknown, "unknown_fields", "Unknown input fields", fields=sorted(unknown))
    for field in required:
        require(field in value, "missing_field", f"Missing {field}")
    return value


def text(value, field, limit=200):
    require(isinstance(value, str) and bool(value.strip()), "invalid_text", f"{field} is required")
    value = value.strip()
    require(len(value) <= limit, "text_limit", f"{field} is too long")
    return value


def integer(value, field, low=0, high=2**31-1):
    require(type(value) is int and low <= value <= high, "invalid_integer", f"Invalid {field}")
    return value


def number(value, field, low=0, high=1e12):
    require(type(value) in (int, float) and math.isfinite(value), "invalid_number", f"Invalid {field}")
    require(low <= value <= high, "number_range", f"{field} is outside supported range")
    return float(value)


def token(value, field):
    require(isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_.:-]{1,100}", value),
            "invalid_token", f"Invalid {field}")
    return value


def decode_json(raw):
    def reject_duplicate(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, "duplicate_key", "Duplicate JSON property", key=key)
            result[key] = value
        return result
    try:
        return json.loads(raw, object_pairs_hook=reject_duplicate,
                          parse_constant=lambda x: (_ for _ in ()).throw(ValueError(x)))
    except (ValueError, UnicodeError) as exc:
        from .errors import LoomError
        if isinstance(exc, LoomError):
            raise
        raise LoomError("invalid_json", "Body is not valid JSON") from exc
