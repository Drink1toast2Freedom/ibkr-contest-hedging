#!/bin/zsh
cd "$(dirname "$0")" || exit 1
exec .venv/bin/python ops/run_practice.py
