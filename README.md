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