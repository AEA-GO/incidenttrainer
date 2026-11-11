"""Reading real Prometheus API answers (shapes copied from Prometheus 3.14)."""

from itr_agent.prometheus import PrometheusClient, fill, parse_alerts, parse_scalar

ALERTS = {
    "status": "success",
    "data": {
        "alerts": [
            {
                "labels": {"alertname": "CheckoutErrorRateHigh", "severity": "page"},
                "annotations": {"summary": "Checkout error rate 31%"},
                "state": "firing",
                "activeAt": "2026-11-11T18:47:20Z",
                "value": "3.1234e+01",
            }
        ]
    },
}


def test_parse_alerts():
    (alert,) = parse_alerts(ALERTS)
    assert alert.name == "CheckoutErrorRateHigh"
    assert alert.state == "firing"
    assert round(alert.value, 2) == 31.23
    assert alert.summary == "Checkout error rate 31%"


def test_parse_scalar():
    payload = {"data": {"result": [{"metric": {}, "value": [1762886840.1, "88"]}]}}
    assert parse_scalar(payload) == 88.0
    assert parse_scalar({"data": {"result": []}}) is None


def test_fill_template():
    template = 'rate(x{service="$service",route=~"$route"}[$window])'
    assert fill(template, "storefront", "/checkout", 30) == (
        'rate(x{service="storefront",route=~"/checkout"}[30s])'
    )
    assert fill(template, "catalog", None, 60) == (
        'rate(x{service="catalog",route=~".*"}[60s])'
    )


def test_unreachable_prometheus_means_no_data():
    client = PrometheusClient("http://127.0.0.1:9", timeout_s=0.5)
    assert client.alerts() == []
    assert client.scalar("up") is None
