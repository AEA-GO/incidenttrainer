"""Forward SNS messages (budgets, cost anomalies, CloudWatch alarms) to the Telegram Alerts topic."""

import json
import os
import urllib.parse
import urllib.request

import boto3

_ssm = boto3.client("ssm")
_token: str | None = None
MAX_LEN = 3500  # Telegram allows 4096 characters per message


def _bot_token() -> str:
    global _token
    if _token is None:
        response = _ssm.get_parameter(
            Name=os.environ["TOKEN_PARAMETER"], WithDecryption=True
        )
        _token = response["Parameter"]["Value"]
    return _token


def _alarm_text(alarm: dict) -> str:
    state = alarm.get("NewStateValue", "?")
    name = alarm.get("AlarmName", "alarm")
    reason = alarm.get("NewStateReason", "")
    return f"{state}: {name}\n{reason}"


def _text(record: dict) -> str:
    sns = record["Sns"]
    subject = sns.get("Subject") or "AWS alert"
    message = sns.get("Message", "")
    try:
        data = json.loads(message)
    except ValueError:
        body = message
    else:
        body = (
            _alarm_text(data)
            if isinstance(data, dict) and "AlarmName" in data
            else json.dumps(data, indent=2)
        )
    return f"{subject}\n\n{body}"[:MAX_LEN]


def handler(event, _context):
    url = f"https://api.telegram.org/bot{_bot_token()}/sendMessage"
    sent = 0
    for record in event.get("Records", []):
        form = {
            "chat_id": os.environ["CHAT_ID"],
            "message_thread_id": os.environ["THREAD_ID"],
            "text": _text(record),
            "disable_web_page_preview": "true",
        }
        request = urllib.request.Request(
            url, data=urllib.parse.urlencode(form).encode()
        )
        # An HTTP error raises, so SNS retries the delivery.
        with urllib.request.urlopen(request, timeout=5):
            sent += 1
    return {"sent": sent}
