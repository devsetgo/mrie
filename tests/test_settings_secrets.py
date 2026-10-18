# -*- coding: utf-8 -*-
"""
Docker-secrets support in src/settings.py: values mounted as files under
/run/secrets (or SECRETS_DIR) are read as settings, with real env vars
taking precedence. The test constructs a fresh Settings subclass pointed at
a temp directory rather than touching the module-level `settings`.
"""

import pytest
from pydantic_settings import SettingsConfigDict

from src.settings import DatabaseDriverEnum, Settings


def _settings_for(secrets_dir):
    class _Settings(Settings):
        model_config = SettingsConfigDict(
            env_file=None,
            secrets_dir=str(secrets_dir),
            extra="allow",
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


def test_session_secret_key_is_required_outside_dev(secret_files, monkeypatch):
    # A per-process random key would differ between uvicorn workers.
    monkeypatch.setenv("RELEASE_ENV", "prd")
    with pytest.raises(ValueError, match="SESSION_SECRET_KEY"):
        _settings_for(secret_files)()


def test_short_session_secret_key_is_refused_outside_dev(secret_files, monkeypatch):
    monkeypatch.setenv("RELEASE_ENV", "prd")
    monkeypatch.setenv("SESSION_SECRET_KEY", "too-short")
    with pytest.raises(ValueError, match="at least 32"):
        _settings_for(secret_files)()


@pytest.mark.parametrize("name", ["REGISTRATION_BOOTSTRAP_TOKEN", "ADMIN_USER"])
def test_unfilled_template_value_is_refused_outside_dev(
    secret_files, monkeypatch, name
):
    # deploy/.env.pre-prd's marker must never reach production as a value.
    monkeypatch.setenv("RELEASE_ENV", "prd")
    monkeypatch.setenv("SESSION_SECRET_KEY", "k" * 32)
    monkeypatch.setenv(name, "__FILL_ME__")
    with pytest.raises(ValueError, match=name):
        _settings_for(secret_files)()


def test_filled_production_settings_load(secret_files, monkeypatch):
    monkeypatch.setenv("RELEASE_ENV", "prd")
    monkeypatch.setenv("SESSION_SECRET_KEY", "k" * 32)
    assert _settings_for(secret_files)().release_env == "prd"


def test_session_secret_key_is_generated_in_dev(secret_files, monkeypatch):
    monkeypatch.setenv("RELEASE_ENV", "dev")
    assert _settings_for(secret_files)().session_secret_key.get_secret_value()


def test_db_driver_defaults_to_sqlite_when_unset(secret_files, monkeypatch):
    # The suite sets DB_DRIVER=memory; an unset one used to fail validation.
    monkeypatch.delenv("DB_DRIVER", raising=False)
    monkeypatch.setenv("RELEASE_ENV", "dev")
    assert _settings_for(secret_files)().db_driver == DatabaseDriverEnum.sqlite


def test_startup_error_does_not_print_secrets(secret_files, monkeypatch):
    # hide_input_in_errors is inherited from Settings (pydantic merges
    # model_config), so this exercises the real setting.
    monkeypatch.setenv("RELEASE_ENV", "prd")
    with pytest.raises(ValueError) as excinfo:
        _settings_for(secret_files)()
    assert "the-phrase" not in str(excinfo.value)
    assert "the-salt" not in str(excinfo.value)
