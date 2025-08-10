import yaml
from src import setup_data

def test_folio_created_and_deleted(config_with_temp):
    config, path = config_with_temp
    config.load_config()

    # Folio should not exist before
    folio_file = path.parent / "data" / "folio.xlsx"
    if folio_file.exists():
        folio_file.unlink()

    setup_data.ensure_folio_exists()
    assert folio_file.exists()

    # Cleanup
    folio_file.unlink()
    assert not folio_file.exists()
