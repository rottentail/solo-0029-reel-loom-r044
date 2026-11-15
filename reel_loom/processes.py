import os
from pathlib import Path
import signal
import subprocess
import tempfile
import threading
import time
from .errors import LoomError, RenderCancelled, require


class Processes:
    def __init__(self, settings):
        self.settings = settings
        self.slots = threading.BoundedSemaphore(settings.workers + 1)

    def command(self, args):
        return [self.settings.ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
                "-threads", "1", "-filter_threads", "1", "-filter_complex_threads", "1"] + list(args)

    def _stop(self, process):
        if process.poll() is not None:
            return
        try:
            if os.name == "posix":
                os.killpg(process.pid, signal.SIGTERM)
            else:
                process.terminate()
            process.wait(timeout=3)
        except (ProcessLookupError, subprocess.TimeoutExpired):
            if process.poll() is None:
                process.kill()
                process.wait(timeout=3)

    def run(self, args, cancelled=lambda: False, progress=lambda value: None, duration=None):
        deadline = time.monotonic() + self.settings.render_timeout
        acquired = False
        while not acquired:
            if cancelled():
                raise RenderCancelled()
            require(time.monotonic() < deadline, "encoder_capacity", "Encoder capacity wait expired", 503)
            acquired = self.slots.acquire(timeout=0.1)
        process = None
        try:
            with tempfile.TemporaryFile() as errors, tempfile.TemporaryFile() as status:
                command = self.command(["-progress", "pipe:1", "-nostats"] + list(args))
                process = subprocess.Popen(command, stdout=status, stderr=errors,
                                           start_new_session=os.name == "posix")
                last = 0
                while process.poll() is None:
                    if cancelled():
                        self._stop(process)
                        raise RenderCancelled()
                    if time.monotonic() >= deadline:
                        self._stop(process)
                        raise LoomError("encoder_timeout", "FFmpeg exceeded the processing deadline", 504)
                    end = status.tell()
                    if end != last and duration:
                        status.seek(0)
                        lines = status.read().decode(errors="replace").splitlines()
                        values = [line.split("=",1)[1] for line in lines if line.startswith("out_time_us=")]
                        if values and values[-1].isdigit():
                            progress(min(0.98, int(values[-1])/1_000_000/duration))
                        status.seek(0, 2)
                        last = end
                    time.sleep(0.05)
                errors.seek(0, 2)
                size = errors.tell()
                errors.seek(max(0, size-6000))
                detail = errors.read().decode(errors="replace")
                require(process.returncode == 0, "encoder_failed", "FFmpeg could not produce this rendition",
                        422, stderr=detail)
                if cancelled():
                    raise RenderCancelled()
                progress(1)
                return {"exit_code": process.returncode, "stderr": detail}
        finally:
            if process is not None:
                self._stop(process)
            self.slots.release()

    def decode_audio(self, path, output, sample_rate=16000):
        self.run(["-protocol_whitelist", "file,pipe", "-i", str(path), "-vn", "-map", "0:a:0",
                  "-ac", "1", "-ar", str(sample_rate), "-c:a", "pcm_s16le", "-f", "s16le", str(output)])
        require(Path(output).is_file(), "decode_output", "Audio decoder produced no output")
        return Path(output)
