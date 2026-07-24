#!/usr/bin/env bash
set -euo pipefail

RUN_USER="${SUDO_USER:-$USER}"
RUN_GROUP="$(id -gn "$RUN_USER")"
RUN_UID="$(id -u "$RUN_USER")"
RUN_GID="$(id -g "$RUN_USER")"

SHARE="//10.11.12.6/Share"
MOUNT_ROOT="/mnt/dp6_share"
REPO_MOUNT="/srv/sen05"
CRED_FILE="/etc/sen05-dp6-share.cred"
FSTAB="/etc/fstab"
STAMP="$(date +%Y%m%d-%H%M%S)"

if [[ ! -f "$CRED_FILE" ]]; then
  echo "Missing $CRED_FILE"
  echo "Create it first with username/password/domain for the DP6 share."
  exit 1
fi

sudo mkdir -p "$MOUNT_ROOT" "$REPO_MOUNT"
sudo cp "$FSTAB" "${FSTAB}.bak.sen05-${STAMP}"

SHARE_LINE="${SHARE} ${MOUNT_ROOT} cifs credentials=${CRED_FILE},vers=3.0,iocharset=utf8,uid=${RUN_UID},gid=${RUN_GID},file_mode=0660,dir_mode=0770,nofail,_netdev,x-systemd.automount 0 0"
BIND_LINE="${MOUNT_ROOT}/SEN05_Autotrading ${REPO_MOUNT} none bind,nofail,x-systemd.requires=mnt-dp6_share.mount,x-systemd.after=mnt-dp6_share.mount 0 0"

if ! grep -Fq "${SHARE} ${MOUNT_ROOT} cifs" "$FSTAB"; then
  echo "$SHARE_LINE" | sudo tee -a "$FSTAB" >/dev/null
  echo "Added DP6 share mount to $FSTAB"
else
  echo "DP6 share mount already present in $FSTAB"
fi

if ! grep -Fq "${MOUNT_ROOT}/SEN05_Autotrading ${REPO_MOUNT} none" "$FSTAB"; then
  echo "$BIND_LINE" | sudo tee -a "$FSTAB" >/dev/null
  echo "Added SEN05 bind mount to $FSTAB"
else
  echo "SEN05 bind mount already present in $FSTAB"
fi

sudo systemctl daemon-reload
sudo mount -a

echo
echo "Mount check:"
findmnt "$MOUNT_ROOT" || true
findmnt "$REPO_MOUNT" || true
echo
ls -la "$REPO_MOUNT" | head
echo
echo "Mount setup complete for user ${RUN_USER}:${RUN_GROUP}."
