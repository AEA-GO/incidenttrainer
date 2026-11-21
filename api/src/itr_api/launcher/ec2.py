"""Ec2Launcher: one EC2 instance per game, from launch template itr-main-<world> (contract 4).

Capacity fallback (decided 21 Nov): AWS sometimes has no t4g.medium in a zone. `start`
tries t4g.medium in every sandbox subnet (zone a first: no cross-zone traffic to the
gateway), then m7g.medium, then m6g.medium, and stops trying after ~9 s (the contract
allows 10). The only things it adds to the template are the subnet, the instance type and
the game's tags; IAM allows exactly those subnets and types and nothing else.

Idempotent per game: it first looks for a live server tagged with the game id, and each try
uses ClientToken "<game_id>-<try>", so a retry of the same start never makes a second server.
"""

import time
from collections.abc import Callable
from datetime import UTC, datetime

from itr_api.launcher import ErrorKind, LaunchError, Server, ServerSpec

TYPES = ("t4g.medium", "m7g.medium", "m6g.medium")
BUDGET_S = 9.0
LIVE_STATES = ["pending", "running", "shutting-down", "stopping", "stopped"]
STATES = {
    "pending": "starting",
    "running": "running",
    "shutting-down": "stopping",
    "stopping": "stopping",
    "stopped": "stopping",
}

CAPACITY = {"InsufficientInstanceCapacity", "Unsupported", "InsufficientCapacity"}
QUOTA = {"VcpuLimitExceeded", "InstanceLimitExceeded"}
CONFIG_PREFIXES = (
    "InvalidLaunchTemplate",
    "InvalidAMIID",
    "InvalidParameter",
    "UnauthorizedOperation",
)


def classify(code: str) -> tuple[ErrorKind, bool]:
    """EC2 error code -> (kind, retryable), the contract's table."""
    if code in CAPACITY:
        return "capacity", True
    if code in QUOTA:
        return "quota", False
    if code.startswith(CONFIG_PREFIXES) or code in {
        "InvalidSubnetID.NotFound",
        "InvalidGroup.NotFound",
    }:
        return "config", False
    return "unknown", True


def _clients():
    import boto3
    from botocore.config import Config

    timeouts = {"connect_timeout": 3, "read_timeout": 8}
    # Launches: no SDK retries. A capacity error comes back as a 500, which the SDK would
    # retry for seconds; we'd rather move on to the next zone or size at once.
    launch = boto3.client(
        "ec2", config=Config(retries={"total_max_attempts": 1}, **timeouts)
    )
    other = boto3.client(
        "ec2",
        config=Config(
            retries={"mode": "standard", "total_max_attempts": 3}, **timeouts
        ),
    )
    return launch, other


def _tags(instance: dict) -> dict[str, str]:
    return {t["Key"]: t["Value"] for t in instance.get("Tags", [])}


def _when(value: str | None, default: datetime) -> datetime:
    if not value:
        return default
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)


def _server(instance: dict) -> Server:
    tags = _tags(instance)
    launched = instance["LaunchTime"]
    state = instance["State"]["Name"]
    return Server(
        server_id=instance["InstanceId"],
        game_id=tags.get("itr-game", ""),
        world=tags.get("itr-world", ""),
        address=instance.get("PrivateIpAddress") if state != "terminated" else None,
        state=STATES.get(state, "gone"),  # type: ignore[arg-type]
        launched_at=launched,
        expires_at=_when(tags.get("itr-expires-at"), launched),
        instance_type=instance.get("InstanceType"),
        zone=(instance.get("Placement") or {}).get("AvailabilityZone"),
    )


class Ec2Launcher:
    def __init__(
        self,
        subnets: list[str],
        template_prefix: str = "itr-main-",
        types: tuple[str, ...] = TYPES,
        clients=None,
        clock: Callable[[], float] = time.monotonic,
        budget_s: float = BUDGET_S,
    ) -> None:
        self.subnets = subnets  # zone a first
        self.template_prefix = template_prefix
        self.types = types
        self.launch_client, self.client = clients or _clients()
        self.clock = clock
        self.budget_s = budget_s
        self.last_attempts: list[str] = []  # what the last start() tried, for logs

    # ----- start ------------------------------------------------------------------------
    def start(self, spec: ServerSpec) -> Server:
        existing = self._find(spec.game_id)
        if existing is not None:
            return existing
        began = self.clock()
        self.last_attempts = []
        tries = [(kind, subnet) for kind in self.types for subnet in self.subnets]
        for number, (instance_type, subnet) in enumerate(tries):
            if number and self.clock() - began > self.budget_s:
                break
            try:
                reply = self.launch_client.run_instances(
                    **self._request(spec, instance_type, subnet, number)
                )
            except Exception as error:  # botocore ClientError and connection errors
                code = getattr(error, "response", {}).get("Error", {}).get("Code", "")
                kind, retryable = classify(code) if code else ("unknown", True)
                self.last_attempts.append(
                    f"{instance_type}@{subnet}: {code or type(error).__name__}"
                )
                if kind == "capacity":
                    continue  # next zone, then next size
                raise LaunchError(
                    kind, f"{code or type(error).__name__}: {error}", retryable
                ) from error
            self.last_attempts.append(f"{instance_type}@{subnet}: ok")
            return _server(reply["Instances"][0])
        raise LaunchError(
            "capacity", "no capacity: " + "; ".join(self.last_attempts), True
        )

    def _request(
        self, spec: ServerSpec, instance_type: str, subnet: str, number: int
    ) -> dict:
        """Everything start() sends. Nothing else may be added (IAM refuses it anyway)."""
        tags = [
            {"Key": "itr-game", "Value": spec.game_id},
            {"Key": "itr-world", "Value": spec.world},
            {
                "Key": "itr-expires-at",
                "Value": spec.expires_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
            },
            {"Key": "env", "Value": "main"},
            {"Key": "cost", "Value": "game"},
            {"Key": "Name", "Value": f"itr-game-{spec.game_id}"},
        ]
        return {
            "LaunchTemplate": {
                "LaunchTemplateName": f"{self.template_prefix}{spec.world}",
                "Version": "$Default",
            },
            "MinCount": 1,
            "MaxCount": 1,
            "ClientToken": f"{spec.game_id}-{number}",
            "InstanceType": instance_type,
            "SubnetId": subnet,
            "TagSpecifications": [
                {"ResourceType": rt, "Tags": tags} for rt in ("instance", "volume")
            ],
        }

    # ----- the rest ---------------------------------------------------------------------
    def _describe(
        self, filters: list[dict], ids: list[str] | None = None
    ) -> list[dict]:
        args: dict = {"Filters": filters}
        if ids:
            args["InstanceIds"] = ids
        instances = []
        for page in self.client.get_paginator("describe_instances").paginate(**args):
            for reservation in page["Reservations"]:
                instances.extend(reservation["Instances"])
        return instances

    def _find(self, game_id: str) -> Server | None:
        found = self._describe(
            [
                {"Name": "tag:itr-game", "Values": [game_id]},
                {"Name": "instance-state-name", "Values": ["pending", "running"]},
            ]
        )
        return _server(found[0]) if found else None

    def stop(self, server_id: str) -> None:
        try:
            found = self._describe(
                [{"Name": "tag-key", "Values": ["itr-game"]}], ids=[server_id]
            )
        except Exception as error:
            if "InvalidInstanceID" in str(
                getattr(error, "response", {}).get("Error", {}).get("Code", "")
            ):
                return  # already gone
            raise
        if not found:
            return  # gone, or not a game server: never touch it
        self.client.terminate_instances(InstanceIds=[server_id])

    def address(self, server_id: str) -> str | None:
        try:
            found = self._describe(
                [{"Name": "tag-key", "Values": ["itr-game"]}], ids=[server_id]
            )
        except Exception as error:
            if "InvalidInstanceID" in str(
                getattr(error, "response", {}).get("Error", {}).get("Code", "")
            ):
                return None
            raise
        return _server(found[0]).address if found else None

    def list(self) -> list[Server]:
        found = self._describe(
            [
                {"Name": "tag-key", "Values": ["itr-game"]},
                {"Name": "instance-state-name", "Values": LIVE_STATES},
            ]
        )
        return [_server(i) for i in found]
