"""Doctor output, both arms, without a live Studio - and with one when asked.

The doctor is the diagnostic a user runs when nothing works, so its failure
text is the product: each arm below asserts the *named fix*, not just a
nonzero exit. Three arms run against the fake proxy in `fake_proxy.py` (no
Studio needed - this is what CI executes), one arm points the launch command
at something that does not exist, and a fifth runs the real thing against a
live Studio when ``ROBLOX_STUDIO_MCP_INTEGRATION=1`` is set, the same gate
`test_integration_studio.py` uses.
"""

import io
import json
import os
import subprocess
import sys
import unittest
from contextlib import redirect_stdout
from unittest import mock

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(_ROOT, "python", "src"))

from roblox_studio_mcp import doctor  # noqa: E402

FAKE_PROXY = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fake_proxy.py")
INTEGRATION = os.environ.get("ROBLOX_STUDIO_MCP_INTEGRATION") == "1"


def _run_doctor(mode=None, command=None, extra_env=None):
    """Run the doctor in-process with the launch pointed somewhere specific,
    returning (exit code, captured stdout)."""
    env = {}
    # The fake proxy is a Python script: launch it with this interpreter so
    # the test runs anywhere. Pointing only ARGS at the script leaves COMMAND
    # on the platform default (`cmd.exe ...`), which does not exist off
    # Windows - and on Windows only works through file association. An
    # explicit command also turns shell mode off in the doctor.
    env["ROBLOX_STUDIO_MCP_PROXY_COMMAND"] = (
        command if command is not None else sys.executable
    )
    if mode is not None:
        env["ROBLOX_STUDIO_MCP_PROXY_ARGS"] = json.dumps(
            [FAKE_PROXY, "--mode", mode]
        )
    if extra_env:
        env.update(extra_env)
    buffer = io.StringIO()
    with mock.patch.dict(os.environ, env), redirect_stdout(buffer):
        code = doctor.main([])
    return code, buffer.getvalue()


class DoctorSuccessArm(unittest.TestCase):
    def test_a_healthy_proxy_and_studio_pass(self):
        code, out = _run_doctor(mode="ok")
        self.assertEqual(code, 0, out)
        self.assertIn("doctor: PASS", out)
        # The fake's Studio instance is reported by name and id.
        self.assertIn("Fake Place", out)
        self.assertIn("studio-fake-1", out)
        # Both trust labels appear, separately.
        self.assertIn("proxied_upstream", out)
        self.assertIn("extended", out)
        # Step 4 compares against the checked-in snapshot and finds no drift.
        self.assertIn("no drift", out)

    def test_the_extended_contract_line_is_reported(self):
        _code, out = _run_doctor(mode="ok")
        self.assertIn("contract/tools.json", out)


class DoctorFailureArmsNameTheFix(unittest.TestCase):
    def test_proxy_not_on_path(self):
        code, out = _run_doctor(
            command=os.path.join(_ROOT, "definitely-not-a-proxy-xyz"),
            extra_env={"ROBLOX_STUDIO_MCP_PROXY_ARGS": "[]"},
        )
        self.assertEqual(code, 1, out)
        self.assertIn("FAIL", out)
        # Names where the proxy actually lives, so the fix is actionable.
        self.assertIn("mcp.bat", out)
        self.assertIn("FIX", out)

    def test_proxy_that_never_speaks(self):
        code, out = _run_doctor(mode="mute")
        self.assertEqual(code, 1, out)
        self.assertIn("handshake", out)

    def test_studio_unreachable_names_the_toggle_fix(self):
        code, out = _run_doctor(mode="no-studio")
        self.assertEqual(code, 1, out)
        self.assertIn("MCP plugin", out)
        self.assertIn("Studio is not reachable", out)

    def test_upstream_drift_fails_with_the_refresh_named(self):
        code, out = _run_doctor(mode="drift")
        self.assertEqual(code, 1, out)
        self.assertIn("brand_new_upstream_tool", out)  # the warning
        self.assertIn("missing", out)  # the failure
        self.assertIn("contract/build_upstream.py --live", out)  # the fix

    def test_an_unreadable_snapshot_warns_without_failing(self):
        code, out = _run_doctor(
            mode="ok", extra_env={"ROBLOX_STUDIO_MCP_UPSTREAM_SNAPSHOT": os.devnull}
        )
        # /dev/null exists but is not a snapshot: the doctor degrades to a
        # warning rather than failing on an unreadable file - a diagnostic
        # that fails on its own missing data file buries the real answer.
        self.assertEqual(code, 0, out)
        self.assertIn("could not read", out)


class LaunchResolution(unittest.TestCase):
    def test_json_args_and_shell_default_off_when_overridden(self):
        env = {
            "ROBLOX_STUDIO_MCP_PROXY_COMMAND": "my-proxy",
            "ROBLOX_STUDIO_MCP_PROXY_ARGS": '["--flag", "x"]',
        }
        with mock.patch.dict(os.environ, env):
            command, args, shell = doctor._resolve_launch()
        self.assertEqual(command, "my-proxy")
        self.assertEqual(args, ["--flag", "x"])
        self.assertFalse(shell)

    def test_plain_string_args_split_on_whitespace(self):
        env = {
            "ROBLOX_STUDIO_MCP_PROXY_COMMAND": "my-proxy",
            "ROBLOX_STUDIO_MCP_PROXY_ARGS": "--flag x",
        }
        with mock.patch.dict(os.environ, env):
            _command, args, _shell = doctor._resolve_launch()
        self.assertEqual(args, ["--flag", "x"])

    def test_explicit_shell_flag_is_honoured(self):
        env = {
            "ROBLOX_STUDIO_MCP_PROXY_COMMAND": "cmd.exe",
            "ROBLOX_STUDIO_MCP_PROXY_ARGS": '["/c", "echo hi"]',
            "ROBLOX_STUDIO_MCP_PROXY_SHELL": "1",
        }
        with mock.patch.dict(os.environ, env):
            _command, _args, shell = doctor._resolve_launch()
        self.assertTrue(shell)

    def test_no_override_keeps_the_platform_default(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            from roblox_studio_mcp.roblox import default_command, default_shell

            command, args, shell = doctor._resolve_launch()
        self.assertEqual(command, default_command())
        self.assertEqual(shell, default_shell())
        self.assertTrue(args or command)


@unittest.skipUnless(
    INTEGRATION,
    "needs live Studio (ROBLOX_STUDIO_MCP_INTEGRATION=1)",
)
class DoctorAgainstLiveStudio(unittest.TestCase):
    """The real run: `python -m` from a checkout, against the attached Studio.

    This is the assertion the whole snapshot exists for - the one that sees
    the surface Studio actually ships today. It is gated like every other
    live test in the suite (see `test_integration_studio.py`), and read-only:
    the doctor launches a proxy, handshakes, lists tools and reads the
    instance list. It never writes to the place.
    """

    def test_doctor_passes_and_reports_the_real_surface(self):
        env = dict(os.environ)
        env["PYTHONPATH"] = os.path.join(_ROOT, "python", "src")
        proc = subprocess.run(
            [sys.executable, "-m", "roblox_studio_mcp.doctor"],
            capture_output=True,
            text=True,
            timeout=180,
            env=env,
            cwd=_ROOT,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("doctor: PASS", proc.stdout)
        self.assertIn("Studio instance", proc.stdout)
        self.assertIn("no drift", proc.stdout)


if __name__ == "__main__":
    unittest.main()
