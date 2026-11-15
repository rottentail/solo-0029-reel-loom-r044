import fcntl
from .errors import LoomError


class InstanceLock:
    def __init__(self, root):
        self.path = root / "reel_loom.lock"
        self.handle = None

    def acquire(self):
        self.handle = self.path.open("a+")
        try:
            fcntl.flock(self.handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            self.handle.close()
            self.handle = None
            raise LoomError("instance_active", "Another service is using this media workspace", 409) from exc
        return self

    def close(self):
        if self.handle is not None:
            fcntl.flock(self.handle, fcntl.LOCK_UN)
            self.handle.close()
            self.handle = None
