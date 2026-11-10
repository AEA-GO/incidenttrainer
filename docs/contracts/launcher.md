# Contract 4: server launcher

Version: **v0.1 (draft)**. Changes freely until v1; after v1, only through an ADR.

The API and the cleanup Lambda start, find and delete game servers **only** through this interface. Today it is one EC2 instance per game; later it can be a pre-booted pool, Spot, Firecracker or several games per host without touching the API.

## Interface

```python
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol

ServerState = Literal["starting", "running", "stopping", "gone"]


@dataclass(frozen=True)
class ServerSpec:
    game_id: str  # 22 chars, also the idempotency key
    world: str  # e.g. "teapot-basic"
    expires_at: datetime  # UTC; start + 45 min


@dataclass(frozen=True)
class Server:
    server_id: str  # "i-0abc..." for EC2
    game_id: str
    world: str
    address: str | None  # private IPv4; None until known
    state: ServerState
    launched_at: datetime
    expires_at: datetime


class LaunchError(Exception):
    kind: Literal["capacity", "quota", "config", "unknown"]
    retryable: bool


class Launcher(Protocol):
    def start(self, spec: ServerSpec) -> Server: ...
    def stop(self, server_id: str) -> None: ...
    def address(self, server_id: str) -> str | None: ...
    def list(self) -> list[Server]: ...
```

## Rules every implementation follows

| Method | Must |
| --- | --- |
| `start` | Be **idempotent per `game_id`**: calling it twice for the same game returns the same server, never a second one. Return within 10 s. Raise `LaunchError` on failure; never return a half-started server. |
| `stop` | Be idempotent: stopping a server that is already gone is not an error. Never stop a server it didn't start (one without a game tag). Return within 5 s (deletion continues in the background). |
| `address` | Return the private IPv4 once known, `None` if the server is gone. |
| `list` | Return every server it started that isn't fully gone, including ones stuck starting or stopping. Used by the cleanup Lambda, so it must not depend on DynamoDB. |

What the launcher does **not** do: check limits or invite codes (the API's transaction does), talk to the agent (the gateway does), or decide when a game ends.

How the API uses it (data-model.md, "Start shift"): transaction first, then `start`. If `start` raises or times out, the API calls `start` **once more with the same spec** (safe because it is idempotent); if that fails too, it gives the game back and ends it as `failed`.

## Implementations

| Name | Used | Where |
| --- | --- | --- |
| `Ec2Launcher` | AWS | `api/src/itr_api/launcher/ec2.py` |
| `LocalLauncher` | laptop milestone (stage 4) | runs the world's `docker compose` on the laptop; one game at a time; address `127.0.0.1` |
| `FakeLauncher` | unit tests | in memory; can be told to fail with each `LaunchError` kind |

Chosen by the environment variable `ITR_LAUNCHER` (`ec2`, `local`, `fake`).

## `Ec2Launcher`

### `start`

```python
ec2.run_instances(
    LaunchTemplate={
        "LaunchTemplateName": f"itr-main-{spec.world}",
        "Version": "$Default",
    },
    MinCount=1,
    MaxCount=1,
    ClientToken=spec.game_id,  # idempotency: same token -> same instance
    TagSpecifications=[
        {
            "ResourceType": rt,
            "Tags": [
                {"Key": "itr-game", "Value": spec.game_id},
                {"Key": "itr-world", "Value": spec.world},
                {
                    "Key": "itr-expires-at",
                    "Value": spec.expires_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
                },
                {"Key": "env", "Value": "main"},
                {"Key": "cost", "Value": "game"},
                {"Key": "Name", "Value": f"itr-game-{spec.game_id}"},
            ],
        }
        for rt in ("instance", "volume")
    ],
)
```
- Nothing else is passed: everything else comes from the launch template, and IAM refuses overrides (below).
- `PrivateIpAddress` is already in the response, so `start` returns the address immediately.
- Launch-time tags merge with the template's tags (same key: launch-time value wins).
- boto3 client settings: connect timeout 3 s, read timeout 8 s, retry mode `standard`, 3 attempts.

### Launch template `itr-main-<world>` (Terraform, `infra/modules/sandbox`)

| Setting | Value | Why |
| --- | --- | --- |
| Image | `resolve:ssm:/itr/worlds/<world>/ami` | labs CI updates the parameter (data type `aws:ec2:image`); no template change per image |
| Instance type | `t4g.medium` | ~2.5 GB used of 4 GB |
| Network | sandbox subnet, sandbox security group, no public IP | no internet, only the gateway may connect |
| IAM instance profile | none | nothing to steal |
| Metadata | IMDSv2 required, hop limit 1, instance tags in metadata **off** | containers can't reach it; tags not exposed |
| Shutdown behaviour | terminate | the self-destruct timer deletes the server, not just stops it |
| Disks | root 16 GB gp3 + `/data` 4 GB gp3, both encrypted (AWS-managed key), delete on termination | labs can fill `/data` without breaking the OS |
| User data | **none** | the self-destruct timer is a systemd unit baked into the image (`shutdown -h +45` at boot), so there is nothing to read in user data |
| CPU credits | unlimited (the default) | no throttling during boot; worst case a few extra cents per game, to be measured in the beta |
| Tags (instance, volume) | `env=main`, `cost=game`, `Name=itr-game` | |

### `stop`

`terminate_instances(InstanceIds=[id])`. `InvalidInstanceID.NotFound` counts as success.

### `address`, `list`

`describe_instances` with filters `tag-key=itr-game` and `instance-state-name` in `pending, running, shutting-down, stopping, stopped`. EC2 states map to `starting` (pending), `running`, `stopping` (shutting-down, stopping, stopped) and `gone` (terminated, not listed).

### Errors

| EC2 error code | `LaunchError.kind` | Retryable |
| --- | --- | --- |
| `InsufficientInstanceCapacity`, `Unsupported` | `capacity` | yes (later: try the second zone) |
| `VcpuLimitExceeded`, `InstanceLimitExceeded` | `quota` | no |
| `InvalidLaunchTemplate*`, `InvalidAMIID*`, `InvalidParameter*`, `UnauthorizedOperation` | `config` | no, and it alerts Telegram: our setup is broken |
| anything else | `unknown` | yes |

The API shows the player `start_failed` ("We couldn't start a server. Your game wasn't used.") in every case.

### IAM for the API and cleanup roles

Account and template ids come from Terraform; the shape:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "LaunchOnlyFromOurTemplate",
      "Effect": "Allow",
      "Action": "ec2:RunInstances",
      "Resource": "arn:aws:ec2:eu-central-1:557541683274:instance/*",
      "Condition": {
        "ArnLike": { "ec2:LaunchTemplate": "arn:aws:ec2:eu-central-1:557541683274:launch-template/<sandbox template id>" },
        "StringEquals": { "ec2:InstanceType": "t4g.medium" },
        "Null": { "aws:RequestTag/itr-game": "false" }
      }
    },
    {
      "Sid": "ResourcesMustComeFromTheTemplate",
      "Effect": "Allow",
      "Action": "ec2:RunInstances",
      "Resource": [
        "arn:aws:ec2:eu-central-1:557541683274:subnet/*",
        "arn:aws:ec2:eu-central-1:557541683274:security-group/*",
        "arn:aws:ec2:eu-central-1:557541683274:network-interface/*",
        "arn:aws:ec2:eu-central-1:557541683274:volume/*",
        "arn:aws:ec2:eu-central-1::image/*"
      ],
      "Condition": {
        "ArnLike": { "ec2:LaunchTemplate": "arn:aws:ec2:eu-central-1:557541683274:launch-template/<sandbox template id>" },
        "Bool": { "ec2:IsLaunchTemplateResource": "true" }
      }
    },
    {
      "Sid": "UseTheTemplate",
      "Effect": "Allow",
      "Action": "ec2:RunInstances",
      "Resource": "arn:aws:ec2:eu-central-1:557541683274:launch-template/<sandbox template id>"
    },
    {
      "Sid": "TagOnlyWhileLaunching",
      "Effect": "Allow",
      "Action": "ec2:CreateTags",
      "Resource": ["arn:aws:ec2:eu-central-1:557541683274:instance/*", "arn:aws:ec2:eu-central-1:557541683274:volume/*"],
      "Condition": { "StringEquals": { "ec2:CreateAction": "RunInstances" } }
    },
    {
      "Sid": "ReadTheAmiParameter",
      "Effect": "Allow",
      "Action": "ssm:GetParameters",
      "Resource": "arn:aws:ssm:eu-central-1:557541683274:parameter/itr/worlds/*"
    },
    {
      "Sid": "TerminateOnlyGameServers",
      "Effect": "Allow",
      "Action": "ec2:TerminateInstances",
      "Resource": "arn:aws:ec2:eu-central-1:557541683274:instance/*",
      "Condition": {
        "Null": { "aws:ResourceTag/itr-game": "false" },
        "StringEquals": { "aws:ResourceTag/env": "main" }
      }
    },
    {
      "Sid": "Describe",
      "Effect": "Allow",
      "Action": "ec2:DescribeInstances",
      "Resource": "*"
    }
  ]
}
```
The cleanup role needs only `DescribeInstances` and `TerminateInstances`. The `itr-guardrails` policy still applies on top (region, instance sizes). Terraform `plan` plus one real launch from the `main` environment are the real check of this policy; EC2's IAM evaluation for `RunInstances` touches many resource types, so expect one or two rounds of fixing.

## Required tests

- `FakeLauncher` and `Ec2Launcher` (with moto) pass the same test set: `start` twice with the same game returns one server; `stop` twice is fine; `stop` of an unknown id is fine; `list` shows started servers and not stopped ones; each `LaunchError` kind is raised for its EC2 error.
- A test that `start` sends nothing but the template, counts, client token and tags (guards against someone adding overrides).

## Cost

The launch template, the SSM parameter (standard tier) and the IAM policy are free. Each game server is ~3 cents (t4g.medium ≈ $0.04/hour for up to 45 min, plus disks), as before.

## Changes to this contract

New optional fields on `ServerSpec` or `Server` (e.g. `size`, `zone`): minor version. Changing method meaning: ADR.
