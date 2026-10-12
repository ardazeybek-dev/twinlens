from __future__ import annotations

from pathlib import Path

import pytest

from twinlens.demo import DemoFile, generate


@pytest.fixture(scope="session")
def demo_library(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, list[DemoFile]]:
    root = tmp_path_factory.mktemp("library")
    return root, generate(root, scenes=6, singles=4)
