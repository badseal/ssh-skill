from __future__ import annotations

import contextlib
import hashlib
import io
import json
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch

from tests import support  # noqa: F401
from ssh_config_manager_v3 import SSHConfigManager, backup_file, cmd_delete


CONFIG_TEXT = """# ===== keep-host =====
# description: keep this block
Host keep-host
    HostName keep.invalid
    User keep

# ===== remove-host =====
# description: remove this block
# password: legacy-secret
Host remove-host
    HostName remove.invalid
    User remove

# ===== next-host =====
# description: keep next metadata
Host next-host
    HostName next.invalid
    User next
"""


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class ConfigMutationTests(unittest.TestCase):
    def test_delete_preview_does_not_modify_config_or_expose_password(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config"
            config_path.write_text(CONFIG_TEXT, encoding="utf-8")
            before = sha256(config_path)
            manager = SSHConfigManager(str(config_path))
            stdout = io.StringIO()

            with (
                patch("ssh_config_manager_v3.SSHConfigManager", return_value=manager),
                contextlib.redirect_stdout(stdout),
            ):
                cmd_delete(Namespace(alias="remove-host", apply=False))

            result = json.loads(stdout.getvalue())
            self.assertEqual(before, sha256(config_path))
            self.assertEqual("preview", result["data"]["mode"])
            self.assertEqual("remove-host", result["data"]["host"]["alias"])
            self.assertNotIn("legacy-secret", stdout.getvalue())
            self.assertEqual([], list(config_path.parent.glob("config.backup-*")))

    def test_delete_apply_backs_up_then_atomically_replaces_config(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config"
            config_path.write_text(CONFIG_TEXT, encoding="utf-8")
            before = sha256(config_path)
            manager = SSHConfigManager(str(config_path))

            result = manager.delete_host(
                "remove-host",
                apply=True,
                timestamp="20260803-140000",
            )

            backup_path = Path(result["backup_path"])
            self.assertTrue(result["applied"])
            self.assertEqual(before, sha256(backup_path))
            self.assertEqual("config.backup-20260803-140000", backup_path.name)
            updated = config_path.read_text(encoding="utf-8")
            self.assertNotIn("Host remove-host", updated)
            self.assertIn("# description: keep next metadata", updated)
            self.assertIn("Host next-host", updated)

    def test_backup_file_uses_supplied_timestamp(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "config"
            path.write_text("content", encoding="utf-8")

            backup = backup_file(path, "20260803-140001")

            self.assertEqual("config.backup-20260803-140001", backup.name)
            self.assertEqual(path.read_bytes(), backup.read_bytes())

            with self.assertRaises(FileExistsError):
                backup_file(path, "20260803-140001")


if __name__ == "__main__":
    unittest.main()
