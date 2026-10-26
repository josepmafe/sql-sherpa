from __future__ import annotations

from sherpa_mcp.masking import MASK, PiiMasker

PII = frozenset({"email", "first_name"})


class TestPiiMasker:
    def test_masks_pii_by_name_case_insensitive(self) -> None:
        masker = PiiMasker(PII)

        rows = masker.mask(["Email", "FIRST_NAME", "order_id"], [["a@b.com", "Ann", 7]])

        assert rows == [[MASK, MASK, 7]]

    def test_keeps_nulls_and_other_columns(self) -> None:
        masker = PiiMasker(PII)

        rows = masker.mask(["email", "country_code"], [[None, "ES"], ["a@b.com", None]])

        assert rows == [[None, "ES"], [MASK, None]]

    def test_masks_names_the_columns_it_masks_case_insensitive(self) -> None:
        masker = PiiMasker(PII)

        assert [masker.masks(name) for name in ("email", "EMAIL", "e", "order_id")] == [True, True, False, False]

    def test_empty_result(self) -> None:
        masker = PiiMasker(PII)

        assert masker.mask(["email"], []) == []
        assert masker.mask([], []) == []
