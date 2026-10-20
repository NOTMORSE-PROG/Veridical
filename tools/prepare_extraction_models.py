"""Prepare pinned local extraction artifacts from an already downloaded HF cache.

No network request, account credential, manuscript access or model training.
"""

import argparse
import hashlib
import json
import shutil
from pathlib import Path


def digest(path: Path, chunk_bytes: int) -> str:
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(chunk_bytes), b""):
            result.update(chunk)
    return result.hexdigest()


def prepare(manifest_path: Path, cache: Path, output: Path, chunk_bytes: int) -> int:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    cache = cache.resolve(strict=True)
    output = output.resolve()
    copied = 0
    for model in manifest["models"]:
        folder_name = model["repo_id"].replace("/", "--")
        source_root = cache / ("models--" + folder_name) / "snapshots" / model["revision"]
        target_root = output / folder_name
        for name, expected in model["files"].items():
            source = (source_root / name).resolve(strict=True)
            target = (target_root / name).resolve()
            if not source.is_relative_to(cache) or not target.is_relative_to(output):
                raise ValueError("Artifact path escaped the selected directories")
            if digest(source, chunk_bytes) != expected:
                raise ValueError("Cached model artifact hash does not match the manifest")
            if target.exists():
                if digest(target, chunk_bytes) != expected:
                    raise ValueError("Existing destination differs; it was not overwritten")
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with source.open("rb") as incoming, target.open("xb") as outgoing:
                shutil.copyfileobj(incoming, outgoing, chunk_bytes)
            copied += 1
        # Preserve the upstream model card/license notice beside its weights.
        notice = source_root / "README.md"
        if notice.is_file() and not (target_root / "README.md").exists():
            with notice.open("rb") as incoming, (target_root / "README.md").open("xb") as outgoing:
                shutil.copyfileobj(incoming, outgoing, chunk_bytes)
    return copied


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--hf-cache", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--hash-chunk-bytes", type=int, required=True)
    args = parser.parse_args()
    if args.hash_chunk_bytes <= 0:
        parser.error("hash-chunk-bytes must be positive")
    count = prepare(args.manifest, args.hf_cache, args.output, args.hash_chunk_bytes)
    print(json.dumps({"new_files": count, "identity": "manifest_hashes_matched"}))


if __name__ == "__main__":
    main()
