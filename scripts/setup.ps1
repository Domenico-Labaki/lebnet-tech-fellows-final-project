$ErrorActionPreference = 'Stop'
uv python install 3.11
uv venv --python 3.11
uv pip install --python .\.venv\Scripts\python.exe -e '.[dev]'
