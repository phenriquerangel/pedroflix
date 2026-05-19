#!/bin/bash
set -e

BASE_DIR="/run/media/pedro/Nvme_1TB/pedroflix"

echo "==> Criando estrutura de diretórios em $BASE_DIR..."

mkdir -p \
  "$BASE_DIR/config/jellyfin" \
  "$BASE_DIR/config/radarr" \
  "$BASE_DIR/config/sonarr" \
  "$BASE_DIR/config/prowlarr" \
  "$BASE_DIR/config/bazarr" \
  "$BASE_DIR/config/qbittorrent" \
  "$BASE_DIR/media/movies" \
  "$BASE_DIR/media/tv" \
  "$BASE_DIR/downloads/complete" \
  "$BASE_DIR/downloads/incomplete"

chmod -R 755 "$BASE_DIR"
chown -R 1000:1000 "$BASE_DIR"

echo "==> Estrutura criada:"
find "$BASE_DIR" -type d | sort
