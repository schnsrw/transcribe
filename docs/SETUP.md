# Setup guide

End-to-end instructions for the four supported run modes.

| Run mode | When | Read |
|---|---|---|
| [Mac host (Apple Silicon)](#1-mac-host-dev-apple-silicon) | Daily M-series dev with Metal GPU | §1 |
| [Linux host](#2-linux-host-dev-cuda-or-cpu) | Linux workstation, prod-parity dev | §2 |
| [Docker (any platform)](#3-docker-dev-any-platform) | CI / cross-platform / "works everywhere" | §3 |
| [Production (CUDA + JWT)](#4-production-deployment) | Real deploy on a Linux GPU server | §4 |

Common reference sections at the bottom:

- [§5 Environment variables](#5-environment-variables)
- [§6 Docker Hub setup](#6-docker-hub-setup-for-publishing)
- [§7 GitHub environment setup](#7-github-environment-setup-for-the-release-pipeline)
- [§8 Troubleshooting](#8-troubleshooting)

---

## 1. Mac host dev (Apple Silicon)

**When to use.** You're on an M-series Mac (M1+) and want the fastest
local dev loop. Bypasses Docker (which can't expose Metal/ANE to
containers) and runs uvicorn against an in-repo venv that uses
mlx-whisper on the Apple GPU.

### 1.1 Prerequisites

| What | How |
|---|---|
| macOS 13+ | Built-in |
| Python ≥ 3.11 | `brew install python@3.12` (or pyenv) |
| Docker Desktop | Only needed if you'll also run `make dev-docker` for the demo container. Not required for dev-mac itself. |
| Free disk | ~3 GB for venv + model cache |

### 1.2 First-time setup

```bash
git clone git@github.com:schnsrw/transcribe.git casual-sst
cd casual-sst
cp .env.example .env       # optional — fill in HF_TOKEN, ADMIN_TOKEN, etc.
```

If your `python3` is older than 3.11 (macOS default is 3.9), point
`PYTHON` at the right interpreter the first time you run:

```bash
PYTHON=/opt/homebrew/bin/python3.12 make dev-mac
```

After the venv exists, just `make dev-mac` works.

### 1.3 What the first run does

```
[dev-mac] Creating venv at .venv-mac/
[dev-mac] Installing / updating deps...   (~3 minutes, ~2 GB)
[dev-mac] Casual-SST on http://localhost:8100
```

Then on the **first transcription request**, mlx-whisper downloads
the model (~250 MB for whisper-small, ~1.6 GB for large-v3-turbo)
into `~/.cache/huggingface/`. Subsequent runs reuse both.

### 1.4 Verify

```bash
# In another terminal:
curl -fsSL http://localhost:8100/docs >/dev/null && echo "API ✓"
```

Live test:

```bash
# Bring up the demo (nginx-served HTML) on :8180:
make dev-docker

# Open http://localhost:8180 in Chrome. Change WS host to
# ws://localhost:8100/ws (because dev-mac listens on :8100, not :8000).
# Click Transcribe and speak.
```

Or hit the WS directly with the live probe:

```bash
WS_URL=ws://localhost:8100/ws \
  .venv-mac/bin/python tests/live/probes/compare.py \
  tests/e2e/fixtures/hello-en.wav en
```

### 1.5 Hot reload

`--reload` is **off by default** in `dev-mac` — the MLX model has a
non-trivial Metal-kernel warm-up after each restart. Enable when
you're iterating on server code:

```bash
RELOAD=1 make dev-mac
```

### 1.6 Switching the model

Edit `config/dev-mac.yaml`:

```yaml
backends:
  mlx_whisper:
    path_or_hf_repo: mlx-community/whisper-small-mlx           # streaming default
    # path_or_hf_repo: mlx-community/whisper-large-v3-turbo-q4 # batch-quality
```

Or try the whisper.cpp + Metal backend:

```bash
make dev-mac-cpp
```

Same endpoint (`:8100`) but uses `config/dev-mac-cpp.yaml` with the
pywhispercpp backend. Faster cold start, known word-boundary issues
on STREAM mode (see ADR-013).

### 1.7 Clean up

```bash
make clean          # wipes .venv-mac/ and .venv-linux/
rm -rf ~/.cache/huggingface  # drops model cache too (optional)
```

---

## 2. Linux host dev (CUDA or CPU)

**When to use.** You're on a Linux workstation with or without an
NVIDIA GPU. Same backend as production (faster-whisper); the only
difference from `make prod` is no JWT enforcement and no Docker.

### 2.1 Prerequisites

| What | How |
|---|---|
| Linux (Ubuntu 22.04+ recommended) | — |
| Python ≥ 3.11 | `sudo apt install python3.11 python3.11-venv python3.11-dev` (or pyenv) |
| ffmpeg + libsndfile | `sudo apt install ffmpeg libsndfile1` |
| (optional) NVIDIA driver ≥ 535 | `nvidia-smi` must work |
| (optional) cuDNN 9 | Installed by `nvidia-cudnn-cuXX` packages |
| Free disk | ~2.5 GB |

### 2.2 Run

```bash
make dev-linux
```

Auto-detects CUDA via `nvidia-smi`. If present, faster-whisper uses
the GPU automatically (`device: auto` resolves to CUDA). If absent,
it falls back to CPU.

Listens on **`:8100`** (same as dev-mac, different config).

### 2.3 Verify

```bash
curl -fsSL http://localhost:8100/docs >/dev/null && echo "API ✓"

# If you have a GPU, confirm faster-whisper picked it up:
.venv-linux/bin/python -c "
from faster_whisper import WhisperModel
m = WhisperModel('tiny', device='auto')
print('device:', m.model.device)
"
```

### 2.4 Differences from prod

| | dev-linux | prod |
|---|---|---|
| Container? | host venv | Docker (Dockerfile.cuda) |
| JWT? | `bypass_auth: true` | enforced |
| Restart policy | `make dev-linux` only | `restart: unless-stopped` |
| Logs | stdout / `make logs` | `make prod-logs` (json-file driver) |

For end-to-end prod-parity testing on the same box, use §4.

---

## 3. Docker dev (any platform)

**When to use.** Cross-platform — Linux without a GPU, Windows via
WSL2, macOS when you don't want to manage a host venv. CI uses this
exact path.

### 3.1 Prerequisites

| What | How |
|---|---|
| Docker Engine ≥ 24, or Docker Desktop | https://docs.docker.com/get-docker/ |
| `docker compose` plugin | Bundled with modern Docker |
| Free disk | ~3 GB for image + model cache |

No host Python required.

### 3.2 Run

```bash
make dev-docker
```

This brings up two containers:

| Container | Port | Purpose |
|---|---|---|
| `casual-sst` | `:8000` | API + WS endpoint |
| `casual-sst-demo` | `:8180` | nginx serving `demo/` |

Open `http://localhost:8180` in Chrome and click *Transcribe*.

First run takes ~5 minutes (image build + dep install). Subsequent
runs start in seconds. The whisper model downloads on first
transcription into the `casual-sst-hf-cache` named volume, persisted
across `make stop` / `make dev-docker` cycles.

### 3.3 Verify

```bash
docker ps --filter name=casual                 # both healthy
docker logs casual-sst | tail -20              # Uvicorn running on :8000
make logs                                      # tail live
```

### 3.4 Stop / cleanup

```bash
make stop                                      # stop, keep model cache
docker compose -f compose.dev.yaml down -v     # also wipe the cache volume
```

### 3.5 Apple Silicon note

On M-series Macs, Docker uses Rosetta or the native arm64 Linux VM
under the hood — **it cannot pass Metal/ANE through to containers**.
This is fine for cross-platform CI / parity testing but slow for
real-time streaming work. Use `make dev-mac` for that. See ADR-013.

---

## 4. Production deployment

**When to use.** Linux server with an NVIDIA GPU. Designed for one-box
deploys behind a reverse proxy.

### 4.1 Server prerequisites

| Component | Min version | Check |
|---|---|---|
| Linux (Ubuntu 22.04 LTS recommended) | kernel 5.15+ | `uname -a` |
| Docker Engine | 24.0+ | `docker version` |
| NVIDIA driver | 535+ | `nvidia-smi` |
| NVIDIA Container Toolkit | 1.14+ | `nvidia-ctk --version` |
| Free GPU memory | ≥ 4 GB for large-v3-turbo | `nvidia-smi --query-gpu=memory.free --format=csv` |
| Free disk | ≥ 10 GB | model cache + image + logs |

Install the NVIDIA Container Toolkit if missing:

```bash
distribution=$(. /etc/os-release; echo $ID$VERSION_ID)
curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey | sudo gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
curl -fsSL https://nvidia.github.io/libnvidia-container/${distribution}/libnvidia-container.list \
  | sudo sed 's|deb https://|deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://|' \
  | sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list
sudo apt-get update && sudo apt-get install -y nvidia-container-toolkit
sudo nvidia-ctk runtime configure --runtime=docker
sudo systemctl restart docker

# Verify:
docker run --rm --gpus all nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi
```

### 4.2 First deploy

```bash
# 1. Clone:
git clone git@github.com:schnsrw/transcribe.git casual-sst
cd casual-sst

# 2. Fill in secrets:
cp .env.example .env
$EDITOR .env       # set ADMIN_TOKEN, JWT_*, HF_TOKEN, etc.

# 3. Either build locally:
make prod
# ... or pull the published image and just start the compose stack:
docker pull schnsrw/casual-sst:cuda-latest
docker compose -f compose.prod-cuda.yaml up -d --no-build
```

`make prod` brings up the container with:

- GPU access via `deploy.resources.reservations.devices`
- `config/prod.yaml` mounted (JWT enforced, `device: cuda`)
- HF model cache as the `casual-sst-hf-cache-prod` volume
- Logs to `./logs/` on the host (json-file driver, rotated)
- Healthcheck every 15 s
- `restart: unless-stopped`

### 4.3 Verify

```bash
# Service up?
make prod-logs                                   # tail logs
docker ps --filter name=casual-sst              # status

# Healthcheck:
docker inspect casual-sst --format='{{.State.Health.Status}}'    # → healthy

# GPU actually in use?
nvidia-smi                                       # casual-sst process listed

# Metrics:
curl http://localhost:8000/metrics | head -20
```

### 4.4 Behind a reverse proxy

Production-grade deploys put nginx / caddy / Traefik in front of port
8000 to terminate TLS and (optionally) gate `/metrics` and `/admin/*`
by source IP. The service itself does not terminate TLS.

Minimal nginx snippet:

```nginx
location /ws/ {
    proxy_pass http://127.0.0.1:8000;
    proxy_http_version 1.1;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";
    proxy_read_timeout 7200;       # WS keep-alive
}
location /metrics { allow 10.0.0.0/8; deny all; proxy_pass http://127.0.0.1:8000; }
location /admin   { allow 10.0.0.0/8; deny all; proxy_pass http://127.0.0.1:8000; }
location /        { proxy_pass http://127.0.0.1:8000; }
```

### 4.5 Updating to a new release

```bash
docker pull schnsrw/casual-sst:cuda-0.3.0       # or :cuda-latest
docker compose -f compose.prod-cuda.yaml up -d  # rolling restart
```

### 4.6 Operating

```bash
make prod-logs                                  # tail
make prod-stop                                  # graceful stop, keep cache
docker compose -f compose.prod-cuda.yaml down -v  # wipe cache too
```

Watch the admin dashboard at `http://<host>:8000/admin/` (requires
`ADMIN_TOKEN`).

---

## 5. Environment variables

Full list lives in [.env.example](../.env.example). Quick reference:

| Var | Purpose | Where |
|---|---|---|
| `CONFIG_PATH` | YAML config to load | all |
| `LOG_LEVEL` | DEBUG/INFO/WARNING/ERROR | all |
| `ADMIN_TOKEN` | Enables `/admin/*` | dev + prod (recommended) |
| `ASAP_PUB_KEYS_FOLDER`, `ASAP_PUB_KEYS_AUDS` | ASAP JWT (Jitsi style) | prod |
| `JWT_SECRET`, `JWT_ALGORITHM`, `JWT_AUDIENCE` | HMAC JWT fallback | prod |
| `HF_TOKEN` | Raises HF rate limit on model pulls | all (optional) |
| `WHISPER_MODEL` | Override Whisper model name | all (optional, also settable in YAML) |
| `LLM_BACKEND` | `openai` / `ollama` to enable `/api/summarize` | all (optional) |
| `OPENAI_API_KEY`, `OPENAI_BASE_URL`, `OPENAI_MODEL` | OpenAI-format LLM | when LLM enabled |
| `OLLAMA_HOST`, `OLLAMA_MODEL` | Ollama LLM | when LLM enabled |
| `RELOAD` | uvicorn auto-reload (dev only) | dev-mac, dev-linux |
| `PYTHON` | Interpreter for the host venv scripts | dev-mac, dev-linux |

---

## 6. Docker Hub setup (for publishing)

You only need this if you'll publish releases yourself; pulling
prebuilt images doesn't require an account.

1. Create a Docker Hub account at https://hub.docker.com.
2. Create a repository: https://hub.docker.com/repository/create
   - Name: `casual-sst` (or whatever you set `DOCKERHUB_REPO` to)
   - Visibility: public or private — both work with the workflow.
3. Generate an access token (NOT your password):
   https://hub.docker.com/settings/security → New access token.
   - Description: "casual-sst github actions"
   - Permissions: **Read, Write, Delete** on the `casual-sst` repo only
   - Save the token immediately — it's shown once.

You'll paste that token into the GitHub environment in §7.

---

## 7. GitHub environment setup (for the release pipeline)

The `release.yml` workflow needs Docker Hub credentials. We scope
them to a **GitHub environment** so PR builds + the test gate job
never see them.

1. **Create the environment**
   - Settings → Environments → New environment → name **`release`**.

2. **Add environment secrets** (Environment secrets section):
   | Name | Value |
   |---|---|
   | `DOCKERHUB_USERNAME` | your Docker Hub username (e.g. `schnsrw`) |
   | `DOCKERHUB_TOKEN` | the token from §6 |

3. **(Optional) protection rules**:
   - "Required reviewers" → require a manual approval before publishing.
   - "Deployment branch and tag policy" → restrict to tags matching
     `v*.*.*` so only proper semver tags trigger publishes.

4. **(Optional) repository-level variable** — only needed if your
   image name differs from `casual-sst`:
   - Settings → Secrets and variables → Actions → Variables → New repository variable
   - Name: `DOCKERHUB_REPO`, value: e.g. `my-org-casual-sst`.

### 7.1 Cutting a release

```bash
# Make sure main is clean and CI is green.
git checkout main && git pull
git tag -a v0.2.0 -m "v0.2.0"
git push origin v0.2.0
```

GitHub Actions will:
1. Run pytest in the test image (release gate).
2. Build + push **CPU image** (linux/amd64 + linux/arm64) as
   `<DOCKERHUB_USERNAME>/casual-sst:{0.2.0, 0.2, 0, latest}`.
3. Build + push **CUDA image** (linux/amd64) as
   `<DOCKERHUB_USERNAME>/casual-sst:{cuda-0.2.0, …, cuda-latest}`.

Check progress at `https://github.com/<owner>/transcribe/actions`.

### 7.2 Hotfix a bad release

A bad tag breaks `:latest`. To roll back:

```bash
# Re-tag the previous good commit:
git tag -d v0.2.0                          # local
git push --delete origin v0.2.0            # remote
# Push the older good tag as latest by re-running the workflow on it:
gh workflow run release.yml --ref v0.1.9
```

Or use the manual `workflow_dispatch` trigger from the Actions tab.

---

## 8. Troubleshooting

### "Address already in use" when starting dev-mac

`make dev-mac` listens on `:8100`. If the docker dev stack is also
running, it grabs `:8100` first (see compose.dev.yaml).

```bash
make stop                # stop docker dev stack
make dev-mac             # try again
```

### "Need Python ≥ 3.11"

macOS ships 3.9 at `/usr/bin/python3`. Install a newer one and
point `PYTHON` at it:

```bash
brew install python@3.12
PYTHON=/opt/homebrew/bin/python3.12 make dev-mac
```

### websockets handshake fails: "did not receive a valid HTTP response"

uvicorn 0.31 + websockets ≥ 14 are incompatible. Scripts pin
`--ws wsproto` to dodge this; if you're calling uvicorn yourself,
add `--ws wsproto`.

### CUDA container says "could not find a satisfiable cuDNN"

The Dockerfile.cuda base image is `nvidia/cuda:12.4.1-cudnn-runtime-ubuntu22.04`.
If your host driver only supports CUDA 11.x, override the build:

```bash
docker build -f Dockerfile.cuda --build-arg CUDA_TAG=11.8.0-cudnn8-runtime-ubuntu22.04 \
  -t casual-sst:cuda-cu11 .
```

### Mac dev streaming is slow on Hindi / non-English

Known — see ADR-013. The mlx-whisper per-call overhead is bigger on
non-English. Use `make dev-docker` to simulate streaming behaviour;
the prod path on CUDA does not have this issue.

### "503: LLM backend not configured" on `/api/summarize`

Set `LLM_BACKEND=openai` or `=ollama` in your `.env` and restart the
container. See §5 for the full set of LLM env vars.

### Admin dashboard returns 404

Set `ADMIN_TOKEN=...` in your `.env` and restart. Without a token
the portal is intentionally disabled.

### `make test` complains about a missing image

```bash
docker compose -f compose.test.yaml build
make test
```

### Where do logs live?

- **dev-mac / dev-linux**: stdout in the terminal that ran `make dev-*`.
- **dev-docker**: `make logs` or `docker logs casual-sst`.
- **prod**: `./logs/` on the host (json-file driver, rotated to ~5 × 25 MB).
- **All modes**: the in-process ring buffer is also available at
  `/admin/api/logs` when the admin portal is enabled.
