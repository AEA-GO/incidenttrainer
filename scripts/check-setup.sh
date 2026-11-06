#!/usr/bin/env bash
# Checks a developer laptop for IncidentTrainer. Read-only: it changes nothing.
# Usage: ./scripts/check-setup.sh   (from the repo root)
set -u

n_ok=0; n_warn=0; n_fail=0
ok()   { printf '  ok    %s\n' "$1"; n_ok=$((n_ok + 1)); }
warn() { printf '  WARN  %s\n' "$1"; n_warn=$((n_warn + 1)); }
fail() { printf '  FAIL  %s\n' "$1"; n_fail=$((n_fail + 1)); }

# true if version $1 >= $2
version_ge() { [ "$(printf '%s\n%s\n' "$1" "$2" | sort -V | head -n1)" = "$2" ]; }

echo "System"
if grep -qi microsoft /proc/version; then ok "WSL2 ($(uname -m))"; else ok "native Linux ($(uname -m))"; fi
if [ "$(ps -p 1 -o comm=)" = "systemd" ]; then ok "systemd is running"
else fail "systemd is not running (setup.md, Part 1, step 2)"; fi

mem_gb=$(awk '/MemTotal/ {printf "%d", $2 / 1048576}' /proc/meminfo)
if [ "$mem_gb" -ge 7 ]; then ok "memory: ${mem_gb} GB"
elif [ "$mem_gb" -ge 4 ]; then warn "memory: ${mem_gb} GB, tight for a full lab (see '8 GB laptops')"
else fail "memory: ${mem_gb} GB, too little for a lab"; fi

cpus=$(nproc)
if [ "$cpus" -ge 4 ]; then ok "CPU cores: $cpus"; else warn "CPU cores: $cpus (4 recommended)"; fi

disk_gb=$(df -BG --output=avail "$HOME" | tail -n1 | tr -dc '0-9')
if [ "$disk_gb" -ge 40 ]; then ok "free disk: ${disk_gb} GB"; else warn "free disk: ${disk_gb} GB (40 GB recommended)"; fi

echo "Docker"
docker_path=$(command -v docker || true)
if [ -z "$docker_path" ]; then
  fail "docker is not installed (Part 2, step 2)"
else
  case "$(readlink -f "$docker_path")" in
    /mnt/*) fail "docker comes from Windows (Docker Desktop): $docker_path" ;;
    *)      ok "docker command: $docker_path" ;;
  esac
  if docker info >/dev/null 2>&1; then
    case "$(docker info --format '{{.OperatingSystem}}')" in
      *"Docker Desktop"*) fail "the Docker daemon is Docker Desktop; use Docker Engine in Ubuntu" ;;
      *) ok "Docker Engine $(docker info --format '{{.ServerVersion}}') works without sudo" ;;
    esac
  else
    fail "docker can't reach the daemon (not running, or not in the docker group yet)"
  fi
  if docker compose version >/dev/null 2>&1; then ok "docker compose"; else fail "docker compose plugin is missing"; fi
fi

echo "Tools"
# name, minimum version, command that prints the version
check_tool() {
  local name=$1 min=$2; shift 2
  if ! command -v "$name" >/dev/null; then fail "$name: not installed"; return; fi
  local out ver
  out=$("$@" 2>&1 | head -n1)
  case "$out" in
    *"No version is set"*) fail "$name: mise has no version set here (run 'mise install')"; return ;;
  esac
  ver=$(grep -oE '[0-9]+\.[0-9]+(\.[0-9]+)?' <<<"$out" | head -n1)
  if [ -z "$ver" ]; then warn "$name: could not read the version"
  elif version_ge "$ver" "$min"; then ok "$name $ver"
  else fail "$name $ver is older than $min"; fi
}
for t in curl unzip; do
  if command -v "$t" >/dev/null; then ok "$t"; else fail "$t: not installed"; fi
done
check_tool git        2.40   git --version
check_tool make       4.0    make --version
check_tool mise       2025.1 mise --version
check_tool uv         0.13   uv --version
check_tool node       24.0   node --version
check_tool terraform  1.17   terraform -version
check_tool aws        2.32   aws --version
check_tool pre-commit 4.0    pre-commit --version

for t in node terraform uv; do
  p=$(command -v "$t" || true)
  case "$p" in
    ""|*/mise/*) ;;
    *) warn "$t comes from $p, not mise (remove the nvm/fnm/apt copy)" ;;
  esac
done

if uv python find 3.13 >/dev/null 2>&1; then ok "Python 3.13 available to uv"
else warn "Python 3.13 not downloaded yet (uv fetches it on the first 'uv sync')"; fi

echo "Repo"
if root=$(git rev-parse --show-toplevel 2>/dev/null); then
  case "$root" in
    /mnt/*) fail "repo is on the Windows disk ($root); clone it under ~/IncTrainer" ;;
    *)      ok "repo location: $root" ;;
  esac
  if [ -f "$(git rev-parse --git-path hooks/pre-commit)" ]; then ok "pre-commit hook installed"
  else warn "pre-commit hook missing (run 'pre-commit install')"; fi
else
  warn "not inside a git repo; run this from the repo root"
fi

echo "Accounts"
gh_out=$(ssh -T -o BatchMode=yes -o ConnectTimeout=5 git@github.com 2>&1 || true)
case "$gh_out" in
  *"successfully authenticated"*) ok "GitHub SSH key works" ;;
  *) warn "GitHub SSH: $(head -n1 <<<"$gh_out") (see Part 3)" ;;
esac
if aws sts get-caller-identity >/dev/null 2>&1; then ok "AWS: signed in"
else warn "AWS: not signed in (run 'aws login' when you need AWS)"; fi

echo
echo "$n_ok ok, $n_warn warnings, $n_fail failures"
[ "$n_fail" -eq 0 ]
