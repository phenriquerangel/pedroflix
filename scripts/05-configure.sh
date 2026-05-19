#!/bin/bash
set -e

PROFILE="minikube"
BASE="http://192.168.49.2:32266"
NS="pedroflix"
JELLY_AUTH='MediaBrowser Client="Script", Device="bash", DeviceId="pedroflix-setup", Version="1.0"'

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
ok()   { echo -e "${GREEN}  ✔ $*${NC}"; }
info() { echo -e "${YELLOW}  → $*${NC}"; }
warn() { echo -e "${RED}  ✘ $*${NC}"; }

pod_of() {
  kubectl get pod -n "$NS" -l "app=$1" --context "$PROFILE" \
    -o jsonpath='{.items[0].metadata.name}'
}

secret_val() {
  kubectl get secret app-credentials -n "$NS" --context "$PROFILE" \
    -o jsonpath="{.data.$1}" 2>/dev/null | base64 -d
}

# arr <host> <method> <path> <apikey> [body]
arr() {
  local host=$1 method=$2 path=$3 apikey=$4 data=${5:-}
  local args=(-s -X "$method"
    -H "Host: $host"
    -H "Content-Type: application/json"
    -H "Accept: application/json")
  [ -n "$apikey" ] && args+=(-H "X-Api-Key: $apikey")
  [ -n "$data"   ] && args+=(-d "$data")
  curl "${args[@]}" "$BASE$path"
}

has_name() {
  echo "$1" | python3 -c "
import json,sys
items=json.load(sys.stdin)
sys.exit(0 if any(str(i.get('name',''))==sys.argv[1] for i in items) else 1)
" "$2" 2>/dev/null
}

has_path() {
  echo "$1" | python3 -c "
import json,sys
items=json.load(sys.stdin)
sys.exit(0 if any(str(i.get('path',''))==sys.argv[1] for i in items) else 1)
" "$2" 2>/dev/null
}

# ─────────────────────────────────────────────────────────
echo "==> [1/7] Credenciais do Secret..."
# ─────────────────────────────────────────────────────────

APP_USERNAME=$(secret_val APP_USERNAME)
APP_PASSWORD=$(secret_val APP_PASSWORD)
JELLYFIN_USERNAME=$(secret_val JELLYFIN_USERNAME)
JELLYFIN_PASSWORD=$(secret_val JELLYFIN_PASSWORD)

if [ -z "$APP_USERNAME" ] || [ -z "$APP_PASSWORD" ]; then
  warn "Secret 'app-credentials' não encontrado. Aplicando com valores padrão..."
  kubectl apply -f "$(dirname "$0")/../k8s/secrets/app-credentials.yaml" --context "$PROFILE" > /dev/null
  APP_USERNAME="CHANGE_ME"
  APP_PASSWORD="CHANGE_ME"
  JELLYFIN_USERNAME="CHANGE_ME"
  JELLYFIN_PASSWORD="CHANGE_ME"
fi

ok "Usuário: $APP_USERNAME"

# ─────────────────────────────────────────────────────────
echo ""
echo "==> [2/7] Coletando API keys..."
# ─────────────────────────────────────────────────────────

get_arr_key() {
  kubectl exec -n "$NS" --context "$PROFILE" "$(pod_of "$1")" -- \
    sed -n 's|.*<ApiKey>\([^<]*\)</ApiKey>.*|\1|p' /config/config.xml 2>/dev/null
}

RADARR_KEY=$(get_arr_key radarr)
SONARR_KEY=$(get_arr_key sonarr)
PROWLARR_KEY=$(get_arr_key prowlarr)
BAZARR_KEY=$(kubectl exec -n "$NS" --context "$PROFILE" "$(pod_of bazarr)" -- \
  grep "apikey:" /config/config/config.yaml 2>/dev/null | head -1 | sed 's/.*apikey: *//')

ok "Radarr:   $RADARR_KEY"
ok "Sonarr:   $SONARR_KEY"
ok "Prowlarr: $PROWLARR_KEY"
ok "Bazarr:   $BAZARR_KEY"

# ─────────────────────────────────────────────────────────
echo ""
echo "==> [3/7] qBittorrent – autenticação e preferências..."
# ─────────────────────────────────────────────────────────

QB_POD=$(pod_of qbittorrent)

qb() {
  kubectl exec -n "$NS" --context "$PROFILE" "$QB_POD" -- curl -s "$@" 2>/dev/null
}

# O filesystem 9p do minikube não suporta mmap() — garante DiskIOType=Posix no config
QB_CONF="/mnt/nvme1tb/pedroflix/config/qbittorrent/qBittorrent/qBittorrent.conf"
QB_DISK_FIX=$(python3 - "$QB_CONF" << 'PYEOF'
import sys, os

path = sys.argv[1]
if not os.path.exists(path):
    print('not_found')
    sys.exit(0)

with open(path) as f:
    lines = f.readlines()

if any('Session\\DiskIOType=Posix' in line for line in lines):
    print('already_set')
    sys.exit(0)

# Remove chave legada conflitante (Session\DiskIO_Type=N → valor errado no 5.x)
lines = [l for l in lines if not l.startswith('Session\\DiskIO_Type=')]

new_lines = []
inserted = False
for line in lines:
    new_lines.append(line)
    if line.strip() == '[BitTorrent]':
        new_lines.append('Session\\DiskIOType=Posix\n')
        inserted = True

if not inserted:
    new_lines.append('\n[BitTorrent]\nSession\\DiskIOType=Posix\n')

with open(path, 'w') as f:
    f.writelines(new_lines)
print('updated')
PYEOF
) || QB_DISK_FIX="error"

case "$QB_DISK_FIX" in
  updated)
    ok "qBittorrent: Session\\DiskIOType=Posix adicionado ao config"
    kubectl rollout restart deployment/qbittorrent -n "$NS" --context "$PROFILE" > /dev/null
    kubectl rollout status deployment/qbittorrent -n "$NS" --context "$PROFILE" --timeout=60s > /dev/null 2>&1
    QB_POD=$(pod_of qbittorrent)
    ok "qBittorrent: pod reiniciado com POSIX disk I/O" ;;
  already_set)
    ok "qBittorrent: DiskIOType=Posix já configurado" ;;
  not_found)
    warn "qBittorrent: config não encontrado em $QB_CONF — disk I/O não configurado" ;;
  *)
    warn "qBittorrent: erro ao verificar config de disk I/O" ;;
esac

QB_TEMP=$(kubectl logs -n "$NS" --context "$PROFILE" "$QB_POD" 2>/dev/null \
  | grep -oP "temporary password is provided for this session: \K\S+" | tail -1)

for QB_PASS in "$APP_PASSWORD" "$QB_TEMP" "CHANGE_ME_QB_DEFAULT_PASS"; do
  [ -z "$QB_PASS" ] && continue
  QB_CODE=$(qb -c /tmp/qb_sid.txt -o /dev/null -w "%{http_code}" -X POST \
    -H "Referer: http://localhost:8080" \
    -d "username=admin&password=$QB_PASS" \
    http://localhost:8080/api/v2/auth/login || true)
  { [ "$QB_CODE" = "200" ] || [ "$QB_CODE" = "204" ]; } && break
done

if [ "$QB_CODE" != "200" ] && [ "$QB_CODE" != "204" ]; then
  warn "qBittorrent: autenticação falhou. Verifique a senha manualmente."
  QB_PASS="$APP_PASSWORD"
else
  ok "qBittorrent: autenticado"

  if [ "$QB_PASS" != "$APP_PASSWORD" ]; then
    qb -b /tmp/qb_sid.txt -X POST \
      -d "json={\"web_ui_username\":\"admin\",\"web_ui_password\":\"$APP_PASSWORD\"}" \
      http://localhost:8080/api/v2/app/setPreferences > /dev/null
    ok "qBittorrent: senha atualizada para '$APP_PASSWORD'"
    QB_PASS="$APP_PASSWORD"
  fi

  qb -b /tmp/qb_sid.txt -X POST \
    -d 'json={"save_path":"/downloads/complete","torrent_content_layout":"Original"}' \
    http://localhost:8080/api/v2/app/setPreferences > /dev/null
  ok "qBittorrent: save_path=/downloads/complete"
fi

# ─────────────────────────────────────────────────────────
echo ""
echo "==> [4/7] Prowlarr → FlareSolverr + Radarr + Sonarr..."
# ─────────────────────────────────────────────────────────

PROXIES=$(arr prowlarr.local GET /api/v1/indexerproxy "$PROWLARR_KEY")
if has_name "$PROXIES" "FlareSolverr"; then
  ok "FlareSolverr já configurado"
else
  arr prowlarr.local POST /api/v1/indexerproxy "$PROWLARR_KEY" '{
    "name": "FlareSolverr",
    "implementationName": "FlareSolverr",
    "implementation": "FlareSolverr",
    "configContract": "FlareSolverrSettings",
    "tags": [],
    "fields": [
      {"name": "host",           "value": "http://flaresolverr.pedroflix.svc.cluster.local:8191"},
      {"name": "requestTimeout", "value": 60}
    ]
  }' > /dev/null
  ok "FlareSolverr adicionado"
fi

APPS=$(arr prowlarr.local GET /api/v1/applications "$PROWLARR_KEY")

if has_name "$APPS" "Radarr"; then
  ok "Radarr já conectado ao Prowlarr"
else
  arr prowlarr.local POST /api/v1/applications "$PROWLARR_KEY" "$(cat <<EOF
{
  "name": "Radarr", "syncLevel": "fullSync",
  "implementationName": "Radarr", "implementation": "Radarr",
  "configContract": "RadarrSettings", "tags": [],
  "fields": [
    {"name": "prowlarrUrl",    "value": "http://prowlarr.pedroflix.svc.cluster.local:9696"},
    {"name": "baseUrl",        "value": "http://radarr.pedroflix.svc.cluster.local:7878"},
    {"name": "apiKey",         "value": "$RADARR_KEY"},
    {"name": "syncCategories", "value": [2000,2010,2020,2030,2040,2045,2050,2060,2070,2080]}
  ]
}
EOF
)" > /dev/null
  ok "Radarr conectado ao Prowlarr"
fi

if has_name "$APPS" "Sonarr"; then
  ok "Sonarr já conectado ao Prowlarr"
else
  arr prowlarr.local POST /api/v1/applications "$PROWLARR_KEY" "$(cat <<EOF
{
  "name": "Sonarr", "syncLevel": "fullSync",
  "implementationName": "Sonarr", "implementation": "Sonarr",
  "configContract": "SonarrSettings", "tags": [],
  "fields": [
    {"name": "prowlarrUrl",    "value": "http://prowlarr.pedroflix.svc.cluster.local:9696"},
    {"name": "baseUrl",        "value": "http://sonarr.pedroflix.svc.cluster.local:8989"},
    {"name": "apiKey",         "value": "$SONARR_KEY"},
    {"name": "syncCategories", "value": [5000,5010,5020,5030,5040,5045,5050,5060,5070,5080]}
  ]
}
EOF
)" > /dev/null
  ok "Sonarr conectado ao Prowlarr"
fi

# ─────────────────────────────────────────────────────────
echo ""
echo "==> [5/7] Radarr + Sonarr → qBittorrent + pastas raiz..."
# ─────────────────────────────────────────────────────────

# Garante ownership 1000:1000 nos diretórios de mídia/downloads no node worker
WORKER_NODE=$(kubectl get nodes --context "$PROFILE" \
  --selector='!node-role.kubernetes.io/control-plane' \
  -o jsonpath='{.items[0].metadata.name}' 2>/dev/null)
if [ -n "$WORKER_NODE" ]; then
  minikube ssh --profile "$PROFILE" --node "$WORKER_NODE" -- \
    "sudo chown -R 1000:1000 /mnt/pedroflix/media/movies /mnt/pedroflix/media/tv /mnt/pedroflix/downloads/complete 2>/dev/null || true" 2>/dev/null || true
fi

configure_arr() {
  local app=$1 key=$2 host=$3 cat_field=$4 cat_val=$5 root=$6

  DL_CLIENTS=$(arr "$host" GET /api/v3/downloadclient "$key")
  if has_name "$DL_CLIENTS" "qBittorrent"; then
    ok "$app: qBittorrent já configurado"
  else
    arr "$host" POST /api/v3/downloadclient "$key" "$(cat <<EOF
{
  "enable": true, "protocol": "torrent", "priority": 1,
  "removeCompletedDownloads": true, "removeFailedDownloads": true,
  "name": "qBittorrent", "implementationName": "qBittorrent",
  "implementation": "QBittorrent", "configContract": "QBittorrentSettings",
  "fields": [
    {"name": "host",            "value": "qbittorrent.pedroflix.svc.cluster.local"},
    {"name": "port",            "value": 8080},
    {"name": "useSsl",          "value": false},
    {"name": "username",        "value": "admin"},
    {"name": "password",        "value": "$QB_PASS"},
    {"name": "$cat_field",      "value": "$cat_val"},
    {"name": "initialState",    "value": 0},
    {"name": "sequentialOrder", "value": false},
    {"name": "firstAndLast",    "value": false}
  ]
}
EOF
)" > /dev/null
    ok "$app: qBittorrent adicionado"
  fi

  ROOT_FOLDERS=$(arr "$host" GET /api/v3/rootfolder "$key")
  if has_path "$ROOT_FOLDERS" "$root"; then
    ok "$app: pasta raiz $root já existe"
  else
    arr "$host" POST /api/v3/rootfolder "$key" "{\"path\":\"$root\"}" > /dev/null
    ok "$app: pasta raiz $root adicionada"
  fi
}

configure_arr radarr "$RADARR_KEY" radarr.local movieCategory radarr /movies
configure_arr sonarr "$SONARR_KEY" sonarr.local tvCategory    sonarr /tv

# Filesystem 9p reporta statfs=0 → Sonarr/Radarr recusam imports com "Not enough free space"
# Sonarr v4 ignora minimumFreeSpaceWhenImporting=0 via API (valida >= 100), então editar o SQLite direto
fix_arr_freespace() {
  local app=$1 db_path=$2

  python3 - "$db_path" << 'PYEOF' || true
import sys, sqlite3, os

path = sys.argv[1]
if not os.path.exists(path):
    print(f'skip: {path} not found')
    sys.exit(0)

conn = sqlite3.connect(path, timeout=10)
existing = conn.execute(
    "SELECT Value FROM Config WHERE Key='minimumfreespacewhenimporting'"
).fetchone()

if existing and existing[0] == '0':
    print('already_set')
    conn.close()
    sys.exit(0)

if existing:
    conn.execute("UPDATE Config SET Value='0' WHERE Key='minimumfreespacewhenimporting'")
else:
    conn.execute("INSERT INTO Config (Key, Value) VALUES ('minimumfreespacewhenimporting', '0')")

conn.commit()
conn.close()
print('updated')
PYEOF
}

SONARR_FIX=$(fix_arr_freespace sonarr /mnt/nvme1tb/pedroflix/config/sonarr/sonarr.db)
RADARR_FIX=$(fix_arr_freespace radarr /mnt/nvme1tb/pedroflix/config/radarr/radarr.db)

[ "$SONARR_FIX" = "updated" ] && ok "Sonarr: minimumFreeSpaceWhenImporting=0 (workaround 9p)" \
  || ok "Sonarr: minimumFreeSpaceWhenImporting já configurado"
[ "$RADARR_FIX" = "updated" ] && ok "Radarr: minimumFreeSpaceWhenImporting=0 (workaround 9p)" \
  || ok "Radarr: minimumFreeSpaceWhenImporting já configurado"

# ─────────────────────────────────────────────────────────
echo ""
echo "==> [6/7] Bazarr → idiomas + perfil + Radarr + Sonarr..."
# ─────────────────────────────────────────────────────────

BAZARR_POD=$(pod_of bazarr)

# Configura Bazarr via config.yaml (a API POST não persiste em v1.5+)
configure_bazarr() {
  local tmp_cfg
  tmp_cfg=$(mktemp /tmp/bazarr-config.XXXXXX.yaml)

  kubectl cp "pedroflix/$BAZARR_POD:/config/config/config.yaml" "$tmp_cfg" \
    --context "$PROFILE" 2>/dev/null

  python3 - "$tmp_cfg" "$RADARR_KEY" "$SONARR_KEY" "$APP_USERNAME" "$APP_PASSWORD" << 'PYEOF'
import sys, yaml

path, radarr_key, sonarr_key, user, pw = sys.argv[1:]

with open(path) as f:
    cfg = yaml.safe_load(f)

cfg['radarr']['ip']      = 'radarr.pedroflix.svc.cluster.local'
cfg['radarr']['apikey']  = radarr_key
cfg['sonarr']['ip']      = 'sonarr.pedroflix.svc.cluster.local'
cfg['sonarr']['apikey']  = sonarr_key
cfg['general']['use_radarr']            = True
cfg['general']['use_sonarr']            = True
cfg['general']['enabled_providers']     = ['embeddedsubtitles','opensubtitlescom','yifysubtitles']
cfg.setdefault('opensubtitlescom', {})
cfg['opensubtitlescom']['username'] = 'CHANGE_ME'       # opensubtitles.com username
cfg['opensubtitlescom']['password'] = 'CHANGE_ME'       # opensubtitles.com password
cfg['opensubtitlescom']['apikey']   = 'CHANGE_ME'       # opensubtitles.com API key (opensubtitles.com/consumers)
cfg['opensubtitlescom'].setdefault('use_hash', True)
cfg['opensubtitlescom'].setdefault('include_ai_translated', False)
cfg['opensubtitlescom'].setdefault('include_machine_translated', False)
cfg['general']['movie_default_enabled'] = True
cfg['general']['movie_default_profile'] = '1'
cfg['general']['serie_default_enabled'] = True
cfg['general']['serie_default_profile'] = '1'
import hashlib
cfg['auth']['type']     = 'form'
cfg['auth']['username'] = user
cfg['auth']['password'] = hashlib.md5(pw.encode('utf-8')).hexdigest()

with open(path, 'w') as f:
    yaml.dump(cfg, f, default_flow_style=False, allow_unicode=True)

print('ok')
PYEOF

  kubectl cp "$tmp_cfg" "pedroflix/$BAZARR_POD:/config/config/config.yaml" \
    --context "$PROFILE" 2>/dev/null
  rm -f "$tmp_cfg"
  # Reinicia o pod para que o Bazarr recarregue config.yaml
  kubectl delete pod -n "$NS" --context "$PROFILE" "$BAZARR_POD" > /dev/null 2>&1 || true
  kubectl rollout status deployment/bazarr -n "$NS" --context "$PROFILE" --timeout=60s > /dev/null 2>&1 || true
}

# Habilita PT-BR + EN e cria perfil no banco
configure_bazarr_db() {
  kubectl exec -n "$NS" --context "$PROFILE" "$BAZARR_POD" -- python3 -c "
import sqlite3, json
conn = sqlite3.connect('/config/db/bazarr.db')
c = conn.cursor()
c.execute(\"UPDATE table_settings_languages SET enabled=1 WHERE code3 IN ('pob','eng')\")
c.execute(\"SELECT profileId FROM table_languages_profiles WHERE name='PT-BR / EN'\")
if not c.fetchone():
    items = json.dumps([
        {'id':1,'language':'pb','forced':'False','hi':'False','audio_exclude':'False','audio_only_include':'False'},
        {'id':2,'language':'en','forced':'False','hi':'False','audio_exclude':'False','audio_only_include':'False'}
    ])
    c.execute('''INSERT INTO table_languages_profiles
        (name,items,cutoff,originalFormat,mustContain,mustNotContain,tag)
        VALUES (?,?,?,?,?,?,?)''',
        ('PT-BR / EN', items, None, 0, '[]', '[]', None))
    print('profile_created')
else:
    print('profile_exists')
conn.commit()
conn.close()
" 2>/dev/null || true
}

configure_bazarr_db && ok "Bazarr: perfil PT-BR / EN configurado"
configure_bazarr    && ok "Bazarr: Radarr + Sonarr + providers configurados" \
  || warn "Bazarr: erro ao editar config.yaml"

# ─────────────────────────────────────────────────────────
echo ""
echo "==> [7/8] Auth nos *arr apps + Jellyfin + Configarr keys..."
# ─────────────────────────────────────────────────────────

# Função: GET config/host, injeta auth, PUT de volta
set_arr_auth() {
  local app=$1 host=$2 key=$3 api_version=${4:-v3}

  CURRENT=$(arr "$host" GET "/api/$api_version/config/host" "$key")
  if [ -z "$CURRENT" ] || [ "$CURRENT" = "null" ]; then
    warn "$app: não foi possível obter config/host"
    return
  fi

  ALREADY=$(echo "$CURRENT" | python3 -c "
import json,sys
c=json.load(sys.stdin)
print(c.get('authenticationMethod','None'))
" 2>/dev/null)

  if [ "$ALREADY" = "Forms" ]; then
    ok "$app: autenticação já habilitada"
    return
  fi

  UPDATED=$(echo "$CURRENT" | python3 -c "
import json,sys
c=json.load(sys.stdin)
c['authenticationMethod']  = 'Forms'
c['authenticationRequired'] = 'Enabled'
c['username'] = sys.argv[1]
c['password'] = sys.argv[2]
print(json.dumps(c))
" "$APP_USERNAME" "$APP_PASSWORD" 2>/dev/null || true)

  arr "$host" PUT "/api/$api_version/config/host" "$key" "$UPDATED" > /dev/null
  ok "$app: autenticação Forms habilitada ($APP_USERNAME)"
}

set_arr_auth Radarr   radarr.local   "$RADARR_KEY"   v3
set_arr_auth Sonarr   sonarr.local   "$SONARR_KEY"   v3
set_arr_auth Prowlarr prowlarr.local "$PROWLARR_KEY" v1

# Jellyfin: autenticar e adicionar bibliotecas
JELLY_TOKEN=$(curl -s -X POST \
  -H "Host: jellyfin.local" \
  -H "Content-Type: application/json" \
  -H "Authorization: $JELLY_AUTH" \
  -d "{\"Username\":\"$JELLYFIN_USERNAME\",\"Pw\":\"$JELLYFIN_PASSWORD\"}" \
  "$BASE/Users/AuthenticateByName" 2>/dev/null \
  | python3 -c "import json,sys; print(json.load(sys.stdin).get('AccessToken',''))" 2>/dev/null || true)

if [ -z "$JELLY_TOKEN" ]; then
  warn "Jellyfin: autenticação falhou."
  warn "          → Acesse jellyfin.local:32266, complete o wizard e atualize"
  warn "            JELLYFIN_USERNAME/PASSWORD no Secret app-credentials."
else
  ok "Jellyfin: autenticado"

  add_lib() {
    local name=$1 type=$2 path=$3
    # Busca a lista atual a cada chamada para evitar falso negativo
    local current_libs
    current_libs=$(curl -s \
      -H "Host: jellyfin.local" \
      -H "Authorization: MediaBrowser Token=\"$JELLY_TOKEN\"" \
      "$BASE/Library/VirtualFolders" 2>/dev/null)
    if echo "$current_libs" | python3 -c "
import json,sys
libs=json.load(sys.stdin)
sys.exit(0 if any(l.get('Name','')==sys.argv[1] for l in libs) else 1)
" "$name" 2>/dev/null; then
      ok "Jellyfin: biblioteca '$name' já existe"
    else
      curl -s -X POST \
        -H "Host: jellyfin.local" \
        -H "Content-Type: application/json" \
        -H "Authorization: MediaBrowser Token=\"$JELLY_TOKEN\"" \
        -d "{\"LibraryOptions\":{\"EnableRealtimeMonitor\":true,\"MetadataCountryCode\":\"BR\",\"PreferredMetadataLanguage\":\"pt\"}}" \
        "$BASE/Library/VirtualFolders?collectionType=$type&name=$name&paths=$path&refreshLibrary=false" > /dev/null
      ok "Jellyfin: biblioteca '$name' adicionada ($path)"
    fi
  }

  add_lib "Filmes" "movies"  "/data/movies"
  add_lib "Séries" "tvshows" "/data/tvshows"
fi

# Atualizar Configarr Secret com as API keys atuais
kubectl create secret generic configarr-secrets \
  --namespace "$NS" --context "$PROFILE" \
  --from-literal=secrets.yml="$(printf 'sonarr_api_key: "%s"\nradarr_api_key: "%s"' "$SONARR_KEY" "$RADARR_KEY")" \
  --dry-run=client -o yaml | kubectl apply -f - > /dev/null
ok "Configarr: secret atualizado com API keys atuais"

kubectl create secret generic pedroflix-web-secrets \
  --namespace "$NS" --context "$PROFILE" \
  --from-literal=RADARR_KEY="$RADARR_KEY" \
  --from-literal=SONARR_KEY="$SONARR_KEY" \
  --from-literal=BAZARR_KEY="$BAZARR_KEY" \
  --dry-run=client -o yaml | kubectl apply -f - > /dev/null
ok "pedroflix-web: secret atualizado (Radarr + Sonarr + Bazarr)"

rm -f /tmp/qb_sid.txt /tmp/qb_cookies.txt

# ─────────────────────────────────────────────────────────
echo ""
echo "==> Habilitando port-forwards para acesso da rede local..."
# ─────────────────────────────────────────────────────────
systemctl --user daemon-reload
systemctl --user enable --now jellyfin-forward.service         2>/dev/null || true
systemctl --user enable --now pedroflix-search-forward.service  2>/dev/null || true
systemctl --user enable --now pedroflix-ingress-forward.service 2>/dev/null || true

HOST_IP=$(ip route get 1.1.1.1 2>/dev/null | awk '/src/{print $7}' | head -1)
ok "Port-forwards ativos:"
ok "  Jellyfin (app iOS/TV):   http://${HOST_IP}:8096"
ok "  Busca (rede local):       http://${HOST_IP}:5001"
ok "  Todos os apps (navegador): http://${HOST_IP}:8080  (usar com *.local no /etc/hosts)"

# ─────────────────────────────────────────────────────────
echo ""
echo "==> [8/8] Prowlarr – indexers públicos..."
# ─────────────────────────────────────────────────────────

# Indexers Cardigann (requerem arquivo .yml): definitionName → needs_flare
declare -A INDEXER_FLARE=(
  ["1337x"]="true"
  ["yts"]="false"
  ["eztv"]="false"
  ["thepiratebay"]="false"
  ["nyaasi"]="false"
  ["limetorrents"]="false"
  ["kickasstorrents-to"]="true"
  ["kickasstorrents-ws"]="false"
  ["torrentdownloads"]="false"
)
# Indexers nativos do Prowlarr (sem arquivo .yml):
NATIVE_INDEXERS=("Knaben")

PROWLARR_POD=$(pod_of prowlarr)

# Garante definições Cardigann no pod (necessário em cluster recriado)
ensure_definitions() {
  local def_dir
  def_dir="$(dirname "$0")/../.prowlarr-defs"
  local defs_in_pod
  defs_in_pod=$(kubectl exec -n "$NS" --context "$PROFILE" "$PROWLARR_POD" -- \
    sh -c 'ls /config/Definitions/*.yml 2>/dev/null | wc -l' 2>/dev/null || echo 0)

  if [ "$defs_in_pod" -ge "${#INDEXER_FLARE[@]}" ]; then
    return 0
  fi

  info "Baixando definições Cardigann do GitHub..."
  mkdir -p "$def_dir"
  for defname in "${!INDEXER_FLARE[@]}"; do
    local fname="${defname}.yml"
    if [ ! -f "$def_dir/$fname" ]; then
      curl -sL "https://raw.githubusercontent.com/Prowlarr/Indexers/master/definitions/v11/$fname" \
        -o "$def_dir/$fname" 2>/dev/null || true
    fi
    # Valida que o arquivo é um YAML (começa com 'id:' ou '---'), não uma página 404
    if [ -f "$def_dir/$fname" ]; then
      first=$(head -c 4 "$def_dir/$fname" 2>/dev/null)
      if [ "$first" != "id: " ] && [ "$first" != "---" ]; then
        rm -f "$def_dir/$fname"
        warn "Não foi possível baixar $fname (arquivo inválido)"
        continue
      fi
    fi
  done

  kubectl exec -n "$NS" --context "$PROFILE" "$PROWLARR_POD" -- mkdir -p /config/Definitions

  for f in "$def_dir"/*.yml; do
    [ -f "$f" ] || continue
    kubectl cp "$f" "$NS/$PROWLARR_POD:/config/Definitions/$(basename "$f")" \
      --context "$PROFILE" 2>/dev/null
  done

  # Recarregar definições no Prowlarr
  CMD_ID=$(arr prowlarr.local POST /api/v1/command "$PROWLARR_KEY" \
    '{"name":"IndexerDefinitionUpdate"}' | python3 -c \
    "import json,sys; print(json.load(sys.stdin).get('id',''))" 2>/dev/null || true)
  for i in $(seq 1 15); do
    sleep 2
    STATUS=$(arr prowlarr.local GET "/api/v1/command/$CMD_ID" "$PROWLARR_KEY" | \
      python3 -c "import json,sys; print(json.load(sys.stdin).get('status',''))" 2>/dev/null || true)
    [ "$STATUS" = "completed" ] && break
  done
  ok "Definições Cardigann carregadas"
}

ensure_definitions

# Cria tag "flare" se não existir, retorna o ID
TAGS=$(arr prowlarr.local GET /api/v1/tag "$PROWLARR_KEY")
FLARE_TAG_ID=$(echo "$TAGS" | python3 -c "
import json,sys
tags=json.load(sys.stdin)
t=next((x for x in tags if x.get('label')=='flare'), None)
print(t['id'] if t else '')
" 2>/dev/null || true)

if [ -z "$FLARE_TAG_ID" ]; then
  FLARE_TAG_ID=$(arr prowlarr.local POST /api/v1/tag "$PROWLARR_KEY" \
    '{"label":"flare"}' | python3 -c \
    "import json,sys; print(json.load(sys.stdin).get('id',''))" 2>/dev/null || true)
fi

# Garante que o proxy FlareSolverr tem a tag "flare"
PROXY=$(arr prowlarr.local GET /api/v1/indexerproxy/1 "$PROWLARR_KEY")
PROXY_HAS_TAG=$(echo "$PROXY" | python3 -c "
import json,sys; p=json.load(sys.stdin)
print('yes' if ${FLARE_TAG_ID} in p.get('tags',[]) else 'no')
" 2>/dev/null || echo no)
if [ "$PROXY_HAS_TAG" != "yes" ]; then
  UPDATED_PROXY=$(echo "$PROXY" | python3 -c "
import json,sys; p=json.load(sys.stdin)
if ${FLARE_TAG_ID} not in p.get('tags',[]): p.setdefault('tags',[]).append(${FLARE_TAG_ID})
print(json.dumps(p))
" 2>/dev/null || true)
  [ -n "$UPDATED_PROXY" ] && arr prowlarr.local PUT /api/v1/indexerproxy/1 "$PROWLARR_KEY" "$UPDATED_PROXY" > /dev/null
fi

# Obtém schemas e indexers existentes uma vez só
ALL_SCHEMAS=$(arr prowlarr.local GET /api/v1/indexer/schema "$PROWLARR_KEY")
EXISTING=$(arr prowlarr.local GET /api/v1/indexer "$PROWLARR_KEY")

for defname in "${!INDEXER_FLARE[@]}"; do
  needs_flare="${INDEXER_FLARE[$defname]}"

  # Verifica se já existe pelo definitionName
  already=$(echo "$EXISTING" | python3 -c "
import json,sys
items=json.load(sys.stdin)
sys.exit(0 if any(i.get('definitionName','')==sys.argv[1] for i in items) else 1)
" "$defname" 2>/dev/null && echo yes || echo no)

  if [ "$already" = "yes" ]; then
    ok "Prowlarr: $defname já adicionado"
    continue
  fi

  SCHEMA=$(echo "$ALL_SCHEMAS" | python3 -c "
import json,sys
schemas=json.load(sys.stdin)
s=next((x for x in schemas if x.get('definitionName')==sys.argv[1]), None)
print(json.dumps(s) if s else '')
" "$defname" 2>/dev/null || true)

  if [ -z "$SCHEMA" ]; then
    warn "Prowlarr: schema '$defname' não encontrado (pulando)"
    continue
  fi

  BODY=$(echo "$SCHEMA" | python3 -c "
import json,sys
s=json.load(sys.stdin)
needs_flare=sys.argv[1]=='true'
flare_tag=int(sys.argv[2])
base_url=s.get('indexerUrls',[None])[0] or ''
for f in s.get('fields',[]):
    if f.get('name')=='baseUrl':
        f['value']=base_url
s['enable']=True
s['appProfileId']=1
s['tags']=[flare_tag] if needs_flare else []
print(json.dumps(s))
" "$needs_flare" "$FLARE_TAG_ID" 2>/dev/null || true)

  RESULT=$(arr prowlarr.local POST /api/v1/indexer "$PROWLARR_KEY" "$BODY")
  NAME=$(echo "$RESULT" | python3 -c "import json,sys; print(json.load(sys.stdin).get('name','?'))" 2>/dev/null || true)
  if [ "$NAME" != "?" ] && [ -n "$NAME" ]; then
    FLARE_LABEL=""
    [ "$needs_flare" = "true" ] && FLARE_LABEL=" [+FlareSolverr]"
    ok "Prowlarr: $NAME adicionado$FLARE_LABEL"
  else
    warn "Prowlarr: erro ao adicionar $defname: $(echo "$RESULT" | head -c 120)"
  fi
done

# Indexers nativos (built-in, sem arquivo .yml)
for natname in "${NATIVE_INDEXERS[@]}"; do
  already=$(echo "$EXISTING" | python3 -c "
import json,sys
items=json.load(sys.stdin)
sys.exit(0 if any(i.get('name','')==sys.argv[1] for i in items) else 1)
" "$natname" 2>/dev/null && echo yes || echo no)

  if [ "$already" = "yes" ]; then
    ok "Prowlarr: $natname já adicionado"
    continue
  fi

  SCHEMA=$(echo "$ALL_SCHEMAS" | python3 -c "
import json,sys
schemas=json.load(sys.stdin)
s=next((x for x in schemas if x.get('name')==sys.argv[1]), None)
print(json.dumps(s) if s else '')
" "$natname" 2>/dev/null || true)

  if [ -z "$SCHEMA" ]; then
    warn "Prowlarr: schema '$natname' não encontrado (pulando)"
    continue
  fi

  BODY=$(echo "$SCHEMA" | python3 -c "
import json,sys
s=json.load(sys.stdin)
base_url=s.get('indexerUrls',[None])[0] or ''
for f in s.get('fields',[]):
    if f.get('name')=='baseUrl': f['value']=base_url
s['enable']=True; s['appProfileId']=1; s['tags']=[]
print(json.dumps(s))
" 2>/dev/null || true)

  RESULT=$(arr prowlarr.local POST /api/v1/indexer "$PROWLARR_KEY" "$BODY")
  NAME=$(echo "$RESULT" | python3 -c "import json,sys; print(json.load(sys.stdin).get('name','?'))" 2>/dev/null || true)
  if [ "$NAME" != "?" ] && [ -n "$NAME" ]; then
    ok "Prowlarr: $NAME adicionado"
  else
    warn "Prowlarr: erro ao adicionar $natname: $(echo "$RESULT" | head -c 120)"
  fi
done

echo ""
echo "==> Configuração completa!"
echo ""
echo "    qBittorrent  → http://qbittorrent.local:32266  (admin / $APP_PASSWORD)"
echo "    Radarr       → http://radarr.local:32266       ($APP_USERNAME / $APP_PASSWORD)"
echo "    Sonarr       → http://sonarr.local:32266       ($APP_USERNAME / $APP_PASSWORD)"
echo "    Prowlarr     → http://prowlarr.local:32266     ($APP_USERNAME / $APP_PASSWORD)"
echo "    Bazarr       → http://bazarr.local:32266       ($APP_USERNAME / $APP_PASSWORD)"
echo "    Jellyfin     → http://jellyfin.local:32266     ($JELLYFIN_USERNAME / ***)"
