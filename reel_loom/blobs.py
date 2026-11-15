from contextlib import contextmanager
import hashlib
import os
from pathlib import Path
import tempfile
from .errors import require, missing
from .values import now


class BlobStore:
    def __init__(self, root, database):
        self.root = root / "blobs"
        self.scratch = root / "scratch"
        self.db = database

    def path(self, hash_value):
        require(len(hash_value) == 64 and all(c in "0123456789abcdef" for c in hash_value),
                "invalid_digest", "Blob digest is invalid")
        return self.root / hash_value[:2] / hash_value[2:]

    @contextmanager
    def temporary(self, suffix=""):
        with tempfile.TemporaryDirectory(prefix="loom-", dir=self.scratch) as folder:
            yield Path(folder) / ("media" + suffix)

    def put(self, path, db):
        hasher = hashlib.sha256()
        size = 0
        with Path(path).open("rb") as source:
            for chunk in iter(lambda: source.read(1024*1024), b""):
                hasher.update(chunk)
                size += len(chunk)
        require(size > 0, "empty_output", "Media output is empty")
        key = hasher.hexdigest()
        destination = self.path(key)
        destination.parent.mkdir(exist_ok=True)
        if not destination.exists():
            fd, temp = tempfile.mkstemp(prefix=".incoming-", dir=destination.parent)
            try:
                with os.fdopen(fd, "wb") as target, Path(path).open("rb") as source:
                    for chunk in iter(lambda: source.read(1024*1024), b""):
                        target.write(chunk)
                    target.flush()
                    os.fsync(target.fileno())
                os.replace(temp, destination)
            finally:
                if os.path.exists(temp):
                    os.unlink(temp)
        db.execute("INSERT OR IGNORE INTO blobs VALUES(?,?,?)", (key, size, now()))
        return {"hash": key, "size": size}

    def resolve(self, key, verify=False):
        path = self.path(key)
        if not path.is_file():
            raise missing("blob", key)
        if verify:
            hasher = hashlib.sha256()
            with path.open("rb") as source:
                for chunk in iter(lambda: source.read(1024*1024), b""):
                    hasher.update(chunk)
            require(hasher.hexdigest() == key, "blob_corrupt", "Media digest differs from stored content", 409)
        return path

    def get(self, key):
        with self.db.read() as db:
            row = db.execute("SELECT * FROM blobs WHERE hash=?", (key,)).fetchone()
            if row is None:
                raise missing("blob", key)
            self.resolve(key)
            return dict(row)

    def stats(self):
        with self.db.read() as db:
            row = db.execute("SELECT COUNT(*) count,COALESCE(SUM(size),0) bytes FROM blobs").fetchone()
            return dict(row)
