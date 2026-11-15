import json
import math
from .errors import missing, require
from .probe import compatible_audio
from .samples import read_pcm, describe_samples, silence_intervals, waveform_buckets
from .values import digest, canonical, identifier, now, object_body, integer, number


class AudioAnalysis:
    def __init__(self, db, assets, blobs, processes):
        self.db, self.assets, self.blobs, self.processes = db, assets, blobs, processes

    def normalize(self, kind, raw):
        if kind == "loudness":
            object_body(raw, ())
            return {}
        if kind == "signal":
            object_body(raw, ("threshold_db", "minimum_silence"))
            return {"threshold_db": number(raw.get("threshold_db", -40), "threshold_db", -100, -1),
                    "minimum_silence": number(raw.get("minimum_silence", .2), "minimum_silence", .01, 60)}
        require(kind == "waveform", "analysis_kind", "Unknown audio analysis")
        object_body(raw, ("buckets",))
        return {"buckets": integer(raw.get("buckets", 200), "buckets", 1, 2000)}

    def run(self, asset_id, kind, raw):
        settings = self.normalize(kind, raw)
        asset = self.assets.get(asset_id, ready=True)
        compatible_audio(asset["metadata"])
        fingerprint = digest({"blob": asset["blob"], "kind": kind, "settings": settings, "algorithm": 1})
        with self.db.read() as db:
            existing = db.execute("SELECT id FROM analyses WHERE fingerprint=?", (fingerprint,)).fetchone()
            if existing:
                return self.get(existing["id"], db)
        if kind == "loudness":
            result = self.measure_loudness(asset)
            return self.persist(asset_id, kind, settings, result, fingerprint)
        rate = 16000
        with self.blobs.temporary(".pcm") as pcm:
            self.processes.decode_audio(self.blobs.resolve(asset["blob"], verify=True), pcm, rate)
            samples = read_pcm(pcm)
            require(len(samples) > 0, "empty_audio", "Decoded audio is empty")
            if kind == "signal":
                result = {**describe_samples(samples, rate),
                          "silence": silence_intervals(samples, rate, settings["threshold_db"], settings["minimum_silence"]),
                          "units": "dBFS", "note": "PCM peak/RMS; not EBU R128 integrated loudness"}
            else:
                result = {"sample_rate": rate, "sample_count": len(samples),
                          "buckets": waveform_buckets(samples, settings["buckets"])}
        return self.persist(asset_id, kind, settings, result, fingerprint)

    def persist(self, asset_id, kind, settings, result, fingerprint):
        with self.db.write_unit() as db:
            self.assets.get(asset_id, db, ready=True)
            row = db.execute("SELECT id FROM analyses WHERE fingerprint=?", (fingerprint,)).fetchone()
            if row:
                return self.get(row["id"], db)
            analysis_id = identifier("analysis")
            db.execute("INSERT INTO analyses VALUES(?,?,?,?,?,?,?)",
                       (analysis_id, asset_id, kind, canonical(settings), canonical(result), fingerprint, now()))
            self.db.event(db, asset_id, "analyzed", {"analysis": analysis_id, "kind": kind})
            return self.get(analysis_id, db)

    def measure_loudness(self, asset):
        with self.blobs.temporary(".null") as output:
            completed = self.processes.run([
                "-loglevel", "info", "-protocol_whitelist", "file,pipe",
                "-i", str(self.blobs.resolve(asset["blob"], verify=True)),
                "-map", "0:a:0", "-vn", "-af", "loudnorm=I=-23:TP=-1:LRA=7:print_format=json",
                "-f", "null", str(output)])
        log = completed["stderr"]
        start, end = log.rfind("{"), log.rfind("}")
        require(start >= 0 and end > start, "loudness_report", "Encoder did not return loudness measurements")
        try:
            measured = json.loads(log[start:end+1])
        except ValueError as exc:
            from .errors import LoomError
            raise LoomError("loudness_report", "Loudness measurements could not be decoded") from exc
        result = {"standard": "EBU R128", "units": "LUFS/dBTP", "silence": False}
        mapping = {"input_i": "integrated_lufs", "input_tp": "true_peak_dbtp",
                   "input_lra": "range_lu", "input_thresh": "threshold_lufs"}
        for source, target in mapping.items():
            value = float(measured[source])
            result[target] = round(value, 3) if math.isfinite(value) else None
        result["silence"] = result["integrated_lufs"] is None
        return result

    def get(self, analysis_id, db=None):
        if db is None:
            with self.db.read() as read:
                return self.get(analysis_id, read)
        row = db.execute("SELECT * FROM analyses WHERE id=?", (analysis_id,)).fetchone()
        if row is None:
            raise missing("analysis", analysis_id)
        return {**dict(row), "settings": json.loads(row["settings"]), "result": json.loads(row["result"])}

    def list(self, asset_id):
        with self.db.read() as db:
            return [self.get(row["id"], db) for row in db.execute("SELECT id FROM analyses WHERE asset=? ORDER BY created,id", (asset_id,))]
