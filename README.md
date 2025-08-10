# Folio Updater

A template for a portfolio updater project.

## Features

- Reads excel data
- TODO: display, query, write ticker info
- TODO: import txns and format them
- TODO: visualize data
- TODO: adapt calculation sheets and features

## Requirements

See `requirements.txt` for the full list of dependencies.

## Setup

1. Clone the repository.
2. Install dependencies:
   ```
   pip install -r requirements.txt
   ```
## Configuration (`config.yaml`)

This project uses a `config.yaml` file at the root of the repository to store file paths and sheet names.  
It is **auto-generated** with default values the first time you run the application.

### Default structure
```yaml
folio_path: data/folio.xlsx
sheets:
  tickers: Tickers
```

| Key              | Description                                                                                                                                                                                                                                                            |
| ---------------- |------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| **`folio_path`** | Path to your portfolio Excel file. If this is **relative**, it is treated as relative to the project root (default: `data/folio.xlsx`). If you set an **absolute path** (e.g. `C:/Finance/folio.xlsx`), the project will use it directly without creating any folders. |
| **`sheets`**     | A mapping of logical sheet names (keys) to actual Excel sheet names (values). This allows you to rename sheets without touching the code.                                                                                                                              |


## Usage

Run the notebook `test.ipynb` to test.

## Development

### Setting up nbstripout for Contribution

To ensure clean diffs and avoid committing notebook outputs, set up [nbstripout](https://github.com/kynan/nbstripout):

```bash
nbstripout --install
```

This will automatically strip output cells from Jupyter notebooks before committing changes.

Note: Add to IDE path (.venv $env:PATH) if needed by virtual environment terminal.