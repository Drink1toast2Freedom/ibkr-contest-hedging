#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
exec .venv/bin/python -m hedge.cli serve --demo "$@"
