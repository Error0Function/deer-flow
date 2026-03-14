#!/usr/bin/env python
"""Unified local diagnostics for DeerFlow's Docker + k3s dev stack.

This script exists because DeerFlow's useful logs are split across three places:

1. Host file logs under ``logs/`` for ``frontend``, ``gateway``, and ``langgraph``
2. Docker stdout/stderr for ``nginx`` and ``provisioner``
3. Kubernetes Pod logs and events for provisioner-managed sandbox Pods

The goal is not to replace Docker or kubectl. It is to provide a small, stable
entrypoint that knows which source to query for a given troubleshooting task.

Common usage:

    python scripts/dev_logs.py summary
    python scripts/dev_logs.py tail gateway --lines 120
    python scripts/dev_logs.py inspect-sandbox --sandbox-id <id>
    python scripts/dev_logs.py correlate --run-id <id>
    python scripts/dev_logs.py correlate --thread-id <id> --verbose

Default output is intentionally compact so frequent checks stay readable and
cheap. Add ``--verbose`` on correlation or sandbox inspection commands when you
need the full raw context.
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import time
from collections import deque
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Iterable


REPO_ROOT = Path(__file__).resolve().parents[1]
LOG_DIR = REPO_ROOT / "logs"
THREADS_DIR = REPO_ROOT / "backend" / ".deer-flow" / "threads"
DEFAULT_NAMESPACE = os.environ.get("DEER_FLOW_K8S_NAMESPACE", "deer-flow")
DEFAULT_WSL_DISTRO = os.environ.get("DEER_FLOW_WSL_DISTRO", "Ubuntu")

FILE_SOURCES = {
    "frontend": LOG_DIR / "frontend.log",
    "frontend-v2-dev": LOG_DIR / "frontend-v2-dev.log",
    "gateway": LOG_DIR / "gateway.log",
    "langgraph": LOG_DIR / "langgraph.log",
    "local-nginx": LOG_DIR / "nginx.log",
}

DOCKER_SOURCES = {
    "nginx": "nginx",
    "provisioner": "provisioner",
    "frontend-v2-release": "frontend-v2-release",
}

ERROR_PATTERN = re.compile(
    r"(?i)\b(error|exception|traceback|failed|warning|refused|unhealthy)\b"
)
# Different layers format identifiers differently, so correlation accepts both
# strict runtime IDs and looser path/log variants.
THREAD_ID_PATTERNS = [
    re.compile(
        r"\bthread_id(?:=|['\": ]+)(?P<id>[0-9a-f]{8}-[0-9a-f-]{27,})\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bthread_id(?:=|['\": ]+)(?P<id>[A-Za-z0-9._-]{6,})\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bfor thread ['\"](?P<id>[A-Za-z0-9._-]+)['\"]",
        re.IGNORECASE,
    ),
    re.compile(
        r"/threads/(?P<id>[A-Za-z0-9._-]+)/user-data",
        re.IGNORECASE,
    ),
]
RUN_ID_RE = re.compile(
    r"\brun_id(?:=|['\": ]+)(?P<id>[0-9a-f]{8}-[0-9a-f-]{27,})\b",
    re.IGNORECASE,
)
SANDBOX_ID_PATTERNS = [
    re.compile(
        r"\bsandbox_id(?:=|['\": ]+)(?P<id>[a-z0-9][a-z0-9-]{4,})\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bsandbox-(?P<id>[a-z0-9][a-z0-9-]{4,})\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:created|destroyed|releasing|released|marking for destroy|created sandbox|provisioner created sandbox)\s+(?:idle warm-pool\s+)?sandbox\s+(?P<id>[a-z0-9][a-z0-9-]{4,})\b",
        re.IGNORECASE,
    ),
]
ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;]*m")
LAST_DOCKER_ERROR = ""
DEFAULT_EXCERPT_WIDTH = 220


@dataclass
class CommandResult:
    returncode: int
    stdout: str
    stderr: str


@dataclass
class MatchRecord:
    source: str
    line: str


@dataclass
class CorrelationQuery:
    thread_id: str | None = None
    run_id: str | None = None
    sandbox_id: str | None = None

    def values(self) -> dict[str, str]:
        values: dict[str, str] = {}
        if self.thread_id:
            values["thread_id"] = self.thread_id
        if self.run_id:
            values["run_id"] = self.run_id
        if self.sandbox_id:
            values["sandbox_id"] = self.sandbox_id
        return values


@lru_cache(maxsize=None)
def resolve_executable(name: str) -> str | None:
    """Resolve a CLI from PATH, local shims, or WinGet install directories."""
    direct = shutil.which(name)
    if direct:
        return direct

    for candidate_name in [f"{name}.exe", f"{name}.cmd", f"{name}.bat"]:
        direct = shutil.which(candidate_name)
        if direct:
            return direct

    local_bin = Path.home() / ".local" / "bin"
    for suffix in [".exe", ".cmd", ".bat"]:
        candidate = local_bin / f"{name}{suffix}"
        if candidate.exists():
            return str(candidate)

    local_appdata = os.environ.get("LOCALAPPDATA")
    if local_appdata:
        search_root = Path(local_appdata) / "Microsoft" / "WinGet" / "Packages"
        if search_root.exists():
            package_prefixes = {
                "docker": ["Docker.DockerCLI_"],
                "kubectl": ["Kubernetes.kubectl_"],
                "helm": ["Helm.Helm_"],
            }
            for suffix in [".exe", ".cmd", ".bat"]:
                for prefix in package_prefixes.get(name, []):
                    for package_dir in search_root.glob(f"{prefix}*"):
                        for candidate in package_dir.rglob(f"{name}{suffix}"):
                            return str(candidate)

    return None


def can_fallback_to_wsl(name: str) -> bool:
    return name in {"docker", "kubectl", "helm"}


def run_via_wsl(args: list[str], timeout: float) -> CommandResult:
    """Run a command inside the configured WSL distro as a compatibility fallback."""
    wsl = resolve_executable("wsl")
    if wsl is None:
        return CommandResult(127, "", "Command not found: wsl")

    try:
        completed = subprocess.run(
            [wsl, "-d", DEFAULT_WSL_DISTRO, "--", *args],
            cwd=REPO_ROOT,
            text=True,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return CommandResult(
            124,
            "",
            f"WSL fallback timed out after {timeout}s: {' '.join(args)}",
        )

    return CommandResult(completed.returncode, completed.stdout, completed.stderr)


def run_command(
    args: list[str], check: bool = False, timeout: float = 20
) -> CommandResult:
    """Run a command natively and fall back to WSL for Docker/Kubernetes CLIs."""
    executable = resolve_executable(args[0])
    if executable is None:
        if can_fallback_to_wsl(args[0]):
            fallback = run_via_wsl(args, timeout)
            if fallback.returncode == 0 or fallback.stderr:
                return fallback
        message = f"Command not found: {args[0]}"
        if check:
            raise RuntimeError(message)
        return CommandResult(127, "", message)

    try:
        completed = subprocess.run(
            [executable, *args[1:]],
            cwd=REPO_ROOT,
            text=True,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        if can_fallback_to_wsl(args[0]):
            fallback = run_via_wsl(args, timeout)
            if fallback.returncode == 0 or fallback.stderr:
                return fallback
        message = f"Command timed out after {timeout}s: {' '.join(args)}"
        if check:
            raise RuntimeError(message)
        return CommandResult(124, "", message)
    if check and completed.returncode != 0:
        raise RuntimeError(
            f"Command failed ({completed.returncode}): {' '.join(args)}\n{completed.stderr.strip()}"
        )
    return CommandResult(completed.returncode, completed.stdout, completed.stderr)


def run_docker_logs(args: list[str], timeout: float = 8) -> CommandResult:
    # Docker log streaming through the Windows-side CLI has been noticeably
    # slower and less reliable than calling Docker directly inside WSL.
    if resolve_executable("wsl") is not None:
        return run_via_wsl(["docker", *args], timeout)
    return run_command(["docker", *args], timeout=timeout)


def print_section(title: str) -> None:
    print(f"\n== {title} ==")


def normalize_log_line(line: str) -> str:
    return " ".join(ANSI_ESCAPE_RE.sub("", line).split())


def truncate_text(text: str, width: int = DEFAULT_EXCERPT_WIDTH) -> str:
    normalized = normalize_log_line(text)
    if len(normalized) <= width:
        return normalized
    return normalized[: width - 3].rstrip() + "..."


def human_size(size: int) -> str:
    units = ["B", "KB", "MB", "GB"]
    value = float(size)
    for unit in units:
        if value < 1024 or unit == units[-1]:
            return f"{value:.1f}{unit}" if unit != "B" else f"{int(value)}B"
        value /= 1024
    return f"{size}B"


def detect_deerflow_containers() -> list[tuple[str, str]]:
    global LAST_DOCKER_ERROR
    result = run_command(
        ["docker", "ps", "-a", "--format", "{{.Names}}\t{{.Status}}"]
    )
    if result.returncode != 0:
        LAST_DOCKER_ERROR = result.stderr.strip() or result.stdout.strip()
        return []
    LAST_DOCKER_ERROR = ""

    containers = []
    for line in result.stdout.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t", 1)
        name = parts[0].strip()
        status = parts[1].strip() if len(parts) > 1 else ""
        if "deer-flow" in name or name == "portainer":
            containers.append((name, status))
    return containers


def find_service_container(service: str) -> str | None:
    candidates = [name for name, _ in detect_deerflow_containers()]
    preferred_patterns = [
        f"deer-flow-dev-{service}-1",
        f"deer-flow-{service}",
        f"deer-flow-{service}-1",
    ]

    for pattern in preferred_patterns:
        for candidate in candidates:
            if candidate == pattern:
                return candidate

    for candidate in candidates:
        if service in candidate:
            return candidate
    return None


def list_k8s_pods(namespace: str) -> list[str]:
    result = run_command(
        [
            "kubectl",
            "get",
            "pods",
            "-n",
            namespace,
            "-o",
            "jsonpath={range .items[*]}{.metadata.name}{\"\\n\"}{end}",
        ]
    )
    if result.returncode != 0:
        return []
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def find_thread_dir(thread_id: str) -> Path:
    return THREADS_DIR / thread_id


def thread_dir_summary(thread_id: str) -> list[str]:
    thread_dir = find_thread_dir(thread_id)
    lines = [f"thread dir: {thread_dir}"]
    if not thread_dir.exists():
        lines.append("status: missing")
        return lines

    lines.append("status: present")
    for relative in [
        "user-data/workspace",
        "user-data/uploads",
        "user-data/outputs",
    ]:
        path = thread_dir / relative
        if not path.exists():
            lines.append(f"{relative}: missing")
            continue
        file_count = sum(1 for child in path.rglob("*") if child.is_file())
        modified = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(path.stat().st_mtime))
        lines.append(f"{relative}: present ({file_count} files, mtime {modified})")
    return lines


def read_last_lines(path: Path, lines: int) -> list[str]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        return [line.rstrip("\n") for line in deque(handle, maxlen=lines)]


def tail_text_lines(text: str, lines: int) -> list[str]:
    return list(deque(text.splitlines(), maxlen=lines))


def tail_file(path: Path, lines: int, follow: bool = False) -> int:
    if not path.exists():
        print(f"[missing] {path}")
        return 1

    with path.open("r", encoding="utf-8", errors="replace") as handle:
        recent = deque(handle, maxlen=lines)
        for line in recent:
            print(line.rstrip())

        if not follow:
            return 0

        try:
            while True:
                position = handle.tell()
                line = handle.readline()
                if not line:
                    time.sleep(1)
                    handle.seek(position)
                    continue
                print(line.rstrip())
        except KeyboardInterrupt:
            return 0


def stream_process(args: list[str]) -> int:
    executable = resolve_executable(args[0])
    if executable is None:
        if can_fallback_to_wsl(args[0]):
            wsl = resolve_executable("wsl")
            if wsl is not None:
                executable = wsl
                args = [executable, "-d", DEFAULT_WSL_DISTRO, "--", *args]
            else:
                print(f"Command not found: {args[0]}")
                return 127
        else:
            print(f"Command not found: {args[0]}")
            return 127
    else:
        args = [executable, *args[1:]]

    process = subprocess.Popen(
        args,
        cwd=REPO_ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert process.stdout is not None
    try:
        for line in process.stdout:
            print(line.rstrip())
    except KeyboardInterrupt:
        process.terminate()
    return process.wait()


def print_file_source_summary() -> None:
    print_section("File Logs")
    for source, path in FILE_SOURCES.items():
        if not path.exists():
            print(f"{source:12} missing  {path}")
            continue
        stat = path.stat()
        modified = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(stat.st_mtime))
        print(f"{source:12} present  {human_size(stat.st_size):>8}  {modified}  {path}")


def print_container_summary() -> None:
    print_section("Docker Containers")
    containers = detect_deerflow_containers()
    if not containers:
        docker_path = resolve_executable("docker")
        if docker_path is None:
            print("Docker CLI was not found from Python's current PATH.")
        elif LAST_DOCKER_ERROR:
            print(f"Docker CLI is present but unavailable from this Python process: {LAST_DOCKER_ERROR}")
        else:
            print("No deer-flow containers detected.")
        return
    for name, status in containers:
        print(f"{name:28} {status}")


def print_k8s_summary(namespace: str) -> None:
    print_section(f"Kubernetes Namespace: {namespace}")
    pods = run_command(
        [
            "kubectl",
            "get",
            "pods,svc",
            "-n",
            namespace,
            "-o",
            "wide",
        ]
    )
    if pods.returncode != 0:
        print(pods.stderr.strip() or "kubectl failed")
        return
    output = pods.stdout.strip()
    print(output if output else "No resources found.")


def print_k8s_events(namespace: str, lines: int) -> int:
    return print_k8s_events_for_object(namespace, lines, None)


def print_k8s_events_for_object(
    namespace: str, lines: int, object_name: str | None
) -> int:
    title = f"Kubernetes Events: {namespace}"
    if object_name:
        title += f" ({object_name})"
    print_section(title)
    args = [
        "kubectl",
        "get",
        "events",
        "-n",
        namespace,
        "--sort-by=.metadata.creationTimestamp",
    ]
    if object_name:
        args.extend(["--field-selector", f"involvedObject.name={object_name}"])

    result = run_command(args, timeout=30)
    if result.returncode != 0:
        message = result.stderr.strip() or result.stdout.strip() or "kubectl failed"
        print(message)
        return 1

    event_lines = result.stdout.splitlines()
    if len(event_lines) <= 1:
        print("No recent events.")
        return 0

    if len(event_lines) - 1 <= lines:
        trimmed = event_lines
    else:
        trimmed = event_lines[:1] + event_lines[-lines:]
    for line in trimmed:
        print(line.rstrip())
    return 0


def command_summary(namespace: str) -> int:
    print(f"Repo root: {REPO_ROOT}")
    print(f"Namespace: {namespace}")
    print_file_source_summary()
    print_container_summary()
    print_k8s_summary(namespace)
    print_k8s_events(namespace, 10)
    print_section("Recommended Sources")
    print("gateway/langgraph/frontend/frontend-v2-dev: file logs under logs/")
    print("nginx/provisioner/frontend-v2-release: docker logs")
    print("sandbox pods: kubectl logs")
    print("sandbox pod diagnostics: python scripts/dev_logs.py inspect-sandbox --sandbox-id <id>")
    print("cross-system correlation: python scripts/dev_logs.py correlate --thread-id <id>")
    return 0


def command_paths() -> int:
    print_section("Log Sources")
    for source, path in FILE_SOURCES.items():
        print(f"{source:12} file    {path}")
    for source in DOCKER_SOURCES:
        print(f"{source:12} docker  docker logs <container>")
    print("sandbox      kubectl kubectl logs -n deer-flow <pod>")
    print("events       kubectl kubectl get events -n deer-flow --sort-by=.metadata.creationTimestamp")
    print("threads      fs      backend/.deer-flow/threads/<thread_id>")
    return 0


def command_tail(source: str, lines: int, follow: bool, namespace: str) -> int:
    if source in FILE_SOURCES:
        print_section(f"{source} ({FILE_SOURCES[source]})")
        return tail_file(FILE_SOURCES[source], lines, follow)

    if source in DOCKER_SOURCES:
        container = find_service_container(DOCKER_SOURCES[source])
        if not container:
            print(f"No container found for service '{source}'.")
            return 1
        print_section(f"{source} ({container})")
        args = ["docker", "logs", "--tail", str(lines)]
        if follow:
            args.append("-f")
        args.append(container)
        if follow:
            return stream_process(args)
        result = run_docker_logs(args[1:])
        print(result.stdout.rstrip())
        if result.stderr.strip():
            print(result.stderr.rstrip())
        return 0 if result.returncode == 0 else 1

    if source == "sandbox":
        pods = list_k8s_pods(namespace)
        if not pods:
            print(f"No sandbox pods found in namespace '{namespace}'.")
            return 1
        if follow and len(pods) > 1:
            print("Follow mode for multiple sandbox pods is not supported. Tail a single pod manually with kubectl.")
            return 1
        for pod in pods:
            print_section(f"sandbox pod {pod}")
            args = ["kubectl", "logs", "-n", namespace, pod, "--tail", str(lines)]
            if follow:
                args.append("-f")
                return stream_process(args)
            result = run_command(args)
            print(result.stdout.rstrip())
            if result.stderr.strip():
                print(result.stderr.rstrip())
        return 0

    if source == "all":
        exit_code = 0
        for group_source in ["frontend", "gateway", "langgraph", "nginx", "provisioner"]:
            current_code = command_tail(group_source, lines, False, namespace)
            if current_code != 0:
                exit_code = current_code
        sandbox_pods = list_k8s_pods(namespace)
        if sandbox_pods:
            command_tail("sandbox", lines, False, namespace)
        print_k8s_events(namespace, min(lines, 20))
        return exit_code

    print(f"Unknown source '{source}'.")
    return 1


def scan_lines_for_errors(source: str, lines: Iterable[str]) -> list[str]:
    matches = []
    for line in lines:
        if ERROR_PATTERN.search(line):
            matches.append(f"[{source}] {line}")
    return matches


def command_errors(lines: int, namespace: str) -> int:
    matches: list[str] = []

    for source, path in FILE_SOURCES.items():
        matches.extend(scan_lines_for_errors(source, read_last_lines(path, lines)))

    for source, service in DOCKER_SOURCES.items():
        container = find_service_container(service)
        if not container:
            continue
        result = run_docker_logs(["logs", "--tail", str(lines), container])
        merged = "\n".join(part for part in [result.stdout, result.stderr] if part)
        matches.extend(scan_lines_for_errors(source, tail_text_lines(merged, lines)))

    for pod in list_k8s_pods(namespace):
        result = run_command(
            ["kubectl", "logs", "-n", namespace, pod, "--tail", str(lines)]
        )
        matches.extend(scan_lines_for_errors(f"sandbox:{pod}", result.stdout.splitlines()))

    if not matches:
        print("No obvious errors or warnings found in the inspected logs.")
        return 0

    for line in matches:
        print(line)
    return 0


def sanitize_tail_lines(lines: int) -> int:
    return max(20, lines)


def get_recent_source_lines(
    search_lines: int,
    namespace: str,
    query: CorrelationQuery,
) -> list[MatchRecord]:
    records: list[MatchRecord] = []

    # Keep correlation fast by searching only the sources that can realistically
    # contain the requested identifier instead of scanning every log transport.
    file_sources = ["langgraph"]
    docker_sources: list[str] = []

    if query.thread_id:
        file_sources.extend(["gateway", "frontend"])
        docker_sources.append("provisioner")
    elif query.sandbox_id:
        docker_sources.append("provisioner")

    for source in file_sources:
        path = FILE_SOURCES[source]
        for line in read_last_lines(path, search_lines):
            records.append(MatchRecord(source, line))

    for source in docker_sources:
        service = DOCKER_SOURCES[source]
        container = find_service_container(service)
        if not container:
            continue
        result = run_docker_logs(["logs", "--tail", str(search_lines), container], timeout=8)
        merged = "\n".join(part for part in [result.stdout, result.stderr] if part)
        for line in tail_text_lines(merged, search_lines):
            records.append(MatchRecord(source, line))

    if query.sandbox_id:
        pod_name = f"sandbox-{query.sandbox_id}"
        if k8s_resource_exists("pod", pod_name, namespace):
            result = run_command(
                ["kubectl", "logs", "-n", namespace, pod_name, "--tail", str(min(search_lines, 200))],
                timeout=15,
            )
            for line in result.stdout.splitlines():
                records.append(MatchRecord(f"sandbox:{pod_name}", line))

    return records


def extract_ids_from_line(line: str) -> dict[str, set[str]]:
    normalized = ANSI_ESCAPE_RE.sub("", line)
    related: dict[str, set[str]] = {
        "thread_id": set(),
        "run_id": set(),
        "sandbox_id": set(),
    }
    for pattern in THREAD_ID_PATTERNS:
        for match in pattern.finditer(normalized):
            related["thread_id"].add(match.group("id"))
    for match in RUN_ID_RE.finditer(normalized):
        related["run_id"].add(match.group("id"))
    for pattern in SANDBOX_ID_PATTERNS:
        for match in pattern.finditer(normalized):
            sandbox_id = match.group("id")
            if sandbox_id.endswith("-svc"):
                sandbox_id = sandbox_id[:-4]
            related["sandbox_id"].add(sandbox_id)
    return related


def filter_matches(
    records: list[MatchRecord],
    query: CorrelationQuery,
    limit: int,
) -> list[MatchRecord]:
    needles = [value for value in query.values().values() if value]
    if not needles:
        return []

    matches = [
        record for record in records if any(needle in record.line for needle in needles)
    ]
    return matches[-limit:]


def print_match_records(title: str, records: list[MatchRecord], verbose: bool) -> None:
    print_section(title)
    if not records:
        print("No matches found.")
        return
    for record in records:
        line = record.line if verbose else truncate_text(record.line)
        print(f"[{record.source}] {line}")


def find_related_ids(matches: list[MatchRecord], query: CorrelationQuery) -> dict[str, set[str]]:
    related: dict[str, set[str]] = {
        "thread_id": set(),
        "run_id": set(),
        "sandbox_id": set(),
    }
    for key, value in query.values().items():
        related[key].add(value)
    for record in matches:
        extracted = extract_ids_from_line(record.line)
        for key, values in extracted.items():
            related[key].update(values)
    return related


def k8s_resource_exists(kind: str, name: str, namespace: str) -> bool:
    result = run_command(
        ["kubectl", "get", kind, name, "-n", namespace, "-o", "name"],
        timeout=20,
    )
    return result.returncode == 0 and bool(result.stdout.strip())


def describe_k8s_resource(kind: str, name: str, namespace: str) -> CommandResult:
    return run_command(
        ["kubectl", "describe", kind, name, "-n", namespace],
        timeout=30,
    )


def get_k8s_resource_yaml(kind: str, name: str, namespace: str) -> CommandResult:
    return run_command(
        ["kubectl", "get", kind, name, "-n", namespace, "-o", "yaml"],
        timeout=30,
    )


def get_pod_logs(pod_name: str, namespace: str, lines: int) -> CommandResult:
    return run_command(
        ["kubectl", "logs", "-n", namespace, pod_name, "--tail", str(lines)],
        timeout=30,
    )


def extract_label_from_yaml(yaml_text: str, label_name: str) -> str | None:
    in_labels = False
    label_prefix = f"{label_name}:"
    for raw_line in yaml_text.splitlines():
        stripped = raw_line.strip()
        if stripped == "labels:":
            in_labels = True
            continue
        if in_labels:
            if raw_line and not raw_line.startswith("    ") and not raw_line.startswith("      "):
                in_labels = False
                continue
            if stripped.startswith(label_prefix):
                return stripped.split(":", 1)[1].strip().strip('"')
    return None


def print_k8s_get(kind: str, name: str, namespace: str) -> None:
    result = run_command(
        ["kubectl", "get", kind, name, "-n", namespace, "-o", "wide"],
        timeout=30,
    )
    if result.returncode != 0:
        print(result.stderr.strip() or result.stdout.strip() or "kubectl failed")
        return
    print(result.stdout.strip())


def command_inspect_sandbox(
    namespace: str,
    sandbox_id: str | None,
    pod_name: str | None,
    lines: int,
    include_logs: bool,
    verbose: bool,
) -> int:
    """Inspect one sandbox Pod/Service and optionally expand to full details."""
    if not sandbox_id and not pod_name:
        print("Provide --sandbox-id <id> or --pod <pod-name>.")
        return 1

    resolved_pod = pod_name or f"sandbox-{sandbox_id}"
    resolved_sandbox_id = sandbox_id or (
        resolved_pod[len("sandbox-") :] if resolved_pod.startswith("sandbox-") else None
    )
    resolved_service = (
        f"sandbox-{resolved_sandbox_id}-svc" if resolved_sandbox_id else None
    )

    print_section("Sandbox Target")
    if resolved_sandbox_id:
        print(f"sandbox_id: {resolved_sandbox_id}")
    print(f"pod: {resolved_pod}")
    if resolved_service:
        print(f"service: {resolved_service}")

    pod_exists = k8s_resource_exists("pod", resolved_pod, namespace)
    svc_exists = bool(resolved_service) and k8s_resource_exists(
        "service", resolved_service, namespace
    )

    thread_id = None
    if pod_exists:
        yaml_result = get_k8s_resource_yaml("pod", resolved_pod, namespace)
        thread_id = extract_label_from_yaml(yaml_result.stdout, "thread-id")

    print_section("Live Resources")
    print(f"pod exists: {'yes' if pod_exists else 'no'}")
    print(f"service exists: {'yes' if svc_exists else 'no'}")
    if thread_id:
        print(f"thread_id: {thread_id}")

    if pod_exists:
        print_section(f"Pod Wide: {resolved_pod}")
        print_k8s_get("pod", resolved_pod, namespace)

    if svc_exists and resolved_service:
        print_section(f"Service Wide: {resolved_service}")
        print_k8s_get("service", resolved_service, namespace)

    if pod_exists:
        describe_result = describe_k8s_resource("pod", resolved_pod, namespace)
        if not thread_id:
            extracted = extract_ids_from_line(describe_result.stdout)
            thread_ids = sorted(extracted["thread_id"])
            if thread_ids:
                thread_id = thread_ids[0]
        if thread_id:
            print_section("Thread Data")
            for line in thread_dir_summary(thread_id):
                print(line)
        print_k8s_events_for_object(namespace, lines, resolved_pod)

        if verbose:
            print_section(f"Pod Describe: {resolved_pod}")
            print(describe_result.stdout.strip() or describe_result.stderr.strip() or "No output")

        if include_logs and verbose:
            logs_result = get_pod_logs(resolved_pod, namespace, sanitize_tail_lines(lines))
            print_section(f"Pod Logs: {resolved_pod}")
            if logs_result.stdout.strip():
                print(logs_result.stdout.rstrip())
            if logs_result.stderr.strip():
                print(logs_result.stderr.rstrip())
        elif include_logs and not verbose:
            logs_result = get_pod_logs(resolved_pod, namespace, min(sanitize_tail_lines(lines), 8))
            print_section(f"Pod Log Summary: {resolved_pod}")
            if logs_result.stdout.strip():
                for line in logs_result.stdout.splitlines()[-min(lines, 8):]:
                    print(truncate_text(line))
            if logs_result.stderr.strip():
                print(truncate_text(logs_result.stderr))
    else:
        print_section("Pod Describe")
        print("No live pod found. Use correlate if you only have historical IDs.")
        print_k8s_events_for_object(namespace, lines, resolved_pod)

    return 0


def command_correlate(
    namespace: str,
    query: CorrelationQuery,
    lines: int,
    search_lines: int,
    include_describe: bool,
    verbose: bool,
) -> int:
    """Trace one known ID across LangGraph, provisioner, thread data, and k8s."""
    if not query.values():
        print("Provide at least one of --thread-id, --run-id, or --sandbox-id.")
        return 1

    print_section("Query")
    for key, value in query.values().items():
        print(f"{key}: {value}")

    records = get_recent_source_lines(search_lines, namespace, query)
    matches = filter_matches(records, query, lines)
    print_match_records("Matching Log Lines", matches, verbose)

    related = find_related_ids(matches, query)
    print_section("Related IDs")
    for key in ["thread_id", "run_id", "sandbox_id"]:
        values = sorted(related[key])
        print(f"{key}: {', '.join(values) if values else 'none'}")

    thread_ids = sorted(related["thread_id"])
    if thread_ids:
        print_section("Thread Data")
        for thread_id in thread_ids:
            for line in thread_dir_summary(thread_id):
                print(f"[{thread_id}] {line}")

    sandbox_ids = sorted(related["sandbox_id"])
    if sandbox_ids:
        print_section("Sandbox Resource Hints")
        for sandbox_id in sandbox_ids:
            pod_name = f"sandbox-{sandbox_id}"
            service_name = f"sandbox-{sandbox_id}-svc"
            pod_exists = k8s_resource_exists("pod", pod_name, namespace)
            service_exists = k8s_resource_exists("service", service_name, namespace)
            print(
                f"{sandbox_id}: pod={'yes' if pod_exists else 'no'}, "
                f"service={'yes' if service_exists else 'no'}"
            )
            if include_describe:
                command_inspect_sandbox(
                    namespace=namespace,
                    sandbox_id=sandbox_id,
                    pod_name=None,
                    lines=min(lines, 20),
                    include_logs=False,
                    verbose=verbose,
                )

    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Unified development log reader for DeerFlow Docker + k3s."
    )
    parser.add_argument(
        "--namespace",
        default=DEFAULT_NAMESPACE,
        help=f"Kubernetes namespace for sandbox resources (default: {DEFAULT_NAMESPACE})",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("summary", help="Show current log and runtime sources.")
    subparsers.add_parser("paths", help="Show where each log source lives.")

    tail_parser = subparsers.add_parser("tail", help="Tail a specific log source.")
    tail_parser.add_argument(
        "source",
        choices=[
            *FILE_SOURCES.keys(),
            *DOCKER_SOURCES.keys(),
            "sandbox",
            "all",
        ],
    )
    tail_parser.add_argument("--lines", type=int, default=80)
    tail_parser.add_argument("--follow", action="store_true")

    errors_parser = subparsers.add_parser(
        "errors", help="Scan recent logs for warnings and errors."
    )
    errors_parser.add_argument("--lines", type=int, default=200)

    events_parser = subparsers.add_parser(
        "events", help="Show recent Kubernetes events for sandbox troubleshooting."
    )
    events_parser.add_argument("--lines", type=int, default=20)

    inspect_parser = subparsers.add_parser(
        "inspect-sandbox",
        help="Show pod/service/describe/events/logs for a sandbox.",
    )
    inspect_parser.add_argument("--sandbox-id")
    inspect_parser.add_argument("--pod")
    inspect_parser.add_argument("--lines", type=int, default=40)
    inspect_parser.add_argument("--no-logs", action="store_true")
    inspect_parser.add_argument(
        "--verbose",
        action="store_true",
        help="Show full describe output and longer pod logs.",
    )

    correlate_parser = subparsers.add_parser(
        "correlate",
        help="Correlate thread_id/run_id/sandbox_id across logs, thread data, and k8s.",
    )
    correlate_parser.add_argument("--thread-id")
    correlate_parser.add_argument("--run-id")
    correlate_parser.add_argument("--sandbox-id")
    correlate_parser.add_argument("--lines", type=int, default=40)
    correlate_parser.add_argument(
        "--search-lines",
        type=int,
        default=800,
        help="How many recent lines to inspect from each source.",
    )
    correlate_parser.add_argument(
        "--include-describe",
        action="store_true",
        help="Also run inspect-sandbox for any resolved sandbox IDs.",
    )
    correlate_parser.add_argument(
        "--verbose",
        action="store_true",
        help="Show full matching log lines instead of compact excerpts.",
    )

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if args.command == "summary":
        return command_summary(args.namespace)
    if args.command == "paths":
        return command_paths()
    if args.command == "tail":
        return command_tail(args.source, args.lines, args.follow, args.namespace)
    if args.command == "errors":
        return command_errors(args.lines, args.namespace)
    if args.command == "events":
        return print_k8s_events(args.namespace, args.lines)
    if args.command == "inspect-sandbox":
        return command_inspect_sandbox(
            namespace=args.namespace,
            sandbox_id=args.sandbox_id,
            pod_name=args.pod,
            lines=args.lines,
            include_logs=not args.no_logs,
            verbose=args.verbose,
        )
    if args.command == "correlate":
        return command_correlate(
            namespace=args.namespace,
            query=CorrelationQuery(
                thread_id=args.thread_id,
                run_id=args.run_id,
                sandbox_id=args.sandbox_id,
            ),
            lines=args.lines,
            search_lines=args.search_lines,
            include_describe=args.include_describe,
            verbose=args.verbose,
        )

    parser.error(f"Unhandled command: {args.command}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
