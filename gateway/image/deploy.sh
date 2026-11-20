#!/usr/bin/env bash
# Puts a new gateway image on the running gateway without replacing the server: EC2
# swaps its root disk for one made from the image, in one reboot. The instance keeps its
# id, private IP and DNS name, so CloudFront's VPC origin needs no change (updating that
# takes up to 15 min). /s/* is down for the reboot (~1-2 min).
# Usage: gateway/image/deploy.sh <ami-id>     (env: GATEWAY_NAME, PUBLIC_URL)
set -euo pipefail
AMI=$1
NAME=${GATEWAY_NAME:-itr-main-gateway}
PUBLIC_URL=${PUBLIC_URL:-https://drer7le45ultl.cloudfront.net}

read -r ID CURRENT < <(aws ec2 describe-instances \
  --filters "Name=tag:Name,Values=${NAME}" "Name=instance-state-name,Values=running" \
  --query 'Reservations[0].Instances[0].[InstanceId,ImageId]' --output text)
if [[ -z $ID || $ID == None ]]; then
  echo "No running ${NAME} yet: Terraform creates it from this image."
  exit 0
fi
if [[ $CURRENT == "$AMI" ]]; then
  echo "${NAME} (${ID}) already runs ${AMI}."
  exit 0
fi

echo "Replacing the root disk of ${NAME} (${ID}): ${CURRENT} -> ${AMI}"
TASK=$(aws ec2 create-replace-root-volume-task --instance-id "$ID" --image-id "$AMI" \
  --delete-replaced-root-volume --query 'ReplaceRootVolumeTask.ReplaceRootVolumeTaskId' --output text)
for _ in $(seq 1 60); do
  STATE=$(aws ec2 describe-replace-root-volume-tasks --replace-root-volume-task-ids "$TASK" \
    --query 'ReplaceRootVolumeTasks[0].TaskState' --output text)
  case $STATE in
    succeeded) break ;;
    failed | failed-detached) echo "::error::Root volume replacement ${TASK} ${STATE}" >&2; exit 1 ;;
  esac
  sleep 10
done
[[ $STATE == succeeded ]] || { echo "::error::${TASK} still ${STATE} after 10 min" >&2; exit 1; }

# Smoke test through CloudFront: a made-up game without a cookie must get the gateway's
# own JSON answer (nginx + gateway-auth up), not a CloudFront or nginx error page.
for _ in $(seq 1 30); do
  BODY=$(curl -s --max-time 5 "${PUBLIC_URL}/s/AAAAAAAAAAAAAAAAAAAAAA/api/v1/state" || true)
  if [[ $BODY == *'"bad_cookie"'* ]]; then
    echo "Gateway is up on ${AMI}: ${BODY}"
    exit 0
  fi
  sleep 10
done
echo "::error::The gateway didn't answer through CloudFront after 5 min (last: ${BODY:0:200})" >&2
exit 1
