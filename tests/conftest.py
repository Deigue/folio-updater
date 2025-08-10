import shutil
import pytest

@pytest.fixture
def temp_project(tmp_path):
    """
    Creates a temporary project structure with an isolated config.yaml.
    """
    project_root = tmp_path
    (project_root / "src").mkdir()
    (project_root / "data").mkdir()

    # Copy minimal source files needed for config + setup_data
    import sys
    sys.path.insert(0, str(project_root))

    yield project_root

    # Cleanup after test
    shutil.rmtree(project_root)

@pytest.fixture
def temp_config_path(temp_project):
    """
    Returns a path to a config.yaml inside the temp project.
    """
    return temp_project / "config.yaml"
