"""Refuse documents with more image pieces than the server can prepare (BUG-532)."""

from app import messages
from app.config import Settings
from app.errors import FileTooLargeError
from app.ingest.schemas import ExtractionResult


def refuse_fragment_heavy(result: ExtractionResult, settings: Settings) -> None:
    count = len(result.images)
    if count > settings.preparation_max_image_fragments:
        raise FileTooLargeError(messages.TOO_MANY_IMAGE_FRAGMENTS.format(count=count))
