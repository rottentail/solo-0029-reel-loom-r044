class LoomError(Exception):
    def __init__(self, code, detail, status=422, context=None):
        super().__init__(detail)
        self.code = code
        self.detail = detail
        self.status = status
        self.context = context or {}

    def as_dict(self):
        return {"error": self.code, "detail": self.detail, "context": self.context}


def require(condition, code, detail, status=422, **context):
    if not condition:
        raise LoomError(code, detail, status, context)


def missing(kind, identifier):
    return LoomError("not_found", f"{kind} does not exist", 404, {"id": identifier})


def revision_conflict(expected, current):
    return LoomError("revision_conflict", "The reel has changed; reload before editing", 409,
                     {"expected": expected, "current": current})


class RenderCancelled(LoomError):
    def __init__(self):
        super().__init__("render_cancelled", "The render was cancelled", 409)
