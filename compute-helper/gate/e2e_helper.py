"""Serve the staged helper's real handler on 127.0.0.1 for the signed-job gate.

Run with an interpreter that has ONLY compute-helper/requirements.txt
installed, so a request-path import the helper does not install fails here as
it would on Vercel. Storage is local (DATA_DIR), standing in for R2: an
artifact the helper stores is copied there, as an upload would store it. The
secret is whatever NATIVE_COMPUTE_SECRET holds; no network is used. Before serving, the
production storage client is built with fake credentials, which exercises its
lazy imports without connecting.

    python -I -B e2e_helper.py STAGED_DIR

Prints "listening <url>" once ready, then serves until stdin closes.
"""

import importlib.util
import shutil
import sys
import threading
from http.server import HTTPServer
from pathlib import Path

staged = Path(sys.argv[1])
spec = importlib.util.spec_from_file_location(
    "native_extract", staged / "api" / "native_extract.py"
)
entry = importlib.util.module_from_spec(spec)
spec.loader.exec_module(entry)

from app.config import Settings  # noqa: E402  (importable once the entrypoint set the path)
from app.storage import LocalDiskStorage, get_storage  # noqa: E402


def _stored_copy(self, local_path, key):
    # In production the helper uploads each artifact to R2. Local storage
    # skips the upload because the API always writes at root/key itself;
    # the helper writes elsewhere, so here the upload is a real copy.
    target = self._root / key
    target.parent.mkdir(parents=True, exist_ok=True)
    if Path(local_path).resolve() != target.resolve():
        shutil.copyfile(local_path, target)


LocalDiskStorage.put_file = _stored_copy

remote = Settings(
    _env_file=None,
    storage_backend="r2",
    r2_bucket="gate",
    r2_endpoint_url="https://example.invalid",
    r2_access_key_id="gate",
    r2_secret_access_key="gate",
    storage_namespace="gate",
)
get_storage(remote).close()

server = HTTPServer(("127.0.0.1", 0), entry.handler)
threading.Thread(target=server.serve_forever, daemon=True).start()
print(f"listening http://127.0.0.1:{server.server_address[1]}/", flush=True)
sys.stdin.read()
server.shutdown()
