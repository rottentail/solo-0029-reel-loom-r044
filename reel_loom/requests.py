import json
from .values import canonical, digest, token, now
from .errors import require


class RequestKeys:
    def replay(self, db, scope, key, body):
        if key is None:
            return None
        token(key, "Idempotency-Key")
        row = db.execute("SELECT * FROM request_keys WHERE scope=? AND key=?", (scope, key)).fetchone()
        if row is None:
            return None
        require(row["fingerprint"] == digest(body), "idempotency_conflict",
                "This key was already used with different content", 409)
        return json.loads(row["response"])

    def record(self, db, scope, key, body, result):
        if key is not None:
            db.execute("INSERT INTO request_keys VALUES(?,?,?,?,?)",
                       (scope, key, digest(body), canonical(result), now()))
        return result
