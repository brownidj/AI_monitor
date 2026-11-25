import socket
import sqlite3
from contextlib import closing
from unittest.mock import patch

import pytest

from desktop_entry import DESKTOP_PORT, bootstrap, create_listener


def test_desktop_import_is_private_preserves_history_and_does_not_overwrite_edits(tmp_path):
    source = tmp_path / "original"
    (source / "data").mkdir(parents=True)
    (source / ".env").write_text(
        "MONITOR_API_TOKEN=test-local-token\nOPENAI_ADMIN_KEY=fake-secret\n"
        "MONITOR_DATABASE_PATH=/some/old/path.sqlite3\nMONITOR_SCHEDULER_ENABLED=false\n"
    )
    with closing(sqlite3.connect(source / "data/monitor.sqlite3")) as connection:
        connection.execute("CREATE TABLE preserved (value TEXT)")
        connection.execute("INSERT INTO preserved VALUES ('saved history')")
        connection.commit()
    support = tmp_path / "support"
    settings = bootstrap(support, source)
    assert settings.database_path == support / "monitor.sqlite3"
    assert settings.scheduler_enabled is True
    assert settings.openai_admin_key.get_secret_value() == "fake-secret"
    assert support.stat().st_mode & 0o777 == 0o700
    assert (support / "config.env").stat().st_mode & 0o777 == 0o600
    with closing(sqlite3.connect(support / "monitor.sqlite3")) as connection:
        assert connection.execute("SELECT value FROM preserved").fetchone()[0] == "saved history"
        connection.execute("INSERT INTO preserved VALUES ('new app data')")
        connection.commit()
    (support / "config.env").write_text("MONITOR_API_TOKEN=replacement\nOPENAI_ADMIN_KEY=new-key\n")
    settings = bootstrap(support, source)
    assert settings.api_token.get_secret_value() == "replacement"
    with closing(sqlite3.connect(support / "monitor.sqlite3")) as connection:
        assert connection.execute("SELECT COUNT(*) FROM preserved").fetchone()[0] == 2


def test_new_mac_install_creates_local_token_without_provider_credential(tmp_path):
    settings = bootstrap(tmp_path / "support", None)
    assert len(settings.api_token.get_secret_value()) >= 32
    assert settings.openai_admin_key.get_secret_value() == ""


def test_fixed_listener_defaults_to_8781():
    assert DESKTOP_PORT == 8781
    with patch("desktop_entry.socket.socket") as factory:
        create_listener()
        factory.return_value.bind.assert_called_once_with(("127.0.0.1", 8781))


def test_occupied_port_fails_without_choosing_another_listener():
    # Isolate the conflict test from the real app, which may already be using 8781.
    with socket.socket() as occupied:
        occupied.bind(("127.0.0.1", 0))
        occupied.listen()
        with patch("desktop_entry.DESKTOP_PORT", occupied.getsockname()[1]):
            with pytest.raises(RuntimeError, match="requires local port 8781"):
                create_listener()
