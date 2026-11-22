import importlib.util
from pathlib import Path

import boto3
import pytest
from itr_api import codes
from moto import mock_aws

SCRIPTS = Path(__file__).parent.parent / "scripts"


def load(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


LAB = {
    "id": "example-lab",
    "version": 2,
    "world": "teapot-basic",
    "course": {"id": "example-course", "order": 1},
    "card": {
        "title": "Tuesday afternoon errors",
        "level": "junior",
        "hook": "Some fail.",
    },
    "scoring": {"target_time_s": 300},
    "fault": [{"verb": "set_flag"}],
    "debrief": {"cause": "the flag"},
    "secrets": ["SEARCH_V2"],
}


def test_card_has_only_public_parts():
    card = load("publish_card").build_card(LAB)
    assert set(card) == {"id", "version", "world", "course", "card", "scoring"}
    assert card["card"]["terminal"] is True


def test_debrief_file_has_the_debrief_only():
    assert load("publish_card").build_debrief(LAB) == {
        "id": "example-lab",
        "version": 2,
        "debrief": {"cause": "the flag"},
    }


def test_card_with_a_secret_word_is_refused():
    lab = LAB | {"card": LAB["card"] | {"hook": "search_v2 broke it"}}
    with pytest.raises(ValueError, match="secret"):
        load("publish_card").build_card(lab)


def test_invite_codes_add_list_off():
    script = load("invite_codes")
    with mock_aws():
        db = boto3.resource("dynamodb", region_name="eu-central-1")
        tbl = db.create_table(
            TableName="t",
            KeySchema=[
                {"AttributeName": "pk", "KeyType": "HASH"},
                {"AttributeName": "sk", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "pk", "AttributeType": "S"},
                {"AttributeName": "sk", "AttributeType": "S"},
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        made = script.add(tbl, "tester", 6, count=2)
        assert len(set(made)) == 2 and all(codes.normalise(c) == c for c in made)
        assert script.switch_off(tbl, made[0].lower()) == codes.shown(made[0])
        rows = script.listing(tbl)
        assert len(rows) == 2 and any(" off " in r and "tester" in r for r in rows)
