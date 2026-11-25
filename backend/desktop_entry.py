"""Bundled macOS backend: private configuration, one-time import and fixed port 8781."""

import argparse
import asyncio
import json
import os
import secrets
import socket
import sqlite3
from contextlib import closing
from pathlib import Path

import httpx
import uvicorn

from app.config import Settings
from app.main import create_app

DESKTOP_PORT = 8781


def create_listener():
    listener = socket.socket()
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        listener.bind(("127.0.0.1", DESKTOP_PORT))
    except OSError:
        listener.close()
        raise RuntimeError(
            "AI Billing Monitor requires local port 8781, but it is occupied or unavailable. "
            "Close the other instance or service using that port, then try again."
        ) from None
    return listener


def bootstrap(support: Path, source: Path | None) -> Settings:
    support.mkdir(parents=True, exist_ok=True)
    support.chmod(0o700)
    environment = support / "config.env"
    if not environment.exists():
        original = source / ".env" if source else None
        content = (
            original.read_text()
            if original and original.exists()
            else ("MONITOR_API_TOKEN=" + secrets.token_urlsafe(32) + "\nOPENAI_ADMIN_KEY=\n")
        )
        environment.write_text(content)
    environment.chmod(0o600)
    database = support / "monitor.sqlite3"
    original_database = source / "data" / "monitor.sqlite3" if source else None
    if not database.exists() and original_database and original_database.exists():
        temporary = support / "import.sqlite3"
        with (
            closing(sqlite3.connect(original_database)) as old,
            closing(sqlite3.connect(temporary)) as new,
        ):
            old.backup(new)
        temporary.replace(database)
    # Explicit desktop overrides ensure imported .env paths never send data back to the project.
    return Settings(
        _env_file=environment,
        MONITOR_DATABASE_PATH=database,
        MONITOR_SCHEDULER_ENABLED=True,
    )


def finish_import(config: Settings, support: Path):
    """Retire the previously used local scheduler only after the bundled server starts."""
    marker = support / "import-complete.json"
    if marker.exists():
        return
    retired = False
    try:
        with httpx.Client(
            base_url="http://127.0.0.1:8780",
            timeout=3,
            headers={"Authorization": "Bearer " + config.api_token.get_secret_value()},
        ) as client:
            health = client.get("/health")
            if health.is_success and health.json().get("version") == "0.2.0":
                response = client.get("/v1/monitor/settings")
                if response.is_success:
                    preferences = response.json()
                    preferences.pop("budget_revision", None)
                    preferences["enabled"] = False
                    retired = client.put("/v1/monitor/settings", json=preferences).is_success
    except (httpx.HTTPError, ValueError):
        pass
    marker.write_text(json.dumps({"previous_scheduler_paused": retired}))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--support", type=Path, required=True)
    parser.add_argument("--import-dir", type=Path)
    parser.add_argument("--ready-file", type=Path, required=True)
    parser.add_argument("--parent-pid", type=int, required=True)
    args = parser.parse_args()
    config = bootstrap(args.support, args.import_dir)
    try:
        listener = create_listener()
    except RuntimeError as error:
        args.ready_file.write_text(json.dumps({"error": str(error)}))
        raise SystemExit(str(error)) from None
    port = DESKTOP_PORT

    class DesktopServer(uvicorn.Server):
        async def startup(self, sockets=None):
            await super().startup(sockets)
            if self.should_exit:
                return
            if args.import_dir:
                await asyncio.to_thread(finish_import, config, args.support)
            args.ready_file.write_text(json.dumps({"port": port, "pid": os.getpid()}))
            self.parent_task = asyncio.create_task(self.watch_parent())

        async def watch_parent(self):
            while not self.should_exit:
                await asyncio.sleep(2)
                try:
                    os.kill(args.parent_pid, 0)
                except ProcessLookupError:
                    self.should_exit = True

    server = DesktopServer(
        uvicorn.Config(
            create_app(config),
            host="127.0.0.1",
            port=port,
            log_level="warning",
            access_log=False,
            loop="asyncio",
            http="h11",
            ws="none",
        )
    )
    try:
        server.run(sockets=[listener])
    finally:
        args.ready_file.unlink(missing_ok=True)
        listener.close()


if __name__ == "__main__":
    main()
