from pathlib import Path

import pytest

from workorder2data.taxonomy import TAXONOMY_DIR

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "taxonomy"


def has_real_taxonomy() -> bool:
    """True when the user's ISO-derived taxonomy/*.csv files are present (they are git-ignored)."""
    return (TAXONOMY_DIR / "equipment_classes.csv").exists()


@pytest.fixture(scope="session")
def taxonomy_dir() -> Path:
    """Real taxonomy if present, otherwise the fictional example taxonomy."""
    return TAXONOMY_DIR if has_real_taxonomy() else FIXTURE_DIR
