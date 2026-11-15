from .errors import require
from .values import digest
from .timebase import decimal, seconds
from .probe import select_stream


class Planner:
    def __init__(self, assets, reels, looks):
        self.assets, self.reels, self.looks = assets, reels, looks

    def freeze(self, db, reel_id, revision, look_id, version):
        reel = self.reels.get(reel_id, revision, db)
        look = self.looks.get(look_id, version, db)
        closure = {}
        for item in reel["document"]["slices"]:
            if item["asset"] not in closure:
                asset = self.assets.get(item["asset"], db, ready=True)
                closure[asset["id"]] = {"id": asset["id"], "blob": asset["blob"], "metadata": asset["metadata"]}
        if look["spec"]["kind"] == "video":
            require(all(x["metadata"]["has_video"] for x in closure.values()),
                    "video_required", "Video looks require video in every slice")
        else:
            require(all(x["metadata"]["has_audio"] for x in closure.values()),
                    "audio_required", "Audio looks require audio in every slice")
        snapshot = {"schema": 1, "reel": reel_id, "revision": reel["revision"],
                    "reel_hash": reel["hash"], "document": reel["document"],
                    "look": {"id": look["id"], "version": look["version"], "spec": look["spec"], "hash": look["hash"]},
                    "sources": closure}
        return {**snapshot, "fingerprint": digest(snapshot)}

    def _input_roles(self, snapshot):
        roles = []
        previous_audio = None
        for position, item in enumerate(snapshot["document"]["slices"]):
            source = snapshot["sources"][item["asset"]]
            metadata = source["metadata"]
            video = select_stream(metadata, "video")
            audio = select_stream(metadata, "audio") if metadata.get("has_audio") else None
            if audio is not None:
                previous_audio = audio
            elif position and video is not None:
                audio = previous_audio
            roles.append({"video": video, "audio": audio})
        return roles

    def compile(self, snapshot, resolve, output):
        spec = snapshot["look"]["spec"]
        args, filters, segments = [], [], []
        input_roles = self._input_roles(snapshot)
        for i, item in enumerate(snapshot["document"]["slices"]):
            asset = snapshot["sources"][item["asset"]]
            args += ["-protocol_whitelist", "file,pipe", "-i", str(resolve(asset["blob"]))]
            a, b = decimal(item["in_tick"]), decimal(item["out_tick"])
            duration = seconds(item["out_tick"]-item["in_tick"])
            audio = input_roles[i]["audio"]
            if spec["kind"] == "video":
                video = input_roles[i]["video"]
                if spec["fit"] == "contain":
                    sizing = (f'scale={spec["width"]}:{spec["height"]}:force_original_aspect_ratio=decrease,'
                              f'pad={spec["width"]}:{spec["height"]}:(ow-iw)/2:(oh-ih)/2')
                else:
                    sizing = (f'scale={spec["width"]}:{spec["height"]}:force_original_aspect_ratio=increase,'
                              f'crop={spec["width"]}:{spec["height"]}')
                filters.append(f'[{i}:{video["index"]}]trim=start={a}:end={b},setpts=PTS-STARTPTS,'
                               f'{sizing},setsar=1,fps={spec["frame_rate"]},format=yuv420p[v{i}]')
            layout = "mono" if spec["channels"] == 1 else "stereo"
            if audio:
                filters.append(f'[{i}:{audio["index"]}]atrim=start={a}:end={b},asetpts=PTS-STARTPTS,'
                               f'aresample={spec["sample_rate"]},aformat=sample_fmts=fltp:channel_layouts={layout},'
                               f'apad,atrim=duration={duration:.6f},volume={spec["gain_db"]}dB[a{i}]')
            else:
                filters.append(f'anullsrc=r={spec["sample_rate"]}:cl={layout},atrim=duration={duration:.6f}[a{i}]')
            segments.append((f"[v{i}]" if spec["kind"] == "video" else "") + f"[a{i}]")
        count = len(segments)
        if spec["kind"] == "video":
            filters.append("".join(segments) + f"concat=n={count}:v=1:a=1[vjoined][aout]")
            filters.append(f'[vjoined]fps={spec["frame_rate"]},tpad=stop_mode=clone:stop_duration=0.1,'
                           f'trim=duration={seconds(snapshot["document"]["duration_tick"]):.6f}[vout]')
            maps = ["-map", "[vout]", "-map", "[aout]", "-c:v", "libx264", "-preset", "veryfast",
                    "-crf", str(spec["crf"]), "-c:a", "aac", "-movflags", "+faststart"]
        else:
            filters.append("".join(segments) + f"concat=n={count}:v=0:a=1[aout]")
            maps = ["-map", "[aout]", "-c:a", "pcm_s16le"]
        return args + ["-filter_complex", ";".join(filters)] + maps + [str(output)]
