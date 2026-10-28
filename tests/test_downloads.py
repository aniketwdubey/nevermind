import importlib.util
from pathlib import Path
from unittest.mock import patch

import httpx2
import pytest
from huggingface_hub.errors import HfHubHTTPError

spec = importlib.util.spec_from_file_location(
    "runtime_downloads", Path(__file__).parents[1] / "deploy/runtime_downloads.py"
)
downloads = importlib.util.module_from_spec(spec)
spec.loader.exec_module(downloads)


def test_transport_disconnect_is_retried():
    with patch.object(
        downloads,
        "download_snapshot",
        side_effect=[httpx2.RemoteProtocolError("Disconnected"), "/model"],
    ) as download:
        with patch.object(downloads.time, "sleep") as sleep:
            assert downloads.snapshot_download("public/model") == "/model"
            assert download.call_count == 2
            sleep.assert_called_once_with(1)


def test_download_retry_is_bounded_and_failure_propagates():
    with patch.object(
        downloads, "download_snapshot", side_effect=httpx2.RemoteProtocolError("Disconnected")
    ) as download:
        with patch.object(downloads.time, "sleep"):
            with pytest.raises(httpx2.RemoteProtocolError):
                downloads.snapshot_download("public/model")
            assert download.call_count == 3


def test_missing_model_is_not_retried():
    response = httpx2.Response(404, request=httpx2.Request("GET", "https://huggingface.co/example"))
    with patch.object(
        downloads, "download_snapshot", side_effect=HfHubHTTPError("Not found", response=response)
    ) as download:
        with patch.object(downloads.time, "sleep") as sleep:
            with pytest.raises(HfHubHTTPError):
                downloads.snapshot_download("public/missing-model")
            download.assert_called_once()
            sleep.assert_not_called()
