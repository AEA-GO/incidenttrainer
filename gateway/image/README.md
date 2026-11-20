# The gateway image

One small server for all games (t4g.micro, ~$7/month + ~$0.80 for its 8 GB disk), built
from this image. The gateway has no internet, so everything it runs is baked in here.

| What | Where in the image |
| --- | --- |
| Ubuntu 24.04 arm64, Ubuntu's nginx 1.24 | Canonical's image; security fixes come with the next image build |
| nginx site + includes | `/etc/nginx/conf.d/itr-gateway.conf`, `/etc/nginx/itr-auth.conf`, `/etc/nginx/itr-proxy-headers.conf` (from `gateway/nginx/`) |
| gateway-auth with its own Python 3.13 | `/opt/itr-gateway/venv` (uv 0.13.7), user `itr-gateway`, unit `itr-gateway-auth.service` (hardened, 400 MB cap) |
| What was built | `/opt/itr-gateway/BUILD` (platform commit, nginx version, time) |
| Settings | **not** in the image: user data writes `/etc/itr-gateway.env` at boot (`ITR_TABLE`, `ITR_LAB_BUCKET`, `ITR_PUBLIC_ORIGIN`, `AWS_REGION`) |

No snaps (the SSM agent is removed: no SSM endpoint), no apt timers, journald capped at 300 MB.

## Build and deploy

`.github/workflows/gateway-image.yml`, on a merge to `main` that changes `gateway/src`,
`gateway/nginx`, `gateway/image`, `pyproject.toml` or `uv.lock` (or by hand: Actions →
gateway-image → Run workflow):

1. Packer builds on a temporary t4g.micro in the **default VPC** (it has internet; ~10 min,
   under 1 cent), tagged `cost=image-build`.
2. The AMI id goes to SSM `/itr/gateway/ami` (Terraform creates the gateway from it).
3. `deploy.sh` swaps the running gateway's root disk for one made from the new image
   (`create-replace-root-volume-task`): same instance, private IP and DNS name, so
   CloudFront needs no change; one reboot, about 1-2 minutes when `/s/*` doesn't answer.
   **Don't merge gateway changes while someone is playing.** It then checks through
   CloudFront that the gateway answers again.
4. `prune.sh` keeps the newest 2 gateway images (~$0.10/month each).

The build uses the platform repo's apply role in the `main` environment.

## Look inside the running gateway

```bash
aws ec2-instance-connect ssh --instance-id <gateway id> --connection-type eice --os-user ubuntu
cat /opt/itr-gateway/BUILD
systemctl status itr-gateway-auth nginx --no-pager
journalctl -u itr-gateway-auth -n 50 --no-pager     # one JSON line per refusal / broker step
sudo tail -n 20 /var/log/nginx/itr-access.log         # one JSON line per request
```
