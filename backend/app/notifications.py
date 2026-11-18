import asyncio
import sys

from .monitoring_models import Alert


class MacNotifier:
    """Submit notifications locally. OS acceptance does not prove the user saw an alert."""

    async def submit(self, alert: Alert) -> tuple[str, str | None]:
        if sys.platform != "darwin":
            return "unsupported", "Desktop notifications currently require macOS."
        script = (
            "on run argv\n"
            "display notification (item 2 of argv) with title (item 1 of argv)\n"
            "end run"
        )
        process = None
        try:
            # Pass message text as arguments, never interpolate it into AppleScript or a shell.
            process = await asyncio.create_subprocess_exec(
                "/usr/bin/osascript",
                "-e",
                script,
                "--",
                alert.title,
                alert.message,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await asyncio.wait_for(process.wait(), timeout=5)
            if process.returncode != 0:
                return "failed", "macOS did not accept the notification request."
            return "submitted_to_macos", None
        except (OSError, TimeoutError):
            return "failed", "The macOS notification service could not be reached."
        finally:
            if process is not None and process.returncode is None:
                process.kill()
                await process.wait()
