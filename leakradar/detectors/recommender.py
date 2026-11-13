"""Maps a leak to a product. If the merchant already has the fitting product, recommend a
configuration review instead of cross-selling it again."""
from __future__ import annotations

import json

from leakradar.common.models import Leak


def recommend(leak: Leak, products_active: list[str] | str, catalogue: dict) -> Leak:
    if isinstance(products_active, str):
        products_active = json.loads(products_active)
    fit = catalogue["detector_product"][leak.detector]
    leak.fit_product = fit
    if fit in products_active:
        leak.recommended_product = "config_review"
        leak.recommendation_type = "config_review"
    else:
        leak.recommended_product = fit
        leak.recommendation_type = "cross_sell"
    return leak
