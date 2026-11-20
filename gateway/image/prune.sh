#!/usr/bin/env bash
# Keeps the newest KEEP gateway images and deletes older ones with their snapshots.
# Usage: gateway/image/prune.sh [KEEP]
set -euo pipefail
KEEP=${1:-2}

mapfile -t OLD < <(aws ec2 describe-images --owners self \
  --filters "Name=tag:itr-image,Values=gateway" \
  --query 'sort_by(Images, &CreationDate)[].ImageId' --output text | tr '\t' '\n' | head -n "-${KEEP}")

for ami in "${OLD[@]}"; do
  [[ -n $ami ]] || continue
  mapfile -t SNAPS < <(aws ec2 describe-images --image-ids "$ami" \
    --query 'Images[0].BlockDeviceMappings[].Ebs.SnapshotId' --output text | tr '\t' '\n')
  echo "Deleting ${ami} (snapshots: ${SNAPS[*]})"
  aws ec2 deregister-image --image-id "$ami"
  for snap in "${SNAPS[@]}"; do
    [[ -n $snap && $snap != None ]] && aws ec2 delete-snapshot --snapshot-id "$snap"
  done
done
echo "Kept the newest ${KEEP} gateway images."
