"""Publish a lab's card and debrief (contract 1: labs/<lab>/v<version>/{card,debrief}.json),
by hand until labs CI does it. Run it on the lab file in the private labs repo:

    uv run --with "botocore[crt]" --with pyyaml python scripts/publish_card.py \
        ~/IncTrainer/incidenttrainer-labs/labs/checkout-down-3am/lab.yaml

card.json gets only `id`, `version`, `world`, `course`, `card` (+ `terminal`) and `scoring`,
and is refused if any of the lab's `secrets` words appear in it. debrief.json holds the
answer: the bucket is private and the API sends it only after a scored game. It doesn't touch labs/<lab>/current: the gateway's
hand_game.py publish sets that with the runbook, so publish both for the same version.
"""

import argparse
import json
import os
import sys
from pathlib import Path

REGION = os.environ.get("AWS_REGION", "eu-central-1")


def build_card(lab: dict) -> dict:
    card = dict(lab["card"])
    card["terminal"] = (
        card.get("level") == "junior"
    )  # contract 1: derived when publishing
    published = {
        "id": lab["id"],
        "version": int(lab["version"]),
        "world": lab["world"],
        "course": lab.get("course"),
        "card": card,
        "scoring": lab["scoring"],
    }
    text = json.dumps(published, ensure_ascii=False).lower()
    leaks = [word for word in lab.get("secrets") or [] if str(word).lower() in text]
    if leaks:
        raise ValueError(
            f"{len(leaks)} secret word(s) appear in the card; not publishing"
        )
    return published


def build_debrief(lab: dict) -> dict:
    return {"id": lab["id"], "version": int(lab["version"]), "debrief": lab["debrief"]}


def main(argv: list[str] | None = None) -> None:
    import boto3
    import yaml

    parser = argparse.ArgumentParser(description="Publish a lab's card.json.")
    parser.add_argument("lab_file", type=Path)
    parser.add_argument(
        "--bucket", default=None, help="default itr-main-labs-<account>"
    )
    args = parser.parse_args(argv)

    lab = yaml.safe_load(args.lab_file.read_text())
    try:
        card = build_card(lab)
    except ValueError as error:
        sys.exit(str(error))
    bucket = args.bucket or (
        "itr-main-labs-"
        + boto3.client("sts", region_name=REGION).get_caller_identity()["Account"]
    )
    s3 = boto3.client("s3", region_name=REGION)
    folder = f"labs/{card['id']}/v{card['version']}"
    for name, body in (("card.json", card), ("debrief.json", build_debrief(lab))):
        s3.put_object(
            Bucket=bucket,
            Key=f"{folder}/{name}",
            Body=json.dumps(body, ensure_ascii=False).encode(),
            ContentType="application/json",
        )
    print(
        f"Published s3://{bucket}/{folder}/ card.json + debrief.json: {card['card'].get('title')}"
    )


if __name__ == "__main__":
    main()
