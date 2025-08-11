import shutil
import pytest
import sys
import logging

def _close_log_handlers():
    for handler in logging.root.handlers[:]:
        handler.close()
        logging.root.removeHandler(handler)

@pytest.fixture
def config_with_temp(tmp_path):
    """
    Creates a temporary project structure with an isolated config.yaml.
    """
    project_root = tmp_path
    (project_root / "src").mkdir()
    (project_root / "data").mkdir()

    temp_config_path = project_root / "config.yaml"

    """
    Reloads src.config fresh and patches CONFIG_PATH + PROJECT_ROOT
    to use the temporary config path for isolation in tests.
    """
    sys.modules.pop("src.config", None)
    from src import config

    config.CONFIG_PATH = temp_config_path
    config.PROJECT_ROOT = temp_config_path.parent

    yield config, temp_config_path
    _close_log_handlers()  # <-- close before deleting temp dir
    shutil.rmtree(project_root, ignore_errors=True)
    if str(project_root) in sys.path:
        sys.path.remove(str(project_root))
