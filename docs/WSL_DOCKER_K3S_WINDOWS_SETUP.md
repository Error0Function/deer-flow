# WSL2 + Docker Engine + K3s + Portainer Setup Notes

This document captures the environment work completed on 2026-03-14 for running DeerFlow on Windows with WSL2.

Important scope note:

- Up to this point, the work is still environment bootstrap and validation.
- DeerFlow itself has not yet been deployed onto this new stack.
- The focus so far has been making the host environment reproducible, reachable, and operable from Windows.

## Final Baseline

The current target environment is:

- Windows host
- Ubuntu 24.04 LTS on WSL2
- Docker Engine inside WSL
- K3s single-node inside WSL
- Portainer CE inside Docker
- NAT networking for WSL, not Mirrored
- Windows-native `docker` and `kubectl` clients

The intended responsibility split is:

- Docker Engine / Compose:
  - run DeerFlow application services
  - run Portainer
- K3s:
  - run provisioner-managed sandbox workloads
- Portainer:
  - container management UI
- Windows native CLI:
  - `docker`
  - `kubectl`
  - `helm`

## Key Conclusion

For this machine and this project, `WSL NAT` is more reliable than `WSL Mirrored`.

Mirrored mode looked attractive at first, but in practice it introduced extra instability:

- inconsistent Windows-to-WSL access for container-published ports
- extra Hyper-V firewall handling
- confusing localhost behavior
- more moving parts while K3s was also trying to come up cleanly

After switching back to NAT and explicitly enabling localhost forwarding, Portainer became reachable again from Windows.

## Why Portainer Kept "Randomly" Going Offline

The root cause was not Portainer itself.

WSL lifecycle behavior matters here:

- `systemd` services do not keep the WSL instance alive by themselves
- Docker, K3s, and Portainer can be healthy inside WSL
- but if the WSL instance idles out and stops, all externally visible ports disappear with it

This means "Portainer is unreachable" can actually mean:

- WSL stopped
- therefore Docker stopped
- therefore Portainer disappeared

This was verified repeatedly during testing by checking `wsl -l -v` after idle periods.

## Current Host-Side Behavior

The environment currently uses a Windows-side keepalive/bootstrap flow so that WSL stays available when needed.

Machine-specific helper scripts and startup hooks should stay in a local ignored
notes folder rather than in tracked documentation. On a fresh machine, recreate
equivalents of:

- a Windows startup hook that launches the keepalive/bootstrap step
- a WSL keepalive start helper
- a WSL keepalive stop helper
- an optional Portainer launcher shortcut

What they do:

- start WSL on demand
- keep a Windows-side `wsl.exe` process alive
- allow Docker/K3s/Portainer to remain reachable from Windows

This is not a DeerFlow-specific requirement.
It is a WSL lifecycle workaround.

## Networking Decisions

### Chosen

Use:

- `networkingMode=NAT`
- `localhostForwarding=true`

Current local `.wslconfig` shape:

```ini
[wsl2]
memory=8589934592
swap=17179869184
networkingMode=NAT
localhostForwarding=true
vmIdleTimeout=10800000

[experimental]
sparseVhd=true
autoMemoryReclaim=DropCache
hostAddressLoopback=true
bestEffortDnsParsing=true
```

### Rejected

Do not use Mirrored mode as the default for this setup unless there is a specific reason to revisit it.

Reasons:

- Portainer access was less reliable
- Hyper-V firewall workarounds were needed
- Kubernetes and container exposure became harder to reason about
- overall maintenance cost was higher

## WSL Distribution Choice

Use Ubuntu 24.04 LTS.

Why:

- Docker installation path is straightforward
- K3s setup and troubleshooting are well documented
- WSL + systemd guidance is common and predictable

There was no practical benefit found in trying to optimize this with a "lighter" distro at this stage.

## Installed Components Inside WSL

The WSL Ubuntu environment now contains:

- Docker Engine
- Docker Compose plugin
- Helm
- K3s
- OpenSSH server

Enabled services:

- `docker.service`
- `k3s.service`
- `ssh.service`

Container restart policy:

- Portainer uses Docker restart policy so it comes back with Docker

## DNS Fix Applied in WSL

K3s initially failed to pull images because WSL-generated DNS was not reliable enough for this setup.

The fix was:

- disable auto-generated `resolv.conf`
- write static resolvers manually

Current WSL-side network config:

```ini
[boot]
systemd=true

[user]
default=<your-wsl-user>

[network]
generateResolvConf=false
```

Manual `/etc/resolv.conf` was set to public resolvers.

This was necessary because K3s system pods were getting stuck on image pulls before DNS was corrected.

## Portainer Exposure

Current stable Windows-side endpoints are:

- `https://127.0.0.1:19443`
- `http://127.0.0.1:19000`

Do not treat `9443` as the canonical external port on this machine.

Internally, Portainer still exposes its normal container ports, but the reliable Windows-facing mapping is:

- host `19000` -> container `9000`
- host `19443` -> container `9443`

Use the helper command:

```powershell
portainer
```

Or open:

```text
https://127.0.0.1:19443
```

## Windows-Native CLI Decisions

### Docker

The final goal was not "fake native via `wsl docker`", but actual Windows-native `docker.exe`.

Current result:

- `docker` is Windows-native
- it talks to Docker Engine inside WSL over SSH
- the default Docker context is `deer-wsl`

Important detail:

- this is native Windows CLI behavior
- but the engine still lives in WSL

That is the intended design.

### kubectl

`kubectl` is now a Windows-native binary as well.

Windows kubeconfig should live at:

- `%USERPROFILE%\\.kube\\config`

The config was copied from K3s and adjusted for Windows-side access.

One validated externally reachable API shape is:

- `https://127.0.0.1:6444`

Not:

- `https://127.0.0.1:6443`

That distinction matters on this machine.

### docker-compose

Use the native Windows `docker-compose` CLI installed via WinGet.

## Verified Working Commands

The following were verified after the environment changes:

```powershell
docker version
docker ps
docker context ls
kubectl get nodes -o wide
kubectl config current-context
docker-compose version
portainer
```

Expected behavior:

- `docker` shows both client and server versions
- `docker ps` shows the `portainer` container
- `kubectl get nodes -o wide` shows `deer-wsl` as `Ready`

## K3s Notes

K3s is installed and working, but this does not mean DeerFlow sandbox integration is complete yet.

At this point we know:

- K3s is up
- Windows-native `kubectl` can reach it
- node status is healthy

This does not yet guarantee:

- DeerFlow provisioner integration
- AIO sandbox end-to-end execution
- DeerFlow application routing through provisioner

Those still belong to the next deployment phase.

## What Was Not Done Yet

The following work still remains:

- move DeerFlow off the old Podman/Kind path
- define final Docker Compose layout for DeerFlow services
- wire DeerFlow provisioner to K3s
- validate AIO sandbox creation
- verify frontend -> gateway -> langgraph -> model -> sandbox end-to-end
- write project-specific runbooks for DeerFlow services on this stack

## Rebuild Checklist For a New Machine

When rebuilding on a future Windows machine, use this order:

1. Install WSL2 and Ubuntu 24.04 LTS.
2. Enable `systemd` in WSL.
3. Install Docker Engine in WSL.
4. Install K3s in WSL.
5. Fix WSL DNS if K3s image pulls fail.
6. Set WSL networking to NAT with localhost forwarding.
7. Install Portainer in Docker with host-side ports `19000` and `19443`.
8. Install Windows-native `docker` and `kubectl`.
9. Configure Windows-native `docker` to use SSH context into WSL.
10. Copy K3s kubeconfig to Windows and adjust it for the Windows-reachable API endpoint.
11. Add the Windows-side startup/bootstrap hook so WSL does not silently disappear when idle.
12. Only after all of that, begin DeerFlow deployment work.

Keep exact machine-specific values such as:

- the concrete Windows startup script path
- the concrete WSL helper script paths
- the exact local username
- the exact kubeconfig copy destination

in an ignored local notes file instead of tracked docs.

## Operator Reminder

If the UI or CLI suddenly cannot reach services, check this first:

```powershell
wsl -l -v
```

If Ubuntu is `Stopped`, the first problem is not DeerFlow.
The first problem is that the WSL environment is gone.
