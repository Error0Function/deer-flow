# DeerFlow on Docker + K3s

This guide captures the deployment shape that has been validated in this repository on Windows + WSL2:

- DeerFlow app stack runs in Docker Compose
- `provisioner` runs in Docker Compose
- AIO sandbox instances run as Pods in k3s
- Windows-native `docker` and `kubectl` can be used for daily operations
- Portainer can optionally own the Docker stack and observe the Kubernetes side

## Architecture

```text
Windows browser / PowerShell
        |
        v
WSL2 Ubuntu
  |- Docker Engine
  |   |- frontend
  |   |- gateway
  |   |- langgraph
  |   |- nginx
  |   `- provisioner
  |
  `- k3s
      `- sandbox pods created by provisioner
```

This keeps DeerFlow itself easy to rebuild with Compose while preserving Pod-level isolation for sandbox execution.

## Prerequisites

- WSL2 Ubuntu with `systemd` enabled
- Docker Engine and Docker Compose plugin installed in WSL
- Single-node k3s running in WSL
- Valid `~/.kube/config` for the local k3s cluster
- Windows-native `docker`, `kubectl`, and optionally `helm`
- Project checked out inside WSL-accessible storage

For the Windows/WSL environment decisions and pitfalls, see [WSL_DOCKER_K3S_WINDOWS_SETUP.md](/E:/Code/AI/deer-flow/docs/WSL_DOCKER_K3S_WINDOWS_SETUP.md).

## Recommended config

In `config.yaml`, provisioner-backed AIO sandbox should look like this:

```yaml
sandbox:
  use: src.community.aio_sandbox:AioSandboxProvider
  provisioner_url: http://provisioner:8002
```

The validated default sandbox image is:

```text
ghcr.io/agent-infra/sandbox:latest
```

The Docker scripts automatically pass these values to the provisioner when sandbox mode is enabled:

- kubeconfig source path
- Kubernetes namespace
- container-visible K8s API server
- `host.docker.internal` as the backend-to-NodePort host

## First boot

Generate config files if needed:

```bash
make config
```

Fill in at least one working model and the required API keys in `.env` and `config.yaml`.

Pre-pull the sandbox image into both Docker and k3s/containerd:

```bash
make docker-init
```

On the validated WSL2 host, the k3s-backed sandbox path hits the k3s/containerd cache rather than re-pulling on every Pod start. A healthy cache hit usually shows up in Pod events as:

```text
Container image "ghcr.io/agent-infra/sandbox:latest" already present on machine
```

If you are reusing an existing checkout and do not care about previous LangGraph queue data, thread data, or old sandbox pods:

```bash
make docker-reset-state
```

Start the Docker stack:

```bash
make docker-start
```

Access DeerFlow at:

```text
http://127.0.0.1:2026
```

## What starts where

Docker Compose starts:

- `frontend`
- `gateway`
- `langgraph`
- `nginx`
- `provisioner` when `config.yaml` uses provisioner mode

k3s starts:

- the namespace used for sandbox resources
- sandbox Pods and Services created on demand by the provisioner

## Daily operations

Start:

```bash
make docker-start
```

Stop:

```bash
make docker-stop
```

Clear persisted runtime state:

```bash
make docker-reset-state
```

View logs:

```bash
make docker-logs
make docker-logs-gateway
docker logs deer-flow-langgraph --tail 100
kubectl get pods,svc -n deer-flow
kubectl describe pod -n deer-flow sandbox-<id>
```

## Fast Resume After Reboot

If the machine was shut down and you want to get back to the current working
state quickly, use this order.

### 1. Confirm the base host services are back

In Windows PowerShell:

```powershell
docker version
docker-compose version
kubectl get nodes -o wide
```

Expected:

- `docker version` should show the Windows client and the WSL Docker server
- `docker-compose version` should return `v5.1.0` or newer
- `kubectl get nodes -o wide` should show the local k3s node as `Ready`

If `docker` or `kubectl` fail, check whether WSL is running:

```powershell
wsl -l -v
```

### 2. Start the DeerFlow stack from WSL-backed scripts

Use the repo scripts for actual stack startup:

```bash
make docker-start
```

If you want a clean runtime state first:

```bash
make docker-reset-state
make docker-start
```

### 3. Verify the three frontend entrypoints

```text
http://127.0.0.1:2026/
http://127.0.0.1:2026/v2/
http://127.0.0.1:2026/v2-dev/
```

Current meaning:

- `/` = legacy frontend
- `/v2/` = stable Solid release build
- `/v2-dev/` = hot-reloading Solid dev build

### 4. Use the right CLI for the right job

Keep this distinction in mind on this machine:

- Windows-native `docker`, `docker-compose`, and `kubectl` are good for status
  checks and daily operations.
- For this repository's bind-mounted development stack, actual Compose rebuilds
  are more reliable from the WSL side because the Docker Engine lives in WSL and
  the repo is currently on a Windows-mounted path.

That means:

- okay from Windows:
  - `docker ps`
  - `docker logs ...`
  - `docker-compose version`
  - `kubectl get pods -A`
- prefer WSL-backed repo scripts for rebuild/start flows:
  - `make docker-start`
  - `make docker-stop`
  - `make docker-reset-state`

### 5. If the v2 routes look broken

Check the two v2 services and nginx first:

```bash
python scripts/dev_logs.py summary
make docker-logs-frontend-v2-dev
docker logs deer-flow-frontend-v2-release --tail 100
docker logs deer-flow-nginx --tail 100
```

The common healthy state is:

- `frontend-v2-dev` running and serving `/v2-dev/`
- `frontend-v2-release` running and serving `/v2/`
- `nginx` proxying all three frontends on port `2026`

## Verification checklist

Gateway health:

```bash
curl http://127.0.0.1:2026/health
```

Model list:

```bash
curl http://127.0.0.1:2026/api/models
```

Provisioner health from inside the stack:

```bash
docker exec deer-flow-provisioner curl -s http://localhost:8002/health
```

Sandbox resources in k3s:

```bash
kubectl get pods,svc -n deer-flow
```

## Known operational notes

- Old files under `backend/.langgraph_api` can block new messages by restoring stale LangGraph runs.
- Old files under `backend/.deer-flow/threads` can bring back thread-local sandbox data you no longer care about.
- `make docker-reset-state` is the fast way to factory-reset those runtime artifacts.
- On WSL2, DeerFlow does not require Windows to access sandbox NodePorts directly. The important path is container-to-host access via `host.docker.internal:<NodePort>`.
- The first sandbox Pod may take noticeable time to start because the sandbox image is large.

## When to prefer this deployment shape

Choose this layout when you want:

- Compose-based app rebuilds
- Kubernetes-backed sandbox isolation
- a lighter local setup than Docker Desktop + Kind
- easy migration to another machine by moving WSL, Docker, kubeconfig, and repo state

## Optional Portainer ownership

The script-based path remains the canonical CLI deployment path:

```bash
make docker-start
```

If you prefer Portainer to own the Docker stack instead of merely discovering it as an external stack:

1. Stop any existing script-managed DeerFlow stack first.
2. Deploy [portainer-stack-dev.yaml](/E:/Code/AI/deer-flow/docker/portainer-stack-dev.yaml) from Portainer as a new stack.
3. Provide these environment variables in Portainer when creating the stack:

```text
DEER_FLOW_REPO_ROOT=<repo-root-visible-inside-wsl>
DEER_FLOW_KUBECONFIG_SOURCE_PATH=<wsl-home>/.kube/config
DEER_FLOW_K8S_API_SERVER=https://host.docker.internal:6443
DEER_FLOW_K8S_NAMESPACE=deer-flow
DEER_FLOW_NODE_HOST=host.docker.internal
PNPM_STORE_PATH=<wsl-home>/.local/share/pnpm/store
DEER_FLOW_UV_CACHE_PATH=<wsl-home>/.cache/uv
```

Also provide the same model/provider secrets you would normally place in `.env`, for example:

```text
OPENAI_API_KEY=...
OPENAI_BASE_URL=...
TAVILY_API_KEY=...
```

This Portainer-specific stack file avoids repository-relative `env_file` usage so it can be owned by Portainer CE without replacing the existing script-driven path.

### Portainer and k3s

Portainer CE cannot manage this k3s cluster through `kubeconfig import`; the practical route is the official Portainer Kubernetes agent.

Install it with:

```bash
make portainer-agent-install
```

Check its status with:

```bash
make portainer-agent-status
```

On a given host, the Portainer Kubernetes agent is typically exposed to Docker
containers through the host bridge address plus the NodePort assigned to the
agent Service. Record the exact value in your local ignored notes if you need to
reuse it on the same machine.
