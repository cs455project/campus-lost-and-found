#!/usr/bin/env bash
# Runs on the AWS server (Ubuntu 24.04). Safe to run again after every update.
#   1. installs Docker and adds swap memory (first time only)
#   2. creates deploy/.env with random passwords (first time only)
#   3. builds and (re)starts the app
set -euo pipefail
cd "$(dirname "$0")"

# ---- 1. Docker ----
if ! command -v docker >/dev/null 2>&1; then
  echo "Installing Docker..."
  sudo apt-get update -y
  sudo apt-get install -y docker.io docker-compose-v2
  sudo systemctl enable --now docker
  sudo usermod -aG docker "$USER" || true
fi

# ---- Swap: building Next.js and loading the AI models needs headroom ----
if ! sudo swapon --show | grep -q /swapfile; then
  echo "Adding 2 GB of swap..."
  sudo fallocate -l 2G /swapfile
  sudo chmod 600 /swapfile
  sudo mkswap /swapfile >/dev/null
  sudo swapon /swapfile
  grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab >/dev/null
fi

# ---- 2. Settings ----
if [ ! -f .env ]; then
  echo "Creating deploy/.env..."
  TOKEN=$(curl -s -X PUT "http://169.254.169.254/latest/api/token" -H "X-aws-ec2-metadata-token-ttl-seconds: 60" || true)
  IP=$(curl -s -H "X-aws-ec2-metadata-token: $TOKEN" http://169.254.169.254/latest/meta-data/public-ipv4 || true)
  [ -n "$IP" ] || IP=$(curl -s https://checkip.amazonaws.com)
  SITE="$(echo "$IP" | tr '.' '-').sslip.io"
  DBPASS=$(openssl rand -hex 24)
  SECRET=$(openssl rand -base64 48 | tr -d '\n/+=' | cut -c1-64)
  sed -e "s|__SITE__|$SITE|g" \
      -e "s|__DB_PASSWORD__|$DBPASS|" \
      -e "s|__SECRET_KEY__|$SECRET|" \
      .env.example > .env
  chmod 600 .env
  echo "Site address: https://$SITE"
fi

# ---- 3. Build and start ----
sudo docker compose up -d --build
sudo docker image prune -f >/dev/null

SITE=$(grep '^SITE_ADDRESS=' .env | cut -d= -f2)
echo
echo "Done. Open https://$SITE"
echo "(The first visit can take a minute while the HTTPS certificate is issued.)"
