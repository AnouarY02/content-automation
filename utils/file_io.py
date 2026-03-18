"""
Atomic file I/O helpers.

Alle kritieke JSON-schrijfoperaties lopen via deze module.
Gebruikt temp-file + os.replace() zodat een crash of kill -9
nooit een half-geschreven bestand achterlaat.

os.replace() is atomisch op POSIX én op Windows (zelfde volume).
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def atomic_write_json(
    path: Path | str,
    data: Any,
    *,
    indent: int = 2,
    ensure_ascii: bool = False,
    **kwargs,
) -> None:
    """
    Schrijf JSON naar path via een temp-bestand.

    Args:
        path:         Doelpad (wordt aangemaakt inclusief mappen).
        data:         JSON-serialiseerbaar object.
        indent:       Inspringing (default 2).
        ensure_ascii: Zie json.dumps (default False).
        **kwargs:     Doorgegeven aan json.dumps (bijv. default=str).

    Raises:
        OSError / TypeError: bij schrijf- of serialisatiefout.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    try:
        tmp.write_text(
            json.dumps(data, indent=indent, ensure_ascii=ensure_ascii, **kwargs),
            encoding="utf-8",
        )
        os.replace(tmp, path)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise


def atomic_write_text(path: Path | str, content: str) -> None:
    """
    Schrijf tekst naar path via een temp-bestand.

    Args:
        path:    Doelpad (wordt aangemaakt inclusief mappen).
        content: Te schrijven tekst (UTF-8).

    Raises:
        OSError: bij schrijffout.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    try:
        tmp.write_text(content, encoding="utf-8")
        os.replace(tmp, path)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise
