"""Query documented read-only Codex methods through the installed CLI's own sign-in."""

import asyncio
import json
import os
import re
import shutil
from datetime import UTC, datetime
from pathlib import Path

from pydantic import AwareDatetime, Field

from ..models import Model
from .openai_costs import CollectorError


class CodexWindow(Model):
    label: str
    used_percent: float = Field(ge=0, le=100)
    duration_minutes: int = Field(gt=0)
    resets_at: AwareDatetime


class CodexUsage(Model):
    windows: list[CodexWindow]
    plan: str | None = None
    credits_balance: str | None = None
    unlimited_credits: bool = False


def find_codex(configured: Path | None = None) -> Path | None:
    if configured is not None:
        return configured if configured.is_file() and os.access(configured, os.X_OK) else None
    found = shutil.which("codex")
    candidates = ([Path(found)] if found else []) + [
        Path.home() / ".local/bin/codex",
        Path("/opt/homebrew/bin/codex"),
        Path("/usr/local/bin/codex"),
    ]
    nvm = list((Path.home() / ".nvm/versions/node").glob("*/bin/codex"))
    candidates += sorted(
        nvm, key=lambda p: tuple(map(int, re.findall(r"\d+", p.parts[-3]))), reverse=True
    )
    return next((p for p in candidates if p.is_file() and os.access(p, os.X_OK)), None)


def parse_limits(data: dict) -> CodexUsage:
    buckets = data.get("rateLimitsByLimitId") or {"codex": data.get("rateLimits")}
    windows, plan, balance, unlimited = [], None, None, False
    for identifier, bucket in buckets.items():
        if not isinstance(bucket, dict):
            continue
        plan = plan or bucket.get("planType")
        credit = bucket.get("credits") or {}
        if identifier == "codex" or balance is None:
            balance = credit.get("balance")
            unlimited = bool(credit.get("unlimited", False))
        for field in ("primary", "secondary"):
            window = bucket.get(field)
            if window is None:
                continue
            windows.append(
                CodexWindow(
                    label=bucket.get("limitName") or identifier,
                    used_percent=window["usedPercent"],
                    duration_minutes=window["windowDurationMins"],
                    resets_at=datetime.fromtimestamp(window["resetsAt"], UTC),
                )
            )
    if not windows:
        raise CollectorError("Codex did not report an allowance window. Sign in with ChatGPT.")
    return CodexUsage(
        windows=windows, plan=plan, credits_balance=balance, unlimited_credits=unlimited
    )


async def collect_codex(executable: Path | None = None) -> CodexUsage:
    executable = find_codex(executable)
    if executable is None:
        raise CollectorError("Codex CLI was not found. Install it and sign in with ChatGPT.")
    process = None
    try:
        async with asyncio.timeout(40):
            env = os.environ.copy()
            env["PATH"] = str(executable.parent) + os.pathsep + env.get("PATH", os.defpath)
            process = await asyncio.create_subprocess_exec(
                str(executable),
                "app-server",
                "--listen",
                "stdio://",
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                env=env,
                limit=2**20,
            )

            async def send(message):
                process.stdin.write((json.dumps(message) + "\n").encode())
                await process.stdin.drain()

            async def reply(identifier):
                for _ in range(100):
                    line = await process.stdout.readline()
                    if not line:
                        raise ValueError()
                    value = json.loads(line)
                    if value.get("id") == identifier:
                        if "error" in value:
                            raise CollectorError(
                                "Codex usage is unavailable. Check CLI version and ChatGPT sign-in."
                            )
                        return value["result"]
                raise ValueError()

            await send(
                {
                    "id": 0,
                    "method": "initialize",
                    "params": {"clientInfo": {"name": "ai_billing_monitor", "version": "0.5.0"}},
                }
            )
            await reply(0)
            await send({"method": "initialized"})
            await send({"id": 1, "method": "account/rateLimits/read"})
            return parse_limits(await reply(1))
    except CollectorError:
        raise
    except (OSError, ValueError, KeyError, TypeError, OverflowError, TimeoutError):
        raise CollectorError(
            "Codex allowance could not be read. Check CLI sign-in and retry."
        ) from None
    finally:
        if process is not None and process.returncode is None:
            try:
                process.terminate()
                await asyncio.wait_for(process.wait(), timeout=3)
            except (TimeoutError, ProcessLookupError):
                if process.returncode is None:
                    process.kill()
                    await process.wait()
