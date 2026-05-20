from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from jules_agent.config import Config, load_config

def test_config_from_dict() -> None:
    data = {
        "api_key": "test-key",
        "repo": "owner/repo",
        "codex_bin": "/path/to/codex",
        "base_url": "https://example.com/v1",
    }
    config = Config.from_dict(data)
    assert config.api_key == "test-key"
    assert config.repo == "owner/repo"
    assert config.codex_bin == "/path/to/codex"
    assert config.base_url == "https://example.com/v1"

def test_config_defaults() -> None:
    config = Config.from_dict({})
    assert config.api_key is None
    assert config.repo is None
    assert config.codex_bin == "codex"
    assert config.base_url == "https://jules.googleapis.com/v1alpha"

def test_load_config_merging() -> None:
    with TemporaryDirectory() as tmpdir:
        tmpdir_path = Path(tmpdir)

        # Create a mock home and cwd structure
        home_dir = tmpdir_path / "home"
        home_dir.mkdir()
        cwd_dir = tmpdir_path / "cwd"
        cwd_dir.mkdir()

        config_home = home_dir / ".jules-agent.toml"
        config_cwd = cwd_dir / "jules-agent.toml"

        config_home.write_text('api_key = "home-key"\nrepo = "home-repo"', encoding="utf-8")
        config_cwd.write_text('api_key = "cwd-key"\ncodex_bin = "cwd-codex"', encoding="utf-8")

        with patch("jules_agent.config.Path.home", return_value=home_dir):
            with patch("jules_agent.config.Path.cwd", return_value=cwd_dir):
                config = load_config()

                # cwd-key should override home-key
                assert config.api_key == "cwd-key"
                # home-repo should be kept
                assert config.repo == "home-repo"
                # cwd-codex should be used
                assert config.codex_bin == "cwd-codex"

def test_load_config_from_path() -> None:
    with TemporaryDirectory() as tmpdir:
        config_path = Path(tmpdir) / "config.toml"
        config_path.write_text('api_key = "secret"\nrepo = "acme/roadrunner"', encoding="utf-8")

        config = load_config(config_path)
        assert config.api_key == "secret"
        assert config.repo == "acme/roadrunner"

def test_load_config_invalid_toml_ignored() -> None:
    with TemporaryDirectory() as tmpdir:
        config_path = Path(tmpdir) / "config.toml"
        config_path.write_text("invalid = toml [", encoding="utf-8")

        config = load_config(config_path)
        assert config.codex_bin == "codex"  # Still has defaults
