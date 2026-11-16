"""Service map colours."""

from itr_agent.observe import service_status


def test_app_without_data_is_no_data(world):
    assert service_status("catalog", None, False, world).label == "NO DATA"


def test_database_is_up_unless_service_down_fires(world):
    assert service_status("postgres", None, False, world, "database").label == "OK"
    assert service_status("redis", None, False, world, "cache").label == "OK"
    assert service_status("postgres", None, True, world, "database").label == "DOWN"


def test_app_error_rates(world):
    assert service_status("catalog", 0.2, False, world).label == "OK"
    assert service_status("catalog", 12.0, False, world).label == "ERRORS"
    assert service_status("catalog", 80.0, False, world).label == "FAILING"
