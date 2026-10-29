"""Isolated native extraction worker (F1). No database, model, or API imports."""

import hashlib
import math
from pathlib import Path
from tempfile import TemporaryDirectory

from app.config import Settings
from app.errors import FileMalformedError, GoneError
from app.ingest import docx, pdf
from app.ingest.compute_contract import BoundedWriter, NativeJob, canonical_json, digest
from app.ingest.fragment_guard import refuse_fragment_heavy
from app.ingest.image_source import _raster, _selected_indices, prepare_figures
from app.ingest.patterns import load_patterns
from app.storage import Storage


def execute_native_job(job: NativeJob, settings: Settings, storage: Storage) -> dict:
    policy = job.validate_policy(settings)
    prefix = job.result_prefix(settings)
    with TemporaryDirectory(prefix="veridical-native-") as temporary:
        root = Path(temporary)
        source = root / "source"
        with source.open("wb") as target:
            storage.download_to(
                job.source_key,
                BoundedWriter(target, job.source_bytes),
                chunk_bytes=settings.storage_download_chunk_bytes,
            )
        with source.open("rb") as reader:
            actual_sha = hashlib.file_digest(reader, "sha256").hexdigest()
        if source.stat().st_size != job.source_bytes or actual_sha != job.source_sha256:
            raise ValueError("Native source identity did not match the authorized job.")
        with source.open("rb") as reader:
            magic = reader.read(8)
        if magic.startswith(b"%PDF-"):
            result = pdf.extract_document(str(source), policy)
        elif magic.startswith(b"PK\x03\x04"):
            result = docx.extract_document(str(source), policy)
        else:
            raise FileMalformedError("The uploaded file is not a readable PDF or DOCX.")
        # Refused here, before the API ever parses a result it cannot hold.
        refuse_fragment_heavy(result, settings)
        extraction = result.model_dump_json().encode()
        if len(extraction) > settings.native_compute_max_result_mb * 1024 * 1024:
            raise ValueError("Native extraction exceeded the configured result limit.")
        extracted_path = root / "extraction.json"
        extracted_path.write_bytes(extraction)
        extraction_key = f"{prefix}/extraction.json"
        storage.put_file(extracted_path, extraction_key)
        patterns = load_patterns(policy.ingest_patterns_file)
        indices = sorted(
            set(_selected_indices(result, patterns, policy))
            | set(prepare_figures(result, patterns, policy))
        )
        crops = []
        for index in indices:
            image = result.images[index]
            item = {"image_index": index, "image": image.model_dump(mode="json")}
            try:
                if image.docx_source is not None and (
                    image.docx_source.width_px * image.docx_source.height_px
                    > settings.native_compute_max_crop_pixels
                ):
                    raise ValueError("Native crop exceeds the pixel budget.")
                if image.bbox is not None:
                    x0, y0, x1, y1 = image.bbox
                    pixels = math.ceil(
                        max(0, x1 - x0) * policy.ingest_vision_crop_dpi / 72
                    ) * math.ceil(max(0, y1 - y0) * policy.ingest_vision_crop_dpi / 72)
                    if pixels > settings.native_compute_max_crop_pixels:
                        raise ValueError("Native crop exceeds the pixel budget.")
                png, binding = _raster(source, result, index, job.source_sha256, policy)
                if len(png) > settings.native_compute_max_crop_mb * 1024 * 1024:
                    raise ValueError("Native crop exceeds the result budget.")
                crop_path = root / "crop.png"
                crop_path.write_bytes(png)
                key = f"{prefix}/image-{index}.png"
                storage.put_file(crop_path, key)
                item.update(key=key, sha256=digest(png), bytes=len(png), binding=binding)
            except (ValueError, RuntimeError, IndexError, OSError, GoneError):
                item["error"] = "crop_unavailable"
            crops.append(item)
        manifest = {
            "version": job.version,
            "job_id": job.job_id,
            "owner_id": job.owner_id,
            "source_sha256": job.source_sha256,
            "source_key": job.source_key,
            "parser_version": job.parser_version,
            "storage_namespace": job.storage_namespace,
            "policy_sha256": job.policy_sha256,
            "extraction": {
                "key": extraction_key,
                "sha256": digest(extraction),
                "bytes": len(extraction),
            },
            "crops": crops,
        }
        content = canonical_json(manifest)
        manifest_path = root / "manifest.json"
        manifest_path.write_bytes(content)
        manifest_key = f"{prefix}/manifest.json"
        storage.put_file(manifest_path, manifest_key)
        return {
            "version": job.version,
            "job_id": job.job_id,
            "source_sha256": job.source_sha256,
            "policy_sha256": job.policy_sha256,
            "manifest_key": manifest_key,
            "manifest_sha256": digest(content),
            "manifest_bytes": len(content),
        }
