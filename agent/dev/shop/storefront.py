"""storefront: checkout.

Looks the product up in catalog; if catalog fails, the checkout fails.
"""

import json
import os
import random
import urllib.error
import urllib.request

from common import create_app, log
from fastapi import HTTPException
from prometheus_client import Counter

CATALOG_URL = os.environ.get("CATALOG_URL", "http://catalog:8000")
CATALOG_TIMEOUT_S = float(os.environ.get("CATALOG_TIMEOUT_S", "2"))

# The health bar counts failed checkouts (world.yaml: metrics.failed_checkouts_total).
CHECKOUTS = Counter("shop_checkouts_total", "Checkouts by result", ["result"])
CHECKOUTS.labels("ok")
CHECKOUTS.labels("failed")

app = create_app()


@app.post("/checkout")
def checkout() -> dict:
    product_id = random.randint(1, 30)
    try:
        with urllib.request.urlopen(
            f"{CATALOG_URL}/products/{product_id}", timeout=CATALOG_TIMEOUT_S
        ) as response:
            product = json.load(response)
    except (urllib.error.URLError, TimeoutError) as error:
        CHECKOUTS.labels("failed").inc()
        log(
            "error",
            "checkout failed: catalog error",
            product_id=product_id,
            error=str(error),
        )
        raise HTTPException(
            status_code=502, detail="payment failed, please try again"
        ) from error
    CHECKOUTS.labels("ok").inc()
    return {
        "status": "ok",
        "product": product["name"],
        "price_cents": product["price_cents"],
    }
