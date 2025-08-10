import yaml
from pathlib import Path
import logging

from tests.conftest import config_with_temp

logger = logging.getLogger(__name__)

def test_default_config_creation(config_with_temp):
    config, path = config_with_temp
    logger.info("Temp config path: %s", path)

    assert not path.exists()
    config.load_config()
    logger.info("After load_config, exists? %s", path.exists())
    assert path.exists()

    with open(path) as f:
        saved = yaml.safe_load(f)

    # Compare the default values
    from src.config import DEFAULT_CONFIG
    assert saved == DEFAULT_CONFIG

def test_relative_path_resolves(config_with_temp):
    config, path = config_with_temp
    logger.info("Relative folio path: %s", "data/testfolio.xlsx")
    yaml.safe_dump({"folio_path": "data/testfolio.xlsx"}, open(path, "w"))
    config.load_config()

    logger.info("After load_config: %s", config.FOLIO_PATH)
    assert config.FOLIO_PATH.is_absolute()
    assert "testfolio.xlsx" in str(config.FOLIO_PATH)

def test_absolute_path_kept(config_with_temp):
    config, path = config_with_temp
    abs_path = Path(path.parent) / "absolute.xlsx"
    yaml.safe_dump({"folio_path": str(abs_path)}, open(path, "w"))
    config.load_config()

    assert config.FOLIO_PATH == abs_path.resolve()
