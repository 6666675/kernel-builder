import io
import json
import os
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import urlsplit

from helpers import BuilderFixture
from builder_lib import receipt
from cnb_artifacts import CNBClient, publish


class Response(io.BytesIO):
    status = 200


class ArtifactTests(BuilderFixture):
    def setUp(self):
        super().setUp()
        os.environ.update(CNB_COMMIT=os.environ["CI_COMMIT_SHA"], CNB_REPO_SLUG="demo/kernel_build", CNB_TOKEN="test-only-secret", CNB_API_ENDPOINT="https://api.cnb.cool", ARTIFACT_TTL_DAYS="30")
        self.boot = self.artifacts / "boot.img"
        self.boot.write_bytes(b"verified-new-image")
        receipt("kernel", self.boot)
        self.calls = []
        self.client = CNBClient()
        self.client.opener = self
        self.foreign_verify = False
        self.fail_delete = False
        self.addCleanup(self.close_errors)
        self.errors = []

    def close_errors(self):
        for error in self.errors:
            error.close()

    def open(self, request, timeout):
        method, url = request.get_method(), request.full_url
        self.calls.append((method, url, dict(request.headers)))
        if urlsplit(url).hostname == "storage.example.test":
            self.assertIsNone(request.get_header("Authorization"))
            self.assertEqual(request.data.read(), self.boot.read_bytes())
            return Response(b"")
        self.assertEqual(request.get_header("Authorization"), "Bearer test-only-secret")
        if method == "DELETE":
            self.assertTrue(url.endswith("/old-boot"))
            if self.fail_delete:
                error = HTTPError(url, 403, "denied", {}, io.BytesIO())
                self.errors.append(error)
                raise error
            return Response(b"")
        if method == "GET":
            return Response(json.dumps([{"id": "old-boot", "name": "boot.img"}, {"id": "keep-source", "name": "source-old.tar.gz"}]).encode())
        if url.endswith("/asset-upload-url"):
            verify = self.client.base + self.client.prefix + "/asset-upload-confirmation/token/path"
            if self.foreign_verify:
                verify = "https://storage.example.test/steal-token"
            return Response(json.dumps({"upload_url": "https://storage.example.test/object?signature=temporary", "verify_url": verify}).encode())
        self.assertIn("/asset-upload-confirmation/token/path?ttl=30", url)
        return Response(b"")

    def test_only_old_boot_is_deleted_and_storage_never_receives_token(self):
        with patch("cnb_artifacts.CNBClient", return_value=self.client):
            publish("kernel")
        self.assertEqual([call[0] for call in self.calls], ["GET", "DELETE", "POST", "PUT", "POST"])
        self.assertFalse(any("keep-source" in call[1] for call in self.calls))

    def test_delete_failure_stops_before_upload(self):
        self.fail_delete = True
        with patch("cnb_artifacts.CNBClient", return_value=self.client), self.assertRaises(HTTPError):
            publish("kernel")
        self.assertEqual([call[0] for call in self.calls], ["GET", "DELETE"])

    def test_modified_result_or_wrong_run_receipt_cannot_publish(self):
        self.boot.write_bytes(b"not-the-verified-image")
        with patch("cnb_artifacts.CNBClient", return_value=self.client), self.assertRaises(ValueError):
            publish("kernel")
        self.assertEqual(self.calls, [])
        receipt("kernel", self.boot)
        with patch.dict(os.environ, {"CNB_BUILD_ID": "different-run"}), self.assertRaises(ValueError):
            publish("kernel")

    def test_foreign_confirmation_url_never_receives_credentials(self):
        self.foreign_verify = True
        with patch("cnb_artifacts.CNBClient", return_value=self.client), self.assertRaises(ValueError):
            publish("kernel")
        self.assertFalse(any(urlsplit(call[1]).hostname == "storage.example.test" for call in self.calls))

    def test_invalid_ttl_stops_before_deleting_existing_artifacts(self):
        with patch.dict(os.environ, {"ARTIFACT_TTL_DAYS": "999"}), patch("cnb_artifacts.CNBClient", return_value=self.client), self.assertRaises(ValueError):
            publish("kernel")
        self.assertEqual(self.calls, [])
