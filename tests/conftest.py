import os

import pytest

os.environ.setdefault("CITE_DESK_WARM", "0")
os.environ.pop("CITE_DESK_MODE", None)

from cite_desk.models import FakeEmbedder, FakeReranker  # noqa: E402
from cite_desk.service import Engine  # noqa: E402


@pytest.fixture(scope="session")
def engine(tmp_path_factory):
    """Both collections indexed with tiny deterministic models, in a temp dir: no downloads, real corpus."""
    return Engine(embedder=FakeEmbedder(), reranker=FakeReranker(), data_dir=tmp_path_factory.mktemp("idx"))
