from __future__ import annotations

import asyncio
from typing import Callable
from uuid import uuid4

from .models import SessionInfo


class ScrcpySession:
    """One running scrcpy virtual-display process, tracked without a Qt event loop."""

    def __init__(
        self,
        program: str,
        arguments: list[str],
        serial: str,
        package: str,
        title: str,
        on_change: Callable[[SessionInfo], None],
        device_key: str = "",
    ) -> None:
        self.info = SessionInfo(
            session_id=uuid4().hex,
            device_serial=serial,
            package=package,
            title=title,
            command=arguments,
            device_key=device_key or serial,
        )
        self._program = program
        self._arguments = arguments
        self._on_change = on_change
        self._process: asyncio.subprocess.Process | None = None

    @property
    def pid(self) -> int:
        """PID of the scrcpy process, or 0 before start / after exit."""
        if self._process is None or self._process.returncode is not None:
            return 0
        return self._process.pid

    @property
    def is_running(self) -> bool:
        return self._process is not None and self._process.returncode is None

    def _changed(self) -> None:
        self._on_change(self.info)

    async def start(self) -> None:
        try:
            self._process = await asyncio.create_subprocess_exec(
                self._program,
                *self._arguments,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError as error:
            self.info.state = "error"
            self.info.log += str(error)
            self._changed()
            return
        asyncio.create_task(self._pump(self._process.stdout))
        asyncio.create_task(self._pump(self._process.stderr))
        asyncio.create_task(self._wait())
        self._changed()

    async def stop(self) -> None:
        if self._process is None or self._process.returncode is not None:
            return
        self.info.state = "stopping"
        self._changed()
        self._process.terminate()
        try:
            await asyncio.wait_for(self._process.wait(), timeout=1.5)
        except asyncio.TimeoutError:
            self._process.kill()

    async def _pump(self, stream: asyncio.StreamReader | None) -> None:
        if stream is None:
            return
        while True:
            chunk = await stream.read(4096)
            if not chunk:
                return
            self.info.log += chunk.decode(errors="replace")
            self._changed()

    async def _wait(self) -> None:
        assert self._process is not None
        exit_code = await self._process.wait()
        self.info.state = "finished" if exit_code == 0 else f"stopped ({exit_code})"
        self._changed()
