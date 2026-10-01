"""Shared contract between the Python and Node implementations.

See `build_contract.py` for why this is a generated file rather than a
hand-written list, and `python/tests/test_parity.py` and
`node/tests/parity.test.ts` for the two suites that enforce it.

An explicit `__init__.py` because pytest does not resolve the namespace package
reliably here, and a parity check that fails to import is worse than none -
it looks like a missing dependency rather than a broken contract.
"""

from .build_contract import PER_TOOL_CAP, TOTAL_CAP, build, _normalise_schema  # noqa: F401

__all__ = ["build", "PER_TOOL_CAP", "TOTAL_CAP", "_normalise_schema"]
