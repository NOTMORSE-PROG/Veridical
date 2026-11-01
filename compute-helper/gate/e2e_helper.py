"""Serve the staged helper's real handler and send it signed jobs.

Run with an interpreter that has ONLY compute-helper/requirements.txt
installed, so a request-path import the helper does not install fails here as
it would on Vercel. Storage is local and the secret is a throwaway value; no
network is used. Afterwards the production storage client is built with fake
credentials, which exercises its lazy imports without connecting.

    python -I e2e_helper.py STAGED_DIR DATA_DIR FIXTURE [FIXTURE ...]

Prints one line per fixture: "<name>: HTTP <status>; error=<error or None>".
"""

import hashlib
import importlib.util
import json
import os
import shutil
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from http.server import HTTPServer
from pathlib import Path

staged, data = Path(sys.argv[1]), Path(sys.argv[2])
fixtures = [Path(p) for p in sys.argv[3:]]
os.environ.update(
    {"DATA_DIR": str(data), "STORAGE_BACKEND": "local", "NATIVE_COMPUTE_SECRET": "x" * 48}
)
spec = importlib.util.spec_from_file_location(
    "native_extract", staged / "api" / "native_extract.py"
)
entry = importlib.util.module_from_spec(spec)
spec.loader.exec_module(entry)

from app.config import Settings  # noqa: E402  (importable only once the entrypoint set the path)
from app.ingest.compute_contract import (  # noqa: E402
    SIGNATURE_HEADER,
    NativeJob,
    canonical_json,
    digest,
    policy_for,
    signature,
)

server = HTTPServer(("127.0.0.1", 0), entry.handler)
threading.Thread(target=server.serve_forever, daemon=True).start()
url = f"http://127.0.0.1:{server.server_address[1]}/"
settings = Settings(_env_file=None)
for fixture in fixtures:
    key = f"gate/{fixture.name}"
    target = data / key
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(fixture, target)
    raw = fixture.read_bytes()
    policy = policy_for(settings)
    job = NativeJob(
        job_id=str(uuid.uuid4()),
        owner_id=1,
        source_key=key,
        source_sha256=hashlib.sha256(raw).hexdigest(),
        source_bytes=len(raw),
        expires=int(time.time()) + 120,
        policy=policy,
        policy_sha256=digest(canonical_json(policy)),
    )
    body = job.model_dump_json().encode()
    request = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            SIGNATURE_HEADER: signature(body, settings.native_compute_secret),
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=240) as response:
            status, answer = response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        status, answer = exc.code, json.loads(exc.read())
    receipt = answer.get("receipt", answer)
    error = receipt.get("error") or receipt.get("error_code")
    print(f"{fixture.name}: HTTP {status}; error={error}")
server.shutdown()

from app.storage import get_storage  # noqa: E402

remote = Settings(
    _env_file=None,
    storage_backend="r2",
    r2_bucket="gate",
    r2_endpoint_url="https://example.invalid",
    r2_access_key_id="gate",
    r2_secret_access_key="gate",
    storage_namespace="gate",
)
storage = get_storage(remote)
print("remote storage client:", type(storage).__name__)
storage.close()
