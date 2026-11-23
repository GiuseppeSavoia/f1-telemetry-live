#!/bin/bash
# Doppio clic nel Finder: avvia la dashboard in modalità automatica
cd "$(dirname "$0")"
exec .venv/bin/python server.py "$@"
