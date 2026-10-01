"""Fold the measured library-layer probes into parity/errors.json.

One-off generator. Kept out of the test suite: the contract is the artefact, and
regenerating it from a probe would let a divergence be written *into* the
contract instead of caught by it. Run it only when deliberately changing a
message, then read the diff.

Not every site in `sites` comes from the probe. `insert: bad parent` needs a
real temporary file, which the probe did not create, so its row is written here
from a direct measurement instead.
"""
import json
import os

P = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "parity", "errors.json")

PINNED = [
    "breakpoints: line 0",
    "grep: query empty",
    "grep: max_results 0",
    "grep: max_results 'x'",
    "grep: context_lines 11",
    "grep: bad regex",
    "write: bad className",
    "write: non-game path",
    "update: non-game path",
    "update: no match",
    "update: ambiguous",
    "update: noop",
    "update: empty old_string",
    "update: bad edit shape",
    "insert: missing file",
    "capture: rgba length mismatch",
    "capture: short header",
    "capture: studio bad allocation",
]

# Measured directly rather than through the probe, which hit the missing-file
# check first because it passed a path that did not exist.
MANUAL = {
    "insert: bad parent": {
        "site": "insert: bad parent",
        "code": "INVALID_ARGUMENT",
        "message": 'parent_path must be a game-tree path starting with "game."; got "/tmp".',
    }
}

# The two argument faults that used to live here are now driven through real code
# in `sites`, where the code comes from the raise site rather than from
# `classify()` on a hand-written string. The two that remain are genuine bare
# raises, so the classifier is the right lens for them.
DROPPED = ("update_script strict mode", "insert_asset_from_file, missing local file")


def load(path):
    out = {}
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if line.startswith("{"):
            row = json.loads(line)
            out[row["site"]] = row
    return out


def main():
    python_side = load(os.path.join(os.environ["TEMP"], "py_sites.json"))
    with open(P, encoding="utf-8") as handle:
        contract = json.load(handle)

    sites = []
    for name in PINNED:
        row = python_side[name]
        entry = {"site": name, "code": row["code"], "message": row["message"]}
        if name == "grep: bad regex":
            entry["code_only"] = True
            entry["why"] = (
                "The tail is the regex engine's own wording - Python re and JS "
                "RegExp differ - passed through verbatim rather than paraphrasing "
                "an error we did not write. The code is ours and is pinned."
            )
        elif name.startswith("capture:"):
            entry["why"] = (
                "Left on UNKNOWN/SIZE_LIMIT on purpose. These check our own "
                "integrity against what Studio sent, not the caller's argument. "
                "INVALID_ARGUMENT would send the caller off to edit a request that "
                "was fine. See deliberately_not_argument_faults."
            )
        sites.append(entry)
    sites.extend(MANUAL[name] for name in sorted(MANUAL))

    contract["sites"] = sites
    contract["deliberately_not_argument_faults"] = [
        c
        for c in contract["deliberately_not_argument_faults"]
        if not any(c["site"].startswith(drop) for drop in DROPPED)
    ]
    with open(P, "w", encoding="utf-8") as handle:
        json.dump(contract, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
    print("sites:", len(sites), "kept:", len(contract["deliberately_not_argument_faults"]))


if __name__ == "__main__":
    main()
