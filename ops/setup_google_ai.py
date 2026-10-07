"""Masked, local-only key entry. Never puts a key in shell history or the browser."""

import getpass
import os
from pathlib import Path


def main():
    root = Path(__file__).resolve().parents[1]
    key = getpass.getpass("Google Gemini API key (hidden; saved only on this Mac): ").strip()
    if not key or any(c.isspace() for c in key):
        raise ValueError("Enter a nonempty API key without whitespace")
    folder = root / ".runtime"
    folder.mkdir(exist_ok=True)
    path = folder / "gemini-api-key"
    if path.is_symlink():
        raise ValueError("Refusing a symbolic-link key file")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as file:
        os.fchmod(file.fileno(), 0o600)
        file.write(key + "\n")
    print("Key saved privately. Dashboard → Hedge decisions → Enable AI. No trading enabled.")


if __name__ == "__main__":
    main()
