#!/bin/bash
set -e

PROFILE="minikube"
MOUNT_DST="/mnt/pedroflix"

BEFORE=$(kubectl get nodes --context "$PROFILE" \
  --selector='!node-role.kubernetes.io/control-plane' \
  -o jsonpath='{.items[*].metadata.name}')

echo "==> Adicionando worker node ao cluster '$PROFILE'..."
minikube node add --worker --profile "$PROFILE"

echo ""
echo "==> Identificando o novo node..."
WORKER_NODE=""
for i in $(seq 1 30); do
  ALL=$(kubectl get nodes --context "$PROFILE" \
    --selector='!node-role.kubernetes.io/control-plane' \
    -o jsonpath='{.items[*].metadata.name}')
  for n in $ALL; do
    if ! echo "$BEFORE" | grep -qw "$n"; then
      WORKER_NODE="$n"
      break 2
    fi
  done
  sleep 2
done

if [ -z "$WORKER_NODE" ]; then
  echo "ERRO: Não foi possível identificar o novo node após 60s."
  exit 1
fi

echo ""
echo "==> Aguardando o node '$WORKER_NODE' ficar Ready..."
kubectl wait node "$WORKER_NODE" \
  --for=condition=Ready \
  --timeout=180s \
  --context "$PROFILE"

echo ""
echo "==> Worker node identificado: $WORKER_NODE"

echo ""
echo "==> Aplicando label 'role=media'..."
kubectl label node "$WORKER_NODE" role=media --overwrite --context "$PROFILE"

echo ""
echo "==> Aplicando taint 'dedicated=media:NoSchedule'..."
kubectl taint node "$WORKER_NODE" dedicated=media:NoSchedule --overwrite --context "$PROFILE"

echo ""
echo "==> Verificando se o mount está acessível dentro do worker node..."
minikube ssh --profile "$PROFILE" --node "$WORKER_NODE" \
  "ls ${MOUNT_DST} && echo 'Mount OK'" || \
  echo "AVISO: Mount ainda não visível. Certifique-se de que 02-start-cluster.sh está rodando."

echo ""
echo "==> Nodes do cluster:"
kubectl get nodes --show-labels --context "$PROFILE"

echo ""
echo "==> Próximo passo: execute 04-deploy.sh"
