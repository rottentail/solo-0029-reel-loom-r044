from dataclasses import dataclass
import re
from .errors import LoomError, require
from .values import object_body, integer, decode_json


@dataclass
class Download:
    path: object
    mime: str
    name: str
    digest: str


class Api:
    prefix = "/loom/v1"

    def __init__(self, application):
        self.app = application

    def json_body(self, raw):
        value = decode_json(raw or b"{}")
        return object_body(value)

    def handle(self, method, path, query, headers, raw):
        require(path.startswith(self.prefix+"/") or path == self.prefix,
                "not_found", "Unknown API entry", 404)
        route = path[len(self.prefix):] or "/"
        app = self.app
        if method == "GET" and route == "/health":
            return app.health()
        if method == "GET" and route == "/":
            return {"name": "声画织场", "api": self.prefix,
                    "resources": ["assets", "reels", "looks", "tickets", "analyses", "artifacts", "imprints"],
                    "time_units": "seconds at input; integer microseconds in frozen records",
                    "render_modes": ["video/h264+aac", "audio/pcm_s16le"],
                    "editing": "ordered hard cuts; immutable revisions",
                    "captions": "source SRT tracks projected to a reel revision"}
        if route == "/assets":
            if method == "POST":
                return app.assets.ingest(raw, headers.get("X-Media-Label", "未命名媒体"))
            if method == "GET":
                return {"items": app.assets.list(query.get("state"))}
        match = re.fullmatch(r"/assets/([^/]+)(?:/(retire|restore|signal|waveform|loudness|contact-sheet|cues|analyses|artifacts))?", route)
        if match:
            asset_id, action = match.groups()
            if method == "GET" and not action:
                return app.assets.get(asset_id)
            if method == "GET" and action == "analyses":
                return {"items": app.analysis.list(asset_id)}
            if method == "GET" and action == "artifacts":
                return {"items": app.artifacts.list_for_asset(asset_id)}
            if method == "POST" and action in ("retire", "restore"):
                return getattr(app.assets, action)(asset_id)
            if method == "POST" and action in ("signal", "waveform", "loudness"):
                return app.analysis.run(asset_id, action, self.json_body(raw))
            if method == "POST" and action == "contact-sheet":
                return app.frames.contact_sheet(asset_id, self.json_body(raw))
            if method == "POST" and action == "cues":
                body = self.json_body(raw)
                object_body(body, ("language", "srt"), ("language", "srt"))
                return app.captions.import_track(asset_id, body["language"], body["srt"])
        if route == "/reels":
            if method == "GET":
                return {"items": app.reels.list()}
            if method == "POST":
                return app.reels.create(self.json_body(raw), headers.get("Idempotency-Key"))
        match = re.fullmatch(r"/reels/([^/]+)(?:/(seal|captions|history))?", route)
        if match:
            reel_id, action = match.groups()
            if method == "GET" and not action:
                revision = self.query_integer(query, "revision")
                return app.reels.get(reel_id, revision)
            if method == "PUT" and not action:
                expected = self.expected(headers)
                return app.reels.replace(reel_id, expected, self.json_body(raw))
            if method == "GET" and action == "history":
                return {"items": app.reels.history(reel_id)}
            if method == "POST" and action == "seal":
                return app.reels.seal(reel_id, self.expected(headers))
            if method == "POST" and action == "captions":
                body = self.json_body(raw)
                object_body(body, ("revision", "tracks"), ("tracks",))
                return app.captions.project(reel_id, body.get("revision"), body["tracks"])
        if method == "GET" and route == "/looks":
            return {"items": app.looks.list()}
        look_history = re.fullmatch(r"/looks/([^/]+)/history", route)
        if look_history and method == "GET":
            return {"items": app.looks.history(look_history[1])}
        match = re.fullmatch(r"/looks/([^/]+)", route)
        if match:
            look_id = match[1]
            if method == "GET":
                return app.looks.get(look_id, self.query_integer(query, "version"))
            if method == "PUT":
                return app.looks.put(look_id, self.json_body(raw), self.expected(headers, default=0))
        if route == "/matrices" and method == "POST":
            return app.batches.submit(self.json_body(raw), headers.get("Idempotency-Key"))
        matrix = re.fullmatch(r"/matrices/([^/]+)", route)
        if matrix and method == "GET":
            return app.batches.get(matrix[1])
        if route == "/tickets":
            if method == "GET":
                return {"items": app.tickets.list(query.get("state"))}
            if method == "POST":
                return app.tickets.enqueue(self.json_body(raw), headers.get("Idempotency-Key"))
        match = re.fullmatch(r"/tickets/([^/]+)(?:/(cancel|retry|events|imprints|lineage))?", route)
        if match:
            ticket_id, action = match.groups()
            if method == "GET" and not action:
                return app.tickets.get(ticket_id)
            if method == "GET" and action == "lineage":
                return app.lineage.trace(ticket_id)
            if method == "GET" and action == "events":
                app.tickets.get(ticket_id)
                return {"items": app.db.events(ticket_id, self.query_integer(query, "after") or 0)}
            if method == "POST" and action in ("cancel", "retry"):
                return getattr(app.tickets, action)(ticket_id)
            if method == "POST" and action == "imprints":
                return app.imprints.create(ticket_id, self.json_body(raw))
        match = re.fullmatch(r"/(analyses|cues|artifacts|imprints)/([^/]+)(?:/(verify|content|conformance))?", route)
        if match and method == "GET":
            kind, entity_id, action = match.groups()
            if action == "conformance" and kind == "artifacts":
                return app.conformance.inspect(entity_id)
            if action == "verify" and kind == "imprints":
                return app.imprints.verify(entity_id)
            service = {"analyses": app.analysis, "cues": app.captions,
                       "artifacts": app.artifacts, "imprints": app.imprints}[kind]
            result = service.get(entity_id)
            if action == "content" and kind in ("artifacts", "imprints"):
                mime = "application/zip" if kind == "imprints" else result["metadata"].get("mime", "application/octet-stream")
                return Download(app.blobs.resolve(result["blob"], verify=True), mime, entity_id, result["blob"])
            if not action:
                return result
        match = re.fullmatch(r"/blobs/([a-f0-9]{64})", route)
        if match and method == "GET":
            app.blobs.get(match[1])
            return Download(app.blobs.resolve(match[1]), "application/octet-stream", match[1], match[1])
        if method == "GET" and route == "/resources":
            return app.resources.reconcile()
        if method == "POST" and route == "/resources/sweep":
            body = self.json_body(raw)
            object_body(body, ("dry_run",))
            require(type(body.get("dry_run", True)) is bool, "invalid_boolean", "dry_run must be boolean")
            return app.resources.sweep(body.get("dry_run", True))
        raise LoomError("not_found", "Unknown method or API entry", 404)

    def expected(self, headers, default=None):
        value = headers.get("If-Match")
        if value is None:
            require(default is not None, "precondition_required", "If-Match revision is required", 428)
            return default
        value = value.strip('"')
        require(value.isdigit(), "invalid_precondition", "If-Match must be a numeric revision")
        return int(value)

    def query_integer(self, query, field):
        if field not in query:
            return None
        value = query[field]
        require(value.isdigit(), "invalid_query", f"{field} must be numeric")
        return int(value)
