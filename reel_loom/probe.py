from pathlib import Path
import av
from .errors import LoomError, require
from .timebase import TICKS_PER_SECOND


def summarize_streams(streams):
    ordered = sorted(streams, key=lambda item: item["index"])
    primary = ordered[0] if ordered else None
    video = next((item for item in ordered if item["kind"] == "video"), None)
    audio = primary if primary and primary["kind"] == "audio" else None
    roles = {}
    if video is not None:
        roles["video"] = video["index"]
    if audio is not None:
        roles["audio"] = audio["index"]
    summary = {
        "catalog_version": 2,
        "primary_stream": primary["index"] if primary else None,
        "stream_roles": roles,
        "has_video": video is not None,
        "has_audio": audio is not None,
    }
    if primary is not None:
        summary["primary_kind"] = primary["kind"]
        summary["primary_codec"] = primary["codec"]
    return summary


def inspect_media(path, settings):
    try:
        with av.open(str(path), mode="r", options={"protocol_whitelist": "file,pipe"}) as container:
            require(container.format.name not in {"hls", "dash", "concat", "sdp"},
                    "unsupported_container", "Only self-contained media is accepted")
            streams = []
            duration_tick = container.duration or 0
            for stream in container.streams:
                if stream.type not in ("video", "audio"):
                    continue
                codec = stream.codec_context
                item = {"index": stream.index, "kind": stream.type,
                        "codec": codec.name, "time_base": str(stream.time_base),
                        "start_tick": round((stream.start_time or 0) * stream.time_base * TICKS_PER_SECOND)}
                if stream.duration is not None:
                    item["duration_tick"] = round(stream.duration * stream.time_base * TICKS_PER_SECOND)
                    duration_tick = max(duration_tick, item["duration_tick"])
                if stream.type == "video":
                    require(codec.width * codec.height <= settings.max_pixels,
                            "pixel_limit", "Video dimensions exceed the service limit")
                    item.update(width=codec.width, height=codec.height,
                                frame_rate=str(stream.average_rate or stream.base_rate or 25),
                                pixel_format=codec.format.name if codec.format else None,
                                frames=stream.frames)
                else:
                    item.update(sample_rate=codec.sample_rate,
                                channels=codec.channels,
                                layout=codec.layout.name if codec.layout else "unknown")
                streams.append(item)
            require(streams, "no_media", "No decodable audio or video streams were found")
            require(0 < duration_tick <= settings.max_seconds * TICKS_PER_SECOND,
                    "duration_limit", "Media duration is unknown or exceeds service capacity")
            return {"container": container.format.name, "duration_tick": duration_tick,
                    "streams": streams, "bytes": Path(path).stat().st_size,
                    **summarize_streams(streams)}
    except LoomError:
        raise
    except (av.FFmpegError, ValueError, OSError) as exc:
        raise LoomError("unreadable_media", "Media cannot be probed", context={"reason": str(exc)[:300]}) from exc


def select_stream(metadata, kind):
    if metadata.get("catalog_version") == 2:
        if not metadata.get("has_" + kind, False):
            return None
        selected = metadata.get("stream_roles", {}).get(kind)
        if selected is not None:
            return next((stream for stream in metadata["streams"]
                         if stream["index"] == selected and stream["kind"] == kind), None)
    return next((stream for stream in metadata["streams"] if stream["kind"] == kind), None)


def compatible_audio(metadata):
    audio = select_stream(metadata, "audio")
    require(audio is not None, "audio_required", "This workflow requires an audio stream")
    return audio
