"""Release helper: test, dry-run, and publish the package to PyPI.

Three subcommands, in safety order. Run from python/:

    python scripts/pypi_release.py test       build + fresh-venv install + smoke
    python scripts/pypi_release.py dry-run    test, then TestPyPI upload + install
    python scripts/pypi_release.py publish    test, then the real upload

Run with no subcommand for an interactive menu that explains the three and
asks which to run. Every upload asks first unless --yes is passed; --yes
skips prompts, never safety checks.

Tokens live in untracked python/.env (the committed template is
python/.env.example, which authenticates nothing) - never in chat, logs, or
git. When a token is missing and stdin is a terminal, the script asks for it
(without echoing) and offers to save it to python/.env. The parser below is
ten lines on purpose: the package is dependency-free and a dotenv library
for two keys would be a dependency bought for nothing.

publish refuses on a dirty tree and when the version already exists on PyPI,
and asks for confirmation unless --yes is passed. There is deliberately no
flag that skips the version check: uploading is the one irreversible step
here, and a flag that skips it silently is how a dry run becomes a release.
"""

import argparse
import getpass
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_PYTHON = _HERE.parent
_NAME = "roblox-studio-mcp"
_PYPI_JSON = "https://pypi.org/pypi/%s/%s/json" % (_NAME, "%s")


def _die(message):
    raise SystemExit("pypi_release: %s" % message)


def _ask(prompt):
    """A yes/no question. Default NO - silence is refusal, not consent.

    EOF on stdin dies with a pointer at --yes instead of an EOFError
    traceback, which would read as a crash.
    """
    try:
        answer = input("  %s [yes/NO] " % prompt).strip()
    except EOFError:
        _die("needs an answer but stdin closed; re-run with --yes")
    return answer == "yes"


def _save_env(path, key, token):
    path.write_text(
        "%s=%s\n" % (key, token), encoding="utf-8"
    )
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    print("  saved to %s (mode 600; git ignores it)" % path)


def _dotenv(path):
    """The two tokens from `path`, or an error naming the file, not the key."""
    values = {}
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip().strip("\"'")
    return values


def _ensure_token(tokens, key, label, env_path):
    """The token, or ask for it interactively rather than dying on sight.

    A missing token aborts only when there is nobody to ask (piped stdin) or
    the user declines. A pasted token can be saved back to python/.env, which
    is the file the next run reads - offered, never assumed.
    """
    token = tokens.get(key, "")
    if token and not token.startswith("paste-"):
        return token
    # No prompt without a terminal. On Windows `getpass` reads the console
    # via msvcrt, ignoring redirected stdin entirely - so a piped stdin does
    # not fail, it hangs: on CI that was two 120-second timeouts. Tokens come
    # from the .env file or an interactive terminal, never a pipe.
    if not sys.stdin.isatty():
        print("  no %s in %s" % (label, env_path))
        print("  stdin is not a terminal, so there is nobody to ask - "
              "put %s in %s" % (key, env_path))
        _die("aborted - nothing uploaded")
    print("  no %s in %s (template: python/.env.example)" % (label, env_path))
    try:
        pasted = getpass.getpass("  paste %s (input hidden): " % label).strip()
    except (EOFError, KeyboardInterrupt):
        _die("aborted - nothing uploaded")
    if not pasted:
        _die("aborted - nothing uploaded")
    if _ask("save it to %s for next time" % env_path):
        with open(str(env_path), "a", encoding="utf-8") as handle:
            handle.write("%s=%s\n" % (key, pasted))
        try:
            os.chmod(env_path, 0o600)
        except OSError:
            pass
        print("  saved (mode 600; git ignores it)")
    return pasted


def _require_tool(module, pip_name):
    import importlib.util

    if importlib.util.find_spec(module) is None:
        _die("need %s; run: pip install %s" % (module, pip_name))


def _declared_version():
    text = (_PYTHON / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'(?m)^version\s*=\s*"([^"]+)"', text)
    if not match:
        _die("no version in pyproject.toml")
    return match.group(1)


def _run(argv, cwd=None, env=None):
    proc = subprocess.run(argv, cwd=cwd, env=env, capture_output=True, text=True)
    return proc


def _build(dist_dir):
    _require_tool("build", "build")
    print("  build: sdist + wheel into %s" % dist_dir)
    proc = _run(
        [sys.executable, "-m", "build", "--outdir", str(dist_dir), str(_PYTHON)]
    )
    if proc.returncode != 0:
        _die("build failed:\n%s" % (proc.stderr or proc.stdout)[-2000:])
    wheels = sorted(dist_dir.glob("*.whl"))
    if not wheels:
        _die("build produced no wheel")
    print("  build: %s" % wheels[-1].name)
    return wheels[-1]


def _fresh_venv():
    tmp = Path(tempfile.mkdtemp(prefix="pypi-venv-"))
    print("  venv: creating at %s" % tmp)
    proc = _run([sys.executable, "-m", "venv", str(tmp)])
    if proc.returncode != 0:
        _die("venv creation failed:\n%s" % (proc.stderr or "")[-1000:])
    bindir = tmp / ("Scripts" if os.name == "nt" else "bin")
    python = bindir / ("python.exe" if os.name == "nt" else "python")
    if not python.is_file():
        _die("venv has no interpreter at %s" % python)
    return tmp, str(python)


def _pip(python, *args):
    proc = _run([python, "-m", "pip", "install", "-q", *args])
    if proc.returncode != 0:
        _die("pip install failed:\n%s" % (proc.stderr or proc.stdout)[-2000:])


def _smoke(python, version):
    """Import, version, skills, entry point - the install proves itself."""
    script = (
        "import importlib.resources as r\n"
        "import roblox_studio_mcp as m\n"
        "assert m.__version__ == %r, m.__version__\n"
        "from roblox_studio_mcp.extended import RobloxStudio\n"
        "skills = list(r.files('roblox_studio_mcp').joinpath('skills').iterdir())\n"
        "source = %r\n"
        "assert len(skills) == len(source), (len(skills), len(source))\n"
        "print('  smoke: version %%s, %%d/%%d skills' %% (m.__version__, len(skills), len(source)))\n"
    )
    source_skills = sorted(
        p.name
        for p in (_PYTHON / "src" / "roblox_studio_mcp" / "skills").iterdir()
        if p.suffix == ".md"
    )
    proc = _run([python, "-c", script % (version, source_skills)])
    if proc.returncode != 0:
        _die("smoke test failed:\n%s" % (proc.stdout or proc.stderr)[-2000:])
    print(proc.stdout.strip())
    bindir = os.path.dirname(python)
    entry = os.path.join(
        bindir, "roblox-studio-mcp.exe" if os.name == "nt" else "roblox-studio-mcp"
    )
    if not os.path.isfile(entry):
        _die("console script missing at %s" % entry)
    print("  smoke: console script present")


def _clean_tree():
    try:
        proc = _run(
            ["git", "status", "--porcelain"], cwd=str(_PYTHON.parent)
        )
    except OSError:
        print("  warn: git unavailable, cannot prove a clean tree")
        return
    if proc.returncode != 0:
        print("  warn: not a git checkout, cannot prove a clean tree")
        return
    dirty = [
        line for line in proc.stdout.splitlines() if not line.startswith("??")
    ]
    if dirty:
        _die(
            "tree is dirty - commit first, then release:\n  "
            + "\n  ".join(dirty[:8])
        )
    print("  tree: clean (tracked files)")


def _pypi_version_exists(version):
    try:
        with urllib.request.urlopen(_PYPI_JSON % version, timeout=30) as _:
            return True
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return False
        _die("PyPI lookup failed with HTTP %s" % exc.code)
    except OSError as exc:
        _die("PyPI unreachable (%s); cannot prove the version is free" % exc)
    return False


def _upload(dist_dir, repository, token, verbose=False):
    _require_tool("twine", "twine")
    env = dict(os.environ)
    env["TWINE_USERNAME"] = "__token__"
    env["TWINE_PASSWORD"] = token
    files = sorted(str(p) for p in dist_dir.iterdir() if p.suffix in (".whl", ".gz"))
    print("  upload: %d file(s) to %s" % (len(files), repository))
    cmd = [sys.executable, "-m", "twine", "upload", "--repository", repository,
           "--non-interactive", "--disable-progress-bar"]
    if verbose:
        cmd.append("--verbose")
    proc = _run(
        cmd + files,
        env=env,
    )
    if proc.returncode != 0:
        _die("upload failed:\n%s" % (proc.stderr or proc.stdout)[-2000:])
    print("  upload: accepted")


def cmd_test(_args):
    version = _declared_version()
    print("test: version %s" % version)
    with tempfile.TemporaryDirectory(prefix="pypi-dist-") as dist:
        wheel = _build(Path(dist))
        _tmp, python = _fresh_venv()
        try:
            _pip(python, str(wheel))
            _smoke(python, version)
        finally:
            shutil.rmtree(_tmp, ignore_errors=True)
    print("test: green - the artifact installs and proves itself")


def cmd_dry_run(args):
    env_file = Path(args.env_file)
    tokens = _dotenv(env_file)
    token = _ensure_token(tokens, "PYPI_TEST_TOKEN", "TestPyPI token",
                           env_file)
    version = _declared_version()
    print("dry-run: version %s against TestPyPI" % version)
    with tempfile.TemporaryDirectory(prefix="pypi-dist-") as dist:
        dist_path = Path(dist)
        wheel = _build(dist_path)
        _tmp, python = _fresh_venv()
        try:
            _pip(python, str(wheel))
            _smoke(python, version)
        finally:
            shutil.rmtree(_tmp, ignore_errors=True)
        if not args.yes and not _ask("upload %s to TestPyPI" % version):
            _die("aborted - nothing uploaded")
        _upload(dist_path, "testpypi", token, verbose=args.verbose)
        _tmp2, python2 = _fresh_venv()
        try:
            _pip(python2, "--index-url", "https://test.pypi.org/simple/",
                 "--extra-index-url", "https://pypi.org/simple/",
                 "%s==%s" % (_NAME, version))
            _smoke(python2, version)
        finally:
            shutil.rmtree(_tmp2, ignore_errors=True)
    print("dry-run: green - TestPyPI serves an install that proves itself")


def cmd_publish(args):
    env_file = Path(args.env_file)
    tokens = _dotenv(env_file)
    token = _ensure_token(tokens, "PYPI_TOKEN", "PyPI token",
                           env_file)
    version = _declared_version()
    print("publish: version %s to real PyPI" % version)
    _clean_tree()
    if _pypi_version_exists(version):
        _die("version %s already exists on PyPI - bump first, re-uploads "
             "are rejected" % version)
    print("  version %s is free on PyPI" % version)
    with tempfile.TemporaryDirectory(prefix="pypi-dist-") as dist:
        dist_path = Path(dist)
        wheel = _build(dist_path)
        _tmp, python = _fresh_venv()
        try:
            _pip(python, str(wheel))
            _smoke(python, version)
        finally:
            shutil.rmtree(_tmp, ignore_errors=True)
        if not args.yes and not _ask("upload %s to PyPI" % version):
            _die("aborted - nothing uploaded")
        _upload(dist_path, "pypi", token, verbose=args.verbose)
    print("publish: done - verify the landing page renders, then tag v%s" % version)


def _menu():
    """No subcommand: explain the three and ask. A bare run that guessed
    would pick the wrong risk level, so guessing is the one thing it never
    does."""
    print("release helper - what should happen?")
    print("  1. test     build + fresh-venv install + smoke. No upload.")
    print("  2. dry-run  test, then TestPyPI upload + install. Needs a token.")
    print("  3. publish  test, then the real PyPI upload. Needs a token.")
    try:
        choice = input("  pick 1, 2, or 3: ").strip()
    except (EOFError, KeyboardInterrupt):
        _die("aborted - nothing uploaded")
    return {"1": "test", "2": "dry-run", "3": "publish"}.get(choice)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Test, dry-run, or publish the package. Run from python/."
    )
    parser.add_argument("--yes", action="store_true",
                        help="skip confirmation prompts (never safety checks)")
    parser.add_argument("--verbose", action="store_true",
                        help="twine --verbose: show the server response on failure")
    parser.add_argument("--env-file", default=str(_PYTHON / ".env"),
                        help="where tokens are read from (default: python/.env)")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("test", help="build + fresh-venv install + smoke, no upload")
    sub.add_parser("dry-run", help="test, then TestPyPI upload + install")
    sub.add_parser("publish", help="test, then the real upload")
    args = parser.parse_args(argv)
    command = args.command or _menu()
    if command is None:
        _die("pick 1, 2, or 3 - nothing uploaded")
    {"test": cmd_test, "dry-run": cmd_dry_run, "publish": cmd_publish}[command](args)


if __name__ == "__main__":
    main()
