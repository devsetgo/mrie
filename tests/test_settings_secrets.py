# -*- coding: utf-8 -*-
"""
Docker-secrets support in src/settings.py: values mounted as files under
/run/secrets (or SECRETS_DIR) are read as settings, with real env vars
taking precedence. The test constructs a fresh Settings subclass pointed at
a temp directory rather than touching the module-level `settings`.
"""

import pytest
from pydantic_settings import SettingsConfigDict

from src.settings import Settings


def _settings_for(secrets_dir):
    class _Settings(Settings):
        model_config = SettingsConfigDict(
            env_file=None, secrets_dir=str(secrets_dir), extra="allow"
        )

    return _Settings


@pytest.fixture
def secret_files(tmp_path, monkeypatch):
    # The suite's own env supplies these; remove them so the files are the
    # only source being tested.
    for name in ("PHRASE", "SALT", "OPENAI_KEY", "SESSION_SECRET_KEY"):
        monkeypatch.delenv(name, raising=False)
    # Trailing newline, as `echo`/editors leave it: PHRASE and SALT derive
    # the Fernet key, so a stray "\n" would make every note undecryptable.
    (tmp_path / "phrase").write_text("the-phrase\n")
    (tmp_path / "salt").write_text("the-salt\n")
    (tmp_path / "openai_key").write_text("sk-from-file\n")
    return tmp_path


def test_secrets_are_read_from_files_without_the_trailing_newline(secret_files):
    s = _settings_for(secret_files)()
    assert s.phrase.get_secret_value() == "the-phrase"
    assert s.salt.get_secret_value() == "the-salt"
    assert s.openai_key.get_secret_value() == "sk-from-file"


def test_env_var_overrides_secret_file(secret_files, monkeypatch):
    monkeypatch.setenv("OPENAI_KEY", "sk-from-env")
    s = _settings_for(secret_files)()
    assert s.openai_key.get_secret_value() == "sk-from-env"
    assert s.phrase.get_secret_value() == "the-phrase"


def test_optional_secret_stays_unset_without_a_file(secret_files):
    (secret_files / "openai_key").unlink()
    assert _settings_for(secret_files)().openai_key is None
