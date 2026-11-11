"""Improvement #9: machine-specific config overlay."""
from rd import config


def test_overlay_merges_nested(tmp_path):
    (tmp_path / "config.toml").write_text('[screen]\nparallel = 4\ntriage_model = "haiku"\n[deep]\nparallel = 3\n')
    (tmp_path / "config.local.toml").write_text("[screen]\nparallel = 1\n")
    cfg = config.load(tmp_path / "config.toml")
    assert cfg["screen"] == {"parallel": 1, "triage_model": "haiku"} and cfg["deep"]["parallel"] == 3


def test_no_overlay(tmp_path):
    (tmp_path / "config.toml").write_text("[a]\nb = 1\n")
    assert config.load(tmp_path / "config.toml") == {"a": {"b": 1}}


def test_vm_overlay_is_valid_and_serial(tmp_path):
    import tomllib
    from conftest import ROOT
    over = tomllib.loads((ROOT / "deploy/vm/config.local.toml").read_text())
    cfg = config.merge(config.load(ROOT / "config.toml", local=tmp_path / "none"), over)
    assert cfg["screen"]["parallel"] == 1 and cfg["deep"]["parallel"] == 1


def test_vm_units_reference_real_paths():
    from conftest import ROOT
    for unit in ("research-digest.service", "research-digest-bot.service"):
        text = (ROOT / "deploy/vm" / unit).read_text()
        assert "MemoryMax=" in text and "EnvironmentFile=/home/ubuntu/research-digest/secrets/digest.env" in text
    assert "Asia/Bishkek" in (ROOT / "deploy/vm/research-digest.timer").read_text()
