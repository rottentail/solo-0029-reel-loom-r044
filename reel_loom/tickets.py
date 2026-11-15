import json
from .errors import require, missing
from .values import identifier, now, canonical, object_body, integer


class Tickets:
    def __init__(self, db, planner, keys):
        self.db, self.planner, self.keys = db, planner, keys

    def enqueue(self, body, key=None):
        object_body(body, ("reel", "revision", "look", "look_version"), ("reel", "look"))
        with self.db.write_unit() as db:
            previous = self.keys.replay(db, "tickets", key, body)
            if previous:
                return previous
            snapshot = self.planner.freeze(db, body["reel"], body.get("revision"),
                                           body["look"], body.get("look_version"))
            ticket_id, created = identifier("render"), now()
            db.execute("INSERT INTO tickets(id,reel,revision,look_id,look_version,snapshot,fingerprint,state,attempt,created,updated) "
                       "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                       (ticket_id, snapshot["reel"], snapshot["revision"], snapshot["look"]["id"],
                        snapshot["look"]["version"], canonical(snapshot), snapshot["fingerprint"], "queued", 1, created, created))
            self.db.event(db, ticket_id, "queued", {"fingerprint": snapshot["fingerprint"]})
            result = self.get(ticket_id, db)
            return self.keys.record(db, "tickets", key, body, result)

    def get(self, ticket_id, db=None):
        if db is None:
            with self.db.read() as read:
                return self.get(ticket_id, read)
        row = db.execute("SELECT * FROM tickets WHERE id=?", (ticket_id,)).fetchone()
        if row is None:
            raise missing("render ticket", ticket_id)
        result = dict(row)
        result["snapshot"] = json.loads(result["snapshot"])
        result["error"] = json.loads(result["error"]) if result["error"] else None
        result["cancel"] = bool(result["cancel"])
        result["artifacts"] = [dict(item) for item in db.execute(
            "SELECT id,kind,blob,metadata FROM artifacts WHERE ticket=? ORDER BY created,id", (ticket_id,))]
        for item in result["artifacts"]:
            item["metadata"] = json.loads(item["metadata"])
        return result

    def list(self, state=None):
        with self.db.read() as db:
            if state:
                rows = db.execute("SELECT id FROM tickets WHERE state=? ORDER BY created DESC,id LIMIT 100", (state,))
            else:
                rows = db.execute("SELECT id FROM tickets ORDER BY created DESC,id LIMIT 100")
            return [self.get(row["id"], db) for row in rows]

    def claim(self):
        with self.db.write_unit() as db:
            row = db.execute("SELECT id FROM tickets WHERE state='queued' ORDER BY created,id LIMIT 1").fetchone()
            if row is None:
                return None
            db.execute("UPDATE tickets SET state='running',updated=? WHERE id=? AND state='queued'", (now(), row["id"]))
            self.db.event(db, row["id"], "started", {})
            return self.get(row["id"], db)

    def cancellation_requested(self, ticket_id):
        with self.db.read() as db:
            row = db.execute("SELECT cancel FROM tickets WHERE id=?", (ticket_id,)).fetchone()
            return row is None or bool(row["cancel"])

    def progress(self, ticket_id, value):
        with self.db.write_unit() as db:
            db.execute("UPDATE tickets SET progress=MAX(progress,?),updated=? WHERE id=? AND state='running'",
                       (min(value, .99), now(), ticket_id))

    def cancel(self, ticket_id):
        with self.db.write_unit() as db:
            ticket = self.get(ticket_id, db)
            if ticket["state"] == "queued":
                db.execute("UPDATE tickets SET state='cancelled',cancel=1,updated=? WHERE id=?", (now(), ticket_id))
            elif ticket["state"] == "running":
                db.execute("UPDATE tickets SET cancel=1,updated=? WHERE id=?", (now(), ticket_id))
            else:
                return ticket
            self.db.event(db, ticket_id, "cancel_requested", {})
            return self.get(ticket_id, db)

    def fail(self, ticket_id, error):
        with self.db.write_unit() as db:
            ticket = self.get(ticket_id, db)
            if ticket["state"] != "running":
                return ticket
            state = "cancelled" if ticket["cancel"] or error.code == "render_cancelled" else "failed"
            db.execute("UPDATE tickets SET state=?,error=?,updated=? WHERE id=?",
                       (state, canonical(error.as_dict()), now(), ticket_id))
            self.db.event(db, ticket_id, state, error.as_dict())
            return self.get(ticket_id, db)

    def retry(self, ticket_id):
        with self.db.write_unit() as db:
            ticket = self.get(ticket_id, db)
            require(ticket["state"] in ("failed", "cancelled"), "retry_state", "Only failed or cancelled renders can retry", 409)
            db.execute("UPDATE tickets SET state='queued',attempt=attempt+1,progress=0,cancel=0,error=NULL,updated=? WHERE id=?",
                       (now(), ticket_id))
            self.db.event(db, ticket_id, "retried", {"attempt": ticket["attempt"]+1})
            return self.get(ticket_id, db)

    def recover(self):
        with self.db.write_unit() as db:
            rows = db.execute("SELECT id,cancel FROM tickets WHERE state='running'").fetchall()
            for row in rows:
                state = "cancelled" if row["cancel"] else "failed"
                error = {"error": "worker_interrupted", "detail": "Previous worker stopped before committing output", "context": {}}
                db.execute("UPDATE tickets SET state=?,error=?,updated=? WHERE id=?",
                           (state, canonical(error), now(), row["id"]))
                self.db.event(db, row["id"], "recovered", {"state": state})
            return {"interrupted": len(rows)}
