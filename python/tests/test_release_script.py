"""The release helper refuses safely, without uploading anything.

Every test runs the script as a subprocess with stdin closed, so the
interactive paths must degrade rather than hang. Nothing here touches the
network: the missing-token aborts happen before any upload, version check,
or build. The slow path (a real build + venv) is exercised by hand, not
here - a 30-second test would punish every suite run for a script that
releases rarely.
"""

import os
import subprocess
import sys
import tempfile
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_SCRIPT = os.path.join(_ROOT, "python", "scripts", "pypi_release.py")


def _run(*argv, stdin_data=""):
    # A token file that cannot exist, so the refusal paths hold on every
    # machine: the developer checkout may carry a real python/.env (which
    # would take the token path instead), and CI carries none. Without this
    # the tests assert one machine's state, not the script's behavior.
    missing = os.path.join(
        tempfile.gettempdir(), "pypi-release-no-such-env-%d" % os.getpid()
    )
    proc = subprocess.run(
        [sys.executable, _SCRIPT, "--env-file", missing, *argv],
        cwd=os.path.join(_ROOT, "python"),
        input=stdin_data,
        capture_output=True,
        text=True,
        timeout=120,
    )
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


class ReleaseRefusals(unittest.TestCase):
    def test_the_menu_rejects_a_bad_pick(self):
        code, out = _run(stdin_data="9\n")
        self.assertNotEqual(code, 0)
        self.assertIn("nothing uploaded", out)

    def test_dry_run_without_a_token_aborts_cleanly(self):
        code, out = _run("dry-run")
        self.assertNotEqual(code, 0)
        self.assertIn("nothing uploaded", out)
        # The message names the file it looked in, so a refusal on a custom
        # --env-file does not send the reader to the default one.
        self.assertIn("pypi-release-no-such-env", out)

    def test_publish_without_a_token_aborts_cleanly(self):
        code, out = _run("publish")
        self.assertNotEqual(code, 0)
        self.assertIn("nothing uploaded", out)
        self.assertIn("pypi-release-no-such-env", out)

    def test_help_lists_all_three_modes(self):
        code, out = _run("--help")
        self.assertEqual(code, 0)
        for mode in ("test", "dry-run", "publish"):
            self.assertIn(mode, out)


if __name__ == "__main__":
    unittest.main()
