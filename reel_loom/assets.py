import json
from .errors import require, missing
from .probe import inspect_media, summarize_streams
from .values import identifier, now, canonical, text


class Assets:
    def __init__(self, db, blobs, settings):
        self.db, self.blobs, self.settings = db, blobs, settings
        self._upgrade_catalog()

    def _upgrade_catalog(self):
        with self.db.write_unit() as db:
            rows = db.execute("SELECT id,metadata FROM assets ORDER BY created,id").fetchall()
            for row in rows:
                metadata = json.loads(row["metadata"])
                if metadata.get("catalog_version") == 2:
                    continue
                streams = metadata.get("streams", [])
                if not streams:
                    continue
                summary = summarize_streams(streams)
                selected = set(summary["stream_roles"].values())
                compact = []
                for stream in streams:
                    if stream["index"] not in selected:
                        continue
                    compact.append(dict(stream))
                updated = dict(metadata)
                updated.update(summary)
                updated["streams"] = compact
                updated["catalog_origin"] = "stored_inventory"
                updated["catalog_stream_count"] = len(compact)
                if canonical(updated) == row["metadata"]:
                    continue
                db.execute("UPDATE assets SET metadata=? WHERE id=?",
                           (canonical(updated), row["id"]))
                self.db.event(db, row["id"], "catalog_compacted", {
                    "version": updated["catalog_version"],
                    "stream_count": updated["catalog_stream_count"],
                    "primary_stream": updated["primary_stream"],
                })

    def ingest(self, content, label):
        label = text(label, "label")
        require(0 < len(content) <= self.settings.max_upload, "upload_size", "Media body is empty or too large", 413)
        with self.blobs.temporary() as temp:
            temp.write_bytes(content)
            metadata = inspect_media(temp, self.settings)
            with self.db.write_unit() as db:
                blob = self.blobs.put(temp, db)
                row = db.execute("SELECT id FROM assets WHERE blob=? AND state='ready' ORDER BY created,id LIMIT 1",
                                 (blob["hash"],)).fetchone()
                if row:
                    return self.get(row["id"], db)
                asset_id = identifier("asset")
                db.execute("INSERT INTO assets VALUES(?,?,?,?,?,?)",
                           (asset_id, label, blob["hash"], canonical(metadata), "ready", now()))
                self.db.event(db, asset_id, "ingested", {"blob": blob["hash"], "metadata": metadata})
                return self.get(asset_id, db)

    def get(self, asset_id, db=None, ready=False):
        if db is None:
            with self.db.read() as read:
                return self.get(asset_id, read, ready)
        row = db.execute("SELECT * FROM assets WHERE id=?", (asset_id,)).fetchone()
        if row is None:
            raise missing("asset", asset_id)
        require(not ready or row["state"] == "ready", "asset_retired", "Source media is retired", 409)
        return {**dict(row), "metadata": json.loads(row["metadata"])}

    def list(self, state=None, limit=100):
        with self.db.read() as db:
            if state:
                rows = db.execute("SELECT id FROM assets WHERE state=? ORDER BY created DESC,id LIMIT ?", (state, limit)).fetchall()
            else:
                rows = db.execute("SELECT id FROM assets ORDER BY created DESC,id LIMIT ?", (limit,)).fetchall()
            return [self.get(row["id"], db) for row in rows]

    def retire(self, asset_id):
        with self.db.write_unit() as db:
            asset = self.get(asset_id, db)
            if asset["state"] == "ready":
                db.execute("UPDATE assets SET state='retired' WHERE id=?", (asset_id,))
                self.db.event(db, asset_id, "retired", {"blob": asset["blob"]})
            return self.get(asset_id, db)

    def restore(self, asset_id):
        with self.db.write_unit() as db:
            asset = self.get(asset_id, db)
            self.blobs.resolve(asset["blob"], verify=True)
            db.execute("UPDATE assets SET state='ready' WHERE id=?", (asset_id,))
            self.db.event(db, asset_id, "restored", {})
            return self.get(asset_id, db)
