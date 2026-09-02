from __future__ import annotations

import re
import ssl
from dataclasses import dataclass, field
from enum import StrEnum
from urllib.parse import urlsplit

import grpc
from google.protobuf.empty_pb2 import Empty

from tuntu.magnet import normalize_btih, parse_magnet

from . import clouddrive_v1_pb2 as pb
from . import clouddrive_v1_pb2_grpc as pb_grpc


class AuthMode(StrEnum):
    API_TOKEN = "api_token"
    USER_PASSWORD = "user_password"


class CloudDriveError(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class CloudDriveConfigurationError(CloudDriveError):
    pass


class CloudDriveAuthenticationError(CloudDriveError):
    pass


class CloudDriveRejected(CloudDriveError):
    pass


class ExternalTaskAlreadyExists(CloudDriveError):
    pass


class ResultUnknown(CloudDriveError):
    def __init__(
        self,
        code: str,
        *,
        baseline=None,
        btih: str | None = None,
        destination: str | None = None,
    ):
        super().__init__(code)
        self.baseline = baseline
        self.btih = btih
        self.destination = destination


class CloudDriveNetworkError(CloudDriveError):
    pass


def _normalize_absolute_path(path: str) -> str:
    if not path or not path.startswith("/") or "\\" in path:
        raise CloudDriveConfigurationError("invalid_path")
    parts = path.split("/")
    if any(part in {".", ".."} for part in parts):
        raise CloudDriveConfigurationError("invalid_path")
    normalized = "/" + "/".join(part for part in parts if part)
    return normalized if normalized != "" else "/"


def resolve_destination(root_path: str, profile_subdir: str) -> str:
    root = _normalize_absolute_path(root_path)
    if (
        not profile_subdir
        or profile_subdir.startswith("/")
        or "\\" in profile_subdir
        or any(part in {"", ".", ".."} for part in profile_subdir.split("/"))
    ):
        raise CloudDriveConfigurationError("invalid_profile_subdir")
    return _normalize_absolute_path(
        f"/{profile_subdir}" if root == "/" else f"{root}/{profile_subdir}"
    )


def resolve_task_destination(profile_destination: str, btih: str) -> str:
    destination = _normalize_absolute_path(profile_destination)
    identity = normalize_btih(btih)
    return _normalize_absolute_path(
        f"/{identity}" if destination == "/" else f"{destination}/{identity}"
    )


@dataclass(frozen=True, slots=True)
class CloudDriveConfig:
    endpoint: str
    auth_mode: AuthMode
    root_path: str
    api_token: str = field(default="", repr=False)
    username: str = ""
    password: str = field(default="", repr=False)
    tls_verify: bool = True
    ca_certificate_pem: bytes | None = field(default=None, repr=False)
    rpc_timeout_seconds: float = 8
    task_list_timeout_seconds: float = 8
    poll_interval_seconds: int = 300
    attention_after_seconds: int = 86_400
    check_folder_after_seconds: int = 5
    required_stable_observations: int = 2
    max_tree_depth: int = 8
    max_tree_entries: int = 10_000
    target: str = field(init=False)
    secure: bool = field(init=False)
    hostname: str = field(init=False)
    port: int = field(init=False)

    def __post_init__(self) -> None:
        try:
            auth_mode = AuthMode(self.auth_mode)
        except ValueError as exc:
            raise CloudDriveConfigurationError("invalid_auth_mode") from exc
        object.__setattr__(self, "auth_mode", auth_mode)

        endpoint = self.endpoint.strip()
        if "://" not in endpoint:
            endpoint = f"grpc://{endpoint}"
        parsed = urlsplit(endpoint)
        if (
            parsed.scheme.casefold() not in {"grpc", "grpcs", "http", "https"}
            or not parsed.hostname
            or parsed.port is None
            or parsed.username is not None
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
        ):
            raise CloudDriveConfigurationError("invalid_endpoint")
        object.__setattr__(self, "endpoint", endpoint)
        object.__setattr__(self, "target", f"{parsed.hostname}:{parsed.port}")
        object.__setattr__(self, "hostname", parsed.hostname)
        object.__setattr__(self, "port", parsed.port)
        object.__setattr__(
            self, "secure", parsed.scheme.casefold() in {"grpcs", "https"}
        )
        object.__setattr__(self, "root_path", _normalize_absolute_path(self.root_path))

        if auth_mode is AuthMode.API_TOKEN and not self.api_token:
            raise CloudDriveConfigurationError("api_token_required")
        if auth_mode is AuthMode.USER_PASSWORD and (not self.username or not self.password):
            raise CloudDriveConfigurationError("username_password_required")
        if min(
            self.rpc_timeout_seconds,
            self.task_list_timeout_seconds,
            self.poll_interval_seconds,
            self.attention_after_seconds,
            self.required_stable_observations,
            self.max_tree_depth,
            self.max_tree_entries,
        ) <= 0:
            raise CloudDriveConfigurationError("invalid_timing_or_limit")
        if self.check_folder_after_seconds < 0:
            raise CloudDriveConfigurationError("invalid_check_folder_delay")
        if self.required_stable_observations < 2:
            raise CloudDriveConfigurationError("insufficient_stable_observations")


@dataclass(frozen=True, slots=True, repr=False)
class FileFact:
    path: str
    size: int

    def __post_init__(self) -> None:
        if not self.path.startswith("/") or self.size < 0:
            raise ValueError("invalid file fact")


@dataclass(frozen=True, slots=True, repr=False)
class DirectorySnapshot:
    files: tuple[FileFact, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "files", tuple(sorted(self.files, key=lambda item: item.path)))

    @property
    def file_count(self) -> int:
        return len(self.files)

    @property
    def total_size(self) -> int:
        return sum(file.size for file in self.files)

    def as_size_map(self) -> dict[str, int]:
        return {file.path: file.size for file in self.files}

    def __repr__(self) -> str:
        return f"DirectorySnapshot(file_count={self.file_count}, total_size={self.total_size})"


@dataclass(frozen=True, slots=True)
class CloudDriveHealth:
    product_name: str
    product_version: str
    api_version: str


@dataclass(frozen=True, slots=True)
class CloudDriveSystemState:
    is_login: bool
    system_ready: bool
    has_error: bool | None


@dataclass(frozen=True, slots=True, repr=False)
class SubmitResult:
    btih: str
    destination: str
    baseline: DirectorySnapshot

    def __repr__(self) -> str:
        return (
            f"SubmitResult(btih={self.btih!r}, destination=<redacted>, "
            f"baseline={self.baseline!r})"
        )


class TaskSignal(StrEnum):
    INIT = "init"
    DOWNLOADING = "downloading"
    FINISHED = "finished"
    ERROR = "error"
    UNKNOWN = "unknown"


class CloudDriveClient:
    name = "clouddrive2"

    def __init__(
        self,
        config: CloudDriveConfig,
        *,
        stub=None,
        certificate_loader=None,
    ):
        self.config = config
        self._channel = None
        self._password_token: str | None = None
        if stub is not None:
            self._stub = stub
        else:
            self._channel = self._create_channel(certificate_loader)
            self._stub = pb_grpc.CloudDriveFileSrvStub(self._channel)

    def close(self) -> None:
        if self._channel is not None:
            self._channel.close()

    def _create_channel(self, certificate_loader):
        if not self.config.secure:
            return grpc.insecure_channel(self.config.target)
        roots = self.config.ca_certificate_pem
        if not self.config.tls_verify and roots is None:
            loader = certificate_loader or ssl.get_server_certificate
            try:
                roots = loader((self.config.hostname, self.config.port)).encode()
            except Exception as exc:
                raise CloudDriveConfigurationError("tls_certificate_unavailable") from exc
        credentials = grpc.ssl_channel_credentials(root_certificates=roots)
        return grpc.secure_channel(self.config.target, credentials)

    def _metadata(self) -> tuple[tuple[str, str], ...]:
        if self.config.auth_mode is AuthMode.API_TOKEN:
            token = self.config.api_token
        else:
            if self._password_token is None:
                try:
                    result = self._stub.GetToken(
                        pb.GetTokenRequest(
                            userName=self.config.username,
                            password=self.config.password,
                        ),
                        timeout=self.config.rpc_timeout_seconds,
                    )
                except grpc.RpcError as exc:
                    raise self._map_rpc_error(exc, operation="auth") from exc
                if not result.success or not result.token:
                    raise CloudDriveAuthenticationError("authentication_failed")
                self._password_token = result.token
            token = self._password_token
        return (("authorization", f"Bearer {token}"),)

    def health_check(self) -> CloudDriveHealth:
        try:
            result = self._stub.GetRuntimeInfo(
                Empty(),
                metadata=self._metadata(),
                timeout=self.config.rpc_timeout_seconds,
            )
        except grpc.RpcError as exc:
            raise self._map_rpc_error(exc, operation="read") from exc
        return CloudDriveHealth(
            product_name=result.productName,
            product_version=result.productVersion,
            api_version=result.CloudAPIVersion,
        )

    def ensure_destination(self, destination: str) -> None:
        destination = _normalize_absolute_path(destination)
        root = self.config.root_path
        if destination != root and not destination.startswith(root.rstrip("/") + "/"):
            raise CloudDriveConfigurationError("destination_outside_root")
        if destination == root:
            return

        relative = (
            destination.lstrip("/")
            if root == "/"
            else destination[len(root.rstrip("/") + "/") :]
        )
        current = root
        for folder_name in relative.split("/"):
            target = _normalize_absolute_path(
                f"/{folder_name}" if current == "/" else f"{current}/{folder_name}"
            )
            entries = self._list_direct(current, force_refresh=True)
            existing = next((entry for entry in entries if entry.fullPathName == target), None)
            if existing is not None:
                if not (
                    existing.isDirectory
                    or existing.fileType == pb.CloudDriveFile.Directory
                ):
                    raise CloudDriveConfigurationError("destination_component_is_file")
                current = target
                continue
            try:
                result = self._stub.CreateFolder(
                    pb.CreateFolderRequest(
                        parentPath=current,
                        folderName=folder_name,
                    ),
                    metadata=self._metadata(),
                    timeout=self.config.rpc_timeout_seconds,
                )
            except grpc.RpcError as exc:
                if self._directory_exists(current, target):
                    current = target
                    continue
                raise self._map_rpc_error(exc, operation="write") from exc
            if not result.result.success:
                if not self._directory_exists(current, target):
                    raise CloudDriveRejected("create_folder_rejected")
            current = target

    def public_system_state(self) -> CloudDriveSystemState:
        try:
            result = self._stub.GetSystemInfo(
                Empty(), timeout=self.config.rpc_timeout_seconds
            )
        except grpc.RpcError as exc:
            raise self._map_rpc_error(exc, operation="read") from exc
        return CloudDriveSystemState(
            is_login=result.IsLogin,
            system_ready=result.SystemReady,
            has_error=result.hasError if result.HasField("hasError") else None,
        )

    def submit(self, magnet_uri: str, destination: str) -> SubmitResult:
        destination = _normalize_absolute_path(destination)
        parsed = parse_magnet(magnet_uri)
        baseline = self.snapshot(destination, force_refresh=True)
        try:
            result = self._stub.AddOfflineFiles(
                pb.AddOfflineFileRequest(
                    urls=parsed.canonical_uri,
                    toFolder=destination,
                    checkFolderAfterSecs=self.config.check_folder_after_seconds,
                ),
                metadata=self._metadata(),
                timeout=self.config.rpc_timeout_seconds,
            )
        except grpc.RpcError as exc:
            mapped = self._map_rpc_error(exc, operation="submit")
            if isinstance(mapped, ResultUnknown):
                mapped.baseline = baseline
                mapped.btih = parsed.btih
                mapped.destination = destination
            raise mapped from exc
        if not result.success:
            raise CloudDriveRejected("explicit_rejection")
        return SubmitResult(parsed.btih, destination, baseline)

    def snapshot(self, destination: str, *, force_refresh: bool) -> DirectorySnapshot:
        destination = _normalize_absolute_path(destination)
        files: list[FileFact] = []
        pending = [(destination, 0)]
        visited = set()
        metadata = self._metadata()
        while pending:
            path, depth = pending.pop()
            if path in visited:
                continue
            visited.add(path)
            if depth > self.config.max_tree_depth:
                raise CloudDriveConfigurationError("tree_depth_exceeded")
            try:
                replies = self._stub.GetSubFiles(
                    pb.ListSubFileRequest(path=path, forceRefresh=force_refresh),
                    metadata=metadata,
                    timeout=self.config.rpc_timeout_seconds,
                )
                for reply in replies:
                    for entry in reply.subFiles:
                        if len(files) + len(pending) >= self.config.max_tree_entries:
                            raise CloudDriveConfigurationError("tree_entries_exceeded")
                        entry_path = _normalize_absolute_path(entry.fullPathName)
                        if not (
                            entry_path == destination
                            or entry_path.startswith(destination.rstrip("/") + "/")
                            or destination == "/"
                        ):
                            raise CloudDriveConfigurationError(
                                "listed_path_outside_destination"
                            )
                        if entry.isDirectory or entry.fileType == pb.CloudDriveFile.Directory:
                            pending.append((entry_path, depth + 1))
                        elif entry.fileType == pb.CloudDriveFile.File or entry.isCloudFile:
                            files.append(FileFact(entry_path, max(0, entry.size)))
            except grpc.RpcError as exc:
                raise self._map_rpc_error(exc, operation="read") from exc
        return DirectorySnapshot(tuple(files))

    def _list_direct(self, path: str, *, force_refresh: bool):
        entries = []
        try:
            replies = self._stub.GetSubFiles(
                pb.ListSubFileRequest(path=path, forceRefresh=force_refresh),
                metadata=self._metadata(),
                timeout=self.config.rpc_timeout_seconds,
            )
            for reply in replies:
                for entry in reply.subFiles:
                    if len(entries) >= self.config.max_tree_entries:
                        raise CloudDriveConfigurationError("tree_entries_exceeded")
                    entry_path = _normalize_absolute_path(entry.fullPathName)
                    if not (
                        entry_path == path
                        or entry_path.startswith(path.rstrip("/") + "/")
                        or path == "/"
                    ):
                        raise CloudDriveConfigurationError(
                            "listed_path_outside_destination"
                        )
                    entries.append(entry)
        except grpc.RpcError as exc:
            raise self._map_rpc_error(exc, operation="read") from exc
        return entries

    def _directory_exists(self, parent: str, target: str) -> bool:
        return any(
            entry.fullPathName == target
            and (
                entry.isDirectory
                or entry.fileType == pb.CloudDriveFile.Directory
            )
            for entry in self._list_direct(parent, force_refresh=True)
        )

    def get_task_signal(self, btih: str, destination: str) -> TaskSignal:
        destination = _normalize_absolute_path(destination)
        try:
            result = self._stub.ListOfflineFilesByPath(
                pb.FileRequest(path=destination, forceRefresh=True),
                metadata=self._metadata(),
                timeout=self.config.task_list_timeout_seconds,
            )
        except grpc.RpcError as exc:
            if exc.code() in {
                grpc.StatusCode.DEADLINE_EXCEEDED,
                grpc.StatusCode.UNAVAILABLE,
                grpc.StatusCode.CANCELLED,
            }:
                return TaskSignal.UNKNOWN
            raise self._map_rpc_error(exc, operation="read") from exc
        status_map = {
            pb.OFFLINE_INIT: TaskSignal.INIT,
            pb.OFFLINE_DOWNLOADING: TaskSignal.DOWNLOADING,
            pb.OFFLINE_FINISHED: TaskSignal.FINISHED,
            pb.OFFLINE_ERROR: TaskSignal.ERROR,
            pb.OFFLINE_UNKNOWN: TaskSignal.UNKNOWN,
        }
        normalized_btih = btih.casefold()
        for item in result.offlineFiles:
            if item.infoHash.casefold() == normalized_btih:
                return status_map.get(item.status, TaskSignal.UNKNOWN)
        return TaskSignal.UNKNOWN

    @staticmethod
    def _map_rpc_error(error: grpc.RpcError, *, operation: str) -> CloudDriveError:
        status = error.code()
        details = (error.details() or "").casefold()
        if status in {grpc.StatusCode.UNAUTHENTICATED, grpc.StatusCode.PERMISSION_DENIED}:
            return CloudDriveAuthenticationError("authentication_failed")
        if operation == "submit" and re.search(r"\b10008\b", details):
            return ExternalTaskAlreadyExists("external_task_already_exists")
        if operation == "submit" and status in {
            grpc.StatusCode.DEADLINE_EXCEEDED,
            grpc.StatusCode.UNAVAILABLE,
            grpc.StatusCode.CANCELLED,
        }:
            return ResultUnknown("submission_result_unknown")
        if status in {grpc.StatusCode.INVALID_ARGUMENT, grpc.StatusCode.NOT_FOUND}:
            return CloudDriveConfigurationError("invalid_remote_path_or_request")
        if status in {
            grpc.StatusCode.DEADLINE_EXCEEDED,
            grpc.StatusCode.UNAVAILABLE,
            grpc.StatusCode.CANCELLED,
        }:
            return CloudDriveNetworkError("network_error")
        return CloudDriveRejected("external_rejection")
