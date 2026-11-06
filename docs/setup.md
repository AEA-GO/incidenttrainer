# Developer setup

About 45 minutes. At the end you can run the tests, the website and the shop on your laptop, and sign in to AWS.

Everything runs in a Linux terminal. Windows users get Linux through WSL2; Linux users already have it. The steps assume Ubuntu 24.04. Mac is not tested.

## What you need

- 16 GB RAM recommended, 8 GB minimum (see [8 GB laptops](#8-gb-laptops))
- 4 CPU cores, 40 GB free disk
- Windows 10/11, or Ubuntu 24.04
- Your GitHub account added to the organisation, and an AWS user from the lead (Part 3)

## Part 1 — Windows only

1. **Install Ubuntu.** In PowerShell as administrator:
   ```
   wsl --install -d Ubuntu-24.04
   ```
   Restart when asked. Ubuntu opens and asks for a username and password. `sudo` will ask for this password.

2. **Check systemd** (Docker needs it). In Ubuntu:
   ```
   ps -p 1 -o comm=
   ```
   It should print `systemd`. If it doesn't, run `printf '[boot]\nsystemd=true\n' | sudo tee -a /etc/wsl.conf`, then `wsl --shutdown` in PowerShell, and reopen Ubuntu.

3. **8 GB laptops only: limit WSL's memory.** Create `C:\Users\<you>\.wslconfig`:
   ```
   [wsl2]
   memory=5GB
   swap=4GB
   ```
   Then run `wsl --shutdown`. On 16 GB or more, skip this step.

4. **VS Code:** install it on Windows and add the **WSL** extension (by Microsoft).

5. **Docker Desktop:** we use Docker inside Ubuntu instead. If you have Docker Desktop, either uninstall it, or keep it and turn off both of these:
   - Settings → Resources → WSL integration → Ubuntu
   - Settings → General → Start Docker Desktop when you sign in

From here on, everything happens in the Ubuntu terminal.

## Part 2 — Tools (everyone)

1. **Basics**
   ```
   sudo apt update && sudo apt install -y git make curl unzip
   ```

2. **Docker Engine**
   ```
   curl -fsSL https://get.docker.com | sh
   sudo usermod -aG docker $USER
   ```
   If the script warns that "docker" already exists, that's Docker Desktop's Windows copy; let the script continue.
   Then apply the group change: on WSL, run `wsl --shutdown` in PowerShell and reopen Ubuntu; on Linux, log out and back in.

3. **mise** (installs pinned versions of Node, Terraform and uv)
   ```
   curl https://mise.run | sh
   echo 'eval "$(~/.local/bin/mise activate bash)"' >> ~/.bashrc
   ```
   Reopen the terminal, then set the defaults you get outside the repos:
   ```
   mise use -g uv@latest node@24 terraform@1.17
   ```

   If Node or Terraform were installed another way before, remove that copy: delete its nvm/fnm lines from ~/.bashrc, or run sudo apt remove nodejs terraform.

4. **AWS CLI**
   ```
   cd /tmp
   curl -fsSL "https://awscli.amazonaws.com/awscli-exe-linux-$(uname -m).zip" -o awscliv2.zip
   unzip -q awscliv2.zip && sudo ./aws/install --update
   cd ~
   ```

5. **pre-commit** (runs checks before every commit)
   ```
   uv tool install pre-commit
   uv tool update-shell
   ```
   Reopen the terminal.

**Check**
```
docker run --rm hello-world
node -v; terraform -version; uv --version; aws --version; pre-commit --version
```
Expected: "Hello from Docker!" (without `sudo`), Node v24, Terraform v1.17, and aws-cli 2.32 or newer.

## Part 3 — Accounts

**GitHub SSH key**
```
ls ~/.ssh/id_ed25519.pub 2>/dev/null || ssh-keygen -t ed25519
cat ~/.ssh/id_ed25519.pub
```
Press Enter to accept the default file; a passphrase is optional. Copy the `ssh-ed25519 …` line to GitHub → Settings → SSH and GPG keys → New SSH key. The `.pub` file is safe to share; never share the one without `.pub`.

Check with `ssh -T git@github.com`. The first time, answer `yes` to the fingerprint question. You should see "Hi <you>! You've successfully authenticated".

**AWS**
1. The lead creates an IAM user for you and sends the sign-in link.
2. Sign in to the AWS console, set your password, and add an MFA device (IAM → your user → Security credentials).
3. In Ubuntu:
   ```
   aws configure set region eu-central-1
   aws login
   ```
   Follow the link it shows and sign in with your user and MFA.

Check with `aws sts get-caller-identity`; it should show your user. The sign-in expires after some hours: just run `aws login` again. Never create access keys for your user.

## Part 4 — Get the code

```
mkdir -p ~/IncTrainer && cd ~/IncTrainer
git clone git@github.com:<org>/incidenttrainer.git
cd incidenttrainer
mise trust && mise install
pre-commit install
```

Later, clone the private labs repo into the same folder and run the same
`mise trust && mise install` and `pre-commit install` in it, so you have
`~/IncTrainer/incidenttrainer` and `~/IncTrainer/incidenttrainer-labs`.

Keep repos inside Linux (`~/...`), never under `/mnt/c` (that's your
Windows C: drive; it's slow from Linux).

**Project packages** (libraries like FastAPI or React) install per folder:
- Python (`api/`, `agent/`, `engine/`): nothing to do. `uv run …`
  installs or updates the packages by itself before it runs.
- Website (`web/`): run `npm ci` once after cloning, and again after a
  pull that changed `package-lock.json`.

Open a repo with `code .` from Ubuntu; VS Code's bottom-left corner should
show "WSL: Ubuntu". If Python imports are underlined in red, choose the
project's Python once: Ctrl+Shift+P → "Python: Select Interpreter" →
`.venv/bin/python`.

## Part 5 — Final check

From the repo root:
```
./scripts/check-setup.sh
```
Fix every `FAIL`. A `WARN` is fine for now, for example AWS not signed in.

## Daily use

| Task | Command |
|---|---|
| Get the latest code | `git pull`, then `mise install` if `mise.toml` changed |
| New branch | `git switch -c feat/short-name` |
| Python packages | `uv sync` (in `api/`, `agent/` or `engine/`) |
| Run Python tests | `uv run pytest` |
| Run any Python command | `uv run python …` |
| Add a Python package | `uv add <name>`, then commit `pyproject.toml` and `uv.lock` |
| Website packages | `npm ci` (in `web/`) |
| Run the website | `npm run dev`, then open http://localhost:5173 |
| Add a website package | `npm install <name>`, then commit `package-lock.json` |
| Preview AWS changes | `aws login`, then `terraform plan` in `infra/envs/dev` |
| Run all checks by hand | `pre-commit run --all-files` |

Rules:
- Never `pip install` or `sudo npm`.
- Use `uv run …` instead of calling `python` directly.
- Never `terraform apply` from a laptop; CI applies after merge.

## Why each tool

| Tool | Why |
|---|---|
| WSL2 + Ubuntu | Same Linux as our servers, so commands and scripts match for everyone |
| VS Code + WSL extension | Editor on Windows, code and tools in Linux |
| git + SSH key | Code and pull requests, without passwords |
| make | Short names for long commands |
| Docker Engine | Runs the shop and its monitoring locally; same Docker as the AWS servers |
| mise | Same Node, Terraform and uv versions for everyone, from `mise.toml` |
| Node | Builds and runs the website |
| Terraform | Previews AWS changes before a pull request |
| AWS CLI | Signs in to AWS with MFA, with no stored keys |
| pre-commit | Lint and secret scanning before every commit |

Packer is not needed on laptops: server images are built in CI.

## 8 GB laptops

- On Windows, set `.wslconfig` (Part 1, step 3).
- Close browser tabs and other apps while a lab runs.
- Don't run the website and a lab at the same time.
- If it's still too slow, run labs on an AWS dev server instead (ask the lead).

## When versions change

| After a pull that changed… | Run |
|---|---|
| `mise.toml` | `mise install` |
| `uv.lock` | nothing; `uv run` handles it |
| `package-lock.json` | `npm ci` |
| `.pre-commit-config.yaml` | nothing; it updates on the next commit |
