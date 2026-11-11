"""catalog: product lookups.

Feature flag SEARCH_V2 (config/catalog.env) turns on a "new search path"
that fails for every third product. This is the fault of the fictional
example lab in docs/contracts/examples.
"""

import os

from common import create_app, log
from fastapi import HTTPException

SEARCH_V2 = os.environ.get("SEARCH_V2", "false").lower() == "true"
PRODUCTS = {
    i: {"id": i, "name": f"Tea #{i}", "price_cents": 450 + i * 10} for i in range(1, 31)
}

app = create_app()
log("info", "catalog started", search_v2=SEARCH_V2)


@app.get("/products/{product_id}")
def product(product_id: int) -> dict:
    if product_id not in PRODUCTS:
        raise HTTPException(status_code=404, detail="no such product")
    if SEARCH_V2 and product_id % 3 == 0:
        log("error", "search v2: index has no entry", product_id=product_id)
        raise HTTPException(status_code=500, detail="search index error")
    return PRODUCTS[product_id]
