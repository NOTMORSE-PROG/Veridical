"""Private native-compute HTTP function. Accepts authenticated metadata only."""

import json
import sys
import threading
from http.server import BaseHTTPRequestHandler
from pathlib import Path

import pymupdf

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.config import Settings
from app.errors import FileMalformedError, FileTooLargeError
from app.ingest.compute_contract import (
    SIGNATURE_HEADER,
    canonical_json,
    decode_job,
    signature,
)
from app.ingest.compute_native import execute_native_job
from app.storage import get_storage

_native_lock = threading.Lock()


class handler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        # Never log job metadata, signatures, object keys or manuscript content.
        pass

    def respond(self, status, value):
        payload = canonical_json(value)
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        self.respond(200, {"service": "native-compute", "version": "native-v1"})

    def do_POST(self):
        settings = Settings(_env_file=None)
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= settings.native_compute_max_request_bytes:
                raise ValueError("Invalid descriptor length.")
            payload = self.rfile.read(length)
            if len(payload) != length:
                raise ValueError("Incomplete descriptor.")
            job = decode_job(payload, self.headers.get(SIGNATURE_HEADER, ""), settings)
        except PermissionError:
            return self.respond(401, {"error": "authentication_required"})
        except (ValueError, json.JSONDecodeError):
            return self.respond(400, {"error": "invalid_job"})
        # PyMuPDF does not support concurrent threads in one process. Refuse a
        # busy instance immediately; the durable caller retains retry authority.
        if not _native_lock.acquire(blocking=False):
            return self.respond(503, {"error": "native_worker_busy"})
        storage = None
        try:
            storage = get_storage(settings)
            receipt = execute_native_job(job, settings, storage)
            self.respond(
                200,
                {
                    "receipt": receipt,
                    "signature": signature(
                        canonical_json(receipt), settings.native_compute_secret
                    ),
                },
            )
        except (FileMalformedError, FileTooLargeError) as exc:
            receipt = {
                "version": job.version,
                "job_id": job.job_id,
                "source_sha256": job.source_sha256,
                "policy_sha256": job.policy_sha256,
                "error_code": exc.code,
                "error_message": str(exc),
            }
            self.respond(
                200,
                {
                    "receipt": receipt,
                    "signature": signature(
                        canonical_json(receipt), settings.native_compute_secret
                    ),
                },
            )
        except Exception:
            self.respond(422, {"error": "native_processing_failed"})
        finally:
            try:
                if storage is not None:
                    storage.close()
            finally:
                try:
                    # Every native lifetime has ended and this instance's lock
                    # still excludes other native work. Release decoded caches.
                    pymupdf.TOOLS.store_shrink(100)
                finally:
                    _native_lock.release()
