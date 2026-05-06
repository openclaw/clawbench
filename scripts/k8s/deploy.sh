#!/usr/bin/env bash
# Deploy ClawBench evals on Kubernetes (works on OpenShift too).
#
# 0-to-hero pipeline:
#   Step 0: Create a cluster (see --help for Kind instructions)
#   Step 1: Deploy OpenClaw gateway         (optional — bring your own)
#   Step 2: Deploy MLflow tracking server   (optional — bring your own)
#   Step 3: Run evals via sidecar           (add / remove)
#
# Usage:
#   ./scripts/k8s/deploy.sh                        # Full deploy: OpenClaw + MLflow + eval
#   ./scripts/k8s/deploy.sh --openclaw-only         # Step 1: deploy OpenClaw gateway
#   ./scripts/k8s/deploy.sh --mlflow-only           # Step 2: deploy MLflow
#   ./scripts/k8s/deploy.sh --add-sidecar           # Step 3: add eval sidecar (starts eval)
#   ./scripts/k8s/deploy.sh --remove-sidecar        # Step 3: remove eval sidecar
#   ./scripts/k8s/deploy.sh --logs                  # Tail clawbench sidecar logs
#   ./scripts/k8s/deploy.sh --teardown              # Delete eval namespace (keeps MLflow)
#
# Environment (required):
#   CLAWBENCH_NAMESPACE            Namespace for OpenClaw + eval
#   OPENAI_API_KEY                 Model provider API key (or another provider key)
#
# Environment (optional):
#   CLAWBENCH_IMAGE                Clawbench image (default: quay.io/sallyom/clawbench:latest)
#   OPENCLAW_IMAGE                 OpenClaw image (default: ghcr.io/openclaw/openclaw:latest)
#   CLAWBENCH_MODEL                Model to eval (default: openai/gpt-5.5)
#   MLFLOW_NAMESPACE               MLflow namespace (default: mlflow)
#   MLFLOW_TRACKING_URI            External MLflow URI (skips MLflow deploy if set)
#   MLFLOW_EXPERIMENT_ID           MLflow experiment ID
#   MLFLOW_EXPERIMENT_NAME         MLflow experiment name
#   MLFLOW_IMAGE                   MLflow image (default: ghcr.io/mlflow/mlflow:v2.21.3)
#   ANTHROPIC_API_KEY              Anthropic key (added to secret if set)
#   OPENROUTER_API_KEY             OpenRouter key (added to secret if set)
#   GEMINI_API_KEY                 Gemini key (added to secret if set)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
NS="${CLAWBENCH_NAMESPACE:-}"
MLFLOW_NS="${MLFLOW_NAMESPACE:-mlflow}"
CLAWBENCH_IMG="${CLAWBENCH_IMAGE:-quay.io/sallyom/clawbench:latest}"
OPENCLAW_IMG="${OPENCLAW_IMAGE:-ghcr.io/openclaw/openclaw:latest}"
MLFLOW_IMG="${MLFLOW_IMAGE:-ghcr.io/mlflow/mlflow:v2.21.3}"

command -v kubectl &>/dev/null || { echo "Missing: kubectl" >&2; exit 1; }
kubectl cluster-info &>/dev/null || { echo "Cannot connect to cluster. Check kubeconfig." >&2; exit 1; }

# ---------------------------------------------------------------------------
if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  cat <<'HELP'
ClawBench Kubernetes Deployment
===============================

0-to-hero pipeline for running ClawBench evals on Kubernetes.

  Step 0: Create a cluster
          For local testing with Kind, see:
          https://github.com/openclaw/openclaw/blob/main/docs/install/kubernetes.md#local-testing-with-kind

  Step 1: Deploy OpenClaw gateway (optional — skip if you have one)
  Step 2: Deploy MLflow tracking server (optional — skip if you have one)
  Step 3: Run evals via sidecar (add/remove to OpenClaw deployment)

Usage:
  ./scripts/k8s/deploy.sh                    Full deploy (steps 1+2+3)
  ./scripts/k8s/deploy.sh --openclaw-only     Step 1: OpenClaw only
  ./scripts/k8s/deploy.sh --mlflow-only       Step 2: MLflow only
  ./scripts/k8s/deploy.sh --add-sidecar       Step 3: add eval sidecar (starts eval)
  ./scripts/k8s/deploy.sh --remove-sidecar    Step 3: remove eval sidecar
  ./scripts/k8s/deploy.sh --logs              Tail clawbench sidecar logs
  ./scripts/k8s/deploy.sh --teardown          Delete eval namespace (keeps MLflow)

Required environment:
  CLAWBENCH_NAMESPACE          Namespace for OpenClaw + eval
  OPENAI_API_KEY               Model provider API key (or ANTHROPIC_API_KEY, etc.)

Optional environment:
  CLAWBENCH_IMAGE              Clawbench image (default: quay.io/sallyom/clawbench:latest)
  OPENCLAW_IMAGE               OpenClaw image (default: ghcr.io/openclaw/openclaw:latest)
  CLAWBENCH_MODEL              Model to eval (default: openai/gpt-5.5)
  MLFLOW_NAMESPACE             MLflow namespace (default: mlflow)
  MLFLOW_TRACKING_URI          External MLflow URI (skips MLflow deploy)
  MLFLOW_EXPERIMENT_ID         MLflow experiment ID
  MLFLOW_EXPERIMENT_NAME       MLflow experiment name
  MLFLOW_IMAGE                 MLflow image (default: ghcr.io/mlflow/mlflow:v2.21.3)
  ANTHROPIC_API_KEY            Anthropic key (added to secret if set)
  OPENROUTER_API_KEY           OpenRouter key (added to secret if set)
  GEMINI_API_KEY               Gemini key (added to secret if set)

Works on Kubernetes and OpenShift.
HELP
  exit 0
fi

if [[ -z "$NS" ]]; then
  echo "CLAWBENCH_NAMESPACE is required." >&2
  echo "  export CLAWBENCH_NAMESPACE=clawbench-eval" >&2
  exit 1
fi

MODE="full"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --openclaw-only)   MODE="openclaw-only" ;;
    --mlflow-only)     MODE="mlflow-only" ;;
    --add-sidecar)     MODE="add-sidecar" ;;
    --remove-sidecar)  MODE="remove-sidecar" ;;
    --logs)            MODE="logs" ;;
    --teardown)        MODE="teardown" ;;
    *) echo "Unknown option: $1" >&2; exit 1 ;;
  esac
  shift
done

# ---------------------------------------------------------------------------
# --logs
# ---------------------------------------------------------------------------
if [[ "$MODE" == "logs" ]]; then
  kubectl logs deploy/openclaw -c clawbench -n "$NS" -f
  exit 0
fi

# ---------------------------------------------------------------------------
# --teardown
# ---------------------------------------------------------------------------
if [[ "$MODE" == "teardown" ]]; then
  echo "Deleting namespace '$NS'..."
  kubectl delete namespace "$NS" --ignore-not-found
  echo "Done. MLflow namespace '$MLFLOW_NS' was not deleted."
  exit 0
fi

# ---------------------------------------------------------------------------
# --remove-sidecar
# ---------------------------------------------------------------------------
if [[ "$MODE" == "remove-sidecar" ]]; then
  echo "Removing clawbench sidecar from openclaw in namespace '$NS'..."
  INDEX=$(kubectl get deploy/openclaw -n "$NS" -o json \
    | python3 -c "import json,sys; cs=json.load(sys.stdin)['spec']['template']['spec']['containers']; print(next((i for i,c in enumerate(cs) if c['name']=='clawbench'),-1))")
  if [[ "$INDEX" == "-1" ]]; then
    echo "No clawbench sidecar found."
  else
    kubectl patch deploy/openclaw -n "$NS" --type=json \
      -p "[{\"op\":\"remove\",\"path\":\"/spec/template/spec/containers/$INDEX\"}]"
    echo "Sidecar removed."
  fi
  exit 0
fi

# ---------------------------------------------------------------------------
# Create namespace + secret
# ---------------------------------------------------------------------------
ensure_namespace_and_secret() {
  if ! kubectl get namespace "$NS" &>/dev/null; then
    echo "Creating namespace '$NS'..."
    kubectl create namespace "$NS"
  fi

  if ! kubectl get secret clawbench-secrets -n "$NS" &>/dev/null; then
    echo "Creating clawbench-secrets..."
    GATEWAY_TOKEN=$(python3 -c "import secrets,base64; print(base64.b64encode(secrets.token_bytes(32)).decode())")

    SECRET_ARGS=(
      --from-literal=OPENCLAW_GATEWAY_TOKEN="$GATEWAY_TOKEN"
    )
    [[ -n "${OPENAI_API_KEY:-}" ]] && SECRET_ARGS+=(--from-literal=OPENAI_API_KEY="$OPENAI_API_KEY")
    [[ -n "${ANTHROPIC_API_KEY:-}" ]] && SECRET_ARGS+=(--from-literal=ANTHROPIC_API_KEY="$ANTHROPIC_API_KEY")
    [[ -n "${OPENROUTER_API_KEY:-}" ]] && SECRET_ARGS+=(--from-literal=OPENROUTER_API_KEY="$OPENROUTER_API_KEY")
    [[ -n "${GEMINI_API_KEY:-}" ]] && SECRET_ARGS+=(--from-literal=GEMINI_API_KEY="$GEMINI_API_KEY")

    if [[ ${#SECRET_ARGS[@]} -eq 1 ]]; then
      echo "Warning: No API keys provided. Set OPENAI_API_KEY or another provider key." >&2
    fi

    kubectl create secret generic clawbench-secrets -n "$NS" "${SECRET_ARGS[@]}"
    echo "  Gateway token: generated"
    [[ -n "${OPENAI_API_KEY:-}" ]] && echo "  OPENAI_API_KEY: set"
    [[ -n "${ANTHROPIC_API_KEY:-}" ]] && echo "  ANTHROPIC_API_KEY: set"
    [[ -n "${OPENROUTER_API_KEY:-}" ]] && echo "  OPENROUTER_API_KEY: set"
    [[ -n "${GEMINI_API_KEY:-}" ]] && echo "  GEMINI_API_KEY: set"
  else
    echo "Secret clawbench-secrets already exists in '$NS'."
  fi
}

# ---------------------------------------------------------------------------
# Step 1: Deploy OpenClaw
# ---------------------------------------------------------------------------
deploy_openclaw() {
  echo ""
  echo "Step 1: Deploying OpenClaw gateway (image: $OPENCLAW_IMG)..."

  kubectl apply -f "$SCRIPT_DIR/openclaw/configmap.yaml" -n "$NS"

  # Patch gateway config with custom OpenAI-compatible base URL
  if [[ -n "${OPENAI_API_BASE:-}" ]]; then
    echo "  Patching gateway config: models.providers.openai.baseUrl = $OPENAI_API_BASE"
    EXISTING_JSON=$(kubectl get configmap openclaw-config -n "$NS" -o jsonpath='{.data.openclaw\.json}')
    PATCHED_JSON=$(echo "$EXISTING_JSON" | python3 -c "
import json, sys, os
cfg = json.load(sys.stdin)
openai_cfg = cfg.setdefault('models', {}).setdefault('providers', {}).setdefault('openai', {})
openai_cfg['baseUrl'] = os.environ['OPENAI_API_BASE']
openai_cfg.setdefault('models', [])
json.dump(cfg, sys.stdout, indent=2)
")
    kubectl create configmap openclaw-config -n "$NS" \
      --from-literal="openclaw.json=$PATCHED_JSON" \
      --dry-run=client -o yaml | kubectl apply -f - -n "$NS" >/dev/null
  fi

  kubectl apply -f "$SCRIPT_DIR/openclaw/pvc.yaml" -n "$NS"
  kubectl apply -f "$SCRIPT_DIR/openclaw/service.yaml" -n "$NS"

  if [[ "$OPENCLAW_IMG" != "ghcr.io/openclaw/openclaw:latest" ]]; then
    kubectl apply -f "$SCRIPT_DIR/openclaw/deployment.yaml" -n "$NS"
    kubectl set image "deploy/openclaw" "gateway=$OPENCLAW_IMG" -n "$NS"
  else
    kubectl apply -f "$SCRIPT_DIR/openclaw/deployment.yaml" -n "$NS"
  fi

  echo "Waiting for OpenClaw rollout..."
  kubectl rollout status deploy/openclaw -n "$NS" --timeout=180s || \
    echo "  (rollout still in progress)"
  echo "OpenClaw deployed."
}

# ---------------------------------------------------------------------------
# Step 2: Deploy MLflow
# ---------------------------------------------------------------------------
deploy_mlflow() {
  if [[ -n "${MLFLOW_TRACKING_URI:-}" ]]; then
    echo ""
    echo "Step 2: Skipping MLflow deploy (MLFLOW_TRACKING_URI is set: $MLFLOW_TRACKING_URI)"
    return
  fi

  echo ""
  echo "Step 2: Deploying MLflow (namespace: $MLFLOW_NS, image: $MLFLOW_IMG)..."

  if ! kubectl get namespace "$MLFLOW_NS" &>/dev/null; then
    kubectl create namespace "$MLFLOW_NS"
  fi

  kubectl apply -f "$SCRIPT_DIR/mlflow/pvc.yaml" -n "$MLFLOW_NS"
  kubectl apply -f "$SCRIPT_DIR/mlflow/service.yaml" -n "$MLFLOW_NS"

  if [[ "$MLFLOW_IMG" != "ghcr.io/mlflow/mlflow:v2.21.3" ]]; then
    kubectl apply -f "$SCRIPT_DIR/mlflow/deployment.yaml" -n "$MLFLOW_NS"
    kubectl set image "deploy/mlflow" "mlflow=$MLFLOW_IMG" -n "$MLFLOW_NS"
  else
    kubectl apply -f "$SCRIPT_DIR/mlflow/deployment.yaml" -n "$MLFLOW_NS"
  fi

  echo "Waiting for MLflow rollout..."
  kubectl rollout status deploy/mlflow -n "$MLFLOW_NS" --timeout=120s || \
    echo "  (rollout still in progress)"

  MLFLOW_TRACKING_URI="http://mlflow-service.${MLFLOW_NS}.svc.cluster.local:5000"
  echo "MLflow deployed: $MLFLOW_TRACKING_URI"
}

# ---------------------------------------------------------------------------
# Step 3: Add clawbench sidecar (starts eval)
# ---------------------------------------------------------------------------
add_sidecar() {
  echo ""
  echo "Step 3: Adding clawbench eval sidecar..."

  echo "Applying clawbench ConfigMap..."
  kubectl apply -f "$SCRIPT_DIR/manifests/configmap.yaml" -n "$NS" >/dev/null

  if [[ -n "${CLAWBENCH_MODEL:-}" ]]; then
    kubectl patch configmap clawbench-config -n "$NS" \
      --type merge -p "{\"data\":{\"CLAWBENCH_MODEL\":\"$CLAWBENCH_MODEL\"}}" >/dev/null
    echo "  Model: $CLAWBENCH_MODEL"
  fi

  if [[ -n "${OPENAI_API_BASE:-}" ]]; then
    kubectl patch configmap clawbench-config -n "$NS" \
      --type merge -p "{\"data\":{\"OPENAI_API_BASE\":\"$OPENAI_API_BASE\"}}" >/dev/null
    echo "  OpenAI API base: $OPENAI_API_BASE"
  fi

  # Patch MLflow settings into ConfigMap
  PATCH_DATA=""
  MLFLOW_URI="${MLFLOW_TRACKING_URI:-http://mlflow-service.${MLFLOW_NS}.svc.cluster.local:5000}"
  PATCH_DATA="\"MLFLOW_TRACKING_URI\":\"$MLFLOW_URI\""
  if [[ -n "${MLFLOW_EXPERIMENT_ID:-}" ]]; then
    PATCH_DATA="$PATCH_DATA,\"MLFLOW_EXPERIMENT_ID\":\"$MLFLOW_EXPERIMENT_ID\""
  fi
  if [[ -n "${MLFLOW_EXPERIMENT_NAME:-}" ]]; then
    PATCH_DATA="$PATCH_DATA,\"MLFLOW_EXPERIMENT_NAME\":\"$MLFLOW_EXPERIMENT_NAME\""
  fi
  kubectl patch configmap clawbench-config -n "$NS" \
    --type merge -p "{\"data\":{$PATCH_DATA}}" >/dev/null
  echo "  MLflow URI: $MLFLOW_URI"
  [[ -n "${MLFLOW_EXPERIMENT_ID:-}" ]] && echo "  MLflow experiment ID: $MLFLOW_EXPERIMENT_ID"
  [[ -n "${MLFLOW_EXPERIMENT_NAME:-}" ]] && echo "  MLflow experiment name: $MLFLOW_EXPERIMENT_NAME"

  # Check if sidecar already exists
  HAS_SIDECAR=$(kubectl get deploy/openclaw -n "$NS" -o json \
    | python3 -c "import json,sys; cs=json.load(sys.stdin)['spec']['template']['spec']['containers']; print('yes' if any(c['name']=='clawbench' for c in cs) else 'no')")

  if [[ "$HAS_SIDECAR" == "yes" ]]; then
    echo "Removing existing clawbench sidecar..."
    INDEX=$(kubectl get deploy/openclaw -n "$NS" -o json \
      | python3 -c "import json,sys; cs=json.load(sys.stdin)['spec']['template']['spec']['containers']; print(next(i for i,c in enumerate(cs) if c['name']=='clawbench'))")
    kubectl patch deploy/openclaw -n "$NS" --type=json \
      -p "[{\"op\":\"remove\",\"path\":\"/spec/template/spec/containers/$INDEX\"}]" >/dev/null
  fi

  # Find openclaw-home volume name
  HOME_VOLUME=$(kubectl get deploy/openclaw -n "$NS" -o json \
    | python3 -c "
import json, sys
spec = json.load(sys.stdin)['spec']['template']['spec']
for c in spec['containers']:
    if c['name'] == 'gateway':
        for vm in c.get('volumeMounts', []):
            if vm['mountPath'] == '/home/node/.openclaw':
                print(vm['name'])
                sys.exit(0)
print('openclaw-home')
")

  echo "Adding clawbench sidecar (image: $CLAWBENCH_IMG)..."

  # Check if results volume already exists
  HAS_RESULTS_VOL=$(kubectl get deploy/openclaw -n "$NS" -o json \
    | python3 -c "import json,sys; vs=json.load(sys.stdin)['spec']['template']['spec'].get('volumes',[]); print('yes' if any(v['name']=='clawbench-results' for v in vs) else 'no')")

  PATCH='[{"op":"add","path":"/spec/template/spec/containers/-","value":{'
  PATCH+='"name":"clawbench",'
  PATCH+='"image":"'"$CLAWBENCH_IMG"'",'
  PATCH+='"imagePullPolicy":"IfNotPresent",'
  PATCH+='"command":["/bin/bash","-c","echo \"Waiting for gateway on localhost:18789...\"\nfor i in $(seq 1 90); do\n  python3 -c \"import socket; s=socket.create_connection((\\\"127.0.0.1\\\",18789),2); s.close()\" 2>/dev/null && echo \"Gateway ready\" && break\n  sleep 2\ndone\n\nif [ -n \"${MLFLOW_TRACKING_URI:-}\" ]; then\n  echo \"Checking MLflow at ${MLFLOW_TRACKING_URI}...\"\n  python3 -c \"import httpx,os; r=httpx.get(os.environ[\\\"MLFLOW_TRACKING_URI\\\"]+\\\"/health\\\"); print(\\\"MLflow OK:\\\",r.status_code)\" 2>&1 || echo \"MLflow pre-check failed (will retry at log time)\"\nfi\n\necho \"Starting eval...\"\nclawbench run \\\n  --model \"${CLAWBENCH_MODEL}\" \\\n  --gateway-token \"${OPENCLAW_GATEWAY_TOKEN}\" \\\n  --runs \"${CLAWBENCH_RUNS}\" \\\n  --concurrency \"${CLAWBENCH_CONCURRENCY}\" \\\n  ${CLAWBENCH_JUDGE_MODEL:+--judge-model \"${CLAWBENCH_JUDGE_MODEL}\"} \\\n  $([ -n \"${CLAWBENCH_TASKS:-}\" ] && for t in ${CLAWBENCH_TASKS}; do printf -- \"-t %s \" \"$t\"; done) \\\n  -o /results/benchmark.json\nRC=$?\nif [ $RC -eq 0 ] && [ -n \"${MLFLOW_TRACKING_URI:-}\" ]; then\n  python scripts/log_to_mlflow.py /results/benchmark.json\nfi\necho \"ClawBench finished (exit=$RC)\"\nsleep infinity"],'
  PATCH+='"envFrom":[{"configMapRef":{"name":"clawbench-config"}}],'
  PATCH+='"env":[{"name":"OPENCLAW_GATEWAY_TOKEN","valueFrom":{"secretKeyRef":{"name":"clawbench-secrets","key":"OPENCLAW_GATEWAY_TOKEN"}}}],'
  PATCH+='"resources":{"requests":{"memory":"1Gi","cpu":"500m"},"limits":{"memory":"4Gi","cpu":"2"}},'
  PATCH+='"volumeMounts":[{"name":"'"$HOME_VOLUME"'","mountPath":"/home/node/.openclaw"},{"name":"clawbench-results","mountPath":"/results"},{"name":"tmp-volume","mountPath":"/tmp"}],'
  PATCH+='"securityContext":{"allowPrivilegeEscalation":false,"capabilities":{"drop":["ALL"]}}'
  PATCH+='}}'

  if [[ "$HAS_RESULTS_VOL" == "no" ]]; then
    PATCH+=',{"op":"add","path":"/spec/template/spec/volumes/-","value":{"name":"clawbench-results","emptyDir":{}}}'
  fi

  PATCH+=']'

  kubectl patch deploy/openclaw -n "$NS" --type=json -p "$PATCH" >/dev/null

  echo ""
  echo "Waiting for rollout..."
  kubectl rollout status deploy/openclaw -n "$NS" --timeout=300s 2>/dev/null || \
    echo "  (rollout timeout — eval runs for 30-60 min)"

  echo ""
  echo "Eval is running. Follow logs with:"
  echo "  ./scripts/k8s/deploy.sh --logs"
  echo ""
  echo "When finished, remove the sidecar with:"
  echo "  ./scripts/k8s/deploy.sh --remove-sidecar"
}

# ---------------------------------------------------------------------------
# Execute
# ---------------------------------------------------------------------------
case "$MODE" in
  full)
    ensure_namespace_and_secret
    deploy_openclaw
    deploy_mlflow
    add_sidecar
    ;;
  openclaw-only)
    ensure_namespace_and_secret
    deploy_openclaw
    echo ""
    echo "OpenClaw is running. Next steps:"
    echo "  ./scripts/k8s/deploy.sh --mlflow-only       # Deploy MLflow"
    echo "  ./scripts/k8s/deploy.sh --add-sidecar       # Start eval"
    ;;
  mlflow-only)
    deploy_mlflow
    ;;
  add-sidecar)
    if ! kubectl get deploy/openclaw -n "$NS" &>/dev/null; then
      echo "Deployment 'openclaw' not found in namespace '$NS'." >&2
      echo "Deploy OpenClaw first with: ./scripts/k8s/deploy.sh --openclaw-only" >&2
      exit 1
    fi
    add_sidecar
    ;;
esac
