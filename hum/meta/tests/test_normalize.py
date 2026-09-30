"""Unit tests for hum.meta.normalize.

Parametrized over every input shape documented in
documentation/design/metadata-library-design.md, section 8/10 (plain
string, JSON-encoded string, semicolon-separated string, list, garbage) for
both fields.
"""

import pytest

from hum.meta.normalize import normalize_cve_product, normalize_fix_status


class TestNormalizeCveProduct:
    def test_plain_string(self) -> None:
        assert normalize_cve_product("bash") == "bash"

    def test_json_encoded_list_of_one_collapses_to_string(self) -> None:
        assert normalize_cve_product('["bash"]') == "bash"

    def test_json_encoded_list_of_many(self) -> None:
        assert normalize_cve_product('["bash", "coreutils"]') == ["bash", "coreutils"]

    def test_semicolon_separated_string(self) -> None:
        assert normalize_cve_product("bash;coreutils") == ["bash", "coreutils"]

    def test_semicolon_separated_with_whitespace(self) -> None:
        assert normalize_cve_product(" bash ; coreutils ") == ["bash", "coreutils"]

    def test_actual_list_of_one_collapses_to_string(self) -> None:
        assert normalize_cve_product(["bash"]) == "bash"

    def test_actual_list_of_many(self) -> None:
        assert normalize_cve_product(["bash", "coreutils"]) == ["bash", "coreutils"]

    def test_list_with_blank_and_non_string_entries_ignored(self) -> None:
        assert normalize_cve_product(["bash", "", "  ", 1, None, "coreutils"]) == [
            "bash",
            "coreutils",
        ]

    def test_empty_list_is_none(self) -> None:
        assert normalize_cve_product([]) is None

    def test_list_of_only_blanks_is_none(self) -> None:
        assert normalize_cve_product(["", "  ", 1, None]) is None

    def test_blank_string_is_none(self) -> None:
        assert normalize_cve_product("") is None
        assert normalize_cve_product("   ") is None

    @pytest.mark.parametrize("value", [None, 1, 1.0, {"a": "b"}, True])
    def test_unrecognized_types_are_none(self, value: object) -> None:
        assert normalize_cve_product(value) is None

    def test_json_encoded_non_list_is_treated_as_plain_string(self) -> None:
        # '"bash"' is valid JSON (a string), but not a list -- falls through
        # to being treated as the plain string it looks like.
        assert normalize_cve_product('"bash"') == '"bash"'

    def test_invalid_json_falls_through_to_plain_string(self) -> None:
        assert normalize_cve_product("[not valid json") == "[not valid json"


class TestNormalizeFixStatus:
    @pytest.mark.parametrize(("value", "expected"), [(1, 1), ("1", 1), (0, 0), ("0", 0)])
    def test_recognized_values(self, value: object, expected: int) -> None:
        assert normalize_fix_status(value) == expected

    @pytest.mark.parametrize("value", [None, 2, "2", "yes", 1.0, [], {}])
    def test_unrecognized_values_are_none(self, value: object) -> None:
        assert normalize_fix_status(value) is None

    @pytest.mark.parametrize("value", [True, False])
    def test_bool_is_not_treated_as_int(self, value: bool) -> None:
        # bool is a subclass of int in Python -- True == 1 and False == 0 --
        # but the design doc never mentions booleans as a recognized shape,
        # so they're deliberately excluded rather than silently accepted.
        assert normalize_fix_status(value) is None
