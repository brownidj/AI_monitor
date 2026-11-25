"""Read quota through the official Central CLI, verifying and restoring licence selection."""

import asyncio
import json
import os
import re
import shutil
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from ..models import JetBrainsSnapshot, Model
from .openai_costs import CollectorError

ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
SOURCE = re.compile(r"\(license, ID: ([A-Za-z0-9_-]+), valid through [^)]*\)")


class CentralUsage(Model):
    snapshots: list[JetBrainsSnapshot]


def find_central(configured=None):
    if configured is not None:
        candidates = [Path(configured)]
    else:
        candidates = [
            Path(shutil.which("central") or "/nonexistent/central"),
            Path.home() / ".local/bin/central",
            Path.home() / "Applications/JetBrainsCentralCLI/central",
        ]
    return next((p for p in candidates if p.is_file() and os.access(p, os.X_OK)), None)


async def run_central(executable, *arguments):
    process = None
    try:
        async with asyncio.timeout(12):
            process = await asyncio.create_subprocess_exec(
                str(executable),
                *arguments,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                env={**os.environ, "NO_COLOR": "1"},
            )
            stdout, _ = await process.communicate()
            if process.returncode:
                raise CollectorError(
                    "Central quota unavailable. Check Central login and licence access."
                )
            if len(stdout) > 1_000_000:
                raise ValueError("Oversized Central response")
            return ANSI.sub("", stdout.decode())
    except (OSError, TimeoutError, UnicodeError, ValueError):
        raise CollectorError(
            "Central CLI could not be read. Check its installation and login."
        ) from None
    finally:
        if process is not None and process.returncode is None:
            process.kill()
            await process.wait()


def parse_sources(text):
    identities, current = set(), []
    for line in text.splitlines():
        match = SOURCE.search(line)
        if match:
            identities.add(match[1])
            if ", current)" in line:
                current.append(match[1])
    if len(current) != 1:
        raise CollectorError("Central must have one personal AI licence selected.")
    return identities, current[0]


def parse_quota(text, licence_id, now):
    try:
        data = json.loads(text)
        if data.get("managed") is not False:
            raise ValueError("Not a personal quota")
        monthly, topup = data["tariffQuota"], data["topUpQuota"]
        total, used, available = (Decimal(monthly[k]) for k in ("maximum", "current", "available"))
        if not all(v.is_finite() and v >= 0 for v in (total, used, available)):
            raise ValueError("Invalid quota")
        if total != used + available:
            raise ValueError("Inconsistent quota")
        reset = datetime.fromtimestamp(data["refillNext"] / 1000, UTC)
        return JetBrainsSnapshot(
            licence_id=licence_id,
            monthly_allocation=total,
            monthly_remaining=available,
            top_up_remaining=topup["available"],
            next_reset_at=reset,
            next_reset_date=reset.date(),
            observed_at=now,
            source="central_cli",
        )
    except (ValueError, TypeError, KeyError, ArithmeticError, OverflowError):
        raise CollectorError(
            "Central returned an unrecognized or inconsistent personal quota."
        ) from None


async def collect_central(executable, licence_ids, runner=None, journal_path=None):
    # Separate backend processes must not interleave changes to Central's selected source.
    import fcntl

    lock_path = Path.home() / ".jetbrains-central/billing-monitor.lock"
    if runner is not None:
        return await _collect_central(executable, licence_ids, runner, journal_path)
    try:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock = lock_path.open("a")
    except OSError:
        raise CollectorError("Central monitoring lock is unavailable.") from None
    with lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise CollectorError("Another Central quota check is active.") from None
        return await _collect_central(
            executable, licence_ids, runner, lock_path.with_suffix(".selection")
        )


async def _collect_central(executable, licence_ids, runner=None, journal_path=None):
    executable = find_central(executable)
    if executable is None:
        raise CollectorError("Central CLI was not found. Install it and sign in with JetBrains.")
    run = runner or (lambda *args: run_central(executable, *args))

    async def stopped():
        if not re.search(r"\bProxy\s+stopped\b", await run("status")):
            raise CollectorError("Central proxy is running or unknown. Quota collection is paused.")

    await stopped()
    available, original = parse_sources(await run("access"))
    if journal_path is not None and journal_path.exists():
        # A crash/power loss may interrupt finally. Never adopt that temporary source as
        # the user's preference, or overwrite a selection made outside this collector.
        restore_to = journal_path.read_text().strip()
        if original != restore_to:
            raise CollectorError(
                "A Central check was interrupted. Restore the original licence with "
                "central access license:" + restore_to + " before retrying."
            )
        journal_path.unlink()
    requested = list(dict.fromkeys(licence_ids))
    if not requested or not set(requested) <= available:
        raise CollectorError("A configured JetBrains licence is missing from Central access.")
    selected, changed = original, False
    snapshots = []
    try:
        for identity in requested:
            await stopped()
            if parse_sources(await run("access"))[1] != selected:
                raise CollectorError(
                    "Central selection changed during collection. Retry after selection."
                )
            if identity != selected:
                # Mark before switching so an ambiguous failure still triggers restoration.
                if journal_path is not None and not changed:
                    journal_path.write_text(original)
                    journal_path.chmod(0o600)
                changed = True
                selected = identity
                await run("access", "license:" + identity)
            if parse_sources(await run("access"))[1] != identity:
                raise CollectorError("Central did not select the requested licence.")
            reading = await run("limit", "--json")
            if parse_sources(await run("access"))[1] != identity:
                raise CollectorError(
                    "Central selection changed while reading quota; reading discarded."
                )
            snapshots.append(parse_quota(reading, identity, datetime.now(UTC)))
    finally:
        if changed:

            async def restore():
                current = parse_sources(await run("access"))[1]
                if current == selected:
                    await run("access", "license:" + original)
                elif current != original:
                    raise CollectorError(
                        "Central selection changed externally. Check its selected licence."
                    )
                if parse_sources(await run("access"))[1] != original:
                    raise CollectorError(
                        "Central selection could not be restored. Check Central access."
                    )
                if journal_path is not None:
                    journal_path.unlink(missing_ok=True)

            restoration = asyncio.create_task(restore())
            try:
                await asyncio.shield(restoration)
            except asyncio.CancelledError:
                await restoration
                raise
    return CentralUsage(snapshots=snapshots)
