import json
import re
from .errors import require, missing
from .values import identifier, now, canonical, text
from .timebase import project_interval, stamp, TICKS_PER_SECOND

TIME = re.compile(r"^(\d{2,}):(\d{2}):(\d{2})[,.](\d{3})$")


def parse_stamp(value):
    match = TIME.fullmatch(value.strip())
    require(match is not None, "cue_time", "Invalid subtitle timestamp")
    h, m, s, ms = map(int, match.groups())
    require(m < 60 and s < 60, "cue_time", "Subtitle minute or second is invalid")
    return ((h*60+m)*60+s)*TICKS_PER_SECOND + ms*1000


def parse_srt(body, duration):
    require(isinstance(body, str) and len(body.encode()) <= 2_000_000,
            "caption_size", "Subtitle input exceeds limit")
    blocks = re.split(r"\n\s*\n", body.lstrip("\ufeff").replace("\r\n", "\n").strip())
    require(len(blocks) <= 10_000, "cue_limit", "Too many subtitle cues")
    cues = []
    for block in blocks:
        lines = block.splitlines()
        if lines and lines[0].strip().isdigit():
            lines = lines[1:]
        require(len(lines) >= 2 and " --> " in lines[0], "cue_format", "Invalid SRT cue")
        left, right = lines[0].split(" --> ", 1)
        start, end = parse_stamp(left), parse_stamp(right)
        require(0 <= start < end <= duration, "cue_bounds", "Subtitle lies outside source media")
        caption = "\n".join(lines[1:]).strip()
        require(bool(caption) and len(caption) <= 2000, "cue_text", "Invalid subtitle text")
        require(not cues or start >= cues[-1]["start_tick"], "cue_order", "Subtitles must be in start-time order")
        cues.append({"start_tick": start, "end_tick": end, "text": caption})
    return cues


class Captions:
    def __init__(self, db, assets, reels):
        self.db, self.assets, self.reels = db, assets, reels

    def import_track(self, asset_id, language, body):
        language = text(language, "language", 40)
        with self.db.write_unit() as db:
            asset = self.assets.get(asset_id, db, ready=True)
            cues = parse_srt(body, asset["metadata"]["duration_tick"])
            track_id = identifier("cue")
            db.execute("INSERT INTO cue_tracks VALUES(?,?,?,?,?)",
                       (track_id, asset_id, language, canonical(cues), now()))
            self.db.event(db, asset_id, "cues_imported", {"track": track_id, "count": len(cues)})
            return self.get(track_id, db)

    def get(self, track_id, db=None):
        if db is None:
            with self.db.read() as read:
                return self.get(track_id, read)
        row = db.execute("SELECT * FROM cue_tracks WHERE id=?", (track_id,)).fetchone()
        if row is None:
            raise missing("cue track", track_id)
        return {**dict(row), "cues": json.loads(row["cues"])}

    def project(self, reel_id, revision, track_ids):
        require(isinstance(track_ids, list) and 1 <= len(track_ids) <= 64, "track_count", "Expected 1..64 cue tracks")
        require(len(track_ids) == len(set(track_ids)), "duplicate_track", "Cue tracks cannot repeat")
        with self.db.read() as db:
            reel = self.reels.get(reel_id, revision, db)
            projected = []
            for track_id in track_ids:
                track = self.get(track_id, db)
                slices = [item for item in reel["document"]["slices"] if item["asset"] == track["asset"]]
                require(slices, "unrelated_track", "Cue track does not belong to this reel")
                for cue in track["cues"]:
                    for interval in project_interval(cue["start_tick"], cue["end_tick"], slices):
                        projected.append({**interval, "text": cue["text"], "track": track_id, "language": track["language"]})
            projected.sort(key=lambda x: (x["start_tick"], x["end_tick"], x["track"], x["text"]))
            return {"reel": reel_id, "revision": reel["revision"], "reel_hash": reel["hash"], "cues": projected,
                    "srt": "\n\n".join(f'{i+1}\n{stamp(c["start_tick"])} --> {stamp(c["end_tick"])}\n{c["text"]}'
                                        for i, c in enumerate(projected)) + "\n"}
