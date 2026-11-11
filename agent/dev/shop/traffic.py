"""Fake customers: a fixed 5 checkouts per second (open loop), forever."""

import os
import threading
import time
import urllib.error
import urllib.request

URL = os.environ.get("TARGET_URL", "http://storefront:8000/checkout")
RATE = float(os.environ.get("RATE_PER_S", "5"))


def one_checkout() -> None:
    request = urllib.request.Request(URL, method="POST", data=b"")
    try:
        urllib.request.urlopen(request, timeout=5).close()
    except (urllib.error.URLError, TimeoutError):
        pass  # failures are the shop's problem; the metrics show them


def main() -> None:
    interval = 1 / RATE
    next_at = time.monotonic()
    while True:
        # Open loop: start a request every interval, even if earlier ones are slow.
        threading.Thread(target=one_checkout, daemon=True).start()
        next_at += interval
        time.sleep(max(0.0, next_at - time.monotonic()))


if __name__ == "__main__":
    main()
