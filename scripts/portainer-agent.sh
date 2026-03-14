#!/usr/bin/env bash
set -euo pipefail

MANIFEST_URL="${PORTAINER_AGENT_MANIFEST_URL:-https://downloads.portainer.io/ce-sts/portainer-agent-k8s-nodeport.yaml}"
NAMESPACE="${PORTAINER_AGENT_NAMESPACE:-portainer}"
CMD="${1:-status}"

usage() {
    cat <<'EOF'
Usage: scripts/portainer-agent.sh [install|status|uninstall]

Commands:
  install    Install or update the official Portainer Kubernetes agent manifest
  status     Show Portainer agent namespace, deployment, pod, and service status
  uninstall  Remove the Portainer agent resources from Kubernetes

Environment:
  PORTAINER_AGENT_MANIFEST_URL  Override the manifest URL
  PORTAINER_AGENT_NAMESPACE     Override the namespace (default: portainer)
EOF
}

install_agent() {
    echo "Installing Portainer agent from ${MANIFEST_URL}"
    curl -fsSL "${MANIFEST_URL}" | kubectl apply -f -
    kubectl rollout status deployment/portainer-agent -n "${NAMESPACE}" --timeout=180s
    status_agent
}

status_agent() {
    kubectl get ns "${NAMESPACE}" >/dev/null 2>&1 || {
        echo "Namespace ${NAMESPACE} does not exist."
        exit 1
    }

    kubectl get all -n "${NAMESPACE}" -o wide
}

uninstall_agent() {
    echo "Removing Portainer agent resources from namespace ${NAMESPACE}"
    curl -fsSL "${MANIFEST_URL}" | kubectl delete -f - --ignore-not-found
    kubectl delete namespace "${NAMESPACE}" --ignore-not-found
}

case "${CMD}" in
    install)
        install_agent
        ;;
    status)
        status_agent
        ;;
    uninstall)
        uninstall_agent
        ;;
    help|--help|-h)
        usage
        ;;
    *)
        echo "Unknown command: ${CMD}" >&2
        usage
        exit 1
        ;;
esac
