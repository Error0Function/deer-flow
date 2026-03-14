# Development Pitfalls And Logging

This document records the real operational issues that surfaced while bringing DeerFlow up on the current local stack:

- Windows + WSL2
- Docker Engine in WSL
- k3s in WSL
- Portainer CE for Docker and Kubernetes visibility
- DeerFlow app stack in Docker Compose
- `provisioner` + AIO sandbox in k3s

It is meant to save future setup and development work from rediscovering the same failures.

## What Actually Bit Us

### Environment and control plane

- `Podman + Kind` was abandoned for this branch. It was technically possible, but the maintenance cost was too high for this project and machine.
- `WSL mirrored networking` looked attractive, but it was less stable than `NAT` for this stack. `localhost` and published container ports were less predictable than the plain NAT path.
- `systemd` inside WSL does not keep the WSL VM alive by itself. Background services can disappear when WSL idles out unless Windows keeps the distro alive.
- Windows-native `docker` and `kubectl` are worth keeping, but they still target the WSL Docker Engine and k3s cluster underneath.

### Portainer-specific pitfalls

- If DeerFlow is started with `docker-compose` outside Portainer, Portainer will discover the stack as external and mark it `limited`.
- To make Portainer truly own the stack, the stack must be created by Portainer itself.
- Portainer CE does not use the Business Edition `kubeconfig import` path for Kubernetes management. The practical route is the official Portainer Kubernetes agent.
- Portainer on WSL could not own the stack until its own container was recreated with the repo path and the WSL home path mounted into it.
- Portainer-managed stacks could not rely on repository-relative `env_file` mounts in this setup. The working solution was to pass required environment variables explicitly in the stack definition and in the Portainer Stack UI.

### DeerFlow runtime pitfalls

- Old files under `backend/.langgraph_api` can restore stale LangGraph runs and make new requests look hung.
- Old files under `backend/.deer-flow/threads` can resurrect sandbox state and thread-local artifacts you no longer care about.
- `./scripts/docker.sh reset-state` is the correct factory reset for local runtime state on this branch.
- If you call LangGraph directly, use the same shape the frontend uses. A bare `threads/.../runs/wait` call without the frontend-style `context.thread_id` and other context fields can fail in middleware and look like a runtime bug when it is actually an incomplete API payload.

### Logging and observability pitfalls

- The current Docker dev stack splits logs across two channels:
  - `frontend`, `gateway`, and `langgraph` redirect their process output into host log files under `logs/`.
  - `nginx` and `provisioner` log to container stdout/stderr, so `docker logs` is the right source there.
  - sandbox Pods live in k3s, so their logs only show up through `kubectl logs`.
- This means `docker logs` alone is not enough to debug DeerFlow.
- It also means Portainer alone is not enough to debug DeerFlow cleanly, because the most useful app logs are partly in mounted files, not just in container log streams.

## Current Log Map

### File-based logs

- [logs/frontend.log](/E:/Code/AI/deer-flow/logs/frontend.log)
- [logs/gateway.log](/E:/Code/AI/deer-flow/logs/gateway.log)
- [logs/langgraph.log](/E:/Code/AI/deer-flow/logs/langgraph.log)
- [logs/nginx.log](/E:/Code/AI/deer-flow/logs/nginx.log)
  - Mainly used by the non-Docker local `serve.sh` path, not the Docker stack.

### Container log streams

- `docker logs deer-flow-dev-nginx-1`
- `docker logs deer-flow-dev-provisioner-1`

### Kubernetes log streams

- `kubectl get pods,svc -n deer-flow`
- `kubectl logs -n deer-flow <sandbox-pod>`
- `kubectl describe pod -n deer-flow <sandbox-pod>`

## Assessment: Is The Existing Logging Enough?

Short answer: not for comfortable development and operations.

What is already good:

- `gateway`, `langgraph`, and `provisioner` do emit useful runtime information.
- LangGraph logs already include valuable metadata such as run IDs, thread IDs, assistant IDs, queue stats, and upstream model HTTP calls.
- k3s sandbox logs are accessible with normal Kubernetes tooling.

What is still weak:

- There is no single entrypoint that knows where each log really lives.
- The log transport is split between host files, Docker stdout, and Kubernetes stdout.
- Some middleware and memory-related code paths still relied on `print(...)`, which makes log format and source metadata inconsistent.
- The default `make docker-logs` path overstates how much it can show, because the most useful app services are writing to files instead of container stdout.

## Recommendation

Do not start with a full project-wide logging refactor.

The high-leverage order is:

1. Keep the current service-level logging model.
2. Standardize obvious `print(...)` hotspots to regular Python logging so they land in the same files with module names and levels.
3. Add a unified development log reader that understands all three sources:
   - file logs
   - Docker logs
   - Kubernetes logs
4. Keep a short operating guide so developers know where to look even without the helper tool.

That is enough to remove the “black box” feeling without forcing a risky, repo-wide logging redesign first.

## Unified Dev Log Tool

This branch now includes:

- [dev_logs.py](/E:/Code/AI/deer-flow/scripts/dev_logs.py)

Examples:

```bash
python scripts/dev_logs.py summary
python scripts/dev_logs.py paths
python scripts/dev_logs.py tail gateway --lines 120
python scripts/dev_logs.py tail provisioner --lines 120
python scripts/dev_logs.py tail sandbox --lines 120
python scripts/dev_logs.py events --lines 30
python scripts/dev_logs.py inspect-sandbox --sandbox-id sandbox-demo01
python scripts/dev_logs.py correlate --thread-id 00000000-0000-0000-0000-000000000001
python scripts/dev_logs.py inspect-sandbox --sandbox-id sandbox-demo01 --verbose
python scripts/dev_logs.py correlate --run-id 00000000-0000-0000-0000-000000000002 --verbose
python scripts/dev_logs.py errors --lines 200
```

Make shortcuts:

```bash
make docker-logs-events
make docker-logs-sandbox SANDBOX_ID=sandbox-demo01
make docker-logs-correlate THREAD_ID=00000000-0000-0000-0000-000000000001
```

What it does:

- `summary`
  - shows current file logs
  - shows running DeerFlow containers
  - shows current k3s sandbox resources
- `paths`
  - shows where each log source really lives
- `tail`
  - reads the correct source based on service type
- `events`
  - shows recent Kubernetes events for sandbox scheduling, image pull, probe, and service wiring issues
- `inspect-sandbox`
  - resolves one sandbox Pod and Service
  - default output is compact and safe for frequent use
  - `--verbose` expands to full `kubectl describe` output and longer Pod logs
  - also shows the mounted thread data directory when the Pod label still carries `thread-id`
- `correlate`
  - starts from any known `thread_id`, `run_id`, or `sandbox_id`
  - searches file logs, Docker logs, and live sandbox logs for matching lines
  - default output truncates noisy log lines into short excerpts to keep token usage down
  - `--verbose` prints the full matching lines
  - extracts related IDs seen on those lines so you can jump from a frontend thread to a LangGraph run or sandbox Pod quickly
  - shows whether the related thread directory and live sandbox resources still exist
- `errors`
  - scans recent logs across all sources for warnings and failures

## When A Manual Runbook Is Still Enough

A manual runbook is enough when:

- the stack is already healthy
- you only need to inspect one layer
- you already know whether the failure is in app code, reverse proxy, provisioner, or sandbox

It is not enough when:

- the request disappears somewhere between frontend, gateway, LangGraph, provisioner, and sandbox
- the question is “which system is failing?”
- you are changing multiple systems during development and want one predictable entrypoint

## Next Logging Improvements Worth Doing Later

These are worth doing later, but they are not required before more feature work:

- Add structured request correlation across gateway and LangGraph where practical.
- Expose sandbox ID and thread ID more consistently in provisioner logs.
- Revisit whether `frontend/gateway/langgraph` should continue redirecting to files, or whether their stdout should become the primary transport with file collection handled separately.
