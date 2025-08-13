import time

import pytest
import logging
from pathlib import Path
from src import folio_setup

logger = logging.getLogger(__name__)

def test_folio_created_and_deleted(config_with_temp):
    config, path = config_with_temp
    config.load_config()

    # Folio should not exist before
    folio_file = path.parent / "data" / "folio.xlsx"
    if folio_file.exists():
        folio_file.unlink() # pragma: no cover

    folio_setup.ensure_folio_exists()
    logger.info("Folio file path: %s", folio_file)
    assert folio_file.exists()

    # Capture last modified time
    mtime_before = folio_file.stat().st_mtime
    # Wait a bit to ensure detectable mtime change if rewritten
    time.sleep(0.1)
    # Nothing happens when folio already exists
    folio_setup.ensure_folio_exists()
    # Assert file still exists
    assert folio_file.exists()
    # Assert mtime unchanged
    assert folio_file.stat().st_mtime == mtime_before, "File was unexpectedly modified"

    # Cleanup
    folio_file.unlink()
    assert not folio_file.exists()

def test_folio_not_exists(config_with_temp):
    config, path = config_with_temp

    # Create a non-existent absolute path
    missing_path = Path(config.PROJECT_ROOT) / "nonexistent_folder" / "folio.xlsx"
    assert not missing_path.exists()

    import yaml
    yaml.safe_dump({"folio_path": str(missing_path)}, open(path, "w"))
    config.load_config()

    with pytest.raises(FileNotFoundError):
        folio_setup.ensure_folio_exists()