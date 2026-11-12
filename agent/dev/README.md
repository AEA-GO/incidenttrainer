# Stand-in shop (dev-shop)

A tiny shop for building and testing the agent on a laptop, until the real `teapot-basic` world is ready. Not used on game servers.

| Container | What | Port (laptop only) |
| --- | --- | --- |
| storefront | `POST /checkout`: looks the product up in catalog; fails if catalog fails | 127.0.0.1:8001 |
| catalog | `GET /products/{id}`; flag `SEARCH_V2=true` makes every 3rd product fail | 127.0.0.1:8002 |
| traffic | fake customers, 5 checkouts per second | none |
| prometheus | scrapes both every 5 s; alert rules `CheckoutErrorRateHigh`, `ServiceDown` | 127.0.0.1:9090 |

The fault is the fictional example lab's (`docs/contracts/examples/example-lab.yaml`). `world.yaml` describes this shop for the agent (contract 1).

## Run

```bash
cd agent/dev
docker compose up -d --build
docker compose ps                     # all "running"; storefront and catalog "healthy"
curl -s -X POST localhost:8001/checkout
```
Prometheus: open http://localhost:9090 (Status → Targets: both UP; Alerts: both green).

## Break it and fix it (what the agent will do with verbs)

```bash
sed -i 's/^SEARCH_V2=.*/SEARCH_V2=true/' config/catalog.env && docker compose up -d catalog
```
About a third of checkouts now fail. Within ~30 s the alert `CheckoutErrorRateHigh` turns *pending*, then *firing* ("Checkout error rate 31%").
Useful queries in Prometheus: `sum(shop_checkouts_total{result="failed"})`, and the `error_rate` template from `world.yaml` with `$service=storefront`, `$route=/checkout`, `$window=30s`.

```bash
sed -i 's/^SEARCH_V2=.*/SEARCH_V2=false/' config/catalog.env && docker compose up -d catalog
```
Errors stop; the alert clears after about a minute (the rule averages over 1 minute).

Logs: `docker compose logs -f catalog storefront` (JSON lines).

## Play a game

`play.py` plays a whole game against the agent; see "Play a game on your laptop" in `../README.md`.

## Stop

```bash
docker compose down          # keep nothing; the shop has no data to lose
```
