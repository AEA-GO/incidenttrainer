"""The check verbs, one by one (step 6)."""

import dataclasses
from datetime import UTC, datetime

from itr_agent.checks import Checker, parse_compose_ps, parse_started_at, show


def check(
    verb: str, params: dict, message: str = "value {value}, limit {threshold}"
) -> dict:
    return {"id": "c1", "verb": verb, "params": params, "message": message}


def checker(world, runner, prometheus, http, now=None) -> Checker:
    if now is None:
        return Checker(world, runner, prometheus, http)
    return Checker(world, runner, prometheus, http, now=lambda: now)


def test_show_numbers_like_a_person_reads_them():
    assert show(18.0) == "18"
    assert show(0.43) == "0.4"
    assert show(33.333) == "33.3"
    assert show(None) == "?"
    assert show(200) == "200"


def test_error_rate_below(world, runner, prometheus, http):
    c = checker(world, runner, prometheus, http)
    params = {"service": "storefront", "max_percent": 1, "window_s": 60}
    prometheus.error_rates = {"storefront": 0.4}
    assert c.run(check("error_rate_below", params)).passed
    prometheus.error_rates = {"storefront": 31.2}
    result = c.run(check("error_rate_below", params))
    assert not result.passed
    assert result.message == "value 31.2, limit 1"


def test_no_data_is_never_healthy(world, runner, prometheus, http):
    c = checker(world, runner, prometheus, http)
    prometheus.error_rates = {"storefront": None}
    params = {"service": "storefront", "max_percent": 1, "window_s": 60}
    result = c.run(check("error_rate_below", params))
    assert not result.passed
    assert result.message == "value ?, limit 1"


def test_http_ok_needs_every_attempt(world, runner, prometheus, http):
    c = checker(world, runner, prometheus, http)
    params = {"service": "storefront", "path": "/health", "attempts": 3}
    assert c.run(check("http_ok", params)).passed
    assert len(http.urls) == 3
    http.status = None
    assert c.run(check("http_ok", params)).message == "value no answer, limit 200"


def test_config_equals(world, runner, prometheus, http):
    c = checker(world, runner, prometheus, http)
    params = {"service": "catalog", "key": "SEARCH_V2", "value": "false"}
    assert c.run(check("config_equals", params)).passed
    world.config_file("catalog").write_text("SEARCH_V2=true\n")
    assert not c.run(check("config_equals", params)).passed


def test_metric_compares(world, runner, prometheus, http):
    c = checker(world, runner, prometheus, http)
    prometheus.failed = 5.0
    query = 'sum(shop_checkouts_total{result="failed"})'
    assert c.run(check("metric", {"query": query, "op": "lt", "value": 10})).passed
    assert not c.run(check("metric", {"query": query, "op": "ge", "value": 10})).passed


def test_disk_free_percent(world, runner, prometheus, http, tmp_path):
    c = checker(world, runner, prometheus, http)
    assert c.run(
        check("disk_free_percent", {"mount": str(tmp_path), "min_percent": 0.001})
    ).passed
    assert not c.run(
        check("disk_free_percent", {"mount": str(tmp_path), "min_percent": 100})
    ).passed


def test_parse_compose_ps_both_formats():
    lines = '{"ID":"a1","State":"running","Health":"healthy"}\n{"ID":"b2","State":"exited"}\n'
    assert [c["ID"] for c in parse_compose_ps(lines)] == ["a1", "b2"]
    array = '[{"ID":"a1","State":"running"}]'
    assert parse_compose_ps(array)[0]["State"] == "running"
    assert parse_compose_ps("") == []


def test_container_running(world, runner, prometheus, http):
    c = checker(world, runner, prometheus, http)
    runner.outputs["ps"] = '{"ID":"a1","State":"running","Health":"healthy"}\n'
    assert c.run(check("container_running", {"service": "catalog"})).passed
    runner.outputs["ps"] = '{"ID":"a1","State":"running","Health":"unhealthy"}\n'
    result = c.run(check("container_running", {"service": "catalog"}))
    assert not result.passed
    assert result.message == "value running/unhealthy, limit running"
    argv = runner.calls[-1][0]
    assert argv[-5:] == ["ps", "--all", "--format", "json", "catalog"]


def test_no_restarts(world, runner, prometheus, http):
    now = datetime(2026, 11, 12, 18, 0, 0, tzinfo=UTC)
    c = checker(world, runner, prometheus, http, now=now)
    runner.outputs["ps"] = '{"ID":"a1","State":"running"}\n'
    runner.outputs["inspect"] = "2026-11-12T17:50:00.123456789Z\n"  # 10 min ago
    assert c.run(check("no_restarts", {"service": "catalog", "window_s": 300})).passed
    runner.outputs["inspect"] = "2026-11-12T17:59:30.5Z\n"  # 30 s ago
    result = c.run(check("no_restarts", {"service": "catalog", "window_s": 300}))
    assert not result.passed
    assert result.message == "value 29, limit 300"


def test_parse_started_at_reads_nanoseconds():
    parsed = parse_started_at("2026-11-12T17:50:00.123456789Z")
    assert parsed == datetime(2026, 11, 12, 17, 50, 0, 123456, tzinfo=UTC)


def test_a_broken_check_fails_without_crashing(world, runner, prometheus, http):
    no_url = dataclasses.replace(world.services["catalog"], url=None)
    broken = dataclasses.replace(world, services={**world.services, "catalog": no_url})
    result = checker(broken, runner, prometheus, http).run(
        check("http_ok", {"service": "catalog", "path": "/health"})
    )
    assert not result.passed
    assert result.message.startswith("value error:")


def test_checks_run_together(world, runner, prometheus, http):
    c = checker(world, runner, prometheus, http)
    prometheus.error_rates = {"storefront": 0.1}
    results = c.run_all(
        [
            check(
                "error_rate_below",
                {"service": "storefront", "max_percent": 1, "window_s": 60},
            ),
            check("http_ok", {"service": "storefront", "path": "/health"}),
        ]
    )
    assert [r.passed for r in results] == [True, True]
