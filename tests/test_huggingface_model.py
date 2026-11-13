"""Offline tests for the Hugging Face model downloader.

The Hub API is replaced by a fake, so these tests need no network. The
end-to-end download against the real Hub is in test_repositories.toml.
"""

import hashlib
from pathlib import Path

import pytest

import datahugger
from datahugger.resolvers import _resolve_service
from datahugger.services import HuggingFaceDataset
from datahugger.services import HuggingFaceModel

COMMIT = "0123456789abcdef0123456789abcdef01234567"
WEIGHTS = b"fake safetensors weights"
CONFIG = b'{"model_type": "bert"}'


class FakeResponse:
    def __init__(self, status_code=200, json=None, content=b"", links=None):
        self.status_code = status_code
        self._json = json
        self.content = content
        self.links = links or {}
        self.headers = {"content-length": str(len(content))}

    def json(self):
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def iter_content(self, chunk_size=1):
        yield self.content


def fake_hub(paged=False, status=200):
    """Return a fake `requests.get` for one model, and the calls made."""
    api = "https://huggingface.co/api/models/acme/tiny"
    tree = [
        {"type": "file", "path": "config.json", "size": len(CONFIG), "oid": "x"},
        {"type": "directory", "path": "onnx", "oid": "y"},
        {
            "type": "file",
            "path": "model.safetensors",
            "size": len(WEIGHTS),
            "oid": "z",
            "lfs": {"oid": hashlib.sha256(WEIGHTS).hexdigest()},
        },
        {"type": "file", "path": "onnx/model.onnx", "size": 3, "oid": "w"},
    ]
    files = {"config.json": CONFIG, "model.safetensors": WEIGHTS}
    calls = []

    def get(url, stream=False, **kwargs):
        calls.append(url)
        if status != 200:
            return FakeResponse(status)
        if url.startswith(f"{api}/revision/"):
            return FakeResponse(json={"sha": COMMIT})
        if url == f"{api}/tree/{COMMIT}?recursive=true":
            if paged:
                return FakeResponse(
                    json=tree[:2], links={"next": {"url": f"{api}/page2"}}
                )
            return FakeResponse(json=tree)
        if url == f"{api}/page2":
            return FakeResponse(json=tree[2:])
        prefix = f"https://huggingface.co/acme/tiny/resolve/{COMMIT}/"
        if url.startswith(prefix):
            return FakeResponse(content=files.get(url[len(prefix) :], b"onnx"))
        return FakeResponse(404)

    return get, calls


@pytest.fixture
def hub(monkeypatch):
    def install(**kwargs):
        get, calls = fake_hub(**kwargs)
        monkeypatch.setattr("datahugger.services.requests.get", get)
        monkeypatch.setattr("datahugger.base.requests.get", get)
        return calls

    return install


@pytest.mark.parametrize(
    "url, expected",
    [
        ("https://huggingface.co/datasets/wikitext", HuggingFaceDataset),
        ("https://huggingface.co/google/vit-base-patch16-224", HuggingFaceModel),
        ("https://huggingface.co/acme/tiny/tree/v1.0", HuggingFaceModel),
    ],
)
def test_resolve_service(url, expected):
    assert _resolve_service(url) is expected


@pytest.mark.parametrize(
    "url, record_id, version",
    [
        ("https://huggingface.co/acme/tiny", "acme/tiny", None),
        ("https://huggingface.co/acme/tiny/", "acme/tiny", None),
        ("https://huggingface.co/acme/tiny-v1.5_x", "acme/tiny-v1.5_x", None),
        ("https://huggingface.co/acme/tiny/tree/v1.0", "acme/tiny", "v1.0"),
        ("https://huggingface.co/acme/tiny/blob/main/a.json", "acme/tiny", "main"),
        ("https://huggingface.co/acme/tiny?library=x", "acme/tiny", None),
    ],
)
def test_parse_model_url(url, record_id, version):
    params = HuggingFaceModel(url)._params
    assert params["record_id"] == record_id
    assert params["version"] == version


@pytest.mark.parametrize(
    "url",
    [
        "https://huggingface.co/spaces/acme/demo",
        "https://huggingface.co/docs/transformers",
        "https://huggingface.co/papers/2401.00001",
        "https://huggingface.co/gpt2",
    ],
)
def test_not_a_model_url(url):
    with pytest.raises(ValueError):
        _ = HuggingFaceModel(url)._params


def test_files_are_pinned_to_one_commit(hub):
    calls = hub()
    files = datahugger.info("https://huggingface.co/acme/tiny").files

    assert [f["name"] for f in files] == [
        "config.json",
        "model.safetensors",
        "onnx/model.onnx",
    ]
    assert all(f"/resolve/{COMMIT}/" in f["link"] for f in files)
    assert calls[0].endswith("/revision/main")
    weights = files[1]
    assert weights["hash_type"] == "sha256"
    assert weights["hash"] == hashlib.sha256(WEIGHTS).hexdigest()
    assert files[0]["hash"] is None  # git blob ids are not file checksums


def test_revision_from_url_and_param(hub):
    calls = hub()
    _ = datahugger.info("https://huggingface.co/acme/tiny/tree/v1.0").files
    assert calls[0].endswith("/revision/v1.0")

    calls = hub()
    _ = datahugger.info(
        "https://huggingface.co/acme/tiny/tree/v1.0", params={"revision": "v2"}
    ).files
    assert calls[0].endswith("/revision/v2")


def test_paged_listing(hub):
    hub(paged=True)
    files = datahugger.info("https://huggingface.co/acme/tiny").files
    assert len(files) == 3


def test_include_and_exclude(hub):
    hub()
    files = datahugger.info(
        "https://huggingface.co/acme/tiny",
        params={"include": "*.safetensors, *.json"},
    ).files
    assert [f["name"] for f in files] == ["config.json", "model.safetensors"]

    files = datahugger.info(
        "https://huggingface.co/acme/tiny", params={"exclude": "onnx/*"}
    ).files
    assert [f["name"] for f in files] == ["config.json", "model.safetensors"]


def test_download(hub, tmpdir):
    hub()
    datahugger.get(
        "https://huggingface.co/acme/tiny",
        tmpdir,
        params={"exclude": "onnx/*"},
        progress=False,
    )
    assert Path(tmpdir, "config.json").read_bytes() == CONFIG
    assert Path(tmpdir, "model.safetensors").read_bytes() == WEIGHTS
    assert not Path(tmpdir, "onnx").exists()


def test_max_file_size(hub, tmpdir):
    hub()
    datahugger.get(
        "https://huggingface.co/acme/tiny",
        tmpdir,
        max_file_size=len(WEIGHTS),
        progress=False,
    )
    assert Path(tmpdir, "config.json").exists()
    assert not Path(tmpdir, "model.safetensors").exists()


@pytest.mark.parametrize("status", [401, 403, 404])
def test_clear_error_for_gated_or_missing_models(hub, status):
    hub(status=status)
    with pytest.raises(ValueError, match="Hugging Face"):
        _ = datahugger.info("https://huggingface.co/acme/tiny").files
