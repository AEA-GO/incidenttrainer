#!/usr/bin/env bash
# Escape test: run on a real game server, from the player's terminal (a Junior lab), as
# the player would. Every check tries to get somewhere a player must not reach and
# passes when that fails. Part of the MVP's definition of done; run it again after any
# change to the network, the server image or the agent.
#
# How: start a Junior game, open the Terminal tab and paste this whole file, or
#   cat > /tmp/escape-test.sh <<'EOF' ... EOF; bash /tmp/escape-test.sh
# It changes nothing on the server and takes about a minute.

set -u
GATEWAY_IP=${GATEWAY_IP:-10.20.16.189}
pass=0
fail=0
ok() { printf '  PASS  %s\n' "$1"; pass=$((pass + 1)); }
bad() { printf '  FAIL  %s\n' "$1"; fail=$((fail + 1)); }
blocked() { # blocked "what" command...: passes when the command fails
  local what=$1
  shift
  if "$@" >/dev/null 2>&1; then bad "$what"; else ok "$what"; fi
}

echo "Escape test on $(hostname) as $(whoami), $(date -u +%FT%TZ)"

echo "Internet"
blocked "HTTPS to a website (example.com)" curl -sS -m 5 -o /dev/null https://example.com
blocked "HTTP to an IP address (1.1.1.1)" curl -sS -m 5 -o /dev/null http://1.1.1.1
# A real DNS question for example.com over UDP; passes when no answer comes back.
QUERY='\x12\x34\x01\x00\x00\x01\x00\x00\x00\x00\x00\x00\x07example\x03com\x00\x00\x01\x00\x01'
blocked "DNS straight to a public resolver (8.8.8.8)" timeout 5 bash -c \
  "exec 3<>/dev/udp/8.8.8.8/53; printf '$QUERY' >&3; head -c 1 <&3"

echo "DNS through Amazon's resolver (DNS Firewall)"
blocked "resolve a public name (example.com)" getent hosts example.com
blocked "resolve a made-up name a tunnel would use" getent hosts "x$(date +%s).escape-test.example.org"
if getent hosts dynamodb.eu-central-1.amazonaws.com >/dev/null; then
  ok "AWS names still resolve (the gateway needs them)"
else
  bad "AWS names no longer resolve: the gateway would break"
fi

echo "AWS"
blocked "S3 by name" curl -sS -m 5 -o /dev/null https://s3.eu-central-1.amazonaws.com
blocked "DynamoDB by name" curl -sS -m 5 -o /dev/null https://dynamodb.eu-central-1.amazonaws.com
TOKEN=$(curl -sS -m 3 -X PUT http://169.254.169.254/latest/api/token \
  -H "X-aws-ec2-metadata-token-ttl-seconds: 60" 2>/dev/null)
imds() { curl -sS -f -m 3 -H "X-aws-ec2-metadata-token: $TOKEN" "http://169.254.169.254/latest/$1"; }
blocked "server credentials (no IAM role)" imds meta-data/iam/security-credentials/
# EC2 answers 200 with an empty body when a server has no user data: empty is a pass.
user_data=$(imds user-data 2>/dev/null)
if [ -n "$user_data" ]; then bad "user data is set (${#user_data} characters)"; else ok "user data (none)"; fi
blocked "instance tags in metadata (off)" imds meta-data/tags/instance
blocked "metadata without a token (IMDSv1 off)" curl -sS -f -m 3 http://169.254.169.254/latest/meta-data/

echo "Our own machines"
blocked "the gateway's web port ($GATEWAY_IP:80)" curl -sS -m 3 -o /dev/null "http://$GATEWAY_IP/"
blocked "the gateway's auth service ($GATEWAY_IP:9000)" curl -sS -m 3 -o /dev/null "http://$GATEWAY_IP:9000/"
blocked "the gateway's SSH ($GATEWAY_IP:22)" timeout 3 bash -c "exec 3<>/dev/tcp/$GATEWAY_IP/22"
blocked "another game server's agent (10.20.0.10:8080)" timeout 3 bash -c 'exec 3<>/dev/tcp/10.20.0.10/8080'
blocked "another zone's game server (10.20.32.10:8080)" timeout 3 bash -c 'exec 3<>/dev/tcp/10.20.32.10/8080'

echo "This server"
code=$(curl -s -m 3 -o /dev/null -w '%{http_code}' http://127.0.0.1:8081/control/v1/runbook)
if [ "$code" = "200" ]; then bad "the agent hands out its runbook"; else ok "the agent never hands out its runbook (HTTP $code)"; fi
found=$(sudo find / -xdev \( -name 'lab.yaml' -o -name '*push.json' -o -name 'runbook.json' -o -name 'debrief.json' \) 2>/dev/null | head -3)
if [ -n "$found" ]; then bad "lab files on disk: $found"; else ok "no lab files on disk (the runbook lives only in the agent's memory)"; fi
if sudo test -f /run/systemd/shutdown/scheduled; then
  ok "the 45-minute self-destruct is scheduled"
else
  bad "no shutdown is scheduled (the self-destruct isn't armed)"
fi

echo
echo "$pass passed, $fail failed"
[ "$fail" -eq 0 ]
