#!/bin/bash
set -e

PROFILE="minikube"
K8S_DIR="$(cd "$(dirname "$0")/../k8s" && pwd)"

echo "==> Aplicando manifests no cluster '$PROFILE'..."
echo ""

echo "--- [1/6] Namespace + Credenciais ---"
kubectl apply -f "$K8S_DIR/00-namespace.yaml" --context "$PROFILE"
kubectl apply -f "$K8S_DIR/secrets/app-credentials.yaml" --context "$PROFILE"

echo ""
echo "--- [2/6] StorageClass e Volumes ---"
kubectl apply -f "$K8S_DIR/storage/01-storageclass.yaml" --context "$PROFILE"
kubectl apply -f "$K8S_DIR/storage/02-pvs.yaml" --context "$PROFILE"
kubectl apply -f "$K8S_DIR/storage/03-pvcs.yaml" --context "$PROFILE"

echo ""
echo "--- [3/6] Build imagem pedroflix-web ---"
WEB_DIR="$(cd "$(dirname "$0")/../pedroflix-web" && pwd)"
docker build -t pedroflix-web:latest "$WEB_DIR"
minikube image load pedroflix-web:latest --profile "$PROFILE" --overwrite=true
echo "    Imagem pedroflix-web carregada no minikube."

echo ""
echo "--- [4/6] Deployments ---"
kubectl apply -f "$K8S_DIR/deployments/" --context "$PROFILE"

echo ""
echo "--- [5/6] Services ---"
kubectl apply -f "$K8S_DIR/services/services.yaml" --context "$PROFILE"

echo ""
echo "--- [6/6] Ingress ---"
kubectl apply -f "$K8S_DIR/ingress/ingress.yaml" --context "$PROFILE"

echo ""
echo "--- [7/7] Configarr ---"
kubectl apply -f "$K8S_DIR/configarr/configmap.yaml" --context "$PROFILE"
kubectl apply -f "$K8S_DIR/configarr/secret.yaml" --context "$PROFILE"
kubectl apply -f "$K8S_DIR/configarr/cronjob.yaml" --context "$PROFILE"

echo ""
echo "==> Aguardando pods ficarem prontos..."
kubectl rollout status deployment/jellyfin     -n pedroflix --timeout=120s --context "$PROFILE"
kubectl rollout status deployment/radarr       -n pedroflix --timeout=120s --context "$PROFILE"
kubectl rollout status deployment/sonarr       -n pedroflix --timeout=120s --context "$PROFILE"
kubectl rollout status deployment/prowlarr     -n pedroflix --timeout=120s --context "$PROFILE"
kubectl rollout status deployment/bazarr       -n pedroflix --timeout=120s --context "$PROFILE"
kubectl rollout status deployment/qbittorrent  -n pedroflix --timeout=120s --context "$PROFILE"

echo ""
echo "==> Todos os pods estão rodando!"
kubectl get pods -n pedroflix --context "$PROFILE"

echo ""
MINIKUBE_IP=$(minikube ip --profile "$PROFILE")
echo "==> Adicione ao /etc/hosts (requer sudo):"
echo "    sudo bash -c 'echo \"$MINIKUBE_IP  jellyfin.local radarr.local sonarr.local prowlarr.local bazarr.local qbittorrent.local flaresolverr.local search.local\" >> /etc/hosts'"
echo ""
echo "==> Acesso (porta 32266 via NodePort do Ingress):"
echo "  Jellyfin    → http://jellyfin.local:32266"
echo "  Radarr      → http://radarr.local:32266"
echo "  Sonarr      → http://sonarr.local:32266"
echo "  Prowlarr    → http://prowlarr.local:32266"
echo "  Bazarr      → http://bazarr.local:32266"
echo "  qBittorrent → http://qbittorrent.local:32266"
echo "  Busca       → http://search.local:32266"
echo ""
echo "==> Para acesso da rede local (iPad, TV etc), inicie os port-forwards:"
echo "  systemctl --user enable --now jellyfin-forward.service"
echo "  systemctl --user enable --now pedroflix-search-forward.service"
echo ""
echo "==> Próximo passo: execute 05-configure.sh"
