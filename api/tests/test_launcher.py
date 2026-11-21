"""Contract 4's required tests: FakeLauncher and Ec2Launcher (moto) pass the same set,
plus the EC2 specifics (only template + subnet + type + tags are sent; capacity fallback;
error kinds)."""

from datetime import UTC, datetime

import boto3
import pytest
from botocore.exceptions import ClientError
from itr_api.launcher import LaunchError, ServerSpec
from itr_api.launcher.ec2 import Ec2Launcher, classify
from itr_api.launcher.fake import FakeLauncher
from moto import mock_aws

GAME = "q3V9xYk2Lm8Ta1BcD4eF0g"
OTHER = "Zz9xYk2Lm8Ta1BcD4eF0g_"
EXPIRES = datetime(2026, 12, 8, 18, 45, tzinfo=UTC)


def spec(game_id: str = GAME) -> ServerSpec:
    return ServerSpec(game_id=game_id, world="teapot-basic", expires_at=EXPIRES)


@pytest.fixture
def aws():
    with mock_aws():
        ec2 = boto3.client("ec2", region_name="eu-central-1")
        vpc = ec2.create_vpc(CidrBlock="10.20.0.0/16")["Vpc"]["VpcId"]
        subnets = [
            ec2.create_subnet(VpcId=vpc, CidrBlock=cidr, AvailabilityZone=az)["Subnet"][
                "SubnetId"
            ]
            for cidr, az in (
                ("10.20.0.0/20", "eu-central-1a"),
                ("10.20.32.0/20", "eu-central-1b"),
                ("10.20.48.0/20", "eu-central-1c"),
            )
        ]
        ec2.create_launch_template(
            LaunchTemplateName="itr-main-teapot-basic",
            LaunchTemplateData={
                "ImageId": "ami-12c6146b",
                "InstanceType": "t4g.medium",
            },
        )
        yield ec2, subnets


@pytest.fixture(params=["fake", "ec2"])
def launcher(request, aws):
    if request.param == "fake":
        return FakeLauncher()
    ec2, subnets = aws
    return Ec2Launcher(subnets=subnets, clients=(ec2, ec2))


# ----- the same set for every launcher --------------------------------------------------
def test_start_twice_for_one_game_gives_one_server(launcher) -> None:
    first = launcher.start(spec())
    second = launcher.start(spec())
    assert first.server_id == second.server_id
    assert first.game_id == GAME and first.world == "teapot-basic"
    assert first.address and first.address.startswith("10.20.")
    assert [s.server_id for s in launcher.list()] == [first.server_id]


def test_stop_is_idempotent_and_list_forgets_stopped_servers(launcher) -> None:
    first = launcher.start(spec())
    other = launcher.start(spec(OTHER))
    launcher.stop(first.server_id)
    launcher.stop(first.server_id)
    launcher.stop("i-0000000000000dead")
    assert [s.server_id for s in launcher.list()] == [other.server_id]
    assert launcher.address(first.server_id) is None
    assert launcher.address(other.server_id) == other.address


# ----- FakeLauncher can fail on purpose -------------------------------------------------
@pytest.mark.parametrize(
    ("kind", "retryable"),
    [("capacity", True), ("quota", False), ("config", False), ("unknown", True)],
)
def test_fake_failures(kind, retryable) -> None:
    fake = FakeLauncher(fail_with=kind)
    with pytest.raises(LaunchError) as caught:
        fake.start(spec())
    assert (caught.value.kind, caught.value.retryable) == (kind, retryable)
    assert fake.start(spec()).game_id == GAME  # the next try works


# ----- Ec2Launcher specifics --------------------------------------------------------------
class Recorder:
    """Wraps the launch client: records each request; fails the first `fail` of them."""

    def __init__(
        self, real, fail: int = 0, code: str = "InsufficientInstanceCapacity"
    ) -> None:
        self.real, self.fail, self.code, self.requests = real, fail, code, []

    def run_instances(self, **request):
        self.requests.append(request)
        if len(self.requests) <= self.fail:
            raise ClientError(
                {"Error": {"Code": self.code, "Message": "nope"}}, "RunInstances"
            )
        return self.real.run_instances(**request)


def test_start_sends_only_template_counts_token_subnet_type_and_tags(aws) -> None:
    ec2, subnets = aws
    recorder = Recorder(ec2)
    server = Ec2Launcher(subnets=subnets, clients=(recorder, ec2)).start(spec())
    (request,) = recorder.requests
    assert set(request) == {
        "LaunchTemplate",
        "MinCount",
        "MaxCount",
        "ClientToken",
        "InstanceType",
        "SubnetId",
        "TagSpecifications",
    }
    assert request["LaunchTemplate"] == {
        "LaunchTemplateName": "itr-main-teapot-basic",
        "Version": "$Default",
    }
    assert (request["InstanceType"], request["SubnetId"], request["ClientToken"]) == (
        "t4g.medium",
        subnets[0],
        f"{GAME}-0",
    )
    tags = {t["Key"]: t["Value"] for t in request["TagSpecifications"][0]["Tags"]}
    assert tags == {
        "itr-game": GAME,
        "itr-world": "teapot-basic",
        "itr-expires-at": "2026-12-08T18:45:00Z",
        "env": "main",
        "cost": "game",
        "Name": f"itr-game-{GAME}",
    }
    assert [s["ResourceType"] for s in request["TagSpecifications"]] == [
        "instance",
        "volume",
    ]
    assert server.expires_at == EXPIRES


def test_capacity_fallback_tries_every_zone_then_the_next_size(aws) -> None:
    ec2, subnets = aws
    recorder = Recorder(ec2, fail=3)  # no t4g.medium in a, b or c
    launcher = Ec2Launcher(subnets=subnets, clients=(recorder, ec2))
    server = launcher.start(spec())
    tried = [(r["InstanceType"], r["SubnetId"]) for r in recorder.requests]
    assert tried == [("t4g.medium", s) for s in subnets] + [("m7g.medium", subnets[0])]
    assert [r["ClientToken"] for r in recorder.requests] == [
        f"{GAME}-{n}" for n in range(4)
    ]
    assert server.instance_type == "m7g.medium"
    assert len(launcher.last_attempts) == 4


def test_no_capacity_anywhere_is_a_retryable_capacity_error(aws) -> None:
    ec2, subnets = aws
    recorder = Recorder(ec2, fail=99)
    with pytest.raises(LaunchError) as caught:
        Ec2Launcher(subnets=subnets, clients=(recorder, ec2)).start(spec())
    assert (caught.value.kind, caught.value.retryable) == ("capacity", True)
    assert len(recorder.requests) == 9  # 3 sizes x 3 zones


def test_it_gives_up_within_the_time_budget(aws) -> None:
    ec2, subnets = aws
    now = [0.0]

    def clock() -> float:
        now[0] += 4.0  # every look at the clock: 4 s later
        return now[0]

    recorder = Recorder(ec2, fail=99)
    with pytest.raises(LaunchError):
        Ec2Launcher(subnets=subnets, clients=(recorder, ec2), clock=clock).start(spec())
    assert len(recorder.requests) < 9


@pytest.mark.parametrize(
    ("code", "kind", "retryable"),
    [
        ("VcpuLimitExceeded", "quota", False),
        ("InvalidLaunchTemplateName.NotFoundException", "config", False),
        ("UnauthorizedOperation", "config", False),
        ("RequestLimitExceeded", "unknown", True),
    ],
)
def test_other_errors_stop_at_once_with_their_kind(aws, code, kind, retryable) -> None:
    ec2, subnets = aws
    recorder = Recorder(ec2, fail=1, code=code)
    with pytest.raises(LaunchError) as caught:
        Ec2Launcher(subnets=subnets, clients=(recorder, ec2)).start(spec())
    assert (caught.value.kind, caught.value.retryable) == (kind, retryable)
    assert len(recorder.requests) == 1


def test_contract_error_table() -> None:
    assert classify("InsufficientInstanceCapacity") == ("capacity", True)
    assert classify("Unsupported") == ("capacity", True)
    assert classify("InstanceLimitExceeded") == ("quota", False)
    assert classify("InvalidAMIID.Malformed") == ("config", False)
    assert classify("InvalidParameterValue") == ("config", False)
    assert classify("SomethingNew") == ("unknown", True)


def test_stop_never_touches_a_server_that_is_not_a_game(aws) -> None:
    ec2, subnets = aws
    other = ec2.run_instances(
        ImageId="ami-12c6146b", MinCount=1, MaxCount=1, SubnetId=subnets[0]
    )
    other_id = other["Instances"][0]["InstanceId"]
    Ec2Launcher(subnets=subnets, clients=(ec2, ec2)).stop(other_id)
    state = ec2.describe_instances(InstanceIds=[other_id])["Reservations"][0][
        "Instances"
    ][0]["State"]["Name"]
    assert state == "running"


def test_list_reads_the_tags_back(aws) -> None:
    ec2, subnets = aws
    launcher = Ec2Launcher(subnets=subnets, clients=(ec2, ec2))
    launcher.start(spec())
    (server,) = launcher.list()
    assert (server.game_id, server.world, server.expires_at) == (
        GAME,
        "teapot-basic",
        EXPIRES,
    )
    assert server.state in ("starting", "running")
    assert server.zone == "eu-central-1a"


def test_the_admin_check_starts_and_stops_one_server(aws, monkeypatch) -> None:
    from itr_api import main

    ec2, subnets = aws
    monkeypatch.setenv("ITR_LAUNCHER", "ec2")
    monkeypatch.setenv("ITR_SANDBOX_SUBNETS", ",".join(subnets))
    monkeypatch.setenv("AWS_DEFAULT_REGION", "eu-central-1")
    result = main.handler({"itr_admin": "launcher_check"}, None)
    assert result["ok"] is True and result["instance_type"] == "t4g.medium"
    assert result["attempts"] == [f"t4g.medium@{subnets[0]}: ok"]
    live = ec2.describe_instances(
        Filters=[{"Name": "instance-state-name", "Values": ["pending", "running"]}]
    )
    assert live["Reservations"] == []
