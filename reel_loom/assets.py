import json
from .errors import require, missing, LoomError
from .probe import inspect_media, summarize_streams, CATALOG_VERSION
from .values import identifier, now, canonical, text


class Assets:
    def __init__(self, db, blobs, settings):
        self.db, self.blobs, self.settings = db, blobs, settings
        self._upgrade_catalog()

    def _rebuild_metadata(self, metadata, reference):
        """Rebuild sound/picture roles from a full-stream inventory reference."""
        summary = summarize_streams(reference["streams"])
        # Drop legacy compaction markers; the rebuilt catalog carries the full
        # inventory and a current version, so no lossy-compaction signal remains.
        updated = {k: v for k, v in metadata.items()
                   if k not in ("catalog_origin", "catalog_stream_count")}
        updated.update(summary)
        # Keep the full stream inventory so roles can always be re-derived.
        updated["streams"] = reference["streams"]
        for field in ("container", "duration_tick", "bytes"):
            updated[field] = reference[field]
        return updated

    def _inventory_lost_streams(self, db, asset_id, inventory):
        """True when the reception record proves streams vanished from inventory."""
        event_row = db.execute(
            "SELECT payload FROM events WHERE subject=? AND kind='ingested' "
            "ORDER BY sequence LIMIT 1", (asset_id,)).fetchone()
        if event_row is None:
            return False
        payload = json.loads(event_row["payload"])
        original = payload.get("metadata", {}).get("streams")
        if not isinstance(original, list):
            return False
        present = {(s.get("index"), s.get("kind")) for s in inventory}
        return any((s.get("index"), s.get("kind")) not in present for s in original)

    def _upgrade_catalog(self):
        with self.db.write_unit() as db:
            rows = db.execute("SELECT id,metadata,blob FROM assets ORDER BY created,id").fetchall()
            for row in rows:
                metadata = json.loads(row["metadata"])
                if metadata.get("catalog_version") == CATALOG_VERSION:
                    continue
                # A summary is trustworthy only when it matches the full
                # inventory on record. Older catalogs marked video-first
                # sources as silent even while still storing the audio stream;
                # compacted catalogs may also have dropped the inventory, in
                # which case the flags cannot be verified from streams alone.
                inventory = metadata.get("streams", [])
                summary = summarize_streams(inventory)
                # The v2 compaction kept only role streams and tagged itself;
                # a partial inventory therefore signals a lossy catalog that
                # must be reverified against the original bytes instead of
                # trusting a self-consistent but incomplete has_audio flag.
                legacy_markers = ("catalog_origin" in metadata or "catalog_stream_count" in metadata)
                lossy_compaction = metadata.get("catalog_version") == CATALOG_VERSION - 1 and (
                    legacy_markers or self._inventory_lost_streams(db, row["id"], inventory))
                inventory_covers_roles = bool(inventory) and all(
                    any(s.get("index") == summary["stream_roles"][role] and s.get("kind") == role
                        for s in inventory)
                    for role in summary["stream_roles"])
                summary_matches = (not lossy_compaction) and inventory_covers_roles and all(
                    metadata.get(key) == summary[key] for key in
                    ("has_audio", "has_video", "stream_roles", "primary_stream"))
                if metadata.get("catalog_version") == CATALOG_VERSION - 1 and summary_matches:
                    continue
                updated, origin = None, None
                try:
                    path = self.blobs.resolve(row["blob"], verify=True)
                    fresh = inspect_media(path, self.settings)
                    updated = self._rebuild_metadata(metadata, fresh)
                    origin = "reverified_source"
                except LoomError as exc:
                    # The bytes themselves cannot settle it: consult the
                    # original reception record, which captured every stream
                    # at ingest time and proves what the source really held.
                    event_row = db.execute(
                        "SELECT payload FROM events WHERE subject=? AND kind='ingested' "
                        "ORDER BY sequence LIMIT 1", (row["id"],)).fetchone()
                    if event_row is not None:
                        payload = json.loads(event_row["payload"])
                        if payload.get("blob") == row["blob"] and isinstance(payload.get("metadata"), dict):
                            updated = self._rebuild_metadata(metadata, payload["metadata"])
                            origin = "ingest_record"
                    if updated is None:
                        # Never rewrite an unverifiable catalog into a guess:
                        # a damaged audio source must not become "legal silence".
                        self.db.event(db, row["id"], "catalog_repair_skipped", {
                            "reason": exc.code,
                            "detail": "Original bytes unavailable or undecodable and no "
                                      "matching reception record; stored catalog left untouched"})
                        continue
                updated["catalog_origin"] = origin
                if canonical(updated) == row["metadata"]:
                    continue
                before = {"has_audio": metadata.get("has_audio"), "has_video": metadata.get("has_video")}
                db.execute("UPDATE assets SET metadata=? WHERE id=?",
                           (canonical(updated), row["id"]))
                self.db.event(db, row["id"], "catalog_repaired", {
                    "version": CATALOG_VERSION,
                    "origin": origin,
                    "before": before,
                    "after": {"has_audio": updated["has_audio"], "has_video": updated["has_video"]},
                    "stream_count": len(updated["streams"]),
                    "blob": row["blob"],
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
                rows = db.execute("SELECT id FROM assets WHERE state=? ORDER BY created DESC,id LIMIT ?",
                                  (state, limit)).fetchall()
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
