from .values import now


class Resources:
    def __init__(self, db, blobs):
        self.db, self.blobs = db, blobs

    def sweep(self, dry_run=True):
        with self.db.write_unit() as db:
            protected = {row[0] for row in db.execute("SELECT blob FROM assets UNION SELECT blob FROM artifacts UNION SELECT blob FROM imprints")}
            unreferenced = db.execute("SELECT hash,size FROM blobs ORDER BY hash").fetchall()
            candidates = [dict(row) for row in unreferenced if row["hash"] not in protected]
            removed = []
            if not dry_run:
                for row in candidates:
                    path = self.blobs.path(row["hash"])
                    path.unlink(missing_ok=True)
                    db.execute("DELETE FROM blobs WHERE hash=?", (row["hash"],))
                    removed.append(row["hash"])
                self.db.event(db, "resources", "swept", {"removed": removed})
            return {"dry_run": dry_run, "protected_count": len(protected), "candidates": candidates,
                    "candidate_bytes": sum(row["size"] for row in candidates), "removed": removed}

    def reconcile(self):
        with self.db.read() as db:
            rows = db.execute("SELECT hash,size FROM blobs ORDER BY hash").fetchall()
            missing, bad_size = [], []
            for row in rows:
                path = self.blobs.path(row["hash"])
                if not path.is_file():
                    missing.append(row["hash"])
                elif path.stat().st_size != row["size"]:
                    bad_size.append(row["hash"])
            known = {row["hash"] for row in rows}
        orphaned = []
        for folder in self.blobs.root.iterdir():
            if not folder.is_dir() or len(folder.name) != 2:
                continue
            for path in folder.iterdir():
                key = folder.name + path.name
                if path.is_file() and len(key) == 64 and key not in known:
                    orphaned.append({"hash": key, "bytes": path.stat().st_size})
        return {"consistent": not missing and not bad_size, "missing": missing,
                "bad_size": bad_size, "unregistered_blobs": orphaned}
