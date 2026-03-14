#!/usr/bin/env bash
set -e

# Colors for output
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Get script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
DOCKER_DIR="$PROJECT_ROOT/docker"

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

# Prefer docker-compose for this repo so Windows and WSL operators share the
# same command shape. Fall back to docker compose only when necessary.
COMPOSE_CMD="$(resolve_compose_base_cmd) -p deer-flow-dev -f docker-compose-dev.yaml"

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
        echo -e "${YELLOW}✗ kubeconfig not found at $kubeconfig_source${NC}"
        exit 1
    fi

    echo -e "${BLUE}Ensuring Kubernetes namespace exists: $namespace${NC}"
    kubectl --kubeconfig "$kubeconfig_source" create namespace "$namespace" --dry-run=client -o yaml | \
        kubectl --kubeconfig "$kubeconfig_source" apply -f -
}

reset_runtime_state() {
    local sandbox_mode="${1:-$(detect_sandbox_mode)}"
    local langgraph_state_dir="$PROJECT_ROOT/backend/.langgraph_api"
    local thread_state_dir="$PROJECT_ROOT/backend/.deer-flow/threads"

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

detect_sandbox_mode() {
    local config_file="$PROJECT_ROOT/config.yaml"
    local sandbox_use=""
    local provisioner_url=""

    if [ ! -f "$config_file" ]; then
        echo "local"
        return
    fi

    sandbox_use=$(awk '
        /^[[:space:]]*sandbox:[[:space:]]*$/ { in_sandbox=1; next }
        in_sandbox && /^[^[:space:]#]/ { in_sandbox=0 }
        in_sandbox && /^[[:space:]]*use:[[:space:]]*/ {
            line=$0
            sub(/^[[:space:]]*use:[[:space:]]*/, "", line)
            print line
            exit
        }
    ' "$config_file")

    provisioner_url=$(awk '
        /^[[:space:]]*sandbox:[[:space:]]*$/ { in_sandbox=1; next }
        in_sandbox && /^[^[:space:]#]/ { in_sandbox=0 }
        in_sandbox && /^[[:space:]]*provisioner_url:[[:space:]]*/ {
            line=$0
            sub(/^[[:space:]]*provisioner_url:[[:space:]]*/, "", line)
            print line
            exit
        }
    ' "$config_file")

    if [[ "$sandbox_use" == *"src.sandbox.local:LocalSandboxProvider"* ]]; then
        echo "local"
    elif [[ "$sandbox_use" == *"src.community.aio_sandbox:AioSandboxProvider"* ]]; then
        if [ -n "$provisioner_url" ]; then
            echo "provisioner"
        else
            echo "aio"
        fi
    else
        echo "local"
    fi
}

# Cleanup function for Ctrl+C
cleanup() {
    echo ""
    echo -e "${YELLOW}Operation interrupted by user${NC}"
    exit 130
}

# Set up trap for Ctrl+C
trap cleanup INT TERM

# Initialize: pre-pull the sandbox image so first Pod startup is fast
init() {
    echo "=========================================="
    echo "  DeerFlow Init — Pull Sandbox Image"
    echo "=========================================="
    echo ""

    SANDBOX_IMAGE="${DEER_FLOW_SANDBOX_IMAGE:-ghcr.io/agent-infra/sandbox:latest}"

    if ! docker images --format '{{.Repository}}:{{.Tag}}' | grep -q "^${SANDBOX_IMAGE}$"; then
        echo -e "${BLUE}Pulling sandbox image: $SANDBOX_IMAGE ...${NC}"
        docker pull "$SANDBOX_IMAGE"
    else
        echo -e "${GREEN}Sandbox image already exists locally: $SANDBOX_IMAGE${NC}"
    fi

    if command -v k3s >/dev/null 2>&1; then
        echo ""
        echo -e "${BLUE}Checking k3s/containerd cache for sandbox image...${NC}"
        if sudo -n k3s ctr images ls >/dev/null 2>&1; then
            if sudo -n k3s ctr images ls | awk '{print $1}' | grep -qx "$SANDBOX_IMAGE"; then
                echo -e "${GREEN}Sandbox image already exists in k3s/containerd: $SANDBOX_IMAGE${NC}"
            else
                echo -e "${BLUE}Pulling sandbox image into k3s/containerd...${NC}"
                sudo -n k3s ctr images pull "$SANDBOX_IMAGE"
            fi
        else
            echo -e "${YELLOW}sudo is required to inspect k3s/containerd cache; you may be prompted once.${NC}"
            if sudo k3s ctr images ls | awk '{print $1}' | grep -qx "$SANDBOX_IMAGE"; then
                echo -e "${GREEN}Sandbox image already exists in k3s/containerd: $SANDBOX_IMAGE${NC}"
            else
                echo -e "${BLUE}Pulling sandbox image into k3s/containerd...${NC}"
                sudo k3s ctr images pull "$SANDBOX_IMAGE"
            fi
        fi
    fi

    echo ""
    echo -e "${GREEN}✓ Sandbox image is ready for Docker and k3s.${NC}"
    echo ""
    echo -e "${YELLOW}Next step: make docker-start${NC}"
}

# Start Docker development environment
start() {
    local start_option="${1:-}"
    local sandbox_mode
    local services

    echo "=========================================="
    echo "  Starting DeerFlow Docker Development"
    echo "=========================================="
    echo ""

    sandbox_mode="$(detect_sandbox_mode)"

    if [ "$start_option" = "--reset-state" ]; then
        reset_runtime_state "$sandbox_mode"
        echo ""
    elif [ -n "$start_option" ]; then
        echo -e "${YELLOW}Unknown option for start: $start_option${NC}"
        echo "Usage: $0 start [--reset-state]"
        exit 1
    fi

    if [ "$sandbox_mode" = "provisioner" ]; then
        services="frontend frontend-v2-dev frontend-v2-release gateway langgraph provisioner nginx"
    else
        services="frontend frontend-v2-dev frontend-v2-release gateway langgraph nginx"
    fi

    echo -e "${BLUE}Detected sandbox mode: $sandbox_mode${NC}"
    if [ "$sandbox_mode" = "provisioner" ]; then
        echo -e "${BLUE}Provisioner enabled (Kubernetes mode).${NC}"
    else
        echo -e "${BLUE}Provisioner disabled (not required for this sandbox mode).${NC}"
    fi
    echo ""
    
    # Set DEER_FLOW_ROOT for provisioner if not already set
    if [ -z "$DEER_FLOW_ROOT" ]; then
        export DEER_FLOW_ROOT="$PROJECT_ROOT"
        echo -e "${BLUE}Setting DEER_FLOW_ROOT=$DEER_FLOW_ROOT${NC}"
        echo ""
    fi

    if [ "$sandbox_mode" = "provisioner" ]; then
        export DEER_FLOW_KUBECONFIG_SOURCE_PATH="${DEER_FLOW_KUBECONFIG_SOURCE_PATH:-$(resolve_kubeconfig_source)}"
        export DEER_FLOW_K8S_API_SERVER="${DEER_FLOW_K8S_API_SERVER:-$(resolve_k8s_api_server_for_container)}"
        export DEER_FLOW_K8S_NAMESPACE="${DEER_FLOW_K8S_NAMESPACE:-deer-flow}"
        export DEER_FLOW_NODE_HOST="${DEER_FLOW_NODE_HOST:-host.docker.internal}"

        if [ -z "$DEER_FLOW_K8S_API_SERVER" ]; then
            echo -e "${YELLOW}✗ Could not determine Kubernetes API server for provisioner mode.${NC}"
            exit 1
        fi

        echo -e "${BLUE}Using kubeconfig: $DEER_FLOW_KUBECONFIG_SOURCE_PATH${NC}"
        echo -e "${BLUE}Using Kubernetes API for containers: $DEER_FLOW_K8S_API_SERVER${NC}"
        echo ""
        ensure_k8s_namespace
        echo ""
    fi
    
    # Ensure config.yaml exists before starting.
    if [ ! -f "$PROJECT_ROOT/config.yaml" ]; then
        if [ -f "$PROJECT_ROOT/config.example.yaml" ]; then
            cp "$PROJECT_ROOT/config.example.yaml" "$PROJECT_ROOT/config.yaml"
            echo ""
            echo -e "${YELLOW}============================================================${NC}"
            echo -e "${YELLOW}  config.yaml has been created from config.example.yaml.${NC}"
            echo -e "${YELLOW}  Please edit config.yaml to set your API keys and model   ${NC}"
            echo -e "${YELLOW}  configuration before starting DeerFlow.                  ${NC}"
            echo -e "${YELLOW}============================================================${NC}"
            echo ""
            echo -e "${YELLOW}  Edit the file:  $PROJECT_ROOT/config.yaml${NC}"
            echo -e "${YELLOW}  Then run:        make docker-start${NC}"
            echo ""
            exit 0
        else
            echo -e "${YELLOW}✗ config.yaml not found and no config.example.yaml to copy from.${NC}"
            exit 1
        fi
    fi

    # Ensure extensions_config.json exists as a file before mounting.
    # Docker creates a directory when bind-mounting a non-existent host path.
    if [ ! -f "$PROJECT_ROOT/extensions_config.json" ]; then
        if [ -f "$PROJECT_ROOT/extensions_config.example.json" ]; then
            cp "$PROJECT_ROOT/extensions_config.example.json" "$PROJECT_ROOT/extensions_config.json"
            echo -e "${BLUE}Created extensions_config.json from example${NC}"
        else
            echo "{}" > "$PROJECT_ROOT/extensions_config.json"
            echo -e "${BLUE}Created empty extensions_config.json${NC}"
        fi
    fi

    echo "Building and starting containers..."
    cd "$DOCKER_DIR" && $COMPOSE_CMD up --build -d --remove-orphans $services
    echo ""
    echo "=========================================="
    echo "  DeerFlow Docker is starting!"
    echo "=========================================="
    echo ""
    echo "  🌐 Application: http://localhost:2026"
    echo "  ⚡ v2 Dev:      http://localhost:2026/v2-dev/"
    echo "  📦 v2 Release:  http://localhost:2026/v2/"
    echo "  📡 API Gateway: http://localhost:2026/api/*"
    echo "  🤖 LangGraph:   http://localhost:2026/api/langgraph/*"
    echo ""
    echo "  📋 View logs: make docker-logs"
    echo "  🛑 Stop:      make docker-stop"
    echo ""
}

# View Docker development logs
logs() {
    local service=""
    
    case "$1" in
        --frontend)
            service="frontend"
            echo -e "${BLUE}Viewing frontend logs...${NC}"
            ;;
        --frontend-v2-dev)
            service="frontend-v2-dev"
            echo -e "${BLUE}Viewing frontend-v2-dev logs...${NC}"
            ;;
        --frontend-v2-release)
            service="frontend-v2-release"
            echo -e "${BLUE}Viewing frontend-v2-release logs...${NC}"
            ;;
        --gateway)
            service="gateway"
            echo -e "${BLUE}Viewing gateway logs...${NC}"
            ;;
        --nginx)
            service="nginx"
            echo -e "${BLUE}Viewing nginx logs...${NC}"
            ;;
        --provisioner)
            service="provisioner"
            echo -e "${BLUE}Viewing provisioner logs...${NC}"
            ;;
        "")
            echo -e "${BLUE}Viewing all logs...${NC}"
            ;;
        *)
            echo -e "${YELLOW}Unknown option: $1${NC}"
            echo "Usage: $0 logs [--frontend|--frontend-v2-dev|--frontend-v2-release|--gateway|--nginx|--provisioner]"
            exit 1
            ;;
    esac
    
    cd "$DOCKER_DIR" && $COMPOSE_CMD logs -f $service
}

# Stop Docker development environment
stop() {
    # DEER_FLOW_ROOT is referenced in docker-compose-dev.yaml; set it before
    # running compose down to suppress "variable is not set" warnings.
    if [ -z "$DEER_FLOW_ROOT" ]; then
        export DEER_FLOW_ROOT="$PROJECT_ROOT"
    fi
    echo "Stopping Docker development services..."
    cd "$DOCKER_DIR" && $COMPOSE_CMD down
    echo "Cleaning up sandbox containers..."
    "$SCRIPT_DIR/cleanup-containers.sh" deer-flow-sandbox 2>/dev/null || true
    echo -e "${GREEN}✓ Docker services stopped${NC}"
}

reset_state() {
    reset_runtime_state
}

# Restart Docker development environment
restart() {
    echo "========================================"
    echo "  Restarting DeerFlow Docker Services"
    echo "========================================"
    echo ""
    echo -e "${BLUE}Restarting containers...${NC}"
    cd "$DOCKER_DIR" && $COMPOSE_CMD restart
    echo ""
    echo -e "${GREEN}✓ Docker services restarted${NC}"
    echo ""
    echo "  🌐 Application: http://localhost:2026"
    echo "  ⚡ v2 Dev:      http://localhost:2026/v2-dev/"
    echo "  📦 v2 Release:  http://localhost:2026/v2/"
    echo "  📋 View logs: make docker-dev-logs"
    echo ""
}

# Show help
help() {
    echo "DeerFlow Docker Management Script"
    echo ""
    echo "Usage: $0 <command> [options]"
    echo ""
    echo "Commands:"
    echo "  init          - Pull the sandbox image (speeds up first Pod startup)"
    echo "  start [--reset-state] - Start Docker services (auto-detects sandbox mode from config.yaml)"
    echo "  restart       - Restart all running Docker services"
    echo "  logs [option] - View Docker development logs"
    echo "                  --frontend   View frontend logs only"
    echo "                  --frontend-v2-dev View Solid dev frontend logs only"
    echo "                  --frontend-v2-release View Solid release frontend logs only"
    echo "                  --gateway    View gateway logs only"
    echo "                  --nginx      View nginx logs only"
    echo "                  --provisioner View provisioner logs only"
    echo "  stop          - Stop Docker development services"
    echo "  reset-state   - Clear LangGraph state, thread data, and provisioner-created sandbox resources"
    echo "  help          - Show this help message"
    echo ""
}

main() {
    # Main command dispatcher
    case "$1" in
        init)
            init
            ;;
        start)
            start "$2"
            ;;
        restart)
            restart
            ;;
        logs)
            logs "$2"
            ;;
        stop)
            stop
            ;;
        reset-state)
            reset_state
            ;;
        help|--help|-h|"")
            help
            ;;
        *)
            echo -e "${YELLOW}Unknown command: $1${NC}"
            echo ""
            help
            exit 1
            ;;
    esac
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
    main "$@"
fi
