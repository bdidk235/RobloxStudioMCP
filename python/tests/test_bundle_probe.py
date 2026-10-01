"""The bundle probe's shell logic, executed rather than read.

`studio-bundle-probe.yml` is the one workflow kept in this repo, and its
linkage check is the part with a dangerous failure mode: if the `case` never
matches, or the loop never runs, it prints `missing: 0` and the run's summary
claims the bundle is correctly linked having checked nothing. A diagnostic that
reports success because it did nothing is the recurring failure in this project,
so this executes the real loop out of the real file.

The loop is extracted from the workflow rather than copied, so the test tracks
the shipped script instead of drifting from it. Everything it needs is stubbed,
so it runs on any host - Windows included - with no `otool` present.

`bash -n` is explicitly not the check: it passes on scripts that fail at run
time, and it passed on a shipped workflow that never wrote its summary.
"""

from __future__ import annotations

import os
import pathlib
import re
import shutil
import subprocess

import pytest

WORKFLOW = pathlib.Path(__file__).resolve().parents[2] / ".github/workflows/studio-bundle-probe.yml"


def _find_bash() -> str | None:
    """A real bash, not the WSL launcher.

    `shutil.which("bash")` on Windows finds the WSL *launcher* in System32. It
    does not run a script; it tries to start a distribution and then blocks.
    Every test here hung for its full timeout before this was fixed - four
    minutes to learn that a path lookup was wrong. So on Windows the Git bash is
    looked for by name first, and the System32 launcher is rejected outright.
    """
    if os.name == "nt":
        for candidate in (
            r"C:\Program Files\Git\bin\bash.exe",
            r"C:\Program Files\Git\usr\bin\bash.exe",
            r"C:\Program Files (x86)\Git\bin\bash.exe",
        ):
            if os.path.isfile(candidate):
                return candidate
    found = shutil.which("bash")
    if found and "system32" in found.replace("/", "\\").lower():
        return None
    return found


BASH = _find_bash()

pytestmark = pytest.mark.skipif(
    BASH is None, reason="no usable bash (the WSL launcher does not count)"
)

# Short, because a wrong interpreter should fail fast rather than burn a minute
# per test. The happy path takes well under a second.
TIMEOUT = 20


def _step_text(title: str) -> str:
    """The `run:` body of the named step, straight from the YAML."""
    import yaml

    doc = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    for job in doc["jobs"].values():
        for step in job["steps"]:
            if step.get("name") == title:
                return step["run"]
    raise AssertionError(f"no step named {title!r} in {WORKFLOW.name}")


def _linkage_block() -> str:
    """The `loader=` assignment plus the whole dependency loop, unindented.

    Both halves matter. Extracting only the loop leaves `loader` unbound and the
    loop aborts on the first dependency; extracting only the body drops the
    `missing:` tally the assertions are about. Both mistakes were made.
    """
    run = _step_text("Bundle identity, linkage and signature")
    m = re.search(r'(loader="\$\(cd .*?\n)(.*?echo "missing: \$miss"\n)', run, re.S)
    assert m, "the linkage block moved; update the extraction"
    return m.group(1) + m.group(2)


# A dependency list covering every branch of the `case`: two system libraries to
# be skipped, one resolvable @loader_path, one absent @loader_path, an @rpath that
# cannot resolve to a path at all, an absolute path, and a bare name.
FAKE_OTOOL_L = """/fake/bin:
\t/usr/lib/libSystem.B.dylib (compatibility version 1.0.0, current version 1.0.0)
\t/System/Library/Frameworks/CoreFoundation.framework/Versions/A/CoreFoundation (compatibility version 1.0.0, current version 1.0.0)
\t@loader_path/Frameworks/Present.dylib (compatibility version 1.0.0, current version 1.0.0)
\t@loader_path/Frameworks/Absent.dylib (compatibility version 1.0.0, current version 1.0.0)
\t@rpath/libmimalloc.3.dylib (compatibility version 1.0.0, current version 1.0.0)
\t/opt/roblox/libexternal.dylib (compatibility version 1.0.0, current version 1.0.0)
\tlibbare.dylib (compatibility version 1.0.0, current version 1.0.0)
"""


def _run_linkage(tmp_path: pathlib.Path, otool_output: str) -> str:
    """Execute the workflow's loop with `otool` stubbed, and return its output."""
    contents = tmp_path / "Contents"
    # @loader_path is the directory of the *executable*, so the resolvable
    # fixture must sit beside it. Putting it in Contents/Frameworks instead made
    # correct resolution look broken, which is a fixture bug that reads exactly
    # like a real failure.
    (contents / "MacOS" / "Frameworks").mkdir(parents=True)
    (contents / "MacOS" / "Frameworks" / "Present.dylib").write_bytes(b"\xcf\xfa\xed\xfe")

    script = tmp_path / "linkage.sh"
    script.write_text(
        "#!/bin/bash\nset -uo pipefail\n"
        f'contents="{contents}"\n'
        f'bin="{contents / "MacOS" / "Probe"}"\n'
        f"otool() {{ cat <<'EOF'\n{otool_output}EOF\n}}\n"
        f"{_linkage_block()}\n",
        encoding="utf-8",
        newline="\n",
    )
    r = subprocess.run([BASH, str(script)], capture_output=True, text=True, timeout=TIMEOUT)
    assert r.returncode == 0, f"linkage block exited {r.returncode}: {r.stderr}"
    return r.stdout


def test_linkage_reports_each_dependency_once(tmp_path: pathlib.Path) -> None:
    """A universal binary lists every dependency twice; the report must not.

    `otool -L` prints one slice per architecture. Without `sort -u` the tally
    doubles, and the shipped workflow reported "missing: 2" for a single
    unresolvable dylib - which reads as two separate problems.
    """
    out = _run_linkage(tmp_path, FAKE_OTOOL_L)
    assert out.count("MISSING @rpath/libmimalloc.3.dylib") == 1, out
    assert out.count("MISSING @loader_path/Frameworks/Absent.dylib") == 1, out
    assert "missing: 4" in out, out


def test_linkage_classifies_every_branch(tmp_path: pathlib.Path) -> None:
    out = _run_linkage(tmp_path, FAKE_OTOOL_L)
    # System libraries are skipped, not reported.
    assert "libSystem" not in out, out
    assert "CoreFoundation" not in out, out
    # @loader_path resolves against the executable's own directory.
    assert "ok      @loader_path/Frameworks/Present.dylib" in out, out
    assert "MISSING @loader_path/Frameworks/Absent.dylib" in out, out
    # An @rpath cannot be resolved to a path, and must say so rather than
    # silently counting as present.
    assert "rpath-unresolved" in out, out
    assert "(bare) libbare.dylib" in out, out


def test_present_dependency_is_not_counted_missing(tmp_path: pathlib.Path) -> None:
    """The tally's whole purpose: what exists must not appear in what is missing.

    A `missing:` count that included the resolvable dependency would mean the
    existence test is inverted or skipped, and the number would still look
    plausible.
    """
    out = _run_linkage(tmp_path, FAKE_OTOOL_L)
    ok = [ln for ln in out.splitlines() if ln.startswith("ok ")]
    assert ok == ["ok      @loader_path/Frameworks/Present.dylib"], out
    tally = int(re.search(r"missing: (\d+)", out).group(1))
    assert tally == len([ln for ln in out.splitlines() if ln.startswith("MISSING")]), out


def test_rpath_list_is_deduplicated(tmp_path: pathlib.Path) -> None:
    """LC_RPATH is also printed per architecture, so it needs `sort -u` too."""
    run = _step_text("Bundle identity, linkage and signature")
    m = re.search(r'rpaths="\$\((.*?)\)"', run, re.S)
    assert m, "the rpath extraction moved; update this test"
    pipeline = m.group(1)
    assert "sort -u" in pipeline, (
        "rpaths are not deduplicated, so a universal binary reports every "
        f"search path twice: {pipeline.strip()}"
    )


def test_negative_control_resolution_actually_matters(tmp_path: pathlib.Path) -> None:
    """Break @loader_path resolution and confirm the test would fail.

    Without this, a green run only proves the assertions match the current
    output - not that they would notice a regression.
    """
    good = _run_linkage(tmp_path, FAKE_OTOOL_L)
    assert "ok      @loader_path/Frameworks/Present.dylib" in good

    broken = _linkage_block().replace(
        '@loader_path/*) real="$loader/${dep#@loader_path/}"',
        '@loader_path/*) real="$contents/WRONG/${dep#@loader_path/}"',
    )
    assert broken != _linkage_block(), "the substitution no longer matches; update the test"

    contents = tmp_path / "Contents"
    (contents / "MacOS" / "Frameworks").mkdir(parents=True, exist_ok=True)
    (contents / "MacOS" / "Frameworks" / "Present.dylib").write_bytes(b"\xcf\xfa\xed\xfe")
    script = tmp_path / "broken.sh"
    script.write_text(
        "#!/bin/bash\nset -uo pipefail\n"
        f'contents="{contents}"\n'
        f'bin="{contents / "MacOS" / "Probe"}"\n'
        f"otool() {{ cat <<'EOF'\n{FAKE_OTOOL_L}EOF\n}}\n"
        f"{broken}\n",
        encoding="utf-8",
        newline="\n",
    )
    r = subprocess.run([BASH, str(script)], capture_output=True, text=True, timeout=TIMEOUT)
    assert r.returncode == 0, r.stderr
    assert "ok      @loader_path/Frameworks/Present.dylib" not in r.stdout, (
        "breaking @loader_path resolution did not change the report, so these "
        "assertions cannot detect that class of regression"
    )
