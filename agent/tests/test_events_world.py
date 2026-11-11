from conftest import DEV
from itr_agent.events import EventLog
from itr_agent.world import load_world


def test_event_log_numbers_and_filters():
    log = EventLog()
    log.add("fault_started")
    log.add("paged", {"title": "x"}, t=0)
    assert log.last_seq == 2
    assert [e.seq for e in log.since(0)] == [1, 2]
    assert [e.type for e in log.since(1)] == ["paged"]
    assert log.since(0, limit=1)[0].seq == 1
    assert log.since(2) == []


def test_dev_world_loads():
    world = load_world(DEV / "world.yaml")
    assert world.name == "dev-shop"
    assert set(world.services) == {"storefront", "catalog"}
    assert world.services["storefront"].calls == ("catalog",)
    assert "CheckoutErrorRateHigh" in world.alerts
    assert str(world.compose_dir) == "/opt/dev-shop"


def test_compose_dir_override(tmp_path):
    world = load_world(DEV / "world.yaml", compose_dir_override=tmp_path)
    assert world.compose_dir == tmp_path.resolve()
    assert world.config_file("catalog") == tmp_path.resolve() / "config" / "catalog.env"
