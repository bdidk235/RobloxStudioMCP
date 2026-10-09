"""Path confinement for the tools that read a caller-named file.

**Why these tests exist.** `extended_execute_luau_from_file` and
`extended_insert_asset_from_file` take a `file_path` straight from the model and
read it at the process's full user privilege. Before this, neither checked the
path against anything:

    resolved = os.path.abspath(os.path.expanduser(file_path))
    if not os.path.isfile(resolved):
        raise ToolError(INVALID_ARGUMENT, ...)

`abspath` collapses `..` and stops. It does not confine a path to a tree, and it
does not follow symlinks, so a link *inside* an allowed directory pointing at
`~/.ssh/id_rsa` passed it untouched. Measured across `python/src` before the
change: zero `is_relative_to`, zero `realpath`, zero `commonpath`.

**These assert the refusal CODE, not merely that something raised.** This repo
has shipped refusals that fired for the wrong reason -- see `test_closed_sets.py`
on `LAUNCH_FAILED`, a code that existed, compiled, and never matched. A test that
only asserts "it raised" would pass just as happily if `_confined` refused every
path for the wrong reason, so each case pins `ToolError.code`.

**The root is a configured constant, not a parameter.** `file_root()` reads
`ROBLOX_STUDIO_MCP_FILE_ROOT` (default: the working directory at call time), and
the only opt-out is the operator's `ROBLOX_STUDIO_MCP_ALLOW_OUTSIDE`. The old
model-settable `allow_outside` tool argument was a disclosure primitive --
outside file + `file_type="script"` + read-back, two calls -- so it is gone
from the functions, the schemas, and the dispatchers. A call carrying it is
refused with `INVALID_ARGUMENT` naming the migration, never silently confined.

**The size cap is measured, not guessed.** 18,508 `.luau`/`.lua`/`.rbxm`/`.rbxmx`
files were surveyed: the largest is 13,917,476 bytes and the largest a caller
would realistically send is a 12,220,210-byte `.luau`. The cap sits above that so
real work is not broken; these tests pin that it is enforced rather than constant.

Windows note: symlink creation needs Developer Mode or elevation, so the symlink
test skips rather than failing for a reason that is not the code's.
"""

import asyncio
import os
import sys
import tempfile
import unittest
from pathlib import Path


def _drive(coro):
    """Run a coroutine to completion.

    `_drive(...)` raises a DeprecationWarning
    on 3.12+ and is slated for removal; `asyncio.run` is the supported call and
    these tests do not need a loop they control.
    """
    return asyncio.run(coro)

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(_ROOT, "python", "src"))

from roblox_studio_mcp.extended.errors import (  # noqa: E402
    CAPABILITY_DENIED,
    INVALID_ARGUMENT,
    SIZE_LIMIT,
    ToolError,
)
from roblox_studio_mcp.extended.extensions import (  # noqa: E402
    ALLOW_OUTSIDE_ENV_VAR,
    FILE_ROOT_ENV_VAR,
    MAX_FILE_BYTES,
    MEASURED_MAX_FILE_BYTES,
    _confined,
    confinement_allows_outside,
    execute_luau_from_file,
    file_root,
    insert_asset_from_file,
)


def _skip_on_windows_without_symlinks(test):
    def wrapper(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "target.txt"
            target.write_text("x", encoding="utf-8")
            link = Path(tmp) / "link.txt"
            try:
                link.symlink_to(target)
            except (OSError, NotImplementedError):
                self.skipTest("symlinks unavailable (Windows needs Developer Mode)")
            test(self)

    return wrapper


class Confinement(unittest.TestCase):
    """The root is cwd, so every case runs with cwd inside a temp tree."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name).resolve()
        self.inner = self.root / "work"
        self.inner.mkdir()
        (self.inner / "ok.luau").write_text("print(1)", encoding="utf-8")
        self._cwd = os.getcwd()
        os.chdir(self.inner)

    def tearDown(self):
        os.chdir(self._cwd)
        self._tmp.cleanup()

    # --- the ordinary case ------------------------------------------------- #

    def test_a_file_inside_the_root_resolves(self):
        got = _confined("ok.luau")
        self.assertEqual(got, (self.inner / "ok.luau").resolve())
        self.assertTrue(got.is_relative_to(self.root))

    def test_a_subdirectory_of_the_root_is_fine(self):
        deep = self.inner / "a" / "b"
        deep.mkdir(parents=True)
        (deep / "c.luau").write_text("print(2)", encoding="utf-8")
        self.assertEqual(_confined("a/b/c.luau"), (deep / "c.luau").resolve())

    def test_an_absolute_path_inside_the_root_is_fine(self):
        self.assertEqual(
            _confined(str(self.inner / "ok.luau")),
            (self.inner / "ok.luau").resolve(),
        )

    # --- traversal --------------------------------------------------------- #

    def test_dotdot_escape_is_refused_with_the_code(self):
        outside = self.root / "outside.luau"
        outside.write_text("print(3)", encoding="utf-8")
        with self.assertRaises(ToolError) as caught:
            _confined("../outside.luau")
        self.assertEqual(caught.exception.code, CAPABILITY_DENIED)
        self.assertIn("outside the file root", caught.exception.message)

    def test_dotdot_that_stays_inside_is_allowed(self):
        # The rule is the resolved location, not the presence of "..". A path
        # that goes up and back down into the root is not an escape, and
        # refusing it would make the guard useless in ordinary use.
        self.assertEqual(
            _confined("../work/ok.luau"), (self.inner / "ok.luau").resolve()
        )

    def test_a_deep_traversal_chain_is_refused(self):
        with self.assertRaises(ToolError) as caught:
            _confined("a/b/c/../../../../../../etc/passwd")
        self.assertEqual(caught.exception.code, CAPABILITY_DENIED)

    # --- absolute paths outside the root ---------------------------------- #

    def test_an_absolute_path_outside_is_refused_with_the_code(self):
        with self.assertRaises(ToolError) as caught:
            _confined(str(self.root / "outside.luau"))
        self.assertEqual(caught.exception.code, CAPABILITY_DENIED)

    @_skip_on_windows_without_symlinks
    def test_a_symlink_pointing_out_is_refused_with_the_code(self):
        # The case `abspath` cannot catch: the path as written is inside the
        # root, and only resolving the link reveals where it lands.
        secret = self.root / "secret.txt"
        secret.write_text("token", encoding="utf-8")
        link = self.inner / "innocent.luau"
        link.symlink_to(secret)
        with self.assertRaises(ToolError) as caught:
            _confined("innocent.luau")
        self.assertEqual(caught.exception.code, CAPABILITY_DENIED)

    @_skip_on_windows_without_symlinks
    def test_the_operator_switch_allows_what_the_default_refuses(self):
        secret = self.root / "secret.txt"
        secret.write_text("token", encoding="utf-8")
        link = self.inner / "innocent.luau"
        link.symlink_to(secret)
        old = os.environ.get(ALLOW_OUTSIDE_ENV_VAR)
        os.environ[ALLOW_OUTSIDE_ENV_VAR] = "1"
        try:
            self.assertTrue(confinement_allows_outside())
            self.assertEqual(_confined("innocent.luau"), secret.resolve())
        finally:
            if old is None:
                os.environ.pop(ALLOW_OUTSIDE_ENV_VAR, None)
            else:
                os.environ[ALLOW_OUTSIDE_ENV_VAR] = old

    def test_the_operator_switch_allows_traversal_too(self):
        old = os.environ.get(ALLOW_OUTSIDE_ENV_VAR)
        os.environ[ALLOW_OUTSIDE_ENV_VAR] = "true"
        try:
            self.assertEqual(
                _confined("../outside.luau"),
                (self.root / "outside.luau").resolve(),
            )
        finally:
            if old is None:
                os.environ.pop(ALLOW_OUTSIDE_ENV_VAR, None)
            else:
                os.environ[ALLOW_OUTSIDE_ENV_VAR] = old

    def test_a_per_call_opt_out_no_longer_exists(self):
        # The hole this file's successor closes: a model-settable boolean is
        # not confinement. Direct callers get a loud TypeError, never a gate
        # that opens.
        with self.assertRaises(TypeError):
            _confined("ok.luau", allow_outside=True)  # type: ignore[call-arg]

    def test_the_root_is_configurable_but_defaults_to_cwd(self):
        # Default follows the cwd the test chdir'd into.
        self.assertEqual(file_root(), self.inner.resolve())
        other = self.root / "other"
        other.mkdir()
        (other / "mine.luau").write_text("print(1)", encoding="utf-8")
        old = os.environ.get(FILE_ROOT_ENV_VAR)
        os.environ[FILE_ROOT_ENV_VAR] = str(other)
        try:
            self.assertEqual(file_root(), other.resolve())
            # The inner dir is now *outside* the configured root...
            with self.assertRaises(ToolError) as caught:
                _confined(str(self.inner / "ok.luau"))
            self.assertEqual(caught.exception.code, CAPABILITY_DENIED)
            # ...while a path under the configured root passes.
            self.assertEqual(
                _confined(str(other / "mine.luau")),
                (other / "mine.luau").resolve(),
            )
        finally:
            if old is None:
                os.environ.pop(FILE_ROOT_ENV_VAR, None)
            else:
                os.environ[FILE_ROOT_ENV_VAR] = old

    def test_unset_switch_values_stay_confined(self):
        for value in ("", "0", "false", "no", "off", "2"):
            old = os.environ.get(ALLOW_OUTSIDE_ENV_VAR)
            os.environ[ALLOW_OUTSIDE_ENV_VAR] = value
            try:
                self.assertFalse(
                    confinement_allows_outside(), "value %r must not lift" % value
                )
            finally:
                if old is None:
                    os.environ.pop(ALLOW_OUTSIDE_ENV_VAR, None)
                else:
                    os.environ[ALLOW_OUTSIDE_ENV_VAR] = old

    # --- not a file -------------------------------------------------------- #

    def test_a_directory_is_refused_with_the_code(self):
        with self.assertRaises(ToolError) as caught:
            _confined(str(self.inner))
        self.assertEqual(caught.exception.code, CAPABILITY_DENIED)
        self.assertIn("not a file", caught.exception.message)

    def test_a_missing_file_inside_the_root_passes_confinement(self):
        # Confinement is about *where*, so a missing path inside the root is not
        # this layer's refusal -- it falls through to the caller's existence
        # check and becomes INVALID_ARGUMENT there. Asserting otherwise would
        # pin two different codes to one condition.
        self.assertEqual(
            _confined("nope.luau"), (self.inner / "nope.luau").resolve()
        )

    # --- size cap ---------------------------------------------------------- #

    def test_a_file_over_the_cap_is_refused_with_the_code(self):
        big = self.inner / "big.luau"
        with open(big, "wb") as f:
            f.write(b"x" * 16)
        self.assertLess(16, MAX_FILE_BYTES)  # sanity: the fixture is small
        # Build the oversized file without allocating MAX_FILE_BYTES of RAM by
        # seeking past the cap -- sparse, so the test stays fast.
        with open(big, "wb") as f:
            f.truncate(MAX_FILE_BYTES + 1)
        with self.assertRaises(ToolError) as caught:
            _confined("big.luau")
        self.assertEqual(caught.exception.code, SIZE_LIMIT)
        self.assertEqual(caught.exception.data["limit"], MAX_FILE_BYTES)

    def test_the_cap_clears_the_largest_measured_real_file(self):
        # Compared against the constant, not a literal. When the number lived
        # here, lowering the cap and the number in one commit satisfied this --
        # the check could not fail. `MEASURED_MAX_FILE_BYTES` sits beside the cap
        # in extensions.py so one edit cannot move both sides.
        self.assertGreater(MAX_FILE_BYTES, MEASURED_MAX_FILE_BYTES)

    def test_the_measured_maximum_is_still_the_one_we_measured(self):
        # Pins the measurement itself, so "raise the cap" cannot quietly become
        # "the largest real file got smaller". 18,508 files, 2026-10-04.
        self.assertEqual(MEASURED_MAX_FILE_BYTES, 13_917_476)

    def test_a_file_just_under_the_cap_is_allowed(self):
        edge = self.inner / "edge.luau"
        with open(edge, "wb") as f:
            f.truncate(MAX_FILE_BYTES)
        self.assertEqual(_confined("edge.luau"), edge.resolve())


class ToolsUseTheGuard(unittest.TestCase):
    """The guard has to be on the tools' path, not merely present in the module.

    A helper that exists and is never called is the defect this file exists to
    prevent, so both entry points are driven far enough to observe the refusal.
    No Studio is needed: confinement runs before anything touches the connection.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name).resolve()
        self.inner = self.root / "work"
        self.inner.mkdir()
        (self.root / "outside.luau").write_text("print(9)", encoding="utf-8")
        self._cwd = os.getcwd()
        os.chdir(self.inner)

    def tearDown(self):
        os.chdir(self._cwd)
        self._tmp.cleanup()

    def test_execute_luau_from_file_refuses_outside_the_root(self):
        import asyncio

        with self.assertRaises(ToolError) as caught:
            _drive(
                execute_luau_from_file(None, "../outside.luau")  # type: ignore[arg-type]
            )
        self.assertEqual(caught.exception.code, CAPABILITY_DENIED)

    def test_insert_asset_from_file_refuses_outside_the_root(self):
        import asyncio

        with self.assertRaises(ToolError) as caught:
            _drive(
                insert_asset_from_file(None, "../outside.luau")  # type: ignore[arg-type]
            )
        self.assertEqual(caught.exception.code, CAPABILITY_DENIED)

    def test_refusal_precedes_any_studio_contact(self):
        # Passing None as the studio would raise AttributeError if the guard ran
        # after connection setup. CAPABILITY_DENIED proves it runs first, which is
        # what stops a refusal from costing a Studio round-trip.
        import asyncio

        with self.assertRaises(ToolError) as caught:
            _drive(
                execute_luau_from_file(None, "../outside.luau")  # type: ignore[arg-type]
            )
        self.assertEqual(caught.exception.code, CAPABILITY_DENIED)
        self.assertNotEqual(caught.exception.code, INVALID_ARGUMENT)


class RemovedToolArgumentIsRefused(unittest.TestCase):
    """A call carrying the removed `allow_outside` gets a refusal, not silence.

    The audit's PoC was two calls: insert an outside file as a script with the
    model-set flag, then read it back. The first call must now fail before
    dispatch with INVALID_ARGUMENT naming the operator migration -- the generic
    unknown-argument text would read as a typo, and silently confining would
    change what the caller asked for without saying so.
    """

    def _reject(self, tool, arguments):
        from roblox_studio_mcp import extended_server as es

        try:
            es._reject_unknown_arguments(tool, arguments)  # noqa: SLF001
        except ToolError as exc:
            return exc
        raise AssertionError("%s accepted %r" % (tool, arguments))

    def test_insert_with_allow_outside_is_refused_with_the_migration(self):
        err = self._reject(
            "extended_insert_asset_from_file",
            {"file_path": "/tmp/x.luau", "allow_outside": True},
        )
        self.assertEqual(err.code, INVALID_ARGUMENT)
        self.assertIn("ROBLOX_STUDIO_MCP_ALLOW_OUTSIDE", err.message)
        self.assertIn("ROBLOX_STUDIO_MCP_FILE_ROOT", err.message)

    def test_execute_from_file_with_allow_outside_is_refused(self):
        err = self._reject(
            "extended_execute_luau_from_file",
            {"file_path": "/tmp/x.luau", "allow_outside": True},
        )
        self.assertEqual(err.code, INVALID_ARGUMENT)

    def test_allow_outside_is_gone_from_both_schemas(self):
        from roblox_studio_mcp import extended_server as es

        for name in (
            "extended_insert_asset_from_file",
            "extended_execute_luau_from_file",
        ):
            tool = next(t for t in es._EXTENDED_TOOLS if t.name == name)  # noqa: SLF001
            self.assertNotIn("allow_outside", tool.input_schema.get("properties") or {})

    def test_the_poc_first_call_is_refused_before_any_read(self):
        # The disclosure primitive end to end at the gate: an outside secret
        # with file_type="script" never reaches a read -- confinement fires
        # first, with studio=None proving no Studio contact precedes it.
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            inner = root / "work"
            inner.mkdir()
            secret = root / "id_rsa"
            secret.write_text("PRIVATE", encoding="utf-8")
            cwd = os.getcwd()
            os.chdir(inner)
            try:
                with self.assertRaises(ToolError) as caught:
                    _drive(
                        insert_asset_from_file(None, str(secret), file_type="script")  # type: ignore[arg-type]
                    )
                self.assertEqual(caught.exception.code, CAPABILITY_DENIED)
            finally:
                os.chdir(cwd)


if __name__ == "__main__":
    unittest.main()