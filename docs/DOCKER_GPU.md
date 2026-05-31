# Docker + GPU on Linux

Short answer: **yes, Docker on Linux can use NVIDIA GPUs.** It's how
production Whisper / Casual-SST is meant to run — host driver + NVIDIA
Container Toolkit + a CUDA-based image.

This document covers driver install, toolkit install, verification,
and the common pitfalls. It assumes Ubuntu 22.04 / 24.04. RHEL /
Debian work similarly; only the package manager invocations differ.

> **Mac users:** Docker on macOS **cannot** pass through Metal/ANE to
> Linux containers. See [ADR-013](DECISIONS.md) — use `make dev-mac`
> (host venv with MLX) instead. This doc applies to Linux only.
> **Windows users:** Same path as Linux, but via WSL2. Follow this
> doc, then read NVIDIA's [WSL2 supplement](https://docs.nvidia.com/cuda/wsl-user-guide/).

---

## What gets passed through

| Docker host OS | GPU passthrough? | How |
|---|---|---|
| **Linux** (bare metal / VM) | **Yes** | `nvidia-container-toolkit` + `--gpus` (or compose `deploy.resources.reservations.devices`) |
| **Linux on WSL2 (Windows host)** | **Yes** | Same; NVIDIA driver lives on Windows side |
| **macOS** | No | Docker runs in a Linux VM that can't see Metal |
| **Windows native (no WSL2)** | No | Use WSL2 |

Once the toolkit is installed, a container with the right CUDA base
image sees the GPU as if it were a bare-metal process — `nvidia-smi`
works, ctranslate2/torch/faster-whisper find the device automatically.

---

## 1. Host prerequisites

| Component | Min version | Why |
|---|---|---|
| Linux kernel | 5.15+ | Driver compatibility |
| NVIDIA GPU | Compute capability 7.0+ (Volta or newer) | CUDA 12.x baseline; older GPUs need older base images |
| NVIDIA driver | 535+ | CUDA 12.4 runtime needs 550+ for full features but 535 works |
| Docker Engine | 24.0+ | Earlier versions miss the `--gpus` flag and the toolkit's CDI mode |
| Free disk | ~15 GB | Driver + toolkit + casual-sst CUDA image (~4 GB) + model cache |

### 1.1 Check what's already there

```bash
# Kernel + distribution
uname -a
lsb_release -a 2>/dev/null

# Is there an NVIDIA GPU?
lspci | grep -i nvidia

# Is the driver installed and the GPU visible?
nvidia-smi

# Docker version
docker version --format '{{.Server.Version}}'
```

If `nvidia-smi` returns a table with your GPU, **skip to §2**. If not,
continue to §1.2.

### 1.2 Install the NVIDIA driver (if missing)

Ubuntu's recommended driver is usually a safe choice:

```bash
sudo apt-get update
sudo ubuntu-drivers devices                  # lists candidates
sudo ubuntu-drivers autoinstall              # picks the recommended one
sudo reboot
```

After reboot, `nvidia-smi` should print a table. The first column of
each row shows your GPU; the top-right shows the **driver version**
and the **maximum CUDA version** the driver supports. **Anything
≥ 535** is fine for Casual-SST's CUDA 12.4 image.

For air-gapped or specific-version installs, use NVIDIA's official
`.run` installer from https://www.nvidia.com/Download/index.aspx —
out of scope here.

---

## 2. Install the NVIDIA Container Toolkit

The toolkit is what lets Docker expose `/dev/nvidia*` to containers
and inject the CUDA / cuDNN libraries from the host. Without it,
`--gpus` and `deploy.resources.reservations.devices` do nothing.

```bash
# Add the toolkit's apt repo (Ubuntu / Debian).
curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey \
    | sudo gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
curl -fsSL https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list \
    | sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' \
    | sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list

sudo apt-get update
sudo apt-get install -y nvidia-container-toolkit

# Configure Docker to use the runtime + restart the daemon.
sudo nvidia-ctk runtime configure --runtime=docker
sudo systemctl restart docker

# Verify the runtime is registered:
docker info | grep -i runtime
# → Runtimes: io.containerd.runc.v2 nvidia runc
```

For RHEL / CentOS / Fedora, replace the apt section with `dnf` or
`yum` from the NVIDIA docs:
<https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html>.

### 2.1 Smoke-test GPU passthrough

```bash
# nvidia-smi inside a vanilla CUDA container — should match host output.
docker run --rm --gpus all nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi
```

If you see your GPU listed: **passthrough works.** If you get
`could not select device driver "" with capabilities: [[gpu]]` or
`unknown or invalid runtime name`, the toolkit isn't wired up — see
§5 troubleshooting.

---

## 3. Running Casual-SST on the GPU

Two ways: compose (recommended) or a raw `docker run`.

### 3.1 Via compose (production path)

The repo ships `compose.prod-cuda.yaml` which uses the modern compose
GPU syntax:

```yaml
services:
  casual-sst:
    build:
      context: .
      dockerfile: Dockerfile.cuda
    image: casual-sst:cuda
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: all                 # all GPUs visible to the host
              capabilities: [gpu]
```

To run:

```bash
make prod                                # build locally + start
# or pull a prebuilt release image:
docker pull schnsrw/casual-sst:cuda-latest
docker compose -f compose.prod-cuda.yaml up -d --no-build
```

Pin to specific GPUs by ordinal instead of `count: all`:

```yaml
            - driver: nvidia
              device_ids: ['0', '1']     # only GPU 0 and 1
              capabilities: [gpu]
```

### 3.2 Via `docker run` (one-off testing)

```bash
docker run --rm -it --gpus all \
    -p 8000:8000 \
    -e CONFIG_PATH=/app/config/prod.yaml \
    -e ADMIN_TOKEN="$ADMIN_TOKEN" \
    -v $PWD/.env:/app/.env:ro \
    schnsrw/casual-sst:cuda-latest

# Or restrict to one GPU:
docker run --rm --gpus '"device=0"' ...
docker run --rm --gpus '"device=GPU-d4e16e15-..."' ...   # by UUID
```

### 3.3 Verify Casual-SST actually used the GPU

```bash
# Open another terminal on the host and watch GPU usage:
watch -n 1 nvidia-smi

# Send a transcribe request from a third terminal:
docker exec casual-sst python -c "
from faster_whisper import WhisperModel
m = WhisperModel('tiny', device='cuda', compute_type='int8_float16')
print('faster-whisper sees device:', m.model.device)
"
```

You should see the casual-sst Python process listed under "Processes"
in `nvidia-smi` with non-zero GPU memory.

Alternatively, hit the health endpoint:

```bash
curl http://localhost:8000/health
# {"ok": true, "backends": {"whisper_turbo": "ok"}, ...}
```

---

## 4. Pitfalls (real, not hypothetical)

### 4.1 Driver version too old for the image's CUDA

`nvidia/cuda:12.4.1-cudnn-runtime` needs driver ≥ 550 for full
feature parity but works with **535+** for inference workloads.
**Below 535** the container will start but `cuDNN init failed` shows
up on first transcribe.

Fix: either upgrade the driver (`sudo ubuntu-drivers autoinstall`)
or build a lower-CUDA variant:

```bash
docker build -f Dockerfile.cuda \
    --build-arg CUDA_TAG=11.8.0-cudnn8-runtime-ubuntu22.04 \
    -t casual-sst:cuda-cu11 .
```

### 4.2 Toolkit installed but `docker run --gpus all` still fails

You forgot the daemon restart, or the runtime isn't configured:

```bash
sudo nvidia-ctk runtime configure --runtime=docker
sudo systemctl restart docker
docker info | grep -i nvidia       # should show "nvidia" in Runtimes
```

If it still fails, your Docker is older than 19.03 (the `--gpus` flag
was added then). Upgrade.

### 4.3 Multiple GPUs but Casual-SST grabs all of them

Default compose grabs **every** visible GPU because of `count: all`.
For a shared box you almost certainly want `device_ids` instead. Or
set `NVIDIA_VISIBLE_DEVICES=0` at the daemon level for the whole
container.

### 4.4 WSL2 specifics

On Windows + WSL2:

- Install the NVIDIA driver on **Windows**, not inside WSL.
- Install Docker Engine inside WSL2 (or use Docker Desktop's WSL2
  backend).
- Install `nvidia-container-toolkit` inside the WSL2 distro (same
  apt commands as §2).
- `nvidia-smi` works inside WSL2 *only* on a Windows host with
  driver ≥ 510 + NVIDIA's WSL CUDA support.

The Casual-SST image is identical; nothing in the Dockerfile needs
to change for WSL2.

### 4.5 "No space left on device" during image build

The CUDA image is ~4 GB and the build layer cache can balloon. Clean
periodically:

```bash
docker builder prune -af               # nuke build cache
docker system df                       # see what's eating disk
docker image prune -af                 # remove untagged images
```

### 4.6 Docker Desktop on Linux — not the same path

Docker Desktop for Linux *also* virtualizes the engine in a VM,
similarly to Mac. GPU passthrough is more limited there. For
production GPU deploys use **Docker Engine** (the `docker.io` /
`docker-ce` apt package), not Docker Desktop.

---

## 5. Troubleshooting commands

```bash
# Driver visible?
nvidia-smi
nvidia-smi --query-gpu=index,name,driver_version,cuda_version --format=csv

# Toolkit installed?
nvidia-ctk --version
dpkg -l | grep nvidia-container-toolkit

# Docker runtime configured?
docker info | grep -i runtime
cat /etc/docker/daemon.json

# Can we see /dev/nvidia* from inside a container?
docker run --rm --gpus all alpine ls /dev/nvidia*

# Does our actual CUDA image start?
docker run --rm --gpus all schnsrw/casual-sst:cuda-latest nvidia-smi

# What does compose think the GPU config is?
docker compose -f compose.prod-cuda.yaml config | yq '.services."casual-sst".deploy'

# Inside the running container — is faster-whisper on CUDA?
docker exec casual-sst python -c "import torch; print('cuda:', torch.cuda.is_available(), torch.cuda.device_count())"

# Live GPU usage while traffic flows:
watch -n 1 nvidia-smi
```

---

## 6. Production sizing

Reference numbers for `whisper-large-v3-turbo` (the production model):

| GPU | VRAM used | Approx RTF on continuous streams | Concurrent meetings (rough) |
|---|---|---|---|
| T4 (16 GB) | ~3.5 GB | 0.3 | 8-10 |
| L4 (24 GB) | ~3.5 GB | 0.15 | 15-20 |
| A10 (24 GB) | ~3.5 GB | 0.12 | 20-25 |
| A100 (40/80 GB) | ~3.5 GB | 0.05 | 50+ |
| RTX 4090 (24 GB) | ~3.5 GB | 0.08 | 30+ |

Real numbers depend heavily on chunk size, beam size, and whether you
batch across participants. Treat the table as order-of-magnitude.
Whisper-turbo is bottlenecked by the encoder pass — once you have
GPU saturation, more VRAM doesn't help; more cores or faster cores do.

---

## See also

- [SETUP.md §4 — Production deployment](SETUP.md#4-production-deployment)
- [ADR-013 — Dual-runtime decision](DECISIONS.md)
- NVIDIA Container Toolkit official docs:
  <https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/>
- Compose GPU access reference:
  <https://docs.docker.com/compose/how-tos/gpu-support/>
