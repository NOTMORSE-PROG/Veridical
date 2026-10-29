"""F8: exact disk-backed source images outlive layout and release decoded data."""

import subprocess
import sys
from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image as PillowImage
from reportlab.platypus import Image

from app.report.source_print import source_image_store


def png():
    stream = BytesIO()
    PillowImage.new("RGB", (100, 100), (12, 34, 56)).save(stream, format="PNG")
    return stream.getvalue()


@pytest.mark.parametrize("fail", [False, True])
def test_store_retains_exact_deduplicated_png_until_build_exits(fail):
    encoded = png()
    saved = None
    try:
        with source_image_store() as path_for:
            saved = Path(path_for(encoded))
            assert saved.read_bytes() == encoded
            assert path_for(encoded) == str(saved)
            flow = Image(str(saved), width=100, height=100, lazy=2)

            class Canvas:
                _drawing = False

                def drawImage(self, reader, *args, **kwargs):
                    assert reader.getRGBData() == bytes((12, 34, 56)) * 10000

            flow.canv = Canvas()
            flow.draw()
            assert flow.__dict__.get("_img") is None
            assert flow.__dict__.get("_file") is None
            assert saved.exists()
            if fail:
                raise ValueError("Controlled layout failure")
    except ValueError:
        assert fail
    assert saved is not None and not saved.exists()


def test_deterministic_grim_import_does_not_load_scientific_plotting_stack():
    source = (
        "import sys; import app.checks.forensics.grim; "
        "assert 'pysprite' not in sys.modules; "
        "assert 'matplotlib' not in sys.modules; assert 'seaborn' not in sys.modules"
    )
    result = subprocess.run([sys.executable, "-c", source], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("fail", [False, True])
def test_public_builder_owns_exact_images_until_layout_finishes(monkeypatch, fail):
    from app.report import export

    paths = []

    def layout(data, path_for):
        path = Path(path_for(png()))
        paths.append(path)
        assert path.read_bytes() == png()
        if fail:
            raise ValueError("Controlled renderer failure")
        return b"controlled renderer result"

    monkeypatch.setattr(export, "_build_report_pdf", layout)
    if fail:
        with pytest.raises(ValueError, match="Controlled renderer failure"):
            export.build_report_pdf(None)
    else:
        assert export.build_report_pdf(None) == b"controlled renderer result"
    assert paths and not paths[0].exists()
