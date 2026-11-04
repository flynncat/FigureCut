import hashlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import environment_check as check


class EnvironmentTests(unittest.TestCase):
    def test_models_are_verified_by_content_not_only_filename(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "model.pt"
            expected = hashlib.sha256(b"valid model").hexdigest()
            self.assertFalse(check.valid_model(path, expected))
            path.write_bytes(b"valid model")
            self.assertTrue(check.valid_model(path, expected))
            path.write_bytes(b"truncated")
            self.assertFalse(check.valid_model(path, expected))

    def test_wrong_cpu_wheel_version_is_reported(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "requirements.txt"
            path.write_text("torch==2.9.1+cpu\ntorchvision==0.24.1+cpu\n", encoding="utf-8")
            with patch.object(check.importlib.metadata, "version", side_effect=["2.9.1+cu128", "0.24.1+cpu"]):
                problems = check.package_problems([path])
            self.assertEqual(len(problems), 1)
            self.assertIn("torch==2.9.1+cpu", problems[0])

    def test_offline_check_never_downloads_missing_models(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(check, "download_model") as download:
            self.assertEqual(check.prepare_models(Path(directory), False), 30)
            download.assert_not_called()

    def test_bad_download_does_not_replace_existing_model(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "model.pt"
            path.write_bytes(b"previous file")
            responses = []
            for _ in range(3):
                response = io.BytesIO(b"broken payload")
                response.headers = {"Content-Length": "14"}
                responses.append(response)
            with patch.object(check.urllib.request, "urlopen", side_effect=responses), patch.object(check.time, "sleep"):
                with self.assertRaisesRegex(ValueError, "SHA-256"):
                    check.download_model("model.pt", path, "0" * 64)
            self.assertEqual(path.read_bytes(), b"previous file")
            self.assertFalse(path.with_suffix(".download").exists())

    def test_valid_download_is_installed_atomically(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "model.pt"
            payload = b"verified model"
            response = io.BytesIO(payload)
            response.headers = {"Content-Length": str(len(payload))}
            with patch.object(check.urllib.request, "urlopen", return_value=response):
                check.download_model("model.pt", path, hashlib.sha256(payload).hexdigest())
            self.assertEqual(path.read_bytes(), payload)
            self.assertFalse(path.with_suffix(".download").exists())


if __name__ == "__main__":
    unittest.main()
