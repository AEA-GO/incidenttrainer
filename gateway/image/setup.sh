#!/usr/bin/env bash
# Runs once on the temporary build server (as root), from gateway.pkr.hcl.
# Installs Ubuntu's nginx, gateway-auth with its own Python (uv), and the systemd units.
# Nothing starts gateway-auth here: it needs /etc/itr-gateway.env, which user data writes
# at boot on the real gateway.
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive

UV_VERSION=0.13.7
APP=/opt/itr-gateway

echo "--- the gateway has no internet: no snaps, no background apt jobs"
systemctl disable --now apt-daily.timer apt-daily-upgrade.timer unattended-upgrades.service motd-news.timer || true
snap remove --purge amazon-ssm-agent || true
apt-get purge -y -q snapd

echo "--- packages (Ubuntu's nginx: Canonical backports its security fixes)"
apt-get update -q
apt-get install -y -q --no-install-recommends nginx curl ca-certificates
rm -f /etc/nginx/sites-enabled/default

echo "--- uv ${UV_VERSION}, and gateway-auth with its own Python 3.13"
curl -LsSf "https://astral.sh/uv/${UV_VERSION}/install.sh" | env UV_INSTALL_DIR=/usr/local/bin INSTALLER_NO_MODIFY_PATH=1 sh
mkdir -p /var/tmp/itr-src
tar -xzf /tmp/gateway.tgz -C /var/tmp/itr-src
cd /var/tmp/itr-src
UV_PROJECT_ENVIRONMENT=$APP/venv UV_PYTHON_INSTALL_DIR=$APP/python UV_CACHE_DIR=/var/tmp/uv-cache \
  uv sync --frozen --no-dev --no-editable --python 3.13 --managed-python
"$APP/venv/bin/python" -c "import itr_gateway.app, itr_gateway.broker, boto3; print('gateway-auth imports ok')"

echo "--- nginx config (the same files the tests run)"
install -m 0644 nginx/itr-gateway.conf /etc/nginx/conf.d/itr-gateway.conf
install -m 0644 nginx/itr-auth.conf nginx/itr-proxy-headers.conf /etc/nginx/
nginx -t

echo "--- systemd: gateway-auth as its own user, hardened"
useradd --system --no-create-home --shell /usr/sbin/nologin itr-gateway || true
install -m 0644 image/files/itr-gateway-auth.service /etc/systemd/system/
install -d -m 0755 /etc/systemd/journald.conf.d
install -m 0644 image/files/journald.conf /etc/systemd/journald.conf.d/itr.conf
systemctl daemon-reload
systemctl enable nginx itr-gateway-auth

echo "--- what this image holds"
cat > $APP/BUILD <<EOF
image: gateway
platform_commit: ${BUILD_REF:-unknown}
nginx: $(nginx -v 2>&1 | cut -d/ -f2)
built: $(date -u +%Y-%m-%dT%H:%M:%SZ)
EOF
cat $APP/BUILD

cd /
rm -rf /var/tmp/itr-src /var/tmp/uv-cache /tmp/gateway.tgz
apt-get clean
