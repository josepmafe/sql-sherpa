from __future__ import annotations

from sherpa_agent.variants.base import OUTPUT_MODE, Variant, VariantContext
from sherpa_agent.variants.v0_baseline import V0Baseline

__all__ = ["OUTPUT_MODE", "VARIANTS", "V0Baseline", "Variant", "VariantContext"]

VARIANTS: dict[str, Variant] = {"v0_baseline": V0Baseline()}
