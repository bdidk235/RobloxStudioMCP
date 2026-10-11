"""The pinned upstream tool surface: snapshot, refresh and drift grading.

Why this module exists
----------------------
`contract/tools.json` pins the ``extended_*`` tools this package owns. The
**upstream** tools - the ones the Roblox Studio MCP proxy relays - were pinned
nowhere: a live ``tools/list`` returns them, `docs/catalog.js` hand-carries
them for the docs site, and nothing asserts either against anything. A Studio
update that renames or drops one surfaces as a runtime failure in a user's
session, never as CI red.

So the upstream surface now lives in `contract/upstream_tools.json`, and this
module holds the *one* implementation of the two things both the snapshot and
every consumer need: normalising a ``tools/list`` payload into the snapshot
shape, and grading a live list against the snapshot. The doctor, the refresh
script and the tests all call these functions, so they cannot disagree about
what "drift" means.

Two trust labels, deliberately not one
--------------------------------------
The two surfaces carry different stability guarantees and are graded apart:

``extended``
    Owned by this repository. Every change is deliberate, so any drift is a
    defect: `contract/tools.json` is generated from the code and asserted
    byte-for-byte (`test_contract.py`).

``proxied_upstream``
    Owned by Roblox Studio. It can change at any Studio update, including
    additively, and a fresh proxy can also fail to *enumerate* - a flaky
    uplink reports an empty or partial list. Hence grading rather than
    equality: see :func:`compare_snapshot` for which differences fail and
    which only warn, and why.

What the snapshot records, and what it deliberately does not
------------------------------------------------------------
Per tool: the name, description **length**, required parameters, and each
property's JSON type (plus ``items_type``/``enum``/``has_default`` markers).
That mirrors `contract/build_contract.py::_normalise_schema` so the two
snapshots read the same way.

Description *wording* is not compared: it is Studio's prose and churns on doc
updates with no behavioural change - a comparison over wording would redden
on a typo fix and teach people to ignore the check. The length is recorded
because the tool list is paid on every call of every session.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Sequence, Tuple

#: The trust label of the tools the Studio MCP proxy relays. Its opposite is
#: ``extended`` (this repository's own tools, pinned by `contract/tools.json`).
TRUST_UPSTREAM = "proxied_upstream"

#: How many snapshot tools may be absent from a live list before the absence
#: is read as "the proxy could not enumerate" rather than "Studio dropped
#: them". Measured against the real failure mode: a rename or drop removes
#: *one* tool, while an unhealthy uplink returns nothing or nearly nothing.
#: A single disappearance fails the check; a mass disappearance warns, so a
#: flaky Studio state cannot redden CI on a signal nobody can act on.
TRANSIENT_MISSING_LIMIT = 3

FAIL = "FAIL"
WARNING = "WARNING"


def normalise_tool(tool: Mapping[str, Any]) -> Dict[str, Any]:
    """Reduce one ``tools/list`` entry to the snapshot shape.

    Accepts a raw wire dict (``{"name", "description", "inputSchema",
    "annotations"}``) or the output of :meth:`roblox_studio_mcp.types.Tool.to_dict`,
    which is the same shape with ``annotations`` present only when the tool is
    marked read-only - so "absent" and "false" normalise identically.
    """
    schema = tool.get("inputSchema") or tool.get("input_schema") or {}
    properties: Dict[str, Any] = {}
    for name, spec in (schema.get("properties") or {}).items():
        if not isinstance(spec, Mapping):
            # A malformed entry is kept as a type-only entry rather than
            # dropped: a property whose shape cannot be read is itself drift,
            # and silently omitting it would compare as "unchanged".
            properties[str(name)] = {"type": None}
            continue
        entry: Dict[str, Any] = {"type": spec.get("type")}
        items = spec.get("items")
        if isinstance(items, Mapping):
            entry["items_type"] = items.get("type")
        if "default" in spec:
            entry["has_default"] = True
        if spec.get("enum"):
            entry["enum"] = list(spec["enum"])
        properties[str(name)] = entry
    out: Dict[str, Any] = {
        "name": str(tool.get("name", "")),
        "description_chars": len(str(tool.get("description") or "")),
        "required": sorted(str(r) for r in (schema.get("required") or [])),
        "properties": properties,
    }
    annotations = tool.get("annotations") or {}
    if annotations.get("readOnlyHint"):
        out["read_only"] = True
    return out


def build_snapshot(dump: Mapping[str, Any]) -> Dict[str, Any]:
    """Build the snapshot document from a captured ``tools/list`` dump.

    A *dump* is the raw capture written by `contract/build_upstream.py --live`:
    ``{"captured", "source", "server_info", "tools"}``. The function is pure, so
    the suite can pin it the same way `test_contract.py` pins
    ``build_contract.build()``: regenerate from the checked-in dump and compare
    against the committed snapshot. A hand-edited snapshot then fails a test,
    and so does an edit to this function that the snapshot was not regenerated
    for.
    """
    tools = [normalise_tool(t) for t in (dump.get("tools") or [])]
    tools.sort(key=lambda t: t["name"])
    server_info = dump.get("server_info") or {}
    proxy: Dict[str, Any] = {}
    if server_info.get("name"):
        proxy["name"] = str(server_info["name"])
    if server_info.get("version"):
        proxy["version"] = str(server_info["version"])
    snapshot: Dict[str, Any] = {
        "_comment": (
            "The upstream (relayed) tool surface, captured from a live "
            "tools/list through the StudioMCP proxy. Trust label: "
            "proxied_upstream - owned by Roblox Studio, not this repo, so it "
            "is graded, not asserted byte-for-byte. Refresh after a Studio "
            "update with `python contract/build_upstream.py --live` (writes "
            "this file and contract/upstream_live_dump.json together), and "
            "check any time with `python -m roblox_studio_mcp.doctor "
            "--upstream`. Generated file; do not hand-edit."
        ),
        "trust": TRUST_UPSTREAM,
        "captured": str(dump.get("captured", "")),
        "source": str(dump.get("source", "")),
        "tool_count": len(tools),
        "tools": tools,
    }
    if proxy:
        snapshot["proxy"] = proxy
    return snapshot


def classify_names(
    names: Iterable[str], extended_names: Iterable[str]
) -> Dict[str, List[str]]:
    """Split tool names into the two trust labels, sorted.

    Used by the doctor to keep the surfaces visibly separate in its output,
    which is the point of the exercise: one number covering both would be the
    shared trust label this module exists to avoid.
    """
    extended = set(extended_names)
    out: Dict[str, List[str]] = {"extended": [], "upstream": []}
    for name in names:
        bucket = "extended" if name in extended else "upstream"
        out[bucket].append(str(name))
    out["extended"].sort()
    out["upstream"].sort()
    return out


# --------------------------------------------------------------------------- #
# Drift grading
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class Finding:
    """One graded difference between the snapshot and a live list."""

    level: str  # FAIL or WARNING
    kind: str   # missing | new | property-removed | required-changed | ...
    name: str
    detail: str

    def render(self) -> str:
        return "%s [%s] %s: %s" % (self.level, self.kind, self.name, self.detail)


@dataclass
class UpstreamReport:
    """The verdict of one comparison, plus the reasoning behind each line."""

    findings: List[Finding] = field(default_factory=list)
    checked: int = 0
    live_count: int = 0

    @property
    def failures(self) -> List[Finding]:
        return [f for f in self.findings if f.level == FAIL]

    @property
    def warnings(self) -> List[Finding]:
        return [f for f in self.findings if f.level == WARNING]

    @property
    def ok(self) -> bool:
        return not self.findings

    @property
    def exit_code(self) -> int:
        return 1 if self.failures else 0

    def verdict(self) -> str:
        if self.ok:
            return "no drift"
        parts = []
        if self.failures:
            parts.append("%d failure(s)" % len(self.failures))
        if self.warnings:
            parts.append("%d warning(s)" % len(self.warnings))
        return ", ".join(parts)

    def render(self) -> List[str]:
        return [f.render() for f in self.findings]


def compare_snapshot(
    snapshot: Mapping[str, Any], live_tools: Sequence[Mapping[str, Any]]
) -> UpstreamReport:
    """Grade a live ``tools/list`` against the pinned snapshot.

    The policy, and why each way
    ----------------------------
    * **missing** (in the snapshot, absent from the live list) is a **failure**.
      That is the silent risk this whole mechanism exists for: a renamed or
      dropped upstream tool breaks every caller that invokes it, at runtime,
      in someone's session, with nothing upstream of the call reporting it.
      *Unless* the list looks unenumerated - ``TRANSIENT_MISSING_LIMIT`` or
      more tools gone at once, or nothing live at all. That pattern is a proxy
      whose Studio uplink never warmed, not a Studio that dropped tools, and
      failing on it would redden CI for a state the next run fixes. Those
      absences are warnings, and the report says which pattern it saw.
    * **new** (live, absent from the snapshot) is a **warning**. Roblox may add
      tools at any update; nothing in this repo breaks. It is a prompt to
      refresh the snapshot, not a defect.
    * **a removed property, or a changed required set** is a **failure**. A
      caller built against the snapshot passes a parameter that no longer
      exists (silently ignored or refused, depending on the side), or omits one
      that is now required. This is the same silent-failure class as a missing
      tool, so it grades the same way.
    * **an added property** is a **warning**: additive, and the snapshot is
      meant to lag exactly this.
    * **description length** changed is a **warning**: the list is paid every
      session, and a large jump is worth noticing, but wording churn is not a
      break.
    * **read_only** flipped is a **warning**: it is Studio's parallel-execution
      hint. Failing on it would redden on annotation churn this repo does not
      control; the warning keeps it visible.

    Everything the grader does not know how to compare, it ignores: the point
    is a stable caller-visible surface, not matching Studio's internals.
    """
    pinned = {str(t["name"]): t for t in (snapshot.get("tools") or [])}
    live = {str(normalise_tool(t)["name"]): normalise_tool(t) for t in live_tools}
    report = UpstreamReport(checked=len(pinned), live_count=len(live))

    missing = sorted(set(pinned) - set(live))
    looks_unenumerated = len(missing) >= TRANSIENT_MISSING_LIMIT or not live
    for name in missing:
        if looks_unenumerated:
            report.findings.append(
                Finding(
                    WARNING,
                    "missing",
                    name,
                    "absent from the live list, but %d tool(s) are gone at "
                    "once - that pattern is a proxy that failed to enumerate, "
                    "not a drop. Re-run once Studio is settled; if a single "
                    "tool is still absent next time it becomes a failure."
                    % len(missing),
                )
            )
        else:
            report.findings.append(
                Finding(
                    FAIL,
                    "missing",
                    name,
                    "pinned in the snapshot but absent from the live "
                    "tools/list. A rename or drop upstream breaks every "
                    "caller of this tool at runtime. Refresh the snapshot "
                    "(python contract/build_upstream.py --live) if the "
                    "change is real; investigate the Studio update if it is "
                    "not.",
                )
            )

    for name in sorted(set(live) - set(pinned)):
        report.findings.append(
            Finding(
                WARNING,
                "new",
                name,
                "live but not pinned - upstream added a tool. Nothing here "
                "breaks; refresh the snapshot to record it.",
            )
        )

    for name in sorted(set(pinned) & set(live)):
        want, got = pinned[name], live[name]
        want_props = want.get("properties") or {}
        got_props = got.get("properties") or {}
        for prop in sorted(set(want_props) - set(got_props)):
            report.findings.append(
                Finding(
                    FAIL,
                    "property-removed",
                    name,
                    "parameter %r is pinned but no longer advertised; callers "
                    "built against the snapshot pass it. Refresh the snapshot "
                    "if the change is real." % prop,
                )
            )
        for prop in sorted(set(got_props) - set(want_props)):
            report.findings.append(
                Finding(
                    WARNING,
                    "property-added",
                    name,
                    "new optional parameter %r advertised upstream; additive, "
                    "so only the snapshot is stale." % prop,
                )
            )
        for prop in sorted(set(want_props) & set(got_props)):
            want_entry = want_props[prop]
            got_entry = got_props[prop]
            if want_entry != got_entry:
                report.findings.append(
                    Finding(
                        FAIL,
                        "property-changed",
                        name,
                        "parameter %r changed shape: pinned %r, live %r. A "
                        "type or enum change breaks callers that pass the old "
                        "value." % (prop, want_entry, got_entry),
                    )
                )
        if list(want.get("required") or []) != list(got.get("required") or []):
            report.findings.append(
                Finding(
                    FAIL,
                    "required-changed",
                    name,
                    "required set changed: pinned %r, live %r."
                    % (list(want.get("required") or []), list(got.get("required") or [])),
                )
            )
        if want.get("description_chars") != got.get("description_chars"):
            report.findings.append(
                Finding(
                    WARNING,
                    "description-length",
                    name,
                    "description length changed: pinned %s chars, live %s. "
                    "Wording is Studio's and churns; the list is paid every "
                    "session, so a large jump is worth a look."
                    % (want.get("description_chars"), got.get("description_chars")),
                )
            )
        if bool(want.get("read_only", False)) != bool(got.get("read_only", False)):
            report.findings.append(
                Finding(
                    WARNING,
                    "read-only-flipped",
                    name,
                    "the parallel-execution hint changed: pinned read_only=%s, "
                    "live read_only=%s. Studio's annotation, not ours."
                    % (
                        bool(want.get("read_only", False)),
                        bool(got.get("read_only", False)),
                    ),
                )
            )
    return report


def load_snapshot(path: str) -> Dict[str, Any]:
    """Read a snapshot document from disk (thin wrapper, for the doctor)."""
    import json

    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def snapshot_names(snapshot: Mapping[str, Any]) -> List[str]:
    """The tool names a snapshot pins, sorted."""
    return sorted(str(t["name"]) for t in (snapshot.get("tools") or []))
