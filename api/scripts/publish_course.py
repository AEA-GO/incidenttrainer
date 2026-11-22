"""Publish a course: its title, summary and lab ids in order (contract 1: course/<id>.json).
By hand until labs CI does it:

    uv run --with "botocore[crt]" python scripts/publish_course.py on-call-basics \
        --title "On-call basics" \
        --summary "Your first night shifts: get paged, fix the cause, keep people posted." \
        --labs checkout-down-3am

Public information only (titles and ids); the cards themselves come from publish_card.py.
"""

import argparse
import json
import os
import re

REGION = os.environ.get("AWS_REGION", "eu-central-1")
SLUG = re.compile(r"^[a-z0-9-]{2,40}$")


def build_course(course_id: str, title: str, summary: str, labs: list[str]) -> dict:
    for slug in [course_id, *labs]:
        if not SLUG.match(slug):
            raise ValueError(f"{slug!r} isn't a lab or course id")
    if len(set(labs)) != len(labs):
        raise ValueError("a lab is listed twice")
    return {"id": course_id, "title": title, "summary": summary, "labs": labs}


def main(argv: list[str] | None = None) -> None:
    import boto3

    parser = argparse.ArgumentParser(description="Publish course/<id>.json.")
    parser.add_argument("course_id")
    parser.add_argument("--title", required=True)
    parser.add_argument("--summary", default="")
    parser.add_argument(
        "--labs", required=True, help="lab ids in order, comma-separated"
    )
    parser.add_argument(
        "--bucket", default=None, help="default itr-main-labs-<account>"
    )
    args = parser.parse_args(argv)

    labs = [lab.strip() for lab in args.labs.split(",") if lab.strip()]
    course = build_course(args.course_id, args.title, args.summary, labs)
    bucket = args.bucket or (
        "itr-main-labs-"
        + boto3.client("sts", region_name=REGION).get_caller_identity()["Account"]
    )
    key = f"course/{course['id']}.json"
    boto3.client("s3", region_name=REGION).put_object(
        Bucket=bucket,
        Key=key,
        Body=json.dumps(course, ensure_ascii=False).encode(),
        ContentType="application/json",
    )
    print(f"Published s3://{bucket}/{key}: {course['title']} ({len(labs)} lab(s))")


if __name__ == "__main__":
    main()
