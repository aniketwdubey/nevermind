"""Bounded transport retries for model files, never for invalid model decisions."""

import logging
import time
from typing import Any

import httpx2
from huggingface_hub import snapshot_download as download_snapshot
from huggingface_hub.errors import HfHubHTTPError

logger = logging.getLogger(__name__)


def snapshot_download(*args: Any, **kwargs: Any) -> str:
    kwargs.setdefault("max_workers", 4)
    for attempt in range(3):
        try:
            return download_snapshot(*args, **kwargs)
        except httpx2.TransportError as error:
            if attempt == 2:
                raise
            logger.warning(
                "Retrying model download attempt=%s transport_error=%s",
                attempt + 1,
                type(error).__name__,
            )
        except HfHubHTTPError as error:
            if error.response.status_code not in {429, 500, 502, 503, 504} or attempt == 2:
                raise
            logger.warning(
                "Retrying model download attempt=%s status=%s",
                attempt + 1,
                error.response.status_code,
            )
        time.sleep(2**attempt)
    raise RuntimeError("Model download attempts were exhausted.")
