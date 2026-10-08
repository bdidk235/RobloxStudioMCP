"""A Roblox template autosave names its place id, and that id pairs with 0.

A local file has no place id, so the only way to open a template *by id* was to
hardcode one. But the template's own filename carries it - measured on this
machine, the discovered baseplate is
``Template_95206881_AutoRecovery_4_20260930_135756.rbxl``, so the template is
place 95206881.

That matters because ``universeId 0`` is required for a URI launch to fetch. So
"the template's place id, with universe 0" is a complete launch, and
``list_place_candidates`` reports the id so nobody has to parse a filename by
hand.
"""

import asyncio
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "src"))

from roblox_studio_mcp.extended import instance as inst_mod  # noqa: E402
from roblox_studio_mcp.extended.instance import (  # noqa: E402
    URI_UNIVERSE_ID, build_launch_uri, template_place_id,
)

AUTOSAVES = os.path.join("Roblox", "RobloxStudio", "AutoSaves")
ARCHIVED = os.path.join(AUTOSAVES, "Archived")

CASES = [
    (os.path.join(AUTOSAVES, "Template_95206881_AutoRecovery_3.rbxl"), 95206881),
    (os.path.join(ARCHIVED, "Template_95206881_AutoRecovery_4_20260930_135756.rbxl"), 95206881),
    (os.path.join(ARCHIVED, "Template_6560363541_AutoRecovery_0.rbxl"), 6560363541),
    # A baseplate copy made by this project, not a Roblox template.
    (os.path.join(os.path.dirname(AUTOSAVES), "Baseplate-555883251.rbxl"), None),
    ("Baseplate-1.rbxl", None),
    ("Template_notanumber_AutoRecovery_1.rbxl", None),
    ("", None),
]


class TemplatePlaceId(unittest.TestCase):
    def test_reads_the_id_from_the_measured_names(self):
        for path, expected in CASES:
            self.assertEqual(template_place_id(path), expected, path)

    def test_a_temp_copy_has_no_place_id(self):
        """The copies this project makes are named ``Baseplate-<n>.rbxl``, so
        there is no id to report and the caller must not be given a fake one."""
        self.assertIsNone(
            template_place_id(r"C:\Users\User\AppData\Local\Temp\robloxstudio-mcp-"
                              r"baseplates\Baseplate-555883251.rbxl")
        )

    def test_id_pairs_with_zero_to_make_a_complete_uri(self):
        """The two facts together: the template's id from its filename, and
        universeId 0 as the explicit 'no universe context' value.

        ``universeId 0`` used to be the *default*, on the claim that the real id
        intermittently failed. It did not reproduce, so the default is now
        "derive it from the place" and 0 is a value you pass on purpose."""
        path = os.path.join(ARCHIVED, "Template_95206881_AutoRecovery_4_20260930_135756.rbxl")
        self.assertEqual(
            asyncio.run(build_launch_uri(template_place_id(path), URI_UNIVERSE_ID)),
            "roblox-studio:1+task:EditPlace+placeId:95206881+universeId:0",
        )

    def test_every_candidate_row_carries_an_id_or_none(self):
        """A row must not invent one. A file the caller made has none, and the
        honest value there is ``None`` so the caller knows not to launch it by id.

        Driven against a seeded autosave dir, not the machine's own: on a host
        without Roblox autosaves the real call returns ``[]`` and the loop below
        checks nothing - a green test guarding no rows. The seeded dir covers
        both arms: numeric ids come through as ints, and a non-numeric
        Template name honestly reports ``None``."""
        from roblox_studio_mcp.extended.instance import list_place_candidates

        with tempfile.TemporaryDirectory() as folder:
            for name in (
                "Template_95206881_AutoRecovery_3.rbxl",
                "Template_6560363541_AutoRecovery_0.rbxl",
                "Template_notanumber_AutoRecovery_1.rbxl",
            ):
                with open(os.path.join(folder, name), "wb") as handle:
                    handle.write(b"stub")
            with mock.patch.object(inst_mod, "_autosave_dirs", return_value=[folder]):
                rows = list_place_candidates()
        by_name = {os.path.basename(row["path"]): row for row in rows}
        self.assertEqual(
            set(by_name),
            {
                "Template_95206881_AutoRecovery_3.rbxl",
                "Template_6560363541_AutoRecovery_0.rbxl",
                "Template_notanumber_AutoRecovery_1.rbxl",
            },
        )
        for name, row in by_name.items():
            with self.subTest(name=name):
                self.assertIn("place_id", row, row["path"])
                self.assertTrue(
                    row["place_id"] is None or isinstance(row["place_id"], int),
                    row,
                )
        self.assertEqual(
            by_name["Template_95206881_AutoRecovery_3.rbxl"]["place_id"], 95206881
        )
        self.assertIsNone(
            by_name["Template_notanumber_AutoRecovery_1.rbxl"]["place_id"]
        )


if __name__ == "__main__":
    unittest.main()
