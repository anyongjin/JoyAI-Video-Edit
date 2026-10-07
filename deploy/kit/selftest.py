#!/usr/bin/env python3
"""Bounded offline checks for upload scope, SSH forwarding and DNS mutations."""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent

SHIM = '''#!/usr/bin/env python3
import json,os,pathlib,shutil,sys
name=pathlib.Path(sys.argv[0]).name
with open(os.environ["JOYAI_TEST_LOG"], "a") as f:
    f.write(json.dumps([name, *sys.argv[1:]]) + "\\n")
if name == "ssh" and "cat /etc/joyai/id_ed25519.pub" in sys.argv[-1]:
    print("ssh-ed25519 AAAATEST joyai-test")
elif name == "ssh-keygen":
    print("[example.com]:15298 ssh-ed25519 AAAATEST")
elif name == "scp" and sys.argv[-1].endswith("/joyai-deploy-kit/"):
    capture=pathlib.Path(os.environ["JOYAI_TEST_CAPTURE"])
    capture.mkdir()
    for argument in sys.argv[1:-1]:
        source=pathlib.Path(argument)
        if source.is_dir():
            shutil.copytree(source, capture / source.name)
        elif source.is_file():
            shutil.copyfile(source, capture / source.name)
elif name == "curl":
    print(os.environ.get("JOYAI_TEST_HEALTH", '{"ok":true,"runtime_loaded":true}'))
'''


class DeployChecks(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.bin = self.directory / "bin"
        self.bin.mkdir()
        for name in ("ssh", "scp", "ssh-keygen", "curl"):
            path = self.bin / name
            path.write_text(SHIM)
            path.chmod(0o755)
        self.log = self.directory / "calls.jsonl"
        self.env = {**os.environ, "PATH": str(self.bin) + os.pathsep + os.environ["PATH"],
                    "JOYAI_TEST_LOG": str(self.log),
                    "JOYAI_TEST_CAPTURE": str(self.directory / "upload")}

    def run_action(self, action, directory=ROOT):
        return subprocess.run(["bash", str(directory / "deploy.sh"), action],
                              env=self.env, text=True, capture_output=True)

    def calls(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()]

    def test_gpu_upload_excludes_local_credentials(self):
        result = self.run_action("gpu")
        self.assertEqual(result.returncode, 0, result.stderr)
        names = [path.relative_to(self.directory / "upload").as_posix()
                 for path in (self.directory / "upload").rglob("*")]
        self.assertIn("ops/bootstrap.sh", names)
        for required in (
            "ops/source.sh",
            "ops/export-detector.sh",
            "ops/supervisord.conf",
            "ops/joyai-supervisor.conf",
            "SHA256SUMS",
        ):
            self.assertIn(required, names)
        self.assertFalse(any(name.startswith(("source/", "assets/")) for name in names))
        self.assertIn("deploy/ops/joyai-supervisor.conf",
                      (ROOT / "ops/supervisord.conf").read_text())
        self.assertFalse(any(name.endswith((".gz", ".zip")) for name in names))
        self.assertFalse(any(".env" in name or "id_ed25519" in name for name in names))
        for call in self.calls():
            if call[0] in ("ssh", "scp"):
                self.assertIn("StrictHostKeyChecking=yes", call)

    def test_tunnel_is_loopback_only_and_fails_on_port_conflict(self):
        result = self.run_action("tunnel")
        self.assertEqual(result.returncode, 0, result.stderr)
        call = self.calls()[-1]
        self.assertIn("127.0.0.1:18080:127.0.0.1:8080", call)
        self.assertIn("ExitOnForwardFailure=yes", call)

    def test_public_key_authorization_is_restricted(self):
        result = self.run_action("public")
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = self.calls()
        self.assertTrue(any("/tmp/gateway.sh prepare" in call[-1] for call in calls))
        self.assertTrue(any("/tmp/gateway.sh apply" in call[-1] for call in calls))
        self.assertTrue(any("authorized_keys" in call[-1] for call in calls))
        source = (ROOT / "deploy.sh").read_text()
        self.assertIn('restrict,port-forwarding,permitopen=', source)

    def test_public_rejects_http_200_with_unhealthy_runtime(self):
        self.env["JOYAI_TEST_HEALTH"] = '{"ok":false,"runtime_loaded":true}'
        self.assertNotEqual(self.run_action("public").returncode, 0)

    def test_invalid_host_is_rejected_before_ssh(self):
        (self.directory / "deploy.sh").write_text((ROOT / "deploy.sh").read_text())
        (self.directory / "config.env").write_text(
            (ROOT / "config.env").read_text().replace(
                "GPU_HOST=connect.westd.seetacloud.com", "GPU_HOST='host;echo injected'"))
        self.assertNotEqual(self.run_action("status", self.directory).returncode, 0)
        self.assertFalse(self.log.exists())


@unittest.skipUnless(sys.platform.startswith("linux"), "GPU checkout tests require Linux")
class SourceChecks(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.target = self.directory / "project"
        upstream = self.directory / "upstream"
        upstream.mkdir()
        for required in ("deploy/run_server.sh", "deploy/static/index.html",
                         "deploy/joyomni_ops/setup.py", "deploy/requirements.txt",
                         "assets/cases/case01_source.gif", "LICENSE",
                         "deploy/rv2v_reference/reference.png"):
            path = upstream / required
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("fixture")
        subprocess.run(["git", "init", "-q", str(upstream)], check=True)
        subprocess.run(["git", "-C", str(upstream), "add", "."], check=True)
        subprocess.run(["git", "-C", str(upstream), "-c", "user.name=Kit Test",
                        "-c", "user.email=kit-test@example.invalid",
                        "commit", "-qm", "upstream fixture"], check=True)
        self.revision = subprocess.check_output(
            ["git", "-C", str(upstream), "rev-parse", "HEAD"], text=True).strip()
        script = (ROOT / "ops/source.sh").read_text()
        self.assertIn("COMMIT=ca17e1d1030f454cb98b0ed549b4d31a60139ceb", script)
        self.script = self.directory / "source.sh"
        self.script.write_text(script.replace("ca17e1d1030f454cb98b0ed549b4d31a60139ceb",
                                              self.revision))
        self.env = {**os.environ,
                    "GIT_CONFIG_COUNT": "1",
                    "GIT_CONFIG_KEY_0": "url." + upstream.as_uri() + ".insteadOf",
                    "GIT_CONFIG_VALUE_0": "https://github.com/jd-opensource/JoyAI-Video-Edit.git"}

    def run_source(self):
        return subprocess.run(["bash", str(self.script), str(self.target)],
                              env=self.env, text=True, capture_output=True)

    def test_fixed_checkout_contains_runtime_and_can_be_reused(self):
        result = self.run_source()
        self.assertEqual(result.returncode, 0, result.stderr)
        revision = subprocess.check_output(
            ["git", "-C", str(self.target), "rev-parse", "HEAD"], text=True).strip()
        self.assertEqual(revision, self.revision)
        for required in ("deploy/run_server.sh", "deploy/static/index.html",
                         "deploy/joyomni_ops/setup.py", "deploy/requirements.txt",
                         "assets/cases/case01_source.gif", "LICENSE"):
            self.assertTrue((self.target / required).is_file(), required)
        self.assertTrue(list((self.target / "deploy/rv2v_reference").glob("*.png")))
        sentinel = self.target / "keep.txt"
        sentinel.write_text("keep")
        self.assertEqual(self.run_source().returncode, 0)
        self.assertEqual(sentinel.read_text(), "keep")

    def test_foreign_directory_is_preserved(self):
        self.target.mkdir()
        sentinel = self.target / "keep.txt"
        sentinel.write_text("keep")
        self.assertNotEqual(self.run_source().returncode, 0)
        self.assertEqual(sentinel.read_text(), "keep")

    def test_failed_download_leaves_no_partial_project(self):
        self.env["GIT_CONFIG_KEY_0"] = "url." + (self.directory / "missing.git").as_uri() + ".insteadOf"
        self.assertNotEqual(self.run_source().returncode, 0)
        self.assertFalse(self.target.exists())
        self.assertEqual(list(self.directory.glob("project.download.*")), [])

    def test_concurrent_target_creation_is_not_overwritten_or_nested(self):
        bin_dir = self.directory / "bin"
        bin_dir.mkdir()
        mv = bin_dir / "mv"
        mv.write_text('#!/bin/sh\nmkdir "$JOYAI_TEST_TARGET"\n'
                      'printf keep > "$JOYAI_TEST_TARGET/keep.txt"\n'
                      'exec /usr/bin/mv "$@"\n')
        mv.chmod(0o755)
        self.env["PATH"] = str(bin_dir) + os.pathsep + os.environ["PATH"]
        self.env["JOYAI_TEST_TARGET"] = str(self.target)
        self.assertNotEqual(self.run_source().returncode, 0)
        self.assertEqual([path.name for path in self.target.iterdir()], ["keep.txt"])
        self.assertEqual((self.target / "keep.txt").read_text(), "keep")
        self.assertEqual(list(self.directory.glob("project.download.*")), [])


class DnsChecks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        dotenv = types.ModuleType("dotenv")
        dotenv.dotenv_values = lambda _: {"OSS_ACCESS_KEY_ID": "test-id",
                                          "OSS_ACCESS_KEY_SECRET": "test-secret"}
        spec = importlib.util.spec_from_file_location("joyai_alidns_test", ROOT / "ops/alidns.py")
        cls.module = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {"dotenv": dotenv}):
            spec.loader.exec_module(cls.module)

    def invoke(self, records):
        calls = []

        def request(action, credentials, **parameters):
            calls.append((action, parameters))
            return {"DomainRecords": {"Record": records}}

        args = ["alidns.py", "--set-ip", "47.103.55.27"]
        with patch.object(self.module, "request", request), patch.object(sys, "argv", args), \
                contextlib.redirect_stdout(io.StringIO()):
            self.module.main()
        return calls

    def test_existing_correct_record_is_not_rewritten(self):
        record = {"RecordId": "1", "RR": "joyai", "Type": "A",
                  "Value": "47.103.55.27", "Status": "ENABLE"}
        self.assertTrue(all(action == "DescribeDomainRecords" for action, _ in self.invoke([record])))

    def test_other_subdomain_is_never_updated(self):
        calls = self.invoke([{"RecordId": "2", "RR": "other", "Type": "A"}])
        added = [parameters for action, parameters in calls if action == "AddDomainRecord"]
        self.assertEqual(len(added), 1)
        self.assertEqual(added[0]["RR"], "joyai")
        self.assertEqual(added[0]["DomainName"], "nuvatech.cn")

    def test_conflicting_record_types_are_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "Conflicting"):
            self.invoke([{"RecordId": "3", "RR": "joyai", "Type": "CNAME"}])

    def test_disabled_record_is_enabled(self):
        calls = self.invoke([{"RecordId": "4", "RR": "joyai", "Type": "A",
                              "Value": "47.103.55.27", "Status": "DISABLE"}])
        self.assertIn(("SetDomainRecordStatus", {"RecordId": "4", "Status": "Enable"}), calls)


if __name__ == "__main__":
    unittest.main(verbosity=2)
