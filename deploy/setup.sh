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
info()  { echo -e "${GREEN}[INFO]${NC}  $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*" >&2; exit 1; }
step()  { echo -e "\n${GREEN}══ Step $* ${NC}"; }

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
ENV_FILE="${APP_DIR}/.env"

# ---------------------------------------------------------------------------
# Resolve DB password: read from existing .env or generate a new one.
# Validate that the values read are non-empty to catch corrupt/edited .env files.
# ---------------------------------------------------------------------------
if [[ -f "$ENV_FILE" ]]; then
    DB_PASSWORD=$(grep '^DB_PASSWORD=' "$ENV_FILE" | cut -d= -f2-)
    SECRET_KEY=$(grep  '^SECRET_KEY='  "$ENV_FILE" | cut -d= -f2-)
    [[ -n "$DB_PASSWORD" ]] || error ".env exists but DB_PASSWORD is empty. Fix ${ENV_FILE} before re-running."
    [[ -n "$SECRET_KEY"  ]] || error ".env exists but SECRET_KEY is empty. Fix ${ENV_FILE} before re-running."
    FIRST_RUN=false
    info "Existing .env found — reusing DB credentials."
else
    DB_PASSWORD=$(openssl rand -base64 32 | tr -d '/+=\n' | head -c 32)
    SECRET_KEY=$(openssl rand  -base64 48 | tr -d '/+=\n' | head -c 48)
    FIRST_RUN=true
fi

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
    curl wget git rsync build-essential libssl-dev libffi-dev \
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

# Create DB and user (idempotent).
# SQL is written to a temp file and piped via stdin so the password never
# appears in the process list (ps aux). Both single quotes (' → '') and
# backslashes (\ → \\) are escaped before interpolation — MariaDB treats \
# as an escape character in string literals by default, so both characters
# must be handled. trap guarantees cleanup even if mariadb exits non-zero.
DB_PASSWORD_SQL="${DB_PASSWORD//\\/\\\\}"   # escape backslashes first
DB_PASSWORD_SQL="${DB_PASSWORD_SQL//\'/\'\'}" # then escape single quotes
DB_SQL_FILE=$(mktemp)
chmod 600 "$DB_SQL_FILE"
trap 'rm -f "$DB_SQL_FILE"' EXIT
cat > "$DB_SQL_FILE" <<SQLEOF
CREATE DATABASE IF NOT EXISTS \`${DB_NAME}\`
    CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER IF NOT EXISTS '${DB_USER}'@'localhost' IDENTIFIED BY '${DB_PASSWORD_SQL}';
ALTER  USER              '${DB_USER}'@'localhost' IDENTIFIED BY '${DB_PASSWORD_SQL}';
GRANT ALL PRIVILEGES ON \`${DB_NAME}\`.* TO '${DB_USER}'@'localhost';
FLUSH PRIVILEGES;
SQLEOF
mariadb < "$DB_SQL_FILE"
trap - EXIT
rm -f "$DB_SQL_FILE"
info "Database '${DB_NAME}' and user '${DB_USER}' ready."

# ---------------------------------------------------------------------------
# Step 5 — Nginx (HTTP-only config first; HTTPS added after Certbot in step 5a)
# ---------------------------------------------------------------------------
step "5 — Nginx"
if ! command -v nginx &>/dev/null; then
    DEBIAN_FRONTEND=noninteractive apt-get install -y -qq nginx
    systemctl enable nginx
    info "Nginx installed."
else
    info "Nginx already present — skipping install."
fi

NGINX_CONF="/etc/nginx/sites-available/ibkr-trader"
CERT_PATH="/etc/letsencrypt/live/${DOMAIN}/fullchain.pem"

if [[ ! -f "$CERT_PATH" ]]; then
    # Certificate does not exist yet — write HTTP-only config so nginx -t
    # passes and Certbot's ACME challenge can complete in step 5a.
    cat > "$NGINX_CONF" <<NGINXCONF_HTTP
# Temporary HTTP-only config — replaced with HTTPS config in step 5a.
server {
    listen 80;
    listen [::]:80;
    server_name ${DOMAIN};

    location /.well-known/acme-challenge/ { root /var/www/html; }
    location / { return 301 https://\$host\$request_uri; }
}
NGINXCONF_HTTP
    ln -sf "$NGINX_CONF" /etc/nginx/sites-enabled/ibkr-trader
    rm -f /etc/nginx/sites-enabled/default
    nginx -t && systemctl reload nginx
    info "Temporary HTTP config applied for ${DOMAIN}."
else
    info "Certificate already exists — writing full HTTPS config directly."
fi

# ---------------------------------------------------------------------------
# Step 5a — Certbot / Let's Encrypt
# ---------------------------------------------------------------------------
step "5a — Certbot & Let's Encrypt"
if ! command -v certbot &>/dev/null; then
    DEBIAN_FRONTEND=noninteractive apt-get install -y -qq certbot python3-certbot-nginx
    info "Certbot installed."
fi

if [[ ! -f "$CERT_PATH" ]]; then
    certbot certonly \
        --webroot \
        --webroot-path /var/www/html \
        --non-interactive \
        --agree-tos \
        --email "$LE_EMAIL" \
        -d "$DOMAIN"
    info "Let's Encrypt certificate provisioned for ${DOMAIN}."
else
    info "Certificate already exists for ${DOMAIN} — skipping."
fi

# Ensure the Certbot renewal timer is active. The apt package installs it
# automatically on Ubuntu 22.04, but an explicit enable makes this reliable.
systemctl is-enabled certbot.timer &>/dev/null \
    || systemctl enable --now certbot.timer
info "Certbot renewal timer active."

# Write (or overwrite) the full HTTPS Nginx config now that the cert exists.
cat > "$NGINX_CONF" <<NGINXCONF_HTTPS
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
NGINXCONF_HTTPS

ln -sf "$NGINX_CONF" /etc/nginx/sites-enabled/ibkr-trader
rm -f /etc/nginx/sites-enabled/default
nginx -t && systemctl reload nginx
info "Full HTTPS Nginx config applied for ${DOMAIN}."

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

# Copy project files if running from a different location than APP_DIR.
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
    chown -R "${APP_USER}:${APP_USER}" "$VENV"
    info "Virtual environment created."
else
    info "Virtual environment already exists — skipping creation."
fi

REQUIREMENTS="${APP_DIR}/requirements.txt"
if [[ -f "$REQUIREMENTS" ]]; then
    # Run pip as the application user so all installed files are owned by
    # APP_USER from the start, avoiding root-owned files in the venv.
    sudo -u "$APP_USER" "$VENV/bin/pip" install --quiet --upgrade pip
    sudo -u "$APP_USER" "$VENV/bin/pip" install --quiet -r "$REQUIREMENTS"
    info "Python dependencies installed."
else
    warn "requirements.txt not found — skipping pip install. Run manually after adding it."
fi

# ---------------------------------------------------------------------------
# Step 9 — .env file
# ---------------------------------------------------------------------------
step "9 — .env file"
if [[ "$FIRST_RUN" == true ]]; then
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
DB_NAME="${DB_NAME}"
DB_USER="${DB_USER}"
DB_PASSWORD="${DB_PASSWORD}"
SECRET_KEY="${SECRET_KEY}"
DOMAIN="${DOMAIN}"
ENVFILE
    chmod 600 "$ENV_FILE"
    chown "${APP_USER}:${APP_USER}" "$ENV_FILE"
    info ".env file created at ${ENV_FILE}."
else
    info ".env already exists — credentials preserved."
fi

# ---------------------------------------------------------------------------
# Step 10 & 11 — systemd services
# ---------------------------------------------------------------------------
step "10 & 11 — systemd services"
# Nginx site was already enabled in steps 5/5a. Register bot services here.
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
# Use copytruncate so Python's RotatingFileHandler file descriptors remain
# valid after logrotate runs — the process keeps writing to the original
# (now truncated) file without needing a reload or restart signal.
cat > /etc/logrotate.d/ibkr-trader <<LOGROTATE
${APP_DIR}/logs/*.log {
    daily
    rotate 90
    compress
    delaycompress
    missingok
    notifempty
    copytruncate
}
LOGROTATE
info "Log rotation configured (90-day retention, copytruncate)."

# ---------------------------------------------------------------------------
# Step 13 — UFW firewall
# ---------------------------------------------------------------------------
step "13 — UFW firewall"
# Use grep to check UFW status — avoids locale-dependent string comparison
# ("Status: active" vs "Status: actief" on non-English systems).
if ufw status | grep -q "^Status: active"; then
    info "UFW already active — ensuring required rules exist."
else
    ufw --force reset   > /dev/null
    ufw default deny incoming > /dev/null
    ufw default allow outgoing > /dev/null
fi
ufw allow ssh   comment "SSH"   > /dev/null
ufw allow http  comment "HTTP"  > /dev/null
ufw allow https comment "HTTPS" > /dev/null
ufw --force enable > /dev/null
info "UFW enabled: SSH, HTTP, HTTPS allowed; all other inbound blocked."

# ---------------------------------------------------------------------------
# Step 14 — Fail2ban
# ---------------------------------------------------------------------------
step "14 — Fail2ban"
# nginx-limit-req jail is omitted: it requires limit_req_zone in Nginx config
# which is not present. Only SSH and nginx-http-auth are enabled.
cat > /etc/fail2ban/jail.d/ibkr-trader.conf <<F2B
[sshd]
enabled  = true
maxretry = 5
bantime  = 3600

[nginx-http-auth]
enabled  = true
maxretry = 10
bantime  = 3600
F2B
systemctl enable --now fail2ban
# Use fail2ban-client reload rather than systemctl reload — not all fail2ban
# versions handle SIGHUP correctly via systemd; client reload is always safe.
fail2ban-client reload
info "Fail2ban configured and running."

# ---------------------------------------------------------------------------
# Step 15 — Daily MariaDB backup cron job
# ---------------------------------------------------------------------------
step "15 — MariaDB backup cron job"
BACKUP_SCRIPT="/usr/local/bin/ibkr-db-backup.sh"
# Write the backup script in two parts:
# 1. printf injects APP_DIR, DB_NAME and DB_USER from setup variables so the
#    script stays in sync if these values change in setup.sh.
# 2. Quoted heredoc ('BACKUP_EOF') writes the rest verbatim — no shell
#    expansion — so $(…) and ${…} inside execute at backup run time, not now.
# --defaults-extra-file keeps the DB password out of the process list.
# trap ensures the credentials temp file is always removed, even on failure.
{
    printf '#!/usr/bin/env bash\nset -euo pipefail\n\n'
    printf 'APP_DIR="%s"\n' "$APP_DIR"
    printf 'DB_NAME="%s"\n' "$DB_NAME"
    printf 'DB_USER="%s"\n\n' "$DB_USER"
    cat <<'BACKUP_EOF'
BACKUP_DIR="${APP_DIR}/backups"
RETENTION_DAYS=30
FILENAME="${BACKUP_DIR}/db-$(date +%Y%m%d-%H%M%S).sql.gz"

DB_PASS=$(grep '^DB_PASSWORD=' "${APP_DIR}/.env" | cut -d= -f2-)
[[ -n "$DB_PASS" ]] || { echo "ERROR: DB_PASSWORD not found in .env" >&2; exit 1; }

CREDS_FILE=$(mktemp)
chmod 600 "$CREDS_FILE"
trap 'rm -f "$CREDS_FILE"' EXIT

printf '[client]\nuser=%s\npassword=%s\n' "$DB_USER" "$DB_PASS" > "$CREDS_FILE"

mysqldump --defaults-extra-file="$CREDS_FILE" \
    --single-transaction --quick "$DB_NAME" | gzip > "$FILENAME"

find "$BACKUP_DIR" -name 'db-*.sql.gz' -mtime +"$RETENTION_DAYS" -delete
BACKUP_EOF
} > "$BACKUP_SCRIPT"

chmod 750 "$BACKUP_SCRIPT"

CRON_FILE="/etc/cron.d/ibkr-trader-backup"
echo "0 3 * * * root $BACKUP_SCRIPT >> ${APP_DIR}/logs/backup.log 2>&1" > "$CRON_FILE"
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
if [[ "$FIRST_RUN" == true ]]; then
    printf "║  DB password  : %-45s║\n" "${DB_PASSWORD}"
else
    printf "║  DB password  : %-45s║\n" "(unchanged — see ${ENV_FILE})"
fi
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
if [[ "$FIRST_RUN" == true ]]; then
    warn "Save the DB password above — it is stored in ${ENV_FILE} but shown here only once."
fi
