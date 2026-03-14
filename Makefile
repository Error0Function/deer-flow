# DeerFlow - Unified Development Environment

.PHONY: help config check install dev dev-daemon start stop up down clean docker-init docker-start docker-reset-state docker-stop docker-logs docker-logs-frontend docker-logs-gateway docker-logs-summary docker-logs-errors docker-logs-events docker-logs-sandbox docker-logs-correlate portainer-agent-install portainer-agent-status portainer-agent-uninstall

PYTHON ?= python

help:
	@echo "DeerFlow Development Commands:"
	@echo "  make config          - Generate local config files (aborts if config already exists)"
	@echo "  make check           - Check if all required tools are installed"
	@echo "  make install         - Install all dependencies (frontend + backend)"
	@echo "  make setup-sandbox   - Pre-pull sandbox container image (recommended)"
	@echo "  make dev             - Start all services in development mode (with hot-reloading)"
	@echo "  make dev-daemon      - Start all services in background (daemon mode)"
	@echo "  make start           - Start all services in production mode (optimized, no hot-reloading)"
	@echo "  make stop            - Stop all running services"
	@echo "  make clean           - Clean up processes and temporary files"
	@echo ""
	@echo "Docker Production Commands:"
	@echo "  make up              - Build and start production Docker services (localhost:2026)"
	@echo "  make down            - Stop and remove production Docker containers"
	@echo ""
	@echo "Docker Development Commands:"
	@echo "  make docker-init     - Pre-pull the sandbox image into Docker and k3s/containerd"
	@echo "  make docker-start    - Start Docker services (mode-aware from config.yaml, localhost:2026)"
	@echo "  make docker-reset-state - Clear persisted LangGraph/thread/sandbox state"
	@echo "  make docker-stop     - Stop Docker development services"
	@echo "  make docker-logs     - View Docker development logs"
	@echo "  make docker-logs-frontend - View Docker frontend logs"
	@echo "  make docker-logs-gateway - View Docker gateway logs"
	@echo "  make docker-logs-summary  - Show unified log/runtime summary"
	@echo "  make docker-logs-errors   - Scan recent logs for warnings/errors"
	@echo "  make docker-logs-events   - Show recent k3s sandbox events"
	@echo "  make docker-logs-sandbox SANDBOX_ID=<id> - Inspect a sandbox Pod/Service end-to-end"
	@echo "  make docker-logs-correlate THREAD_ID=<id> [RUN_ID=<id>] [SANDBOX_ID=<id>] - Correlate IDs across logs/runtime"
	@echo ""
	@echo "Portainer Commands:"
	@echo "  make portainer-agent-install   - Install the official Portainer Kubernetes agent into k3s"
	@echo "  make portainer-agent-status    - Show Portainer agent status in k3s"
	@echo "  make portainer-agent-uninstall - Remove the Portainer Kubernetes agent from k3s"

config:
	@$(PYTHON) ./scripts/configure.py

# Check required tools
check:
	@$(PYTHON) ./scripts/check.py

# Install all dependencies
install:
	@echo "Installing backend dependencies..."
	@cd backend && uv sync
	@echo "Installing frontend dependencies..."
	@cd frontend && pnpm install
	@echo "✓ All dependencies installed"
	@echo ""
	@echo "=========================================="
	@echo "  Optional: Pre-pull Sandbox Image"
	@echo "=========================================="
	@echo ""
	@echo "If you plan to use Docker/Container-based sandbox, you can pre-pull the image:"
	@echo "  make setup-sandbox"
	@echo ""

# Pre-pull sandbox Docker image (optional but recommended)
setup-sandbox:
	@echo "=========================================="
	@echo "  Pre-pulling Sandbox Container Image"
	@echo "=========================================="
	@echo ""
	@IMAGE=$$(grep -A 20 "# sandbox:" config.yaml 2>/dev/null | grep "image:" | awk '{print $$2}' | head -1); \
	if [ -z "$$IMAGE" ]; then \
		IMAGE="ghcr.io/agent-infra/sandbox:latest"; \
		echo "Using default image: $$IMAGE"; \
	else \
		echo "Using configured image: $$IMAGE"; \
	fi; \
	echo ""; \
	if command -v container >/dev/null 2>&1 && [ "$$(uname)" = "Darwin" ]; then \
		echo "Detected Apple Container on macOS, pulling image..."; \
		container pull "$$IMAGE" || echo "⚠ Apple Container pull failed, will try Docker"; \
	fi; \
	if command -v docker >/dev/null 2>&1; then \
		echo "Pulling image using Docker..."; \
		docker pull "$$IMAGE"; \
		echo ""; \
		echo "✓ Sandbox image pulled successfully"; \
	else \
		echo "✗ Neither Docker nor Apple Container is available"; \
		echo "  Please install Docker: https://docs.docker.com/get-docker/"; \
		exit 1; \
	fi

# Start all services in development mode (with hot-reloading)
dev:
	@./scripts/serve.sh --dev

# Start all services in production mode (with optimizations)
start:
	@./scripts/serve.sh --prod

# Start all services in daemon mode (background)
dev-daemon:
	@./scripts/start-daemon.sh

# Stop all services
stop:
	@echo "Stopping all services..."
	@-pkill -f "langgraph dev" 2>/dev/null || true
	@-pkill -f "uvicorn src.gateway.app:app" 2>/dev/null || true
	@-pkill -f "next dev" 2>/dev/null || true
	@-pkill -f "next start" 2>/dev/null || true
	@-pkill -f "next-server" 2>/dev/null || true
	@-pkill -f "next-server" 2>/dev/null || true
	@-nginx -c $(PWD)/docker/nginx/nginx.local.conf -p $(PWD) -s quit 2>/dev/null || true
	@sleep 1
	@-pkill -9 nginx 2>/dev/null || true
	@echo "Cleaning up sandbox containers..."
	@-./scripts/cleanup-containers.sh deer-flow-sandbox 2>/dev/null || true
	@echo "✓ All services stopped"

# Clean up
clean: stop
	@echo "Cleaning up..."
	@-rm -rf backend/.deer-flow 2>/dev/null || true
	@-rm -rf backend/.langgraph_api 2>/dev/null || true
	@-rm -rf logs/*.log 2>/dev/null || true
	@echo "✓ Cleanup complete"

# ==========================================
# Docker Development Commands
# ==========================================

# Initialize Docker containers and install dependencies
docker-init:
	@./scripts/docker.sh init

# Start Docker development environment
docker-start:
	@./scripts/docker.sh start

docker-reset-state:
	@./scripts/docker.sh reset-state

# Stop Docker development environment
docker-stop:
	@./scripts/docker.sh stop

# View Docker development logs
docker-logs:
	@./scripts/docker.sh logs

# View Docker development logs
docker-logs-frontend:
	@./scripts/docker.sh logs --frontend
docker-logs-gateway:
	@./scripts/docker.sh logs --gateway

docker-logs-summary:
	@$(PYTHON) ./scripts/dev_logs.py summary

docker-logs-errors:
	@$(PYTHON) ./scripts/dev_logs.py errors

docker-logs-events:
	@$(PYTHON) ./scripts/dev_logs.py events

docker-logs-sandbox:
	@$(PYTHON) ./scripts/dev_logs.py inspect-sandbox $(if $(SANDBOX_ID),--sandbox-id $(SANDBOX_ID),) $(if $(POD),--pod $(POD),)

docker-logs-correlate:
	@$(PYTHON) ./scripts/dev_logs.py correlate $(if $(THREAD_ID),--thread-id $(THREAD_ID),) $(if $(RUN_ID),--run-id $(RUN_ID),) $(if $(SANDBOX_ID),--sandbox-id $(SANDBOX_ID),)

portainer-agent-install:
	@./scripts/portainer-agent.sh install

portainer-agent-status:
	@./scripts/portainer-agent.sh status

portainer-agent-uninstall:
	@./scripts/portainer-agent.sh uninstall

# ==========================================
# Production Docker Commands
# ==========================================

# Build and start production services
up:
	@./scripts/deploy.sh

# Stop and remove production containers
down:
	@./scripts/deploy.sh down
