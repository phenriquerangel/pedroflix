#!/bin/bash
set -e

PROFILE="minikube"
MOUNT_SRC="/run/media/pedro/Nvme_1TB/pedroflix"
MOUNT_DST="/mnt/pedroflix"

echo "==> Verificando se o diretório de dados existe..."
if [ ! -d "$MOUNT_SRC" ]; then
  echo "ERRO: $MOUNT_SRC não encontrado. Execute 01-setup-host.sh primeiro."
  exit 1
fi

echo "==> Verificando cluster existente no profile '$PROFILE'..."
if ! minikube status --profile "$PROFILE" &>/dev/null; then
  echo "ERRO: Nenhum cluster encontrado no profile '$PROFILE'."
  echo "      Certifique-se de que o minikube está rodando antes de continuar."
  exit 1
fi

echo "==> Habilitando addon ingress (se ainda não estiver ativo)..."
minikube addons enable ingress --profile "$PROFILE"

echo ""
echo "==> Verificando CNI (Flannel)..."
if ! kubectl get daemonset kube-flannel-ds -n kube-flannel --context "$PROFILE" &>/dev/null; then
  echo "    Flannel não encontrado. Instalando..."
  FLANNEL_TMP=$(mktemp /tmp/kube-flannel.XXXXXX.yml)
  curl -sL https://github.com/flannel-io/flannel/releases/latest/download/kube-flannel.yml -o "$FLANNEL_TMP"
  python3 -c "
import sys
content = open('$FLANNEL_TMP').read()
patched = content.replace(
    '        - --kube-subnet-mgr\n',
    '        - --kube-subnet-mgr\n        - --iface=eth0\n'
)
open('$FLANNEL_TMP', 'w').write(patched)
"
  kubectl apply -f "$FLANNEL_TMP" --context "$PROFILE"
  rm -f "$FLANNEL_TMP"
  kubectl rollout status daemonset/kube-flannel-ds -n kube-flannel --timeout=120s --context "$PROFILE"
  echo "    Flannel instalado com sucesso."
else
  echo "    Flannel já instalado. OK."
fi

echo ""
echo "==> Iniciando mount do disco NVMe no cluster..."
echo "    Host : $MOUNT_SRC"
echo "    Node : $MOUNT_DST"
echo ""
echo "ATENÇÃO: O processo de mount ficará rodando em background (PID salvo em /tmp/minikube-mount.pid)"
echo "         Para encerrar: kill \$(cat /tmp/minikube-mount.pid)"
echo ""

minikube mount --profile "$PROFILE" "${MOUNT_SRC}:${MOUNT_DST}" &
MOUNT_PID=$!
echo $MOUNT_PID > /tmp/minikube-mount.pid

sleep 3
if ! kill -0 $MOUNT_PID 2>/dev/null; then
  echo "ERRO: O processo de mount falhou. Verifique permissões e tente novamente."
  exit 1
fi

echo "==> Mount ativo (PID: $MOUNT_PID)"
echo "==> Próximo passo: execute 03-setup-nodes.sh"
