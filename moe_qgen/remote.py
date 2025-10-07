"""SSH helper utilities for orchestrating remote experiments."""

from __future__ import annotations

import dataclasses
import getpass
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional, Sequence

import paramiko

logger = logging.getLogger(__name__)


@dataclass
class CommandResult:
    """Result of a remote command execution."""

    command: str
    return_code: int
    stdout: str
    stderr: str
    start_time: float
    end_time: float

    @property
    def duration(self) -> float:
        return self.end_time - self.start_time


class RemoteRunner:
    """Thin wrapper around :mod:`paramiko` to run commands sequentially."""

    def __init__(
        self,
        host: str,
        user: str,
        password: Optional[str] = None,
        port: int = 22,
        connect_timeout: int = 30,
        compress: bool = True,
    ) -> None:
        self.host = host
        self.user = user
        self.password = password
        self.port = port
        self.connect_timeout = connect_timeout
        self.compress = compress
        self._client: Optional[paramiko.SSHClient] = None
        self._sftp: Optional[paramiko.SFTPClient] = None

    def __enter__(self) -> "RemoteRunner":
        self.connect()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def connect(self) -> None:
        if self._client is not None:
            return
        password = self.password or getpass.getpass(prompt=f"Password for {self.user}@{self.host}: ")
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        logger.debug("Connecting to %s:%s", self.host, self.port)
        client.connect(
            hostname=self.host,
            port=self.port,
            username=self.user,
            password=password,
            timeout=self.connect_timeout,
            compress=self.compress,
        )
        self._client = client
        self._sftp = client.open_sftp()

    @property
    def client(self) -> paramiko.SSHClient:
        if self._client is None:
            raise RuntimeError("RemoteRunner has not been connected yet")
        return self._client

    @property
    def sftp(self) -> paramiko.SFTPClient:
        if self._sftp is None:
            raise RuntimeError("SFTP session has not been established")
        return self._sftp

    def close(self) -> None:
        if self._sftp is not None:
            self._sftp.close()
            self._sftp = None
        if self._client is not None:
            self._client.close()
            self._client = None

    def run(self, command: str, env: Optional[Sequence[str]] = None) -> CommandResult:
        """Execute ``command`` remotely and capture stdout/stderr."""

        if env:
            command = " && ".join(list(env) + [command])
        full_cmd = f"bash -lc '{command}'"
        logger.info("Executing on %s: %s", self.host, full_cmd)
        stdin, stdout, stderr = self.client.exec_command(full_cmd)
        start = time.time()
        stdout_data = stdout.read().decode("utf-8", errors="replace")
        stderr_data = stderr.read().decode("utf-8", errors="replace")
        return_code = stdout.channel.recv_exit_status()
        end = time.time()
        logger.debug("Command finished with code %s", return_code)
        return CommandResult(
            command=full_cmd,
            return_code=return_code,
            stdout=stdout_data,
            stderr=stderr_data,
            start_time=start,
            end_time=end,
        )

    def download(self, remote_path: str, local_path: str) -> None:
        logger.info("Downloading %s -> %s", remote_path, local_path)
        self.sftp.get(remote_path, local_path)

    def upload(self, local_path: str, remote_path: str) -> None:
        logger.info("Uploading %s -> %s", local_path, remote_path)
        self.sftp.put(local_path, remote_path)

    def write_text(self, remote_path: str, content: str, mode: int = 0o644) -> None:
        """Write ``content`` to ``remote_path`` using UTF-8 encoding."""

        directory = str(Path(remote_path).parent)
        if directory not in {"", "."}:
            self.ensure_remote_dirs([directory])
        logger.debug("Writing remote file %s", remote_path)
        with self.sftp.open(remote_path, "w") as remote_file:
            remote_file.write(content.encode("utf-8"))
        self.sftp.chmod(remote_path, mode)

    def read_text(self, remote_path: str) -> str:
        logger.debug("Reading remote file %s", remote_path)
        with self.sftp.open(remote_path, "r") as remote_file:
            return remote_file.read().decode("utf-8", errors="replace")

    def exists(self, remote_path: str) -> bool:
        try:
            self.sftp.stat(remote_path)
            return True
        except FileNotFoundError:
            return False

    def ensure_remote_dirs(self, paths: Iterable[str]) -> None:
        for path in paths:
            self._ensure_remote_dir(path)

    def _ensure_remote_dir(self, path: str) -> None:
        directories: List[str] = []
        current = ""
        for component in path.strip("/").split("/"):
            current = f"{current}/{component}" if current else f"/{component}"
            directories.append(current)
        for directory in directories:
            try:
                self.sftp.stat(directory)
            except FileNotFoundError:
                logger.debug("Creating remote directory %s", directory)
                self.sftp.mkdir(directory)


def command_result_to_dict(result: CommandResult) -> dict:
    return dataclasses.asdict(result)
