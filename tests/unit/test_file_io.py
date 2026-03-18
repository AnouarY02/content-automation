"""
Unit tests: utils/file_io.py — atomic JSON/text schrijfoperaties.

Dekt:
  - atomic_write_json(): normaal, nested mappen, default=str, serialisatiefout
  - atomic_write_text(): normaal, nested mappen
  - Crash-safety: tmp-bestand wordt opgeruimd bij fout
"""

import json
from pathlib import Path

import pytest

from utils.file_io import atomic_write_json, atomic_write_text


class TestAtomicWriteJson:
    def test_schrijf_en_lees(self, tmp_path):
        path = tmp_path / "out.json"
        data = {"key": "value", "num": 42}
        atomic_write_json(path, data)
        assert path.exists()
        assert json.loads(path.read_text("utf-8")) == data

    def test_maakt_nested_mappen(self, tmp_path):
        path = tmp_path / "a" / "b" / "c" / "data.json"
        atomic_write_json(path, [1, 2, 3])
        assert json.loads(path.read_text("utf-8")) == [1, 2, 3]

    def test_overschrijft_bestaand(self, tmp_path):
        path = tmp_path / "data.json"
        atomic_write_json(path, {"v": 1})
        atomic_write_json(path, {"v": 2})
        assert json.loads(path.read_text("utf-8"))["v"] == 2

    def test_default_str_serialiseert_datetime(self, tmp_path):
        from datetime import datetime
        path = tmp_path / "dt.json"
        dt = datetime(2026, 3, 10, 12, 0, 0)
        atomic_write_json(path, {"ts": dt}, default=str)
        result = json.loads(path.read_text("utf-8"))
        assert "2026-03-10" in result["ts"]

    def test_unicode_content(self, tmp_path):
        path = tmp_path / "unicode.json"
        atomic_write_json(path, {"tekst": "café ✓ €"})
        result = json.loads(path.read_text("utf-8"))
        assert result["tekst"] == "café ✓ €"

    def test_serialisatiefout_ruimt_tmp_op(self, tmp_path):
        path = tmp_path / "fail.json"
        # set() is niet JSON-serialiseerbaar
        with pytest.raises(TypeError):
            atomic_write_json(path, {"bad": {1, 2, 3}})
        assert not path.exists()
        assert not path.with_suffix(".tmp").exists()


class TestAtomicWriteText:
    def test_schrijf_en_lees(self, tmp_path):
        path = tmp_path / "out.txt"
        atomic_write_text(path, "hello world")
        assert path.read_text("utf-8") == "hello world"

    def test_maakt_nested_mappen(self, tmp_path):
        path = tmp_path / "x" / "y" / "file.txt"
        atomic_write_text(path, "nested")
        assert path.read_text("utf-8") == "nested"

    def test_overschrijft_bestaand(self, tmp_path):
        path = tmp_path / "file.txt"
        atomic_write_text(path, "v1")
        atomic_write_text(path, "v2")
        assert path.read_text("utf-8") == "v2"

    def test_string_pad_werkt(self, tmp_path):
        path = str(tmp_path / "str_path.txt")
        atomic_write_text(path, "content")
        assert Path(path).read_text("utf-8") == "content"
