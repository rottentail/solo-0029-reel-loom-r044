import json
from .errors import require


class Lineage:
    def __init__(self, db, tickets):
        self.db, self.tickets = db, tickets

    def trace(self, ticket_id):
        with self.db.read() as db:
            ticket = self.tickets.get(ticket_id, db)
            snapshot = ticket["snapshot"]
            nodes, edges = {}, []
            def add(key, kind, data):
                nodes[key] = {"id": key, "kind": kind, "data": data}
                return key
            render = add(ticket_id, "render", {"state": ticket["state"], "fingerprint": ticket["fingerprint"]})
            revision = add(f'{ticket["reel"]}@{ticket["revision"]}', "reel_revision",
                           {"hash": snapshot["reel_hash"], "duration_tick": snapshot["document"]["duration_tick"]})
            edges.append({"from": render, "to": revision, "relation": "renders"})
            look = snapshot["look"]
            look_key = add(f'look:{look["id"]}@{look["version"]}', "look_version", {"hash": look["hash"], "spec": look["spec"]})
            edges.append({"from": render, "to": look_key, "relation": "uses_look"})
            for source in snapshot["sources"].values():
                source_key = add(source["id"], "source_media", {"metadata": source["metadata"]})
                blob_key = add(source["blob"], "blob", {})
                edges.append({"from": revision, "to": source_key, "relation": "includes"})
                edges.append({"from": source_key, "to": blob_key, "relation": "content"})
            for artifact in ticket["artifacts"]:
                key = add(artifact["id"], artifact["kind"], artifact["metadata"])
                blob_key = add(artifact["blob"], "blob", {})
                edges.extend([{"from": key, "to": render, "relation": "produced_by"},
                              {"from": key, "to": blob_key, "relation": "content"}])
            for row in db.execute("SELECT id,blob,fingerprint FROM imprints WHERE ticket=? ORDER BY created,id", (ticket_id,)):
                key = add(row["id"], "imprint", {"fingerprint": row["fingerprint"]})
                blob_key = add(row["blob"], "blob", {})
                edges.extend([{"from": key, "to": render, "relation": "packages"},
                              {"from": key, "to": blob_key, "relation": "content"}])
            require(all(e["from"] in nodes and e["to"] in nodes for e in edges), "lineage_closure", "Incomplete rendition lineage", 409)
            return {"ticket": ticket_id, "nodes": sorted(nodes.values(), key=lambda x: x["id"]),
                    "edges": sorted(edges, key=lambda x: (x["from"],x["relation"],x["to"]))}
