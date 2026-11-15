from .errors import require
from .values import object_body, integer, identifier, canonical, now


class RenderBatches:
    def __init__(self, db, planner, tickets, keys):
        self.db, self.planner, self.tickets, self.keys = db, planner, tickets, keys

    def submit(self, body, key=None):
        object_body(body, ("reel", "revision", "looks"), ("reel", "looks"))
        looks = body["looks"]
        require(isinstance(looks, list) and 1 <= len(looks) <= 12,
                "batch_size", "A render matrix requires 1..12 looks")
        with self.db.write_unit() as db:
            previous = self.keys.replay(db, "render_matrix", key, body)
            if previous:
                return previous
            snapshots, identities = [], set()
            for item in looks:
                object_body(item, ("id", "version"), ("id",))
                if "version" in item:
                    integer(item["version"], "version", 1)
                snapshot = self.planner.freeze(db, body["reel"], body.get("revision"), item["id"], item.get("version"))
                identity = (snapshot["look"]["id"], snapshot["look"]["version"])
                require(identity not in identities, "duplicate_look", "A look version cannot repeat within a matrix")
                identities.add(identity)
                snapshots.append(snapshot)
            batch_id, created, ids = identifier("matrix"), now(), []
            for snapshot in snapshots:
                ticket_id = identifier("render")
                db.execute("INSERT INTO tickets(id,reel,revision,look_id,look_version,snapshot,fingerprint,state,attempt,created,updated) "
                           "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                           (ticket_id, snapshot["reel"], snapshot["revision"], snapshot["look"]["id"],
                            snapshot["look"]["version"], canonical(snapshot), snapshot["fingerprint"], "queued", 1, created, created))
                ids.append(ticket_id)
                self.db.event(db, ticket_id, "matrix_queued", {"matrix": batch_id, "fingerprint": snapshot["fingerprint"]})
            self.db.event(db, batch_id, "matrix_submitted", {"tickets": ids})
            result = {"id": batch_id, "tickets": [self.tickets.get(x, db) for x in ids]}
            return self.keys.record(db, "render_matrix", key, body, result)

    def get(self, batch_id):
        with self.db.read() as db:
            import json
            row = db.execute("SELECT payload FROM events WHERE subject=? AND kind='matrix_submitted' ORDER BY sequence LIMIT 1", (batch_id,)).fetchone()
            require(row is not None, "not_found", "Render matrix does not exist", 404)
            ids = json.loads(row["payload"])["tickets"]
            tickets = [self.tickets.get(x, db) for x in ids]
            states = [x["state"] for x in tickets]
            terminal = {"succeeded", "failed", "cancelled"}
            if all(x == "succeeded" for x in states):
                state = "succeeded"
            elif all(x in terminal for x in states):
                state = "finished_with_failures"
            elif any(x in terminal or x == "running" for x in states):
                state = "running"
            else:
                state = "queued"
            return {"id": batch_id, "state": state, "tickets": tickets,
                    "progress": sum(x["progress"] for x in tickets)/len(tickets)}
