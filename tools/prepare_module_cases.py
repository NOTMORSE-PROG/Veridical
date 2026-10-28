"""Prepare licensed published module-check records from saved engine receipts."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import shutil
import sys
from types import SimpleNamespace
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "tools"))
from app.config import Settings
from app.report.sample_page_images import _render_page_png
from prepare_sample_evidence import source_boxes
import pymupdf

EXPECTED = {
 "rojahn-2023.pdf": "993d2a3e660b2a0e45926da11e6b305c44a0a60f4a848d0dc3b40c4e0ac88b38",
 "nuijten-2016.pdf": "5d1a96e0cdf0ff090167379efad475cb6e104936e237a4d62a7e6f3a7da47a5e",
 "veldkamp-2014.pdf": "a91ab7e12f650f80b03c3f6548c3a9709831e469daa4921bd0606d8e7939faed",
}

def prepare(research: Path):
 data = json.loads((research / "module-case-data.json").read_text(encoding="utf-8"))
 public = ROOT / "frontend/public/sample-assets/module-checks"
 public.mkdir(parents=True, exist_ok=True)
 documents = {item["id"]: item for item in data["documents"]}
 for item in documents.values():
  name = item["file"]
  original = (research / name).read_bytes()
  digest = hashlib.sha256(original).hexdigest()
  if EXPECTED.get(name) != digest or item["source_sha256"] != digest or item["licence_short"] != "CC BY 4.0":
   raise ValueError("Unapproved or changed public source")
  shutil.copyfile(research / name, public / name)
  item["pdf_asset"] = f"/sample-assets/module-checks/{name}"
  item["title"] = item["title"].replace("\u2013", "-").replace("\u2014", "-")
 settings = Settings(_env_file=None)
 render_settings = SimpleNamespace(export_page_image_dpi=settings.export_page_image_dpi, export_page_image_max_pixels=4_000_000, export_box_line_merge_tolerance=settings.export_box_line_merge_tolerance, export_page_image_max_height=settings.export_page_image_max_height, export_page_disclosure_font_pt=settings.export_page_disclosure_font_pt)
 def bind(document_id, page_number, quote, fallback=None):
  document = documents[document_id]
  with pymupdf.open(public / document["file"]) as pdf:
   page = pdf[page_number - 1]
   boxes, note = source_boxes(page, quote)
   highlighted = quote
   if not boxes and fallback:
    boxes, note = source_boxes(page, fallback)
    highlighted = fallback
   png = _render_page_png(page, tuple(tuple(box) for box in boxes), llm_disclosure=("Saved local check. Original published source unchanged.", "caution"), settings=render_settings)
   digest = hashlib.sha256(png).hexdigest()
   image = f"e-{digest[:24]}.png"
   (public / image).write_bytes(png)
   return {"boxes_origin": "bottom-left", "page": page_number, "source_sha256": document["source_sha256"], "quote": quote, "highlighted_quote": highlighted, "precision": "page_bbox" if boxes else "page_only", "boxes": boxes, "precision_note": note if highlighted == quote else "Only the reported statistic is boxed. Read the surrounding formula for its p-value.", "image_asset": f"/sample-assets/module-checks/{image}", "image_sha256": digest}
 for entry in data["statistics"]["checked_values"]:
  entry["source"] = bind(data["statistics"]["document_id"], entry["pdf_page"], entry["source_quote"], str(entry["statistic"]))
 for entry in data["reuse"]["source_excerpt_cards"]:
  document_id = Path(entry["file"]).stem
  entry["source"] = bind(document_id, entry["pdf_page"], entry["quote"])
 for pair in data["reuse"].get("pairs", []):
  for entry in pair["source_excerpt_cards"]:
   entry["source"] = bind(Path(entry["file"]).stem, entry["pdf_page"], entry["quote"])
 data["rendered_source_coordinates"] = {"units": "PDF points", "origin": "bottom-left", "fields": "source.boxes", "qualification": "Original top-left native search rectangles remain separately identified as line_rects_pdf_points."}
 data["preparation"] = {"source_bytes_unchanged": True, "local_engine_results_loaded": True, "human_uat": False}
 out = ROOT / "frontend/src/demo/moduleCases.json"
 out.write_text(json.dumps(data, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
 (public / "PROVENANCE.json").write_text(json.dumps(list(documents.values()), indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
 print(json.dumps({"documents": len(documents), "statistical_checks": len(data["statistics"]["checked_values"]), "reuse_pairs": 1, "source_originals_unchanged": True}))

if __name__ == "__main__":
 prepare(Path(sys.argv[1]).resolve())
