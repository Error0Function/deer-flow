#!/usr/bin/env bash
#
# deploy.sh - Build and start (or stop) DeerFlow production services
#
# Usage:
#   deploy.sh [up]   — build images and start containers (default)
#   deploy.sh down   — stop and remove containers
#
# Must be run from the repo root directory.

set -e

CMD="${1:-up}"
SUBCMD_OPTION="${2:-}"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

DOCKER_DIR="$REPO_ROOT/docker"

# Native Windows docker-compose may still default to the legacy builder.
# Force BuildKit so existing backend/frontend Dockerfiles build consistently.
export DOCKER_BUILDKIT="${DOCKER_BUILDKIT:-1}"
export COMPOSE_DOCKER_CLI_BUILD="${COMPOSE_DOCKER_CLI_BUILD:-1}"

resolve_compose_base_cmd() {
    if command -v docker-compose >/dev/null 2>&1; then
        echo "docker-compose"
        return
    fi

    if docker compose version >/dev/null 2>&1; then
        echo "docker compose"
        return
    fi

    echo "Neither docker-compose nor docker compose is available." >&2
    exit 1
}

COMPOSE_CMD="$(resolve_compose_base_cmd)"
COMPOSE_ARGS=(-p deer-flow -f "$DOCKER_DIR/docker-compose.yaml")

resolve_kubeconfig_source() {
    if [ -n "$DEER_FLOW_KUBECONFIG_SOURCE_PATH" ]; then
        echo "$DEER_FLOW_KUBECONFIG_SOURCE_PATH"
        return
    fi

    if [ -n "$KUBECONFIG" ]; then
        echo "${KUBECONFIG%%:*}"
        return
    fi

    echo "$HOME/.kube/config"
}

resolve_k8s_api_server_for_container() {
    if [ -n "$DEER_FLOW_K8S_API_SERVER" ]; then
        echo "$DEER_FLOW_K8S_API_SERVER"
        return
    fi

    local kubeconfig_source
    kubeconfig_source="$(resolve_kubeconfig_source)"

    if [ ! -f "$kubeconfig_source" ]; then
        echo ""
        return
    fi

    local server
    server="$(awk '/^[[:space:]]*server:[[:space:]]*/ { sub(/^[[:space:]]*server:[[:space:]]*/, "", $0); print; exit }' "$kubeconfig_source")"
    if [ -z "$server" ]; then
        echo ""
        return
    fi

    case "$server" in
        https://127.0.0.1:*|https://localhost:*|https://0.0.0.0:*)
            local port="${server##*:}"
            echo "https://host.docker.internal:${port}"
            ;;
        *)
            echo "$server"
            ;;
    esac
}

ensure_k8s_namespace() {
    local namespace="${DEER_FLOW_K8S_NAMESPACE:-deer-flow}"
    local kubeconfig_source
    kubeconfig_source="$(resolve_kubeconfig_source)"

    if [ ! -f "$kubeconfig_source" ]; then
        echo -e "${RED}✗ kubeconfig not found at $kubeconfig_source${NC}"
        exit 1
    fi

    echo -e "${BLUE}Ensuring Kubernetes namespace exists: $namespace${NC}"
    kubectl --kubeconfig "$kubeconfig_source" create namespace "$namespace" --dry-run=client -o yaml | \
        kubectl --kubeconfig "$kubeconfig_source" apply -f -
}

reset_runtime_state() {
    local sandbox_mode="${1:-$(detect_sandbox_mode)}"
    local langgraph_state_dir="$REPO_ROOT/backend/.langgraph_api"
    local thread_state_dir="$DEER_FLOW_HOME/threads"

    echo -e "${BLUE}Resetting DeerFlow runtime state...${NC}"

    mkdir -p "$langgraph_state_dir"
    find "$langgraph_state_dir" -maxdepth 1 -type f -name '*.pckl' -delete

    mkdir -p "$thread_state_dir"
    find "$thread_state_dir" -mindepth 1 -maxdepth 1 -exec rm -rf {} +

    if [ "$sandbox_mode" = "provisioner" ]; then
        local namespace="${DEER_FLOW_K8S_NAMESPACE:-deer-flow}"
        local kubeconfig_source
        kubeconfig_source="$(resolve_kubeconfig_source)"

        if [ -f "$kubeconfig_source" ]; then
            echo -e "${BLUE}Deleting provisioner-managed pods and services in namespace ${namespace}${NC}"
            kubectl --kubeconfig "$kubeconfig_source" delete pod,service --all -n "$namespace" --ignore-not-found >/dev/null 2>&1 || true
        fi
    fi

    echo -e "${GREEN}✓ Runtime state reset complete${NC}"
}

# ── Colors ────────────────────────────────────────────────────────────────────

GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

# ── DEER_FLOW_HOME ────────────────────────────────────────────────────────────

if [ -z "$DEER_FLOW_HOME" ]; then
    export DEER_FLOW_HOME="$REPO_ROOT/backend/.deer-flow"
fi
echo -e "${BLUE}DEER_FLOW_HOME=$DEER_FLOW_HOME${NC}"
mkdir -p "$DEER_FLOW_HOME"

# ── DEER_FLOW_REPO_ROOT (for skills host path in DooD) ───────────────────────

export DEER_FLOW_REPO_ROOT="$REPO_ROOT"

# ── config.yaml ───────────────────────────────────────────────────────────────

if [ -z "$DEER_FLOW_CONFIG_PATH" ]; then
    export DEER_FLOW_CONFIG_PATH="$REPO_ROOT/config.yaml"
fi

if [ ! -f "$DEER_FLOW_CONFIG_PATH" ]; then
    # Try to seed from repo (config.example.yaml is the canonical template)
    if [ -f "$REPO_ROOT/config.example.yaml" ]; then
        cp "$REPO_ROOT/config.example.yaml" "$DEER_FLOW_CONFIG_PATH"
        echo -e "${GREEN}✓ Seeded config.example.yaml → $DEER_FLOW_CONFIG_PATH${NC}"
        echo -e "${YELLOW}⚠ config.yaml was seeded from the example template.${NC}"
        echo "  Edit $DEER_FLOW_CONFIG_PATH and set your model API keys before use."
    else
        echo -e "${RED}✗ No config.yaml found.${NC}"
        echo "  Run 'make config' from the repo root to generate one,"
        echo "  then set the required model API keys."
        exit 1
    fi
else
    echo -e "${GREEN}✓ config.yaml: $DEER_FLOW_CONFIG_PATH${NC}"
fi

# ── extensions_config.json ───────────────────────────────────────────────────

if [ -z "$DEER_FLOW_EXTENSIONS_CONFIG_PATH" ]; then
    export DEER_FLOW_EXTENSIONS_CONFIG_PATH="$REPO_ROOT/extensions_config.json"
fi

if [ ! -f "$DEER_FLOW_EXTENSIONS_CONFIG_PATH" ]; then
    if [ -f "$REPO_ROOT/extensions_config.json" ]; then
        cp "$REPO_ROOT/extensions_config.json" "$DEER_FLOW_EXTENSIONS_CONFIG_PATH"
        echo -e "${GREEN}✓ Seeded extensions_config.json → $DEER_FLOW_EXTENSIONS_CONFIG_PATH${NC}"
    else
        # Create a minimal empty config so the gateway doesn't fail on startup
        echo '{"mcpServers":{},"skills":{}}' > "$DEER_FLOW_EXTENSIONS_CONFIG_PATH"
        echo -e "${YELLOW}⚠ extensions_config.json not found, created empty config at $DEER_FLOW_EXTENSIONS_CONFIG_PATH${NC}"
    fi
else
    echo -e "${GREEN}✓ extensions_config.json: $DEER_FLOW_EXTENSIONS_CONFIG_PATH${NC}"
fi


# ── BETTER_AUTH_SECRET ───────────────────────────────────────────────────────
# Required by Next.js in production. Generated once and persisted so auth
# sessions survive container restarts.

_secret_file="$DEER_FLOW_HOME/.better-auth-secret"
if [ -z "$BETTER_AUTH_SECRET" ]; then
    if [ -f "$_secret_file" ]; then
        export BETTER_AUTH_SECRET
        BETTER_AUTH_SECRET="$(cat "$_secret_file")"
        echo -e "${GREEN}✓ BETTER_AUTH_SECRET loaded from $_secret_file${NC}"
    else
        export BETTER_AUTH_SECRET
        BETTER_AUTH_SECRET="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
        echo "$BETTER_AUTH_SECRET" > "$_secret_file"
        chmod 600 "$_secret_file"
        echo -e "${GREEN}✓ BETTER_AUTH_SECRET generated → $_secret_file${NC}"
    fi
fi

# ── detect_sandbox_mode ───────────────────────────────────────────────────────

detect_sandbox_mode() {
    local sandbox_use=""
    local provisioner_url=""

    [ -f "$DEER_FLOW_CONFIG_PATH" ] || { echo "local"; return; }

    sandbox_use=$(awk '
        /^[[:space:]]*sandbox:[[:space:]]*$/ { in_sandbox=1; next }
        in_sandbox && /^[^[:space:]#]/ { in_sandbox=0 }
        in_sandbox && /^[[:space:]]*use:[[:space:]]*/ {
            line=$0; sub(/^[[:space:]]*use:[[:space:]]*/, "", line); print line; exit
        }
    ' "$DEER_FLOW_CONFIG_PATH")

    provisioner_url=$(awk '
        /^[[:space:]]*sandbox:[[:space:]]*$/ { in_sandbox=1; next }
        in_sandbox && /^[^[:space:]#]/ { in_sandbox=0 }
        in_sandbox && /^[[:space:]]*provisioner_url:[[:space:]]*/ {
            line=$0; sub(/^[[:space:]]*provisioner_url:[[:space:]]*/, "", line); print line; exit
        }
    ' "$DEER_FLOW_CONFIG_PATH")

    if [[ "$sandbox_use" == *"src.community.aio_sandbox:AioSandboxProvider"* ]]; then
        if [ -n "$provisioner_url" ]; then
            echo "provisioner"
        else
            echo "aio"
        fi
    else
        echo "local"
    fi
}

# ── down ──────────────────────────────────────────────────────────────────────

if [ "$CMD" = "down" ]; then
    # Set minimal env var defaults so docker-compose can parse the file without
    # warning about unset variables that appear in volume specs.
    export DEER_FLOW_HOME="${DEER_FLOW_HOME:-$REPO_ROOT/backend/.deer-flow}"
    export DEER_FLOW_CONFIG_PATH="${DEER_FLOW_CONFIG_PATH:-$DEER_FLOW_HOME/config.yaml}"
    export DEER_FLOW_EXTENSIONS_CONFIG_PATH="${DEER_FLOW_EXTENSIONS_CONFIG_PATH:-$DEER_FLOW_HOME/extensions_config.json}"
    export DEER_FLOW_DOCKER_SOCKET="${DEER_FLOW_DOCKER_SOCKET:-/var/run/docker.sock}"
    export DEER_FLOW_REPO_ROOT="${DEER_FLOW_REPO_ROOT:-$REPO_ROOT}"
    export BETTER_AUTH_SECRET="${BETTER_AUTH_SECRET:-placeholder}"
    $COMPOSE_CMD "${COMPOSE_ARGS[@]}" down
    exit 0
fi

if [ "$CMD" = "reset-state" ]; then
    reset_runtime_state
    exit 0
fi

# ── Banner ────────────────────────────────────────────────────────────────────

echo "=========================================="
echo "  DeerFlow Production Deployment"
echo "=========================================="
echo ""

# ── Step 1: Detect sandbox mode ──────────────────────────────────────────────

sandbox_mode="$(detect_sandbox_mode)"
echo -e "${BLUE}Sandbox mode: $sandbox_mode${NC}"

if [ -n "$SUBCMD_OPTION" ] && [ "$SUBCMD_OPTION" != "--reset-state" ]; then
    echo -e "${RED}✗ Unknown option: $SUBCMD_OPTION${NC}"
    echo "  Usage: deploy.sh [up [--reset-state]|down|reset-state]"
    exit 1
fi

if [ "$SUBCMD_OPTION" = "--reset-state" ]; then
    reset_runtime_state "$sandbox_mode"
    echo ""
fi

if [ "$sandbox_mode" = "provisioner" ]; then
    services=""
    extra_args="--profile provisioner"
    export DEER_FLOW_KUBECONFIG_SOURCE_PATH="${DEER_FLOW_KUBECONFIG_SOURCE_PATH:-$(resolve_kubeconfig_source)}"
    export DEER_FLOW_K8S_API_SERVER="${DEER_FLOW_K8S_API_SERVER:-$(resolve_k8s_api_server_for_container)}"
    export DEER_FLOW_K8S_NAMESPACE="${DEER_FLOW_K8S_NAMESPACE:-deer-flow}"
    export DEER_FLOW_NODE_HOST="${DEER_FLOW_NODE_HOST:-host.docker.internal}"
    if [ -z "$DEER_FLOW_K8S_API_SERVER" ]; then
        echo -e "${RED}✗ Could not determine Kubernetes API server for provisioner mode.${NC}"
        exit 1
    fi
    echo -e "${BLUE}Using kubeconfig: $DEER_FLOW_KUBECONFIG_SOURCE_PATH${NC}"
    echo -e "${BLUE}Using Kubernetes API for containers: $DEER_FLOW_K8S_API_SERVER${NC}"
    ensure_k8s_namespace
else
    services="frontend gateway langgraph nginx"
    extra_args=""
fi


# ── DEER_FLOW_DOCKER_SOCKET ───────────────────────────────────────────────────

if [ -z "$DEER_FLOW_DOCKER_SOCKET" ]; then
    export DEER_FLOW_DOCKER_SOCKET="/var/run/docker.sock"
fi

if [ "$sandbox_mode" != "local" ]; then
    if [ ! -S "$DEER_FLOW_DOCKER_SOCKET" ]; then
        echo -e "${RED}⚠ Docker socket not found at $DEER_FLOW_DOCKER_SOCKET${NC}"
        echo "  AioSandboxProvider (DooD) will not work."
        exit 1
    else
        echo -e "${GREEN}✓ Docker socket: $DEER_FLOW_DOCKER_SOCKET${NC}"
    fi
fi

echo ""

# ── Step 2: Build and start ───────────────────────────────────────────────────

echo "Building images and starting containers..."
echo ""

# shellcheck disable=SC2086
$COMPOSE_CMD "${COMPOSE_ARGS[@]}" $extra_args up --build -d --remove-orphans $services

echo ""
echo "=========================================="
echo "  DeerFlow is running!"
echo "=========================================="
echo ""
echo "  🌐 Application: http://localhost:${PORT:-2026}"
echo "  📡 API Gateway: http://localhost:${PORT:-2026}/api/*"
echo "  🤖 LangGraph:   http://localhost:${PORT:-2026}/api/langgraph/*"
echo ""
echo "  Manage:"
echo "    make down        — stop and remove containers"
echo "    make docker-logs — view logs"
echo ""
