"""Optional offline layout worker (F1); imports no application config or secrets.

Run by the ingestion supervisor with an operator-owned Python interpreter.
Docling proposals are untrusted until the parent maps them to native source.
"""

import argparse
import hashlib
import importlib.metadata
import json
import logging
from pathlib import Path
from typing import Any

# The external engine exports heterogeneous dictionaries. The API validates the
# projected wire format with strict models before using any value as evidence.
DocumentDict = dict[str, Any]


def file_digest(path: Path, chunk_bytes: int) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(chunk_bytes), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verified_models(root: Path, manifest: DocumentDict, chunk_bytes: int) -> None:
    for model in manifest["models"]:
        folder = (root / model["repo_id"].replace("/", "--")).resolve()
        if not folder.is_relative_to(root):
            raise ValueError("invalid_model_path")
        for name, expected in model["files"].items():
            source = (folder / name).resolve()
            if not source.is_relative_to(folder) or file_digest(source, chunk_bytes) != expected:
                raise ValueError("model_identity_mismatch")


def _captions(item: DocumentDict, document: DocumentDict) -> list[DocumentDict]:
    captions = []
    by_ref = {text["self_ref"]: text for text in document.get("texts", [])}
    for reference in item.get("captions", []):
        text = by_ref.get(reference["$ref"])
        if text is None or len(text.get("prov", [])) != 1:
            raise ValueError("caption_provenance_unavailable")
        provenance = text["prov"][0]
        captions.append(
            {"text": text["text"], "page": provenance["page_no"], "bbox": provenance["bbox"]}
        )
    return captions


def project_document(document: DocumentDict, page: int) -> DocumentDict:
    """Keep mixed coordinate origins explicitly; the parent converts each box."""
    source_page = document["pages"][str(page)]
    if source_page["page_no"] != page or len(document["pages"]) != 1:
        raise ValueError("page_identity_mismatch")
    tables = []
    figures = []
    for kind, output in (("tables", tables), ("pictures", figures)):
        for item in document.get(kind, []):
            if len(item.get("prov", [])) != 1 or item["prov"][0]["page_no"] != page:
                raise ValueError("region_provenance_unavailable")
            region = {
                "region_id": item["self_ref"],
                "bbox": item["prov"][0]["bbox"],
                "captions": _captions(item, document),
            }
            if kind == "tables":
                data = item["data"]
                region.update(
                    rows=data["num_rows"],
                    cols=data["num_cols"],
                    cells=[
                        {
                            "text": cell["text"],
                            "bbox": cell.get("bbox"),
                            "row_start": cell["start_row_offset_idx"],
                            "row_end": cell["end_row_offset_idx"],
                            "col_start": cell["start_col_offset_idx"],
                            "col_end": cell["end_col_offset_idx"],
                            "column_header": cell.get("column_header", False),
                            "row_header": cell.get("row_header", False),
                        }
                        for cell in data["table_cells"]
                    ],
                )
            output.append(region)
    return {
        "page": page,
        "width": source_page["size"]["width"],
        "height": source_page["size"]["height"],
        "status": "complete",
        "tables": tables,
        "figures": figures,
    }


def run(job: DocumentDict) -> DocumentDict:
    logging.disable(logging.CRITICAL)
    manifest_path = Path(job["manifest"])
    manifest_bytes = manifest_path.read_bytes()
    if hashlib.sha256(manifest_bytes).hexdigest() != job["manifest_sha256"]:
        raise ValueError("manifest_identity_mismatch")
    manifest = json.loads(manifest_bytes)
    versions = {name: importlib.metadata.version(name) for name in manifest["packages"]}
    if versions != manifest["packages"]:
        raise ValueError("worker_version_mismatch")
    model_root = Path(job["models_dir"]).resolve(strict=True)
    verified_models(model_root, manifest, job["hash_chunk_bytes"])
    source = Path(job["source"]).resolve(strict=True)
    if file_digest(source, job["hash_chunk_bytes"]) != job["source_sha256"]:
        raise ValueError("source_identity_mismatch")

    from docling.datamodel.accelerator_options import AcceleratorDevice, AcceleratorOptions
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions
    from docling.document_converter import DocumentConverter, PdfFormatOption

    options = PdfPipelineOptions(artifacts_path=model_root)
    options.do_ocr = False
    options.do_table_structure = True
    options.table_structure_options.do_cell_matching = True
    options.accelerator_options = AcceleratorOptions(
        num_threads=job["threads"], device=AcceleratorDevice.CPU
    )
    options.enable_remote_services = False
    options.generate_page_images = False
    options.generate_picture_images = False
    converter = DocumentConverter(
        allowed_formats=[InputFormat.PDF],
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=options)},
    )
    pages = []
    for page in job["pages"]:
        converted = converter.convert(source, page_range=(page, page), raises_on_error=False)
        if str(converted.status) != "ConversionStatus.SUCCESS":
            pages.append(
                {
                    "page": page,
                    "width": job["geometry"][str(page)]["width"],
                    "height": job["geometry"][str(page)]["height"],
                    "status": "unavailable",
                    "reason": "conversion_incomplete",
                }
            )
        else:
            pages.append(project_document(converted.document.export_to_dict(), page))
    if file_digest(source, job["hash_chunk_bytes"]) != job["source_sha256"]:
        raise ValueError("source_changed_during_recovery")
    return {
        "schema_version": 1,
        "source_sha256": job["source_sha256"],
        "request_sha256": job["request_sha256"],
        "manifest_sha256": job["manifest_sha256"],
        "versions": versions,
        "pages": pages,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--job", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        job = json.loads(args.job.read_text(encoding="utf-8"))
        reply = run(job)
        encoded = json.dumps(reply, ensure_ascii=False, allow_nan=False).encode("utf-8")
        if len(encoded) > job["max_output_bytes"]:
            return 1
        args.output.write_bytes(encoded)
    except Exception:
        # Process boundary: the parent records a coded failure. Never print
        # manuscript text, local paths or library exceptions into API logs.
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
