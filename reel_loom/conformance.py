import av
from fractions import Fraction
from .errors import require, LoomError
from .probe import inspect_media, select_stream
from .timebase import seconds


class Conformance:
    def __init__(self, settings, blobs, tickets, artifacts):
        self.settings, self.blobs, self.tickets, self.artifacts = settings, blobs, tickets, artifacts

    def inspect(self, artifact_id):
        artifact = self.artifacts.get(artifact_id)
        require(artifact["kind"] == "rendition" and artifact["ticket"],
                "rendition_required", "Conformance requires a rendered audio/video artifact")
        ticket = self.tickets.get(artifact["ticket"])
        spec = ticket["snapshot"]["look"]["spec"]
        path = self.blobs.resolve(artifact["blob"], verify=True)
        metadata = inspect_media(path, self.settings)
        expected_duration = seconds(ticket["snapshot"]["document"]["duration_tick"])
        actual_duration = seconds(metadata["duration_tick"])
        checks = [{"check": "duration", "passed": abs(expected_duration-actual_duration) <= .15,
                   "expected": expected_duration, "actual": actual_duration}]
        audio = select_stream(metadata, "audio")
        checks.append({"check": "audio_format", "passed": bool(audio) and audio["sample_rate"] == spec["sample_rate"]
                       and audio["channels"] == spec["channels"],
                       "expected": {"rate": spec["sample_rate"], "channels": spec["channels"]}, "actual": audio})
        if spec["kind"] == "video":
            video = select_stream(metadata, "video")
            checks.append({"check": "video_dimensions", "passed": bool(video) and video["width"] == spec["width"] and video["height"] == spec["height"],
                           "expected": {"width": spec["width"], "height": spec["height"]}, "actual": video})
            checks.append({"check": "frame_rate", "passed": bool(video) and Fraction(video["frame_rate"]) == Fraction(spec["frame_rate"]),
                           "expected": spec["frame_rate"], "actual": video["frame_rate"] if video else None})
        counts, first_pts, last_pts, discontinuities = {}, {}, {}, []
        try:
            with av.open(str(path), options={"protocol_whitelist": "file,pipe"}) as media:
                for packet in media.demux():
                    if packet.stream.type not in ("video", "audio"):
                        continue
                    for frame in packet.decode():
                        kind = packet.stream.type
                        counts[kind] = counts.get(kind, 0)+1
                        if frame.pts is None:
                            continue
                        pts = float(frame.pts * frame.time_base)
                        if kind in last_pts and pts < last_pts[kind]:
                            discontinuities.append({"kind": kind, "previous": last_pts[kind], "current": pts})
                        first_pts.setdefault(kind, pts)
                        last_pts[kind] = pts
        except av.FFmpegError as exc:
            raise LoomError("decode_failed", "Rendition could not be decoded to its end", 409) from exc
        checks.append({"check": "full_decode", "passed": counts.get("audio",0)>0 and
                       (spec["kind"] != "video" or counts.get("video",0)>0), "actual": counts})
        checks.append({"check": "monotonic_frames", "passed": not discontinuities, "actual": discontinuities})
        return {"artifact": artifact_id, "ticket": ticket["id"], "valid": all(x["passed"] for x in checks),
                "checks": checks, "first_pts": first_pts, "last_pts": last_pts, "decoded_frames": counts}
