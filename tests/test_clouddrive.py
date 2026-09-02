from __future__ import annotations

import unittest

import grpc

from tuntu.downloaders import clouddrive_v1_pb2 as pb
from tuntu.downloaders.clouddrive import (
    AuthMode,
    CloudDriveClient,
    CloudDriveConfig,
    CloudDriveConfigurationError,
    CloudDriveRejected,
    DirectorySnapshot,
    ExternalTaskAlreadyExists,
    FileFact,
    ResultUnknown,
    TaskSignal,
    resolve_destination,
)


class FakeRpcError(grpc.RpcError):
    def __init__(self, status, details="fixture error"):
        self._status = status
        self._details = details

    def code(self):
        return self._status

    def details(self):
        return self._details


class UnaryCall:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.calls = []

    def __call__(self, request, **kwargs):
        self.calls.append((request, kwargs))
        if self.error:
            raise self.error
        return self.response


class StreamCall(UnaryCall):
    def __call__(self, request, **kwargs):
        self.calls.append((request, kwargs))
        if self.error:
            raise self.error
        return iter(self.response or [])


class SequenceStreamCall(StreamCall):
    def __init__(self, responses):
        super().__init__()
        self.responses = list(responses)

    def __call__(self, request, **kwargs):
        self.calls.append((request, kwargs))
        return iter(self.responses.pop(0))


class FakeStub:
    def __init__(self):
        self.GetToken = UnaryCall(pb.JWTToken(success=True, token="password-token"))
        self.GetSystemInfo = UnaryCall(
            pb.CloudDriveSystemInfo(IsLogin=True, SystemReady=True, hasError=False)
        )
        self.GetRuntimeInfo = UnaryCall(
            pb.RuntimeInfo(
                productName="CloudDrive2",
                productVersion="fixture",
                CloudAPIVersion="1.0.14",
            )
        )
        self.GetSubFiles = StreamCall([])
        self.CreateFolder = UnaryCall(
            pb.CreateFolderResult(
                result=pb.FileOperationResult(success=True)
            )
        )
        self.AddOfflineFiles = UnaryCall(pb.FileOperationResult(success=True))
        self.ListOfflineFilesByPath = UnaryCall(pb.OfflineFileListResult())


def token_config(**overrides):
    values = {
        "endpoint": "grpc://cd2.fixture:19798",
        "auth_mode": AuthMode.API_TOKEN,
        "api_token": "fixture-secret-token",
        "root_path": "/",
        "rpc_timeout_seconds": 8,
        "task_list_timeout_seconds": 2,
        "poll_interval_seconds": 300,
        "attention_after_seconds": 86400,
    }
    values.update(overrides)
    return CloudDriveConfig(**values)


class ConfigurationTests(unittest.TestCase):
    def test_plain_host_and_port_are_treated_as_normal_grpc(self):
        config = token_config(endpoint="192.168.1.10:19798")

        self.assertEqual(config.endpoint, "grpc://192.168.1.10:19798")
        self.assertEqual(config.target, "192.168.1.10:19798")
        self.assertFalse(config.secure)

    def test_all_environment_values_are_instance_configuration(self):
        config = token_config(
            endpoint="grpcs://nas.example:9443",
            root_path="/api-visible-root",
            tls_verify=False,
            ca_certificate_pem=b"fixture-ca",
            rpc_timeout_seconds=7,
            task_list_timeout_seconds=9,
            poll_interval_seconds=123,
            attention_after_seconds=456,
            check_folder_after_seconds=11,
            required_stable_observations=3,
            max_tree_depth=4,
            max_tree_entries=500,
        )

        self.assertEqual(config.target, "nas.example:9443")
        self.assertTrue(config.secure)
        self.assertEqual(config.root_path, "/api-visible-root")
        self.assertEqual(config.rpc_timeout_seconds, 7)
        self.assertEqual(config.task_list_timeout_seconds, 9)
        self.assertEqual(config.poll_interval_seconds, 123)
        self.assertEqual(config.attention_after_seconds, 456)
        self.assertEqual(config.check_folder_after_seconds, 11)
        self.assertEqual(config.required_stable_observations, 3)
        self.assertEqual(config.max_tree_depth, 4)
        self.assertEqual(config.max_tree_entries, 500)

    def test_secret_values_are_not_in_repr(self):
        config = token_config(api_token="do-not-display")
        password = CloudDriveConfig(
            endpoint="grpc://fixture:19798",
            auth_mode=AuthMode.USER_PASSWORD,
            username="fixture-user",
            password="do-not-display-password",
            root_path="/",
        )

        self.assertNotIn("do-not-display", repr(config))
        self.assertNotIn("do-not-display-password", repr(password))

    def test_invalid_endpoint_credentials_and_paths_fail_before_network(self):
        invalid_configs = (
            {"endpoint": "http://fixture/path"},
            {"endpoint": "grpc://fixture"},
            {"api_token": ""},
            {"root_path": "relative"},
            {"root_path": "/contains/../escape"},
            {"required_stable_observations": 1},
        )
        for overrides in invalid_configs:
            with self.subTest(overrides=overrides), self.assertRaises(
                CloudDriveConfigurationError
            ):
                token_config(**overrides)

    def test_destination_uses_api_visible_root_and_rejects_escape(self):
        self.assertEqual(resolve_destination("/", "weekly"), "/weekly")
        self.assertEqual(resolve_destination("/api-root", "movies/hot"), "/api-root/movies/hot")
        for invalid in ("", "/absolute", "../escape", "safe/../../escape", "."):
            with self.subTest(invalid=invalid), self.assertRaises(
                CloudDriveConfigurationError
            ):
                resolve_destination("/", invalid)


class CloudDriveClientTests(unittest.TestCase):
    def test_ensure_destination_creates_missing_path_under_configured_root(self):
        stub = FakeStub()
        client = CloudDriveClient(
            token_config(root_path="/downloads"), stub=stub
        )

        client.ensure_destination("/downloads/" + "a" * 40)

        request, kwargs = stub.CreateFolder.calls[0]
        self.assertEqual(request.parentPath, "/downloads")
        self.assertEqual(request.folderName, "a" * 40)
        self.assertEqual(kwargs["timeout"], 8)

    def test_ensure_destination_rejects_paths_outside_configured_root(self):
        client = CloudDriveClient(
            token_config(root_path="/downloads"), stub=FakeStub()
        )

        with self.assertRaisesRegex(
            CloudDriveConfigurationError, "destination_outside_root"
        ):
            client.ensure_destination("/other/path")

    def test_ensure_destination_accepts_concurrent_create_when_refresh_finds_folder(self):
        target = "/downloads/" + "a" * 40
        stub = FakeStub()
        stub.GetSubFiles = SequenceStreamCall(
            [
                [],
                [
                    pb.SubFilesReply(
                        subFiles=[
                            pb.CloudDriveFile(
                                name="a" * 40,
                                fullPathName=target,
                                fileType=pb.CloudDriveFile.Directory,
                                isDirectory=True,
                            )
                        ]
                    )
                ],
            ]
        )
        stub.CreateFolder = UnaryCall(
            pb.CreateFolderResult(
                result=pb.FileOperationResult(success=False)
            )
        )
        client = CloudDriveClient(
            token_config(root_path="/downloads"), stub=stub
        )

        client.ensure_destination(target)

        self.assertEqual(len(stub.GetSubFiles.calls), 2)

    def test_public_system_probe_needs_no_authorization_metadata(self):
        stub = FakeStub()
        client = CloudDriveClient(token_config(), stub=stub)

        state = client.public_system_state()

        self.assertTrue(state.is_login)
        self.assertTrue(state.system_ready)
        _, kwargs = stub.GetSystemInfo.calls[0]
        self.assertNotIn("metadata", kwargs)

    def test_api_token_health_check_uses_bearer_and_configured_deadline(self):
        stub = FakeStub()
        client = CloudDriveClient(token_config(rpc_timeout_seconds=7), stub=stub)

        health = client.health_check()

        self.assertEqual(health.api_version, "1.0.14")
        _, kwargs = stub.GetRuntimeInfo.calls[0]
        self.assertEqual(kwargs["metadata"], (("authorization", "Bearer fixture-secret-token"),))
        self.assertEqual(kwargs["timeout"], 7)

    def test_username_password_fetches_token_once_without_logging_password(self):
        stub = FakeStub()
        config = CloudDriveConfig(
            endpoint="grpc://fixture:19798",
            auth_mode=AuthMode.USER_PASSWORD,
            username="fixture-user",
            password="fixture-password",
            root_path="/",
        )
        client = CloudDriveClient(config, stub=stub)

        client.health_check()
        client.health_check()

        self.assertEqual(len(stub.GetToken.calls), 1)
        _, first_health_kwargs = stub.GetRuntimeInfo.calls[0]
        self.assertEqual(
            first_health_kwargs["metadata"],
            (("authorization", "Bearer password-token"),),
        )

    def test_submit_snapshots_destination_then_preserves_magnet_transport_hints(self):
        stub = FakeStub()
        stub.GetSubFiles.response = [
            pb.SubFilesReply(
                subFiles=[
                    pb.CloudDriveFile(
                        name="old.bin",
                        fullPathName="/weekly/old.bin",
                        size=10,
                        fileType=pb.CloudDriveFile.File,
                    )
                ]
            )
        ]
        client = CloudDriveClient(token_config(), stub=stub)

        result = client.submit(
            "magnet:?dn=Fixture&tr=https%3A%2F%2Ftracker.invalid&xt=urn:btih:"
            + "a" * 40,
            "/weekly",
        )

        self.assertEqual(result.btih, "a" * 40)
        self.assertEqual(result.baseline.total_size, 10)
        request, kwargs = stub.AddOfflineFiles.calls[0]
        self.assertEqual(
            request.urls,
            "magnet:?xt=urn:btih:"
            + "a" * 40
            + "&dn=Fixture&tr=https%3A%2F%2Ftracker.invalid",
        )
        self.assertEqual(request.toFolder, "/weekly")
        self.assertEqual(kwargs["timeout"], 8)

    def test_explicit_rejection_duplicate_and_unknown_result_are_distinct(self):
        cases = (
            (
                UnaryCall(pb.FileOperationResult(success=False, errorMessage="fixture")),
                CloudDriveRejected,
            ),
            (
                UnaryCall(
                    error=FakeRpcError(
                        grpc.StatusCode.INTERNAL,
                        "api error: code: 10008, message: duplicate fixture",
                    )
                ),
                ExternalTaskAlreadyExists,
            ),
            (
                UnaryCall(error=FakeRpcError(grpc.StatusCode.DEADLINE_EXCEEDED)),
                ResultUnknown,
            ),
            (
                UnaryCall(error=FakeRpcError(grpc.StatusCode.UNAVAILABLE)),
                ResultUnknown,
            ),
        )
        for call, expected_error in cases:
            with self.subTest(expected_error=expected_error):
                stub = FakeStub()
                stub.AddOfflineFiles = call
                client = CloudDriveClient(token_config(), stub=stub)
                with self.assertRaises(expected_error):
                    client.submit("magnet:?xt=urn:btih:" + "b" * 40, "/weekly")

    def test_offline_status_is_optional_signal_and_timeout_returns_unknown(self):
        stub = FakeStub()
        stub.ListOfflineFilesByPath.response = pb.OfflineFileListResult(
            offlineFiles=[
                pb.OfflineFile(infoHash="c" * 40, status=pb.OFFLINE_DOWNLOADING),
                pb.OfflineFile(infoHash="d" * 40, status=pb.OFFLINE_FINISHED),
            ]
        )
        client = CloudDriveClient(token_config(task_list_timeout_seconds=3), stub=stub)

        self.assertEqual(client.get_task_signal("c" * 40, "/weekly"), TaskSignal.DOWNLOADING)
        self.assertEqual(client.get_task_signal("d" * 40, "/weekly"), TaskSignal.FINISHED)
        self.assertEqual(client.get_task_signal("e" * 40, "/weekly"), TaskSignal.UNKNOWN)
        _, kwargs = stub.ListOfflineFilesByPath.calls[0]
        self.assertEqual(kwargs["timeout"], 3)

        stub.ListOfflineFilesByPath = UnaryCall(
            error=FakeRpcError(grpc.StatusCode.DEADLINE_EXCEEDED)
        )
        self.assertEqual(client.get_task_signal("c" * 40, "/weekly"), TaskSignal.UNKNOWN)


class SnapshotTests(unittest.TestCase):
    def test_snapshot_rejects_paths_returned_outside_requested_destination(self):
        stub = FakeStub()
        stub.GetSubFiles.response = [
            pb.SubFilesReply(
                subFiles=[
                    pb.CloudDriveFile(
                        name="outside.bin",
                        fullPathName="/outside.bin",
                        size=10,
                        fileType=pb.CloudDriveFile.File,
                    )
                ]
            )
        ]
        client = CloudDriveClient(token_config(), stub=stub)

        with self.assertRaisesRegex(
            CloudDriveConfigurationError, "listed_path_outside_destination"
        ):
            client.snapshot("/weekly", force_refresh=True)

    def test_snapshot_value_is_stable_and_does_not_expose_unrelated_names_in_repr(self):
        snapshot = DirectorySnapshot(
            files=(FileFact("/private-name.bin", 10), FileFact("/other.bin", 20))
        )

        self.assertEqual(snapshot.file_count, 2)
        self.assertEqual(snapshot.total_size, 30)
        self.assertNotIn("private-name", repr(snapshot))


if __name__ == "__main__":
    unittest.main()
