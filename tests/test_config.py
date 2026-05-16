from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from jules_agent.config import Config, load_config


class TestConfig(unittest.TestCase):
    def test_config_from_dict(self) -> None:
        data = {
            "api_key": "test-key",
            "repo": "owner/repo",
            "codex_bin": "/path/to/codex",
            "base_url": "https://example.com/v1",
        }
        config = Config.from_dict(data)
        self.assertEqual(config.api_key, "test-key")
        self.assertEqual(config.repo, "owner/repo")
        self.assertEqual(config.codex_bin, "/path/to/codex")
        self.assertEqual(config.base_url, "https://example.com/v1")

    def test_config_defaults(self) -> None:
        config = Config.from_dict({})
        self.assertIsNone(config.api_key)
        self.assertIsNone(config.repo)
        self.assertEqual(config.codex_bin, "codex")
        self.assertEqual(config.base_url, "https://jules.googleapis.com/v1alpha")

    def test_load_config_merging(self) -> None:
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
                    self.assertEqual(config.api_key, "cwd-key")
                    # home-repo should be kept
                    self.assertEqual(config.repo, "home-repo")
                    # cwd-codex should be used
                    self.assertEqual(config.codex_bin, "cwd-codex")

    def test_load_config_from_path(self) -> None:
        with TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.toml"
            config_path.write_text('api_key = "secret"\nrepo = "acme/roadrunner"', encoding="utf-8")

            config = load_config(config_path)
            self.assertEqual(config.api_key, "secret")
            self.assertEqual(config.repo, "acme/roadrunner")

    def test_load_config_invalid_toml_ignored(self) -> None:
        with TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.toml"
            config_path.write_text("invalid = toml [", encoding="utf-8")

            config = load_config(config_path)
            self.assertEqual(config.codex_bin, "codex")  # Still has defaults
