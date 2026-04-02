#!/usr/bin/env bash
# =============================================================================
# deploy/setup.sh — IBKR AI Trader server setup
#
# Run as root on a fresh Ubuntu Server 22.04 LTS instance:
#   chmod +x deploy/setup.sh
#   sudo bash deploy/setup.sh
#
# The script is idempotent: every step checks whether the component is already
# present and skips it if so. Re-running on an existing installation is safe.
# =============================================================================

set -euo pipefail

# ---------------------------------------------------------------------------
# Colour helpers
# ---------------------------------------------------------------------------
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
info()    { echo -e "${GREEN}[INFO]${NC}  $*"; }
warn()    { echo -e "${YELLOW}[WARN]${NC}  $*"; }
error()   { echo -e "${RED}[ERROR]${NC} $*" >&2; exit 1; }
step()    { echo -e "\n${GREEN}══ Step $* ${NC}"; }

# ---------------------------------------------------------------------------
# Must be root
# ---------------------------------------------------------------------------
[[ $EUID -eq 0 ]] || error "Run this script as root: sudo bash deploy/setup.sh"

# ---------------------------------------------------------------------------
# Collect user input up front
# ---------------------------------------------------------------------------
echo ""
echo "╔══════════════════════════════════════════╗"
echo "║       IBKR AI Trader — Server Setup      ║"
echo "╚══════════════════════════════════════════╝"
echo ""

read -rp "Enter your domain name (e.g. trader.example.com): " DOMAIN
[[ -n "$DOMAIN" ]] || error "Domain name cannot be empty."

read -rp "Enter your email address (for Let's Encrypt): " LE_EMAIL
[[ -n "$LE_EMAIL" ]] || error "Email address cannot be empty."

APP_DIR="/opt/ibkr-trader"
APP_USER="trader"
DB_NAME="ibkr_trader"
DB_USER="ibkr_trader"
DB_PASSWORD=$(openssl rand -base64 32 | tr -d '/+=\n' | head -c 32)
SECRET_KEY=$(openssl rand -base64 48 | tr -d '/+=\n' | head -c 48)

# ---------------------------------------------------------------------------
# Step 1 — System update
# ---------------------------------------------------------------------------
step "1 — System update"
apt-get update -qq
DEBIAN_FRONTEND=noninteractive apt-get upgrade -y -qq
info "System packages up to date."

# ---------------------------------------------------------------------------
# Step 2 — System packages
# ---------------------------------------------------------------------------
step "2 — System packages"
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
    curl wget git build-essential libssl-dev libffi-dev \
    ca-certificates gnupg lsb-release software-properties-common \
    logrotate cron ufw fail2ban unzip
info "System packages installed."

# ---------------------------------------------------------------------------
# Step 3 — Python 3.11
# ---------------------------------------------------------------------------
step "3 — Python 3.11"
if ! command -v python3.11 &>/dev/null; then
    add-apt-repository -y ppa:deadsnakes/ppa
    apt-get update -qq
    DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
        python3.11 python3.11-venv python3.11-dev python3-pip
    info "Python 3.11 installed."
else
    info "Python 3.11 already present — skipping."
fi

# ---------------------------------------------------------------------------
# Step 4 — MariaDB 10.11
# ---------------------------------------------------------------------------
step "4 — MariaDB 10.11"
if ! command -v mariadb &>/dev/null; then
    curl -fsSL https://r.mariadb.com/downloads/mariadb_repo_setup \
        | bash -s -- --mariadb-server-version="mariadb-10.11"
    DEBIAN_FRONTEND=noninteractive apt-get install -y -qq mariadb-server
    systemctl enable --now mariadb
    info "MariaDB installed and started."
else
    info "MariaDB already present — skipping install."
fi

# Create DB and user (idempotent)
mariadb -e "CREATE DATABASE IF NOT EXISTS \`${DB_NAME}\` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;"
mariadb -e "CREATE USER IF NOT EXISTS '${DB_USER}'@'localhost' IDENTIFIED BY '${DB_PASSWORD}';"
mariadb -e "GRANT ALL PRIVILEGES ON \`${DB_NAME}\`.* TO '${DB_USER}'@'localhost';"
mariadb -e "FLUSH PRIVILEGES;"
info "Database '${DB_NAME}' and user '${DB_USER}' ready."

# ---------------------------------------------------------------------------
# Step 5 — Nginx
# ---------------------------------------------------------------------------
step "5 — Nginx"
if ! command -v nginx &>/dev/null; then
    DEBIAN_FRONTEND=noninteractive apt-get install -y -qq nginx
    systemctl enable nginx
    info "Nginx installed."
else
    info "Nginx already present — skipping install."
fi

# Write Nginx config
NGINX_CONF="/etc/nginx/sites-available/ibkr-trader"
cat > "$NGINX_CONF" <<NGINXCONF
# Managed by deploy/setup.sh — do not edit manually.

# HTTP → HTTPS redirect
server {
    listen 80;
    listen [::]:80;
    server_name ${DOMAIN};

    location /.well-known/acme-challenge/ { root /var/www/html; }
    location / { return 301 https://\$host\$request_uri; }
}

# HTTPS — reverse proxy to FastAPI
server {
    listen 443 ssl http2;
    listen [::]:443 ssl http2;
    server_name ${DOMAIN};

    ssl_certificate     /etc/letsencrypt/live/${DOMAIN}/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/${DOMAIN}/privkey.pem;
    ssl_protocols       TLSv1.2 TLSv1.3;
    ssl_ciphers         HIGH:!aNULL:!MD5;
    ssl_session_cache   shared:SSL:10m;
    ssl_session_timeout 10m;

    add_header Strict-Transport-Security "max-age=63072000; includeSubDomains" always;
    add_header X-Frame-Options DENY always;
    add_header X-Content-Type-Options nosniff always;

    # Static frontend files
    location /static/ {
        alias ${APP_DIR}/web/frontend/static/;
        expires 30d;
        add_header Cache-Control "public, immutable";
    }

    # FastAPI backend (with WebSocket support)
    location / {
        proxy_pass         http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header   Upgrade \$http_upgrade;
        proxy_set_header   Connection "upgrade";
        proxy_set_header   Host \$host;
        proxy_set_header   X-Real-IP \$remote_addr;
        proxy_set_header   X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto \$scheme;
        proxy_read_timeout 300s;
    }
}
NGINXCONF

ln -sf "$NGINX_CONF" /etc/nginx/sites-enabled/ibkr-trader
rm -f /etc/nginx/sites-enabled/default
nginx -t && systemctl reload nginx
info "Nginx configured for ${DOMAIN}."

# ---------------------------------------------------------------------------
# Step 5a — Certbot / Let's Encrypt
# ---------------------------------------------------------------------------
step "5a — Certbot & Let's Encrypt"
if ! command -v certbot &>/dev/null; then
    DEBIAN_FRONTEND=noninteractive apt-get install -y -qq certbot python3-certbot-nginx
    info "Certbot installed."
fi

CERT_PATH="/etc/letsencrypt/live/${DOMAIN}/fullchain.pem"
if [[ ! -f "$CERT_PATH" ]]; then
    certbot --nginx \
        --non-interactive \
        --agree-tos \
        --email "$LE_EMAIL" \
        --redirect \
        -d "$DOMAIN"
    info "Let's Encrypt certificate provisioned for ${DOMAIN}."
else
    info "Certificate already exists for ${DOMAIN} — skipping."
fi

# Reload Nginx with final TLS config
nginx -t && systemctl reload nginx

# ---------------------------------------------------------------------------
# Step 6 — Node.js 20
# ---------------------------------------------------------------------------
step "6 — Node.js 20"
if ! command -v node &>/dev/null || [[ "$(node --version | cut -d. -f1)" != "v20" ]]; then
    curl -fsSL https://deb.nodesource.com/setup_20.x | bash -
    DEBIAN_FRONTEND=noninteractive apt-get install -y -qq nodejs
    info "Node.js $(node --version) installed."
else
    info "Node.js $(node --version) already present — skipping."
fi

# ---------------------------------------------------------------------------
# Step 7 — System user & application directories
# ---------------------------------------------------------------------------
step "7 — System user and directories"
if ! id "$APP_USER" &>/dev/null; then
    useradd --system --shell /usr/sbin/nologin --home "$APP_DIR" "$APP_USER"
    info "System user '${APP_USER}' created."
else
    info "System user '${APP_USER}' already exists — skipping."
fi

mkdir -p \
    "${APP_DIR}" \
    "${APP_DIR}/logs" \
    "${APP_DIR}/db/migrations" \
    "${APP_DIR}/backups"

# Copy project files if running from repo root
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"
if [[ "$REPO_ROOT" != "$APP_DIR" ]]; then
    rsync -a --exclude='.git' --exclude='*.lgbm' --exclude='.env' \
        "${REPO_ROOT}/" "${APP_DIR}/"
    info "Project files copied to ${APP_DIR}."
fi

chown -R "${APP_USER}:${APP_USER}" "${APP_DIR}"
chmod 750 "${APP_DIR}"
info "Directories created and ownership set."

# ---------------------------------------------------------------------------
# Step 8 — Python virtual environment
# ---------------------------------------------------------------------------
step "8 — Python virtual environment"
VENV="${APP_DIR}/venv"
if [[ ! -d "$VENV" ]]; then
    python3.11 -m venv "$VENV"
    info "Virtual environment created."
else
    info "Virtual environment already exists — skipping creation."
fi

REQUIREMENTS="${APP_DIR}/requirements.txt"
if [[ -f "$REQUIREMENTS" ]]; then
    "$VENV/bin/pip" install --quiet --upgrade pip
    "$VENV/bin/pip" install --quiet -r "$REQUIREMENTS"
    info "Python dependencies installed."
else
    warn "requirements.txt not found — skipping pip install. Run manually after adding it."
fi

chown -R "${APP_USER}:${APP_USER}" "$VENV"

# ---------------------------------------------------------------------------
# Step 9 — .env file
# ---------------------------------------------------------------------------
step "9 — .env file"
ENV_FILE="${APP_DIR}/.env"
if [[ ! -f "$ENV_FILE" ]]; then
    cat > "$ENV_FILE" <<ENVFILE
# Generated by deploy/setup.sh on $(date -u +"%Y-%m-%dT%H:%M:%SZ")
# Fill in your API keys below. All other settings are managed via the web interface.

# ── External API keys (fill in manually) ─────────────────────────────────────
ANTHROPIC_API_KEY=
IBKR_PORT=7497
ALPACA_API_KEY=
ALPACA_API_SECRET=
FINNHUB_API_KEY=
SMTP_PASSWORD=

# ── Generated automatically — do not change ──────────────────────────────────
DB_HOST=localhost
DB_PORT=3306
DB_NAME=${DB_NAME}
DB_USER=${DB_USER}
DB_PASSWORD=${DB_PASSWORD}
SECRET_KEY=${SECRET_KEY}
DOMAIN=${DOMAIN}
ENVFILE
    chmod 600 "$ENV_FILE"
    chown "${APP_USER}:${APP_USER}" "$ENV_FILE"
    info ".env file created at ${ENV_FILE}."
else
    info ".env already exists — skipping. DB_PASSWORD not rotated."
fi

# ---------------------------------------------------------------------------
# Step 10 — Enable Nginx site (already done in step 5)
# ---------------------------------------------------------------------------
step "10 — Nginx site enabled"
info "Already handled in step 5."

# ---------------------------------------------------------------------------
# Step 11 — systemd services
# ---------------------------------------------------------------------------
step "11 — systemd services"
SYSTEMD_DIR="${APP_DIR}/deploy/systemd"
for SERVICE in ibkr-bot ibkr-web; do
    SRC="${SYSTEMD_DIR}/${SERVICE}.service"
    DST="/etc/systemd/system/${SERVICE}.service"
    if [[ -f "$SRC" ]]; then
        cp "$SRC" "$DST"
        systemctl daemon-reload
        systemctl enable "$SERVICE"
        info "Service ${SERVICE} registered and enabled."
    else
        warn "${SRC} not found — skipping. Create deploy/systemd/${SERVICE}.service first."
    fi
done

# ---------------------------------------------------------------------------
# Step 12 — Log rotation
# ---------------------------------------------------------------------------
step "12 — Log rotation"
cat > /etc/logrotate.d/ibkr-trader <<LOGROTATE
${APP_DIR}/logs/*.log {
    daily
    rotate 90
    compress
    delaycompress
    missingok
    notifempty
    create 0640 ${APP_USER} ${APP_USER}
    sharedscripts
    postrotate
        systemctl reload ibkr-bot ibkr-web > /dev/null 2>&1 || true
    endscript
}
LOGROTATE
info "Log rotation configured (90-day retention)."

# ---------------------------------------------------------------------------
# Step 13 — UFW firewall
# ---------------------------------------------------------------------------
step "13 — UFW firewall"
ufw --force reset > /dev/null
ufw default deny incoming > /dev/null
ufw default allow outgoing > /dev/null
ufw allow ssh comment "SSH"
ufw allow http comment "HTTP (redirect to HTTPS)"
ufw allow https comment "HTTPS"
ufw --force enable > /dev/null
info "UFW enabled: SSH, HTTP, HTTPS allowed; all other inbound blocked."

# ---------------------------------------------------------------------------
# Step 14 — Fail2ban
# ---------------------------------------------------------------------------
step "14 — Fail2ban"
cat > /etc/fail2ban/jail.d/ibkr-trader.conf <<F2B
[sshd]
enabled  = true
maxretry = 5
bantime  = 3600

[nginx-http-auth]
enabled  = true
maxretry = 10
bantime  = 3600

[nginx-limit-req]
enabled  = true
maxretry = 20
bantime  = 600
F2B
systemctl enable --now fail2ban
systemctl reload fail2ban
info "Fail2ban configured and running."

# ---------------------------------------------------------------------------
# Step 15 — Daily MariaDB backup cron job
# ---------------------------------------------------------------------------
step "15 — MariaDB backup cron job"
BACKUP_SCRIPT="/usr/local/bin/ibkr-db-backup.sh"
cat > "$BACKUP_SCRIPT" <<BACKUP
#!/usr/bin/env bash
set -euo pipefail
BACKUP_DIR="${APP_DIR}/backups"
RETENTION_DAYS=30
FILENAME="\${BACKUP_DIR}/db-\$(date +%Y%m%d-%H%M%S).sql.gz"
# Read password from .env without sourcing the whole file
DB_PASS=\$(grep '^DB_PASSWORD=' "${APP_DIR}/.env" | cut -d= -f2-)
mysqldump --single-transaction --quick \
    -u "${DB_USER}" -p"\${DB_PASS}" "${DB_NAME}" | gzip > "\${FILENAME}"
find "\${BACKUP_DIR}" -name 'db-*.sql.gz' -mtime +\${RETENTION_DAYS} -delete
BACKUP
chmod 750 "$BACKUP_SCRIPT"

CRON_LINE="0 3 * * * root $BACKUP_SCRIPT >> ${APP_DIR}/logs/backup.log 2>&1"
CRON_FILE="/etc/cron.d/ibkr-trader-backup"
echo "$CRON_LINE" > "$CRON_FILE"
chmod 644 "$CRON_FILE"
info "Daily DB backup scheduled at 03:00 UTC (30-day retention)."

# =============================================================================
# Summary
# =============================================================================
echo ""
echo "╔══════════════════════════════════════════════════════════════╗"
echo "║                    Setup complete!                           ║"
echo "╠══════════════════════════════════════════════════════════════╣"
printf "║  Domain       : %-45s║\n" "${DOMAIN}"
printf "║  App dir      : %-45s║\n" "${APP_DIR}"
printf "║  DB name      : %-45s║\n" "${DB_NAME}"
printf "║  DB user      : %-45s║\n" "${DB_USER}"
printf "║  DB password  : %-45s║\n" "${DB_PASSWORD}"
echo "╠══════════════════════════════════════════════════════════════╣"
echo "║  Next steps:                                                 ║"
echo "║  1. Fill in API keys in /opt/ibkr-trader/.env               ║"
echo "║  2. Run: cd /opt/ibkr-trader                                ║"
echo "║         venv/bin/alembic upgrade head                       ║"
echo "║         venv/bin/python db/seed.py                          ║"
echo "║  3. systemctl start ibkr-bot ibkr-web                       ║"
echo "║  4. Open https://${DOMAIN}                                  ║"
echo "╚══════════════════════════════════════════════════════════════╝"
echo ""
warn "Save the DB password above — it is stored in ${APP_DIR}/.env but shown here only once."
