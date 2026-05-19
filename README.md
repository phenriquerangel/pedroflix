# pedroflix

Media server pessoal rodando no Kubernetes (Minikube) com Jellyfin, Radarr, Sonarr, Prowlarr, Bazarr e qBittorrent.

---

## Arquitetura

```
Host (Linux)
└── Minikube (driver: docker, 3 nodes)
    ├── minikube        — control-plane + worker genérico
    ├── minikube-m02    — (removido / sem uso)
    └── minikube-m03    — worker com label role=media (taint dedicated=media:NoSchedule)
        │
        └── Namespace: pedroflix
            ├── jellyfin       :8096   — Media server
            ├── radarr         :7878   — Gerenciador de filmes
            ├── sonarr         :8989   — Gerenciador de séries
            ├── prowlarr       :9696   — Agregador de indexers
            ├── bazarr         :6767   — Legendas automáticas
            ├── qbittorrent    :8080   — Cliente torrent
            ├── flaresolverr   :8191   — Bypass Cloudflare
            ├── pedroflix-web  :5000   — App de busca (Flask)
            └── configarr      (CronJob diário 04h) — Aplica Trash Guide
```

**Acesso externo:** Ingress NGINX via NodePort `192.168.49.2:32266`

**Armazenamento:** SSD NVMe externo montado em `/run/media/pedro/Nvme_1TB/pedroflix/`  
montado dentro do cluster como `/mnt/pedroflix/` via `minikube mount`

---

## Pré-requisitos

```bash
# Instalar (se não tiver):
minikube   # https://minikube.sigs.k8s.io/docs/start/
kubectl    # https://kubernetes.io/docs/tasks/tools/
docker     # usado como driver do minikube
python3    # para os scripts de configuração
pip install pyyaml   # usado pelo 05-configure.sh (Bazarr)
```

---

## Setup do zero

### 0. Criar o cluster Minikube

```bash
minikube start \
  --driver=docker \
  --cpus=4 \
  --memory=6144 \
  --disk-size=20g \
  --kubernetes-version=v1.35.1 \
  --cni='' \
  --network-plugin=cni
```

> O control-plane precisa de ~2 CPU / 2 GB só para si. Os 4 CPUs e 6 GB são o mínimo
> razoável para rodar tudo junto.

---

### 1. Estrutura de diretórios no host

```bash
./scripts/01-setup-host.sh
```

Cria em `/run/media/pedro/Nvme_1TB/pedroflix/`:
```
config/
  jellyfin/ radarr/ sonarr/ prowlarr/ bazarr/ qbittorrent/
media/
  movies/   tv/
downloads/
  complete/ incomplete/
```

---

### 2. Preparar o cluster (Flannel + mount NVMe)

```bash
./scripts/02-start-cluster.sh
```

- Habilita addon `ingress`
- Instala Flannel CNI com `--iface=eth0`
- Inicia `minikube mount` em background (NVMe → `/mnt/pedroflix/` dentro do cluster)

> **Importante:** O processo `minikube mount` precisa ficar rodando em background.
> O PID fica em `/tmp/minikube-mount.pid`. Ao reiniciar a máquina, rode este script
> novamente antes de usar o cluster.

---

### 3. Adicionar worker node e rotulá-lo

```bash
./scripts/03-setup-nodes.sh
```

- Adiciona um novo worker node ao cluster
- Aplica `label role=media` e `taint dedicated=media:NoSchedule`
- Todos os pods de mídia só rodam neste node

> Se o cluster já tiver o worker node com o label correto, este passo pode ser pulado.
> Verifique com: `kubectl get nodes --show-labels`

---

### 4. Build da imagem e deploy

```bash
./scripts/04-deploy.sh
```

O que faz:
1. **Build** da imagem Docker `pedroflix-web:latest` a partir de `pedroflix-web/Dockerfile`
2. **Carrega** a imagem no Minikube (`minikube image load`) — sem registry externo
3. Aplica todos os manifests Kubernetes em `k8s/`
4. Aguarda todos os pods ficarem `Running`

---

### 5. Configuração automática

```bash
./scripts/05-configure.sh
```

Configura automaticamente, de forma idempotente:

| Step | O que faz |
|------|-----------|
| 1 | Lê credenciais do Secret `app-credentials` |
| 2 | Coleta API keys de Radarr, Sonarr, Prowlarr, Bazarr |
| 3 | qBittorrent: autentica (inclusive com senha temporária), seta `pedroflix`, configura `save_path` |
| 4 | Prowlarr: conecta FlareSolverr, Radarr e Sonarr |
| 5 | Radarr + Sonarr: configura download client (qBittorrent) e pastas raiz |
| 6 | Bazarr: configura PT-BR + EN, perfil de legendas, providers (OpenSubtitles.com) |
| 7 | Habilita autenticação Forms no Radarr, Sonarr, Prowlarr; adiciona bibliotecas no Jellyfin |
| 8 | Prowlarr: adiciona 9 indexers públicos (1337x, YTS, EZTV, TPB, Nyaa, LimeTorrents, KAT, TorrentDownloads, Knaben) |

---

### 6. Entradas no `/etc/hosts`

```bash
# Pegue o IP do minikube:
minikube ip   # → 192.168.49.2

# Adicione ao /etc/hosts:
sudo bash -c 'echo "192.168.49.2  jellyfin.local radarr.local sonarr.local prowlarr.local bazarr.local qbittorrent.local flaresolverr.local search.local" >> /etc/hosts'
```

---

### 7. Port-forwards para acesso da rede local (iPad, TV, celular, notebook)

Três serviços systemd fazem port-forward permanente:

| Serviço | Porta | Para quê |
|---------|-------|----------|
| `jellyfin-forward` | `:8096` | App Jellyfin no iOS/TV (protocolo nativo) |
| `pedroflix-search-forward` | `:5001` | App de busca no navegador |
| `pedroflix-ingress-forward` | `:8080` | **Todos os apps** via hostname (Radarr, Sonarr, etc.) |

```bash
# Recriar os arquivos de serviço (se necessário):
cat > ~/.local/bin/tcp-proxy.py << 'EOF'
#!/usr/bin/env python3
import sys, socket, threading, signal

def pipe(src, dst):
    try:
        while True:
            data = src.recv(65536)
            if not data: break
            dst.sendall(data)
    except OSError: pass
    finally:
        for s in (src, dst):
            try: s.shutdown(socket.SHUT_RDWR)
            except OSError: pass
            try: s.close()
            except OSError: pass

def handle(client, target_host, target_port):
    try:
        server = socket.create_connection((target_host, target_port), timeout=10)
        server.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        client.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        t1 = threading.Thread(target=pipe, args=(client, server), daemon=True)
        t2 = threading.Thread(target=pipe, args=(server, client), daemon=True)
        t1.start(); t2.start(); t1.join(); t2.join()
    except OSError:
        try: client.close()
        except OSError: pass

def main():
    listen_port, target_host, target_port = int(sys.argv[1]), sys.argv[2], int(sys.argv[3])
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("0.0.0.0", listen_port))
    srv.listen(128)
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    print("Proxying 0.0.0.0:%d → %s:%d" % (listen_port, target_host, target_port), flush=True)
    while True:
        try:
            client, _ = srv.accept()
            threading.Thread(target=handle, args=(client, target_host, target_port), daemon=True).start()
        except OSError: break

main()
EOF
chmod +x ~/.local/bin/tcp-proxy.py

cat > ~/.config/systemd/user/jellyfin-forward.service << 'EOF'
[Unit]
Description=Jellyfin TCP proxy para rede local (8096)
After=network.target

[Service]
ExecStart=/usr/bin/python3 /home/pedro/.local/bin/tcp-proxy.py 8096 192.168.49.2 30096
Restart=always
RestartSec=3
StartLimitIntervalSec=0

[Install]
WantedBy=default.target
EOF

cat > ~/.config/systemd/user/pedroflix-search-forward.service << 'EOF'
[Unit]
Description=Pedroflix Search port-forward para rede local (5001)
After=network.target

[Service]
ExecStart=/usr/local/bin/kubectl port-forward service/pedroflix-web 5001:5000 --address 0.0.0.0 -n pedroflix --context minikube
Restart=always
RestartSec=3
StartLimitIntervalSec=0

[Install]
WantedBy=default.target
EOF

cat > ~/.config/systemd/user/pedroflix-ingress-forward.service << 'EOF'
[Unit]
Description=Pedroflix Ingress port-forward para rede local (8080)
After=network.target

[Service]
ExecStart=/usr/local/bin/kubectl port-forward service/ingress-nginx-controller 8080:80 --address 0.0.0.0 -n ingress-nginx --context minikube
Restart=always
RestartSec=3
StartLimitIntervalSec=0

[Install]
WantedBy=default.target
EOF

# Habilitar e iniciar
systemctl --user daemon-reload
systemctl --user enable --now jellyfin-forward.service
systemctl --user enable --now pedroflix-search-forward.service
systemctl --user enable --now pedroflix-ingress-forward.service

# Para os serviços sobreviverem ao logout:
loginctl enable-linger pedro
```

**Acesso direto (sem precisar de /etc/hosts no dispositivo):**
- **Jellyfin** (app iOS/Apple TV/Android): `http://192.168.31.254:8096`
- **Busca**: `http://192.168.31.254:5001`

**Acesso via hostname (browser no notebook/iPad — requer /etc/hosts):**

Adicione em `/etc/hosts` do dispositivo:
```
192.168.31.254  jellyfin.local radarr.local sonarr.local prowlarr.local bazarr.local qbittorrent.local search.local
```

Depois acesse via porta `:8080`:

| App | URL na rede local |
|-----|-------------------|
| Jellyfin | http://jellyfin.local:8080 |
| Radarr | http://radarr.local:8080 |
| Sonarr | http://sonarr.local:8080 |
| Prowlarr | http://prowlarr.local:8080 |
| Bazarr | http://bazarr.local:8080 |
| qBittorrent | http://qbittorrent.local:8080 |
| Busca | http://search.local:8080 |

> **No macOS/Linux:** `sudo nano /etc/hosts`  
> **No Windows:** `C:\Windows\System32\drivers\etc\hosts` (como administrador)  
> **No iOS/iPadOS:** não é possível editar diretamente — use um app DNS como [DNS Override](https://apps.apple.com/app/dns-override/id1080500508) ou configure o roteador

---

### 8. Wizard do Jellyfin (apenas na primeira vez)

O Jellyfin precisa de setup inicial manual:

1. Acesse `http://jellyfin.local:32266`
2. Siga o wizard:
   - Idioma: Português
   - Usuário: `pedro` / Senha: `pedroflix`
   - **Não adicione bibliotecas no wizard** — o `05-configure.sh` as cria automaticamente
3. Após o wizard, rode `05-configure.sh` — ele adicionará as bibliotecas Filmes e Séries

---

## Credenciais

| Serviço | Usuário | Senha |
|---------|---------|-------|
| Jellyfin | `pedro` | `pedroflix` |
| Radarr | `pedro` | `pedroflix` |
| Sonarr | `pedro` | `pedroflix` |
| Prowlarr | `pedro` | `pedroflix` |
| Bazarr | `pedro` | `pedroflix` |
| qBittorrent | `admin` | `pedroflix` |
| Busca web | — | sem auth |

**OpenSubtitles.com:**
- Usuário: `phenriquerangel`
- Senha: `Henrique@123`
- API Key: `shx1k1kdzxyaY6FkQt5u7HOLdrMcLAtz`

---

## URLs de acesso

Via `/etc/hosts` + Ingress (porta 32266):

| Serviço | URL |
|---------|-----|
| Jellyfin | http://jellyfin.local:32266 |
| Radarr | http://radarr.local:32266 |
| Sonarr | http://sonarr.local:32266 |
| Prowlarr | http://prowlarr.local:32266 |
| Bazarr | http://bazarr.local:32266 |
| qBittorrent | http://qbittorrent.local:32266 |
| Busca | http://search.local:32266 |

Via port-forward (rede local / LAN):

| Serviço | Porta | Uso |
|---------|-------|-----|
| Jellyfin | `:8096` | App iOS, TV, etc |
| Busca | `:5001` | Browser na rede |

---

## Estrutura do repositório

```
pedroflix/
├── README.md                     ← esta documentação
├── k8s/
│   ├── 00-namespace.yaml
│   ├── configarr/
│   │   ├── configmap.yaml        ← config Configarr (templates Trash Guide)
│   │   ├── cronjob.yaml          ← roda todo dia às 04h
│   │   └── secret.yaml           ← template (sobrescrito por 05-configure.sh)
│   ├── deployments/
│   │   ├── jellyfin.yaml
│   │   ├── radarr.yaml
│   │   ├── sonarr.yaml
│   │   ├── prowlarr.yaml
│   │   ├── bazarr.yaml
│   │   ├── qbittorrent.yaml
│   │   ├── flaresolverr.yaml
│   │   └── pedroflix-web.yaml
│   ├── ingress/ingress.yaml
│   ├── secrets/app-credentials.yaml
│   ├── services/services.yaml
│   └── storage/
│       ├── 01-storageclass.yaml  ← pedroflix-local (no-provisioner)
│       ├── 02-pvs.yaml           ← hostPath → /mnt/pedroflix/
│       └── 03-pvcs.yaml
├── pedroflix-web/
│   ├── app.py                    ← Flask: busca filmes/séries via Radarr+Sonarr API
│   └── Dockerfile
├── scripts/
│   ├── 01-setup-host.sh          ← cria diretórios no NVMe
│   ├── 02-start-cluster.sh       ← Flannel + minikube mount
│   ├── 03-setup-nodes.sh         ← adiciona worker node com label role=media
│   ├── 04-deploy.sh              ← build imagem + kubectl apply
│   └── 05-configure.sh           ← configura todos os serviços via API
└── .prowlarr-defs/               ← definições Cardigann baixadas do GitHub
    ├── 1337x.yml
    ├── eztv.yml
    ├── thepiratebay.yml
    └── ...
```

---

## Diagrama de fluxo (download automático)

```
Usuário busca em search.local
        ↓
pedroflix-web (Flask) → Radarr / Sonarr API
        ↓
Radarr/Sonarr detecta mídia pendente
        ↓
Prowlarr busca torrents (8 indexers)
        ↓ (sites com Cloudflare)
    FlareSolverr (bypass)
        ↓
qBittorrent baixa para /downloads/complete
        ↓
Radarr/Sonarr detecta download, move para /movies ou /tv
        ↓
Bazarr baixa legenda PT-BR + EN (OpenSubtitles.com)
        ↓
Jellyfin exibe com legenda embutida
```

**Trash Guide (Configarr):** CronJob diário às 04h aplica perfis de qualidade  
HD Bluray+WEB para Radarr e WEB-1080p para Sonarr via templates oficiais.

---

## Quirks conhecidos

### qBittorrent — senha temporária (v5.x)
Na primeira inicialização sem senha configurada, o qBittorrent gera uma senha
temporária visível nos logs:
```bash
kubectl logs -n pedroflix deploy/qbittorrent | grep "temporary password"
```
O `05-configure.sh` lida com isso automaticamente via `grep -oP "temporary password is provided for this session: \K\S+"`.

Caso precise resetar manualmente a senha:
```bash
# 1. Escalar para 0
kubectl scale deployment qbittorrent -n pedroflix --replicas=0

# 2. Pod editor para remover a senha do config na PVC
kubectl apply -n pedroflix -f - << 'EOF'
apiVersion: v1
kind: Pod
metadata:
  name: qb-reset
  namespace: pedroflix
spec:
  nodeSelector: {role: media}
  tolerations: [{key: dedicated, operator: Equal, value: media, effect: NoSchedule}]
  restartPolicy: Never
  volumes: [{name: config, persistentVolumeClaim: {claimName: pvc-config-qbittorrent}}]
  containers:
    - name: editor
      image: python:3.12-slim
      command: ["python3", "-c"]
      args:
        - |
          with open("/config/qBittorrent/qBittorrent.conf") as f: lines = f.readlines()
          out = [l for l in lines if not l.startswith("WebUI\\Password_PBKDF2=")]
          out.append("WebUI\\LocalHostAuth=false\n")
          out.append("WebUI\\CSRFProtection=false\n")
          open("/config/qBittorrent/qBittorrent.conf","w").writelines(out)
          print("done")
      volumeMounts: [{name: config, mountPath: /config}]
EOF

kubectl wait --for=jsonpath='{.status.phase}'=Succeeded pod/qb-reset -n pedroflix --timeout=60s
kubectl delete pod qb-reset -n pedroflix

# 3. Escalar de volta para 1 e pegar a nova senha temporária
kubectl scale deployment qbittorrent -n pedroflix --replicas=1
kubectl logs -n pedroflix deploy/qbittorrent | grep "temporary password"
```

### Bazarr — senha em MD5
O Bazarr armazena a senha como hash MD5 em `config.yaml`. Não usar plaintext.
O `05-configure.sh` faz o hash automaticamente via `hashlib.md5`.

### Jellyfin — reset de senha
A senha do Jellyfin é PBKDF2-SHA512 no SQLite. Para resetar:
```bash
kubectl scale deployment jellyfin -n pedroflix --replicas=0

# Pod temporário para editar o DB
kubectl apply -n pedroflix -f - << 'EOF'
apiVersion: v1
kind: Pod
metadata:
  name: jf-reset
  namespace: pedroflix
spec:
  nodeSelector: {role: media}
  tolerations: [{key: dedicated, operator: Equal, value: media, effect: NoSchedule}]
  restartPolicy: Never
  volumes: [{name: config, persistentVolumeClaim: {claimName: pvc-config-jellyfin}}]
  containers:
    - name: editor
      image: python:3.12-slim
      command: ["python3", "-c"]
      args:
        - |
          import sqlite3, hashlib, os
          salt = os.urandom(16)
          dk = hashlib.pbkdf2_hmac("sha512", b"pedroflix", salt, 210000, 64)
          h = f"$PBKDF2-SHA512$iterations=210000${salt.hex().upper()}${dk.hex().upper()}"
          conn = sqlite3.connect("/config/data/data/jellyfin.db")
          conn.execute("UPDATE Users SET Password=? WHERE Username='pedro'", (h,))
          conn.commit()
          print("done:", h[:40])
      volumeMounts: [{name: config, mountPath: /config}]
EOF

kubectl wait --for=jsonpath='{.status.phase}'=Succeeded pod/jf-reset -n pedroflix --timeout=60s
kubectl delete pod jf-reset -n pedroflix
kubectl scale deployment jellyfin -n pedroflix --replicas=1
```

### minikube mount — precisa ficar rodando
O `minikube mount` precisa estar ativo para os pods acessarem o NVMe.
Ao reiniciar a máquina, execute `02-start-cluster.sh` novamente.
Verificação: `cat /tmp/minikube-mount.pid && kill -0 $(cat /tmp/minikube-mount.pid) && echo "mount ativo"`

---

## Reiniciar após reboot da máquina

```bash
# 1. Iniciar o mount (obrigatório antes de usar o cluster)
./scripts/02-start-cluster.sh

# 2. Verificar se todos os pods estão Running
kubectl get pods -n pedroflix

# 3. Os port-forwards systemd sobem automaticamente (loginctl enable-linger)
systemctl --user status jellyfin-forward.service
systemctl --user status pedroflix-search-forward.service
systemctl --user status pedroflix-ingress-forward.service
```

---

## Destruir tudo e recriar

### Destruição completa

```bash
# Para o mount
kill $(cat /tmp/minikube-mount.pid) 2>/dev/null || true

# Deleta o cluster inteiro (todos os dados do cluster são perdidos)
minikube delete --profile minikube

# OPCIONAL: apaga os dados no NVMe também (filmes, configs, etc.)
# rm -rf /run/media/pedro/Nvme_1TB/pedroflix/
```

> Os dados de configuração dos apps (API keys, qualidade, indexers, etc.) ficam
> nas pastas `config/` do NVMe. Se mantiver o NVMe, os apps vão lembrar de tudo
> na próxima criação do cluster — exceto qBittorrent (que gera nova senha temporária)
> e Jellyfin (que pode precisar resetar a senha via PBKDF2).

### Recriação completa

```bash
# Criar cluster do zero
minikube start \
  --driver=docker \
  --cpus=4 \
  --memory=6144 \
  --disk-size=20g \
  --kubernetes-version=v1.35.1 \
  --cni='' \
  --network-plugin=cni

# Executar scripts em sequência
./scripts/01-setup-host.sh   # (pular se o NVMe já tiver os diretórios)
./scripts/02-start-cluster.sh
./scripts/03-setup-nodes.sh
./scripts/04-deploy.sh
./scripts/05-configure.sh

# Adicionar /etc/hosts se necessário
MINIKUBE_IP=$(minikube ip)
sudo bash -c "echo \"$MINIKUBE_IP  jellyfin.local radarr.local sonarr.local prowlarr.local bazarr.local qbittorrent.local flaresolverr.local search.local\" >> /etc/hosts"

# Systemd port-forwards (se não existirem)
systemctl --user enable --now jellyfin-forward.service
systemctl --user enable --now pedroflix-search-forward.service
```

---

## Segredos — preencher antes do deploy

Todos os arquivos com `CHANGE_ME` precisam ser editados antes de rodar o deploy:

| Arquivo | O que preencher |
|---------|----------------|
| `k8s/secrets/app-credentials.yaml` | `APP_USERNAME`, `APP_PASSWORD`, `JELLYFIN_USERNAME`, `JELLYFIN_PASSWORD` |
| `k8s/monitoring/jellyfin-exporter.yaml` | `api-key` — API key do Jellyfin |
| `k8s/monitoring/values-kube-prometheus.yaml` | `adminPassword` — senha do Grafana |
| `scripts/05-configure.sh` | credenciais OpenSubtitles.com (username, password, apikey) + senha padrão do qBittorrent |

### Como gerar a API key do Jellyfin

```bash
# Após o primeiro boot do Jellyfin:
# Opção 1 — UI: Dashboard → Admin → API Keys → + → copiar token
# Opção 2 — SQLite dentro do pod:
kubectl -n pedroflix exec deploy/jellyfin -- \
  sqlite3 /config/data/data/jellyfin.db "SELECT AccessToken FROM ApiKeys LIMIT 1;"
```

### OpenSubtitles.com

Criar conta em [opensubtitles.com](https://www.opensubtitles.com) e gerar API key em **opensubtitles.com/consumers**.

---

## Monitoramento (kube-prometheus-stack)

```bash
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
helm repo update

# Instalar stack de monitoramento
helm upgrade --install kube-prometheus prometheus-community/kube-prometheus-stack \
  -n monitoring --create-namespace \
  -f k8s/monitoring/values-kube-prometheus.yaml

# Build e load da imagem do jellyfin-exporter (source: rebelcore/jellyfin_exporter)
cd /caminho/para/jellyfin_exporter
docker build -f Dockerfile -t jellyfin-exporter:latest .
docker save jellyfin-exporter:latest | \
  docker exec -i $(minikube node list | awk 'NR==2{print $1}') docker load

# Aplicar manifests de monitoramento
kubectl apply -f k8s/monitoring/jellyfin-exporter.yaml
kubectl apply -f k8s/monitoring/pod-network-exporter.yaml
kubectl apply -f k8s/monitoring/configmap-jellyfin-dashboard.yaml

# Acesso ao Grafana
open http://grafana.pedroflix.local:32266   # admin / <senha configurada>
```

**Dashboards disponíveis:**
- **Pedroflix / Jellyfin** — sessões ativas, transcodings, media count, tarefas agendadas, armazenamento
- **Pedroflix / Network** — tráfego de rede por pod
- **Pedroflix / Storage** — uso de PVCs

---

## Quirks do ambiente (filesystem 9p)

O minikube usa o protocolo **9p (Plan 9)** para montar volumes `hostPath` nos pods.
Isso causa dois problemas conhecidos:

### 1. `statfs` retorna 0 — Sonarr/Radarr recusam imports

```
Not enough free space  ← mesmo com 711 GB livres no disco real
```

**Workaround confirmado:** mover os arquivos manualmente para o path correto e acionar rescan (sem import = sem verificação de espaço).

```bash
# Sonarr — dentro do pod:
kubectl -n pedroflix exec deploy/sonarr -- bash -c '
  mkdir -p "/tv/Nome da Serie (Ano)/Season 01"
  mv /downloads/complete/<pasta>/*.mkv "/tv/Nome da Serie (Ano)/Season 01/"
'
# Acionar rescan:
SONARR_KEY=$(kubectl exec -n pedroflix deploy/sonarr -- \
  sed -n "s|.*<ApiKey>\([^<]*\)</ApiKey>.*|\1|p" /config/config.xml)
curl -X POST -H "X-Api-Key: $SONARR_KEY" \
  http://sonarr.local:32266/api/v3/command \
  -d '{"name":"RescanSeries","seriesId":<id>}'

# Radarr — mesma lógica com /movies/<Filme (Ano)>/ e RescanMovie
```

### 2. `mmap()` falha — qBittorrent não consegue baixar

```
file_mmap (/downloads/complete/...) error: Invalid argument
```

**Fix permanente** (o `05-configure.sh` já aplica automaticamente):

```ini
# /config/qbittorrent/qBittorrent/qBittorrent.conf — seção [BitTorrent]:
Session\DiskIOType=Posix
```

**ATENÇÃO:** no qBittorrent 5.x, `disk_io_type` via API tem valores diferentes do esperado:
- `0` = Default (usa mmap no Linux)
- `1` = **Memory-mapped** ← errado, causa o problema
- `2` = **POSIX** (pread/pwrite) ← correto

---

## Comandos úteis

```bash
# Status de todos os pods
kubectl get pods -n pedroflix

# Logs de um serviço
kubectl logs -n pedroflix deploy/radarr -f

# Restart de um serviço
kubectl rollout restart deployment/bazarr -n pedroflix

# Executar configarr manualmente (aplica Trash Guide agora)
kubectl create job configarr-manual-$(date +%s) \
  --from=cronjob/configarr -n pedroflix

# Verificar IP do minikube (para /etc/hosts)
minikube ip

# Verificar se o mount está ativo
kill -0 $(cat /tmp/minikube-mount.pid 2>/dev/null) 2>/dev/null && echo "mount OK" || echo "mount PARADO"

# Port-forwards manuais (alternativa ao systemd)
kubectl port-forward deployment/jellyfin 8096:8096 --address 0.0.0.0 -n pedroflix &
kubectl port-forward service/pedroflix-web 5001:5000 --address 0.0.0.0 -n pedroflix &
```
