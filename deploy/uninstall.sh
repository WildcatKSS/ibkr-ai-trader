#!/usr/bin/env bash
# =============================================================================
# deploy/uninstall.sh — Remove IBKR AI Trader from the server
#
# Run as root:
#   sudo bash /opt/ibkr-trader/deploy/uninstall.sh
#
# What it always removes:
#   - ibkr-bot and ibkr-web services (stopped, disabled, unit files deleted)
#   - Application directory /opt/ibkr-trader (code, venv, logs, .env)
#   - System user 'trader'
#   - Cron job /etc/cron.d/ibkr-trader-backup
#   - Fail2ban config /etc/fail2ban/jail.d/ibkr-trader.conf
#   - Logrotate config /etc/logrotate.d/ibkr-trader
#
# What is removed on request (with confirmation):
#   - Database backup (taken before removal)
#   - MariaDB database + user (optionally MariaDB itself)
#   - Nginx site config (optionally Nginx itself)
#   - Let's Encrypt certificate
#   - UFW firewall rules for HTTP/HTTPS (SSH is never touched)
#
# What is never removed:
#   - Python 3.11   (system package, shared by other tools)
#   - Node.js       (same)
# =============================================================================

set -euo pipefail

# ---------------------------------------------------------------------------
# Colour helpers
# ---------------------------------------------------------------------------
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'
info()    { echo -e "${GREEN}[INFO]${NC}  $*"; }
warn()    { echo -e "${YELLOW}[WARN]${NC}  $*"; }
removed() { echo -e "${RED}[REMOVED]${NC} $*"; }
step()    { echo -e "\n${CYAN}══ $* ${NC}"; }

# ---------------------------------------------------------------------------
# Must be root
# ---------------------------------------------------------------------------
[[ $EUID -eq 0 ]] || { echo -e "${RED}[ERROR]${NC} Run as root: sudo bash deploy/uninstall.sh" >&2; exit 1; }

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
APP_DIR="/opt/ibkr-trader"
APP_USER="trader"
DB_NAME="ibkr_trader"
DB_USER="ibkr_trader"

# ---------------------------------------------------------------------------
# Banner
# ---------------------------------------------------------------------------
echo ""
echo "╔══════════════════════════════════════════════════════════════╗"
echo "║          IBKR AI Trader — Uninstall                          ║"
echo "╠══════════════════════════════════════════════════════════════╣"
echo "║  This script removes the IBKR AI Trader from this server.    ║"
echo "║  Some steps are irreversible.                                 ║"
echo "╚══════════════════════════════════════════════════════════════╝"
echo ""

# ---------------------------------------------------------------------------
# Collect choices up front — ask everything before touching anything
# ---------------------------------------------------------------------------

# Helper: ask a yes/no question. Default shown in brackets.
# Usage: ask_yn "Question" "y"|"n"  → sets $REPLY_YN to "y" or "n"
ask_yn() {
    local question="$1"
    local default="${2:-n}"
    local prompt
    if [[ "$default" == "y" ]]; then
        prompt="[J/n]"
    else
        prompt="[j/N]"
    fi
    while true; do
        read -rp "$(echo -e "${YELLOW}?${NC} ${question} ${prompt}: ")" raw
        raw="${raw,,}"  # lowercase
        if [[ -z "$raw" ]]; then
            REPLY_YN="$default"
            return
        fi
        case "$raw" in
            j|y|ja|yes) REPLY_YN="y"; return ;;
            n|nee|no)   REPLY_YN="n"; return ;;
            *) echo "  Voer j of n in." ;;
        esac
    done
}

echo -e "${CYAN}Kies welke componenten verwijderd moeten worden:${NC}"
echo ""

ask_yn "Database backup maken vóór verwijdering?" "y"; DO_BACKUP="$REPLY_YN"
ask_yn "MariaDB database + gebruiker verwijderen?" "n";  DO_DROP_DB="$REPLY_YN"
if [[ "$DO_DROP_DB" == "y" ]]; then
    ask_yn "MariaDB zelf ook verwijderen (apt remove)?" "n"; DO_REMOVE_MARIADB="$REPLY_YN"
else
    DO_REMOVE_MARIADB="n"
fi
ask_yn "Nginx site-configuratie verwijderen?" "n"; DO_NGINX_CONF="$REPLY_YN"
if [[ "$DO_NGINX_CONF" == "y" ]]; then
    ask_yn "Nginx zelf ook verwijderen (apt remove)?" "n"; DO_REMOVE_NGINX="$REPLY_YN"
else
    DO_REMOVE_NGINX="n"
fi
ask_yn "Let's Encrypt certificaat intrekken en verwijderen?" "n"; DO_CERTBOT="$REPLY_YN"
ask_yn "UFW firewall regels voor HTTP/HTTPS verwijderen?" "n";     DO_UFW="$REPLY_YN"

# ---------------------------------------------------------------------------
# Final confirmation
# ---------------------------------------------------------------------------
echo ""
echo -e "${YELLOW}══ Overzicht van wat verwijderd wordt ══${NC}"
echo "  • Services ibkr-bot en ibkr-web"
echo "  • Applicatiemap ${APP_DIR}"
echo "  • Systeemgebruiker '${APP_USER}'"
echo "  • Cron job, fail2ban config, logrotate config"
[[ "$DO_BACKUP"        == "y" ]] && echo "  • Database backup (vóór verwijdering)"
[[ "$DO_DROP_DB"       == "y" ]] && echo "  • MariaDB database '${DB_NAME}' + gebruiker '${DB_USER}'"
[[ "$DO_REMOVE_MARIADB" == "y" ]] && echo "  • MariaDB server (apt remove)"
[[ "$DO_NGINX_CONF"    == "y" ]] && echo "  • Nginx site-configuratie"
[[ "$DO_REMOVE_NGINX"  == "y" ]] && echo "  • Nginx server (apt remove)"
[[ "$DO_CERTBOT"       == "y" ]] && echo "  • Let's Encrypt certificaat"
[[ "$DO_UFW"           == "y" ]] && echo "  • UFW regels HTTP/HTTPS (SSH blijft staan)"
echo ""
echo -e "${RED}Dit kan niet ongedaan worden gemaakt.${NC}"
echo ""
read -rp "Typ VERWIJDER om te bevestigen (of iets anders om te annuleren): " CONFIRM
if [[ "$CONFIRM" != "VERWIJDER" ]]; then
    echo ""
    info "Geannuleerd — er is niets verwijderd."
    exit 0
fi
echo ""

# ---------------------------------------------------------------------------
# Step 1 — Optionele database backup
# ---------------------------------------------------------------------------
if [[ "$DO_BACKUP" == "y" ]]; then
    step "1 — Database backup"
    BACKUP_DIR="${APP_DIR}/backups"
    mkdir -p "$BACKUP_DIR"
    ENV_FILE="${APP_DIR}/.env"
    if [[ -f "$ENV_FILE" ]]; then
        DB_PASS=$(grep '^DB_PASSWORD=' "$ENV_FILE" | cut -d= -f2- | sed 's/^"//;s/"$//' || true)
        if [[ -n "$DB_PASS" ]]; then
            BACKUP_FILE="/root/ibkr-trader-final-backup-$(date +%Y%m%d-%H%M%S).sql.gz"
            CREDS_FILE=$(mktemp)
            chmod 600 "$CREDS_FILE"
            printf '[client]\nuser=%s\npassword=%s\n' "$DB_USER" "$DB_PASS" > "$CREDS_FILE"
            if mysqldump --defaults-extra-file="$CREDS_FILE" \
                --single-transaction --quick "$DB_NAME" 2>/dev/null | gzip > "$BACKUP_FILE"; then
                info "Backup opgeslagen: ${BACKUP_FILE}"
            else
                warn "Backup mislukt — doorgaan met verwijdering."
                rm -f "$BACKUP_FILE"
            fi
            rm -f "$CREDS_FILE"
        else
            warn "DB_PASSWORD niet gevonden in .env — backup overgeslagen."
        fi
    else
        warn ".env niet gevonden — backup overgeslagen."
    fi
else
    step "1 — Database backup"
    info "Overgeslagen."
fi

# ---------------------------------------------------------------------------
# Step 2 — Services stoppen en uitschakelen
# ---------------------------------------------------------------------------
step "2 — Services stoppen en uitschakelen"
for SERVICE in ibkr-bot ibkr-web; do
    if systemctl is-active --quiet "$SERVICE" 2>/dev/null; then
        systemctl stop "$SERVICE"
        info "${SERVICE} gestopt."
    fi
    if systemctl is-enabled --quiet "$SERVICE" 2>/dev/null; then
        systemctl disable "$SERVICE"
        info "${SERVICE} uitgeschakeld."
    fi
    UNIT_FILE="/etc/systemd/system/${SERVICE}.service"
    if [[ -f "$UNIT_FILE" ]]; then
        rm -f "$UNIT_FILE"
        removed "${UNIT_FILE} verwijderd."
    fi
done
systemctl daemon-reload
info "systemd herladen."

# ---------------------------------------------------------------------------
# Step 3 — MariaDB database en gebruiker
# ---------------------------------------------------------------------------
step "3 — MariaDB"
if [[ "$DO_DROP_DB" == "y" ]]; then
    if command -v mariadb &>/dev/null; then
        mariadb -e "DROP DATABASE IF EXISTS \`${DB_NAME}\`;" 2>/dev/null && \
            removed "Database '${DB_NAME}' verwijderd." || warn "Kon database niet verwijderen."
        mariadb -e "DROP USER IF EXISTS '${DB_USER}'@'localhost';" 2>/dev/null && \
            removed "Gebruiker '${DB_USER}' verwijderd." || warn "Kon DB-gebruiker niet verwijderen."
        mariadb -e "FLUSH PRIVILEGES;" 2>/dev/null || true
    else
        warn "MariaDB client niet gevonden — database handmatig verwijderen."
    fi

    if [[ "$DO_REMOVE_MARIADB" == "y" ]]; then
        DEBIAN_FRONTEND=noninteractive apt-get remove --purge -y mariadb-server mariadb-client \
            mariadb-common 2>/dev/null || true
        rm -rf /etc/mysql /var/lib/mysql /var/log/mysql
        DEBIAN_FRONTEND=noninteractive apt-get autoremove -y -qq 2>/dev/null || true
        removed "MariaDB server verwijderd."
    else
        info "MariaDB server blijft geïnstalleerd."
    fi
else
    info "Overgeslagen."
fi

# ---------------------------------------------------------------------------
# Step 4 — Nginx
# ---------------------------------------------------------------------------
step "4 — Nginx"
if [[ "$DO_NGINX_CONF" == "y" ]]; then
    rm -f /etc/nginx/sites-enabled/ibkr-trader
    rm -f /etc/nginx/sites-available/ibkr-trader
    removed "Nginx site-configuratie verwijderd."

    if [[ "$DO_REMOVE_NGINX" == "y" ]]; then
        if systemctl is-active --quiet nginx 2>/dev/null; then
            systemctl stop nginx
        fi
        DEBIAN_FRONTEND=noninteractive apt-get remove --purge -y nginx nginx-common \
            nginx-core 2>/dev/null || true
        DEBIAN_FRONTEND=noninteractive apt-get autoremove -y -qq 2>/dev/null || true
        removed "Nginx server verwijderd."
    else
        # Reload so the removed site takes effect
        if command -v nginx &>/dev/null && nginx -t 2>/dev/null; then
            systemctl reload-or-restart nginx 2>/dev/null || true
            info "Nginx herladen (site verwijderd)."
        fi
    fi
else
    info "Overgeslagen."
fi

# ---------------------------------------------------------------------------
# Step 5 — Let's Encrypt certificaat
# ---------------------------------------------------------------------------
step "5 — Let's Encrypt certificaat"
if [[ "$DO_CERTBOT" == "y" ]]; then
    if command -v certbot &>/dev/null; then
        # Read domain from .env if the file still exists, otherwise ask
        DOMAIN=""
        if [[ -f "${APP_DIR}/.env" ]]; then
            DOMAIN=$(grep '^DOMAIN=' "${APP_DIR}/.env" | cut -d= -f2- | sed 's/^"//;s/"$//' || true)
        fi
        if [[ -z "$DOMAIN" ]]; then
            read -rp "Voer de domeinnaam in voor het certificaat: " DOMAIN
        fi
        if [[ -n "$DOMAIN" ]]; then
            certbot delete --cert-name "$DOMAIN" --non-interactive 2>/dev/null && \
                removed "Let's Encrypt certificaat voor ${DOMAIN} verwijderd." || \
                warn "Certbot delete mislukt — certificaat handmatig verwijderen met: certbot delete --cert-name ${DOMAIN}"
        else
            warn "Geen domeinnaam opgegeven — certificaat overgeslagen."
        fi
    else
        warn "Certbot niet gevonden — certificaat al verwijderd of nooit aangemaakt."
    fi
else
    info "Overgeslagen."
fi

# ---------------------------------------------------------------------------
# Step 6 — UFW firewall regels
# ---------------------------------------------------------------------------
step "6 — UFW firewall regels"
if [[ "$DO_UFW" == "y" ]]; then
    if command -v ufw &>/dev/null; then
        ufw delete allow http  2>/dev/null && removed "UFW regel HTTP verwijderd."  || warn "HTTP regel niet gevonden."
        ufw delete allow https 2>/dev/null && removed "UFW regel HTTPS verwijderd." || warn "HTTPS regel niet gevonden."
        ufw reload 2>/dev/null || true
        info "UFW herladen. SSH-toegang is ongewijzigd."
    else
        warn "UFW niet gevonden — overgeslagen."
    fi
else
    info "Overgeslagen."
fi

# ---------------------------------------------------------------------------
# Step 7 — Cron job
# ---------------------------------------------------------------------------
step "7 — Cron job"
CRON_FILE="/etc/cron.d/ibkr-trader-backup"
if [[ -f "$CRON_FILE" ]]; then
    rm -f "$CRON_FILE"
    removed "${CRON_FILE} verwijderd."
else
    info "Cron job niet gevonden — overgeslagen."
fi
BACKUP_SCRIPT="/usr/local/bin/ibkr-db-backup.sh"
if [[ -f "$BACKUP_SCRIPT" ]]; then
    rm -f "$BACKUP_SCRIPT"
    removed "${BACKUP_SCRIPT} verwijderd."
fi

# ---------------------------------------------------------------------------
# Step 8 — Fail2ban config
# ---------------------------------------------------------------------------
step "8 — Fail2ban"
F2B_CONF="/etc/fail2ban/jail.d/ibkr-trader.conf"
if [[ -f "$F2B_CONF" ]]; then
    rm -f "$F2B_CONF"
    removed "${F2B_CONF} verwijderd."
    if systemctl is-active --quiet fail2ban 2>/dev/null; then
        fail2ban-client reload 2>/dev/null || true
        info "Fail2ban herladen."
    fi
else
    info "Fail2ban config niet gevonden — overgeslagen."
fi

# ---------------------------------------------------------------------------
# Step 9 — Logrotate config
# ---------------------------------------------------------------------------
step "9 — Logrotate"
LOGROTATE_CONF="/etc/logrotate.d/ibkr-trader"
if [[ -f "$LOGROTATE_CONF" ]]; then
    rm -f "$LOGROTATE_CONF"
    removed "${LOGROTATE_CONF} verwijderd."
else
    info "Logrotate config niet gevonden — overgeslagen."
fi

# ---------------------------------------------------------------------------
# Step 10 — Applicatiemap verwijderen
# ---------------------------------------------------------------------------
step "10 — Applicatiemap ${APP_DIR}"
if [[ -d "$APP_DIR" ]]; then
    rm -rf "$APP_DIR"
    removed "${APP_DIR} verwijderd."
else
    info "${APP_DIR} bestaat niet meer — overgeslagen."
fi

# ---------------------------------------------------------------------------
# Step 11 — Systeemgebruiker verwijderen
# ---------------------------------------------------------------------------
step "11 — Systeemgebruiker '${APP_USER}'"
if id "$APP_USER" &>/dev/null; then
    userdel "$APP_USER" 2>/dev/null && removed "Gebruiker '${APP_USER}' verwijderd." || \
        warn "Kon gebruiker '${APP_USER}' niet verwijderen — handmatig: userdel ${APP_USER}"
else
    info "Gebruiker '${APP_USER}' bestaat niet meer — overgeslagen."
fi

# ---------------------------------------------------------------------------
# Samenvatting
# ---------------------------------------------------------------------------
echo ""
echo "╔══════════════════════════════════════════════════════════════╗"
echo "║              Verwijdering voltooid                           ║"
echo "╠══════════════════════════════════════════════════════════════╣"
[[ "$DO_BACKUP"        == "y" ]] && echo "║  Backup: /root/ibkr-trader-final-backup-*.sql.gz             ║"
echo "║  Python 3.11 en Node.js zijn niet aangeraakt.                ║"
echo "╚══════════════════════════════════════════════════════════════╝"
echo ""
