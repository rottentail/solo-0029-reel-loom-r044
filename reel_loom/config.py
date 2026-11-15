from dataclasses import dataclass
from pathlib import Path
import os
import shutil
from .errors import require


@dataclass(frozen=True)
class Settings:
    root: Path
    ffmpeg: str
    max_upload: int = 128 * 1024 * 1024
    max_seconds: int = 3600
    max_pixels: int = 3840 * 2160
    render_timeout: int = 180
    workers: int = 1
    host: str = "127.0.0.1"
    port: int = 8729

    @classmethod
    def from_env(cls):
        binary = os.environ.get("LOOM_FFMPEG") or shutil.which("ffmpeg")
        if not binary:
            try:
                import imageio_ffmpeg
                binary = imageio_ffmpeg.get_ffmpeg_exe()
            except ImportError as exc:
                raise RuntimeError("Install the project dependencies or set LOOM_FFMPEG") from exc
        root = Path(os.environ.get("LOOM_DATA", ".reel-loom")).resolve()
        workers = int(os.environ.get("LOOM_WORKERS", "1"))
        port = int(os.environ.get("LOOM_PORT", "8729"))
        require(1 <= workers <= 4, "configuration", "LOOM_WORKERS must be 1..4")
        require(1024 <= port <= 65535, "configuration", "LOOM_PORT must be 1024..65535")
        return cls(root=root, ffmpeg=binary, workers=workers, port=port,
                   host=os.environ.get("LOOM_HOST", "127.0.0.1"))

    def prepare(self):
        self.root.mkdir(parents=True, exist_ok=True)
        for part in ("blobs", "scratch"):
            (self.root / part).mkdir(exist_ok=True)
        require(Path(self.ffmpeg).is_file() or bool(shutil.which(self.ffmpeg)),
                "configuration", "FFmpeg executable is unavailable")
        return self

    def public(self):
        return {"max_upload_bytes": self.max_upload, "max_duration_seconds": self.max_seconds,
                "max_pixels": self.max_pixels, "workers": self.workers,
                "render_timeout_seconds": self.render_timeout}
