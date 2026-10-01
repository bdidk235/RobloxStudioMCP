"""Tests for the launch URI shape and the command-line parsers.

The URI is the smallest thing in this project that can silently do the wrong
thing, because a failed URI launch still produces a process and still attaches
to the mesh. These tests pin three measured things: the key count, that both ids
are **required** arguments, and that ``universeId 0`` is the value that fetches
while the place's real universe id intermittently does not.
"""

import unittest

from roblox_studio_mcp.extended.instance import (
    ROLE_CLIENT, ROLE_EDIT, ROLE_SERVER, ROLE_UNKNOWN, URI_UNIVERSE_ID,
    build_launch_uri, place_from_command_line, role_from_command_line,
)


class TestLaunchUri(unittest.TestCase):
    def test_exact_minimal_shape(self):
        # The form measured working: four keys, no more.
        self.assertEqual(
            build_launch_uri(95206881, URI_UNIVERSE_ID),
            "roblox-studio:1+task:EditPlace+placeId:95206881+universeId:0",
        )

    def test_the_old_working_shape_is_still_accepted_by_the_builder(self):
        # A real universe id is still buildable. It is just not what to pass:
        # 3 of 16 such launches failed with "Error fetching latest place version".
        self.assertEqual(
            build_launch_uri(95206881, 28220420),
            "roblox-studio:1+task:EditPlace+placeId:95206881+universeId:28220420",
        )

    def test_no_optional_keys(self):
        # Every one of these appeared in a real Studio URI but is not required,
        # and adding them back produced an Error dialog that never attached.
        uri = build_launch_uri(1, 2)
        for absent in ("launchtime", "avatar", "browsertrackerid", "robloxLocale",
                       "gameLocale", "channel", "browser", "distributorType",
                       "baseUrl", "launchmode"):
            self.assertNotIn(absent, uri, f"{absent} must not be in the URI")

    def test_prefix_is_not_doubled(self):
        # Some samples show 'roblox-studio:roblox-studio:1+...' and that also
        # works, but it is an artifact of the protocol handler. Single is canonical.
        uri = build_launch_uri(1, 2)
        self.assertTrue(uri.startswith("roblox-studio:1+"))
        self.assertNotIn("roblox-studio:roblox-studio:", uri)

    def test_universe_id_is_a_required_argument(self):
        # 0 is the correct value, but it is still required rather than defaulted:
        # a defaulted one is a default in disguise, and a caller who means a real
        # universe would never notice they were not asked.
        with self.assertRaises(TypeError):
            build_launch_uri(95206881)

    def test_zero_is_the_value_that_fetches(self):
        # File > New opens a place as `-placeId N -universeId 0`, and launches
        # carrying the place's real universe id failed 3 times in 16 with "Error
        # fetching latest place version".
        self.assertEqual(URI_UNIVERSE_ID, 0)
        self.assertIn("universeId:0", build_launch_uri(95206881, URI_UNIVERSE_ID))

    def test_both_keys_are_always_present(self):
        # The key is required even though its value is 0. Dropping it left a
        # Studio that attached and opened no place, which is invisible unless you
        # actually open it.
        uri = build_launch_uri(95206881, URI_UNIVERSE_ID)
        self.assertIn("universeId", uri)
        self.assertIn("placeId", uri)

    def test_place_id_is_required(self):
        with self.assertRaises(TypeError):
            build_launch_uri()
        with self.assertRaises(TypeError):
            build_launch_uri(95206881)

    def test_ids_are_coerced_to_ints(self):
        uri = build_launch_uri("95206881", "28220420")
        self.assertIn("placeId:95206881", uri)
        self.assertIn("universeId:28220420", uri)


class TestRoleParsing(unittest.TestCase):
    CASES = [
        ('"C:\\x\\RobloxStudioBeta.exe" --task EditFile --localPlaceFile C:\\a.rbxl', ROLE_EDIT),
        ('"C:\\x.exe" -task StartServer -localProjectFile C:/a.rbxl', ROLE_SERVER),
        ('"C:\\x.exe" -task StartClient -localProjectFile C:/a.rbxl', ROLE_CLIENT),
        ('"C:\\x.exe" roblox-studio:1+task:EditPlace+placeId:1+universeId:2', ROLE_EDIT),
        ('"C:\\x.exe" roblox-studio:1+task:StartServer+placeId:1+universeId:2', ROLE_SERVER),
        ('"C:\\x.exe" roblox-studio:1+task:StartClient+placeId:1+universeId:2', ROLE_CLIENT),
        ('"C:\\x.exe"', ROLE_UNKNOWN),
        ('', ROLE_UNKNOWN),
    ]

    def test_roles(self):
        for cmd, expected in self.CASES:
            with self.subTest(cmd=cmd[:60]):
                self.assertEqual(role_from_command_line(cmd), expected)

    def test_uri_launches_are_not_unknown(self):
        # A -task-only parse called every URI-launched Studio "unknown", which
        # is what hid the fact that a server and its clients were all identical.
        self.assertNotEqual(
            role_from_command_line('"x.exe" roblox-studio:1+task:EditPlace'), ROLE_UNKNOWN
        )


class TestPlaceParsing(unittest.TestCase):
    def test_file_launch_yields_a_basename(self):
        self.assertEqual(
            place_from_command_line('"x.exe" --task EditFile --localPlaceFile C:\\tmp\\Baseplate-1.rbxl'),
            "Baseplate-1.rbxl",
        )

    def test_project_file_form_also_works(self):
        self.assertEqual(
            place_from_command_line('"x.exe" -task StartServer -localProjectFile C:/tmp/B.rbxl'),
            "B.rbxl",
        )

    def test_uri_launch_has_no_derivable_place_name(self):
        # A URI carries only an id. Studio names the place itself, and while it
        # is still opening, the mesh reports name: null too.
        self.assertIsNone(
            place_from_command_line('"x.exe" roblox-studio:1+task:EditPlace+placeId:1+universeId:2')
        )


if __name__ == "__main__":
    unittest.main()
