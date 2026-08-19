$ErrorActionPreference = 'Stop'
& .\.venv\Scripts\python.exe -m statebench doctor
& .\.venv\Scripts\python.exe -m statebench run --config configs/final.yaml --resume
& .\.venv\Scripts\python.exe -m statebench analyze --config configs/final.yaml
