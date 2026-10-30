from __future__ import annotations

import pytest
from helpers import assert_rate


class TestAssertRate:
    def test_passes_inside_the_bound(self) -> None:
        # 1 % of 10,000: sd 0.000995, so z = 4 allows 0.006 to 0.014 (60 to 140 hits)
        assert_rate(61, 10_000, 0.01)
        assert_rate(139, 10_000, 0.01)

    def test_fails_outside_the_bound_with_the_counts(self) -> None:
        with pytest.raises(AssertionError) as raised:
            assert_rate(141, 10_000, 0.01)

        message = str(raised.value)
        assert "141/10000" in message
        assert "0.0141" in message
        assert "0.0100" in message
        assert "0.0140" in message  # the upper bound

    def test_fails_below_the_bound(self) -> None:
        with pytest.raises(AssertionError, match="59/10000"):
            assert_rate(59, 10_000, 0.01)

    def test_a_wider_z_widens_the_bound(self) -> None:
        with pytest.raises(AssertionError):
            assert_rate(150, 10_000, 0.01)

        assert_rate(150, 10_000, 0.01, z=5.1)

    def test_refuses_a_sample_whose_bound_includes_zero(self) -> None:
        # 100 trials at 3 %: the z = 4 interval is -0.039 to 0.099, so a rate of 0 would pass it
        with pytest.raises(AssertionError, match="sample too small"):
            assert_rate(3, 100, 0.03)

    def test_accepts_the_smallest_sample_that_excludes_zero(self) -> None:
        # lower bound > 0 needs n > z^2 (1 - p) / p = 16 x 0.97 / 0.03 = 517.3
        assert_rate(16, 518, 0.03)
        with pytest.raises(AssertionError, match="sample too small"):
            assert_rate(16, 517, 0.03)

    def test_refuses_zero_trials(self) -> None:
        with pytest.raises(AssertionError, match="sample too small"):
            assert_rate(0, 0, 0.5)
