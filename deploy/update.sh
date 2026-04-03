#!/usr/bin/env bash
# =============================================================================
# deploy/update.sh — Update IBKR AI Trader to the latest version
#
# Run as root on the server:
#   sudo bash deploy/update.sh
#
# What it does:
#   1. Stops ibkr-bot and ibkr-web
#   2. Pulls the latest code from git
#   3. Syncs files to /opt/ibkr-trader (if running from a separate clone)
#   4. Installs new/updated Python dependencies
#   5. Runs pending database migrations
#   6. Reloads Nginx
#   7. Restarts ibkr-bot and ibkr-web
#
# Safety: if any step fails, services are automatically restarted with the
# previous version so the server is never left in a stopped state.
#
# ⚠ Do not run this script during an active trading session. The bot is
#   stopped for the duration of the update.
# =============================================================================

set -euo pipefail

# ---------------------------------------------------------------------------
# Colour helpers
# ---------------------------------------------------------------------------
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
info()  { echo -e "${GREEN}[INFO]${NC}  $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*" >&2; exit 1; }
step()  { echo -e "\n${GREEN}══ $* ${NC}"; }

# ---------------------------------------------------------------------------
# Must be root
# ---------------------------------------------------------------------------
[[ $EUID -eq 0 ]] || error "Run this script as root: sudo bash deploy/update.sh"

# ---------------------------------------------------------------------------
# Constants — must match setup.sh
# ---------------------------------------------------------------------------
APP_DIR="/opt/ibkr-trader"
APP_USER="trader"
VENV="${APP_DIR}/venv"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"

# ---------------------------------------------------------------------------
# Banner
# ---------------------------------------------------------------------------
echo ""
echo "╔══════════════════════════════════════════╗"
echo "║       IBKR AI Trader — Update            ║"
echo "╚══════════════════════════════════════════╝"
echo ""

# ---------------------------------------------------------------------------
# Preconditions
# ---------------------------------------------------------------------------
[[ -d "$APP_DIR" ]] || error "${APP_DIR} not found. Run deploy/setup.sh first."
[[ -d "$VENV"    ]] || error "Virtual environment not found at ${VENV}. Run deploy/setup.sh first."
command -v git     &>/dev/null || error "git is not installed."

# ---------------------------------------------------------------------------
# Trap: restart services if the update fails so the server is never left idle
# ---------------------------------------------------------------------------
SERVICES_STOPPED=false
_on_error() {
    if [[ "$SERVICES_STOPPED" == true ]]; then
        warn "Update failed — restarting services with the previous version..."
        systemctl start ibkr-bot ibkr-web 2>/dev/null || true
    fi
}
trap _on_error EXIT

# ---------------------------------------------------------------------------
# Step 1 — Stop services
# ---------------------------------------------------------------------------
step "1 — Stopping services"
SERVICES_STOPPED=true
for SERVICE in ibkr-bot ibkr-web; do
    if systemctl is-active --quiet "$SERVICE"; then
        systemctl stop "$SERVICE"
        info "${SERVICE} stopped."
    else
        info "${SERVICE} was not running — skipping."
    fi
done

# ---------------------------------------------------------------------------
# Step 2 — Git pull
# ---------------------------------------------------------------------------
step "2 — Pulling latest code"
cd "$REPO_ROOT"
COMMIT_BEFORE=$(git rev-parse HEAD)
git pull origin main
COMMIT_AFTER=$(git rev-parse HEAD)
if [[ "$COMMIT_BEFORE" == "$COMMIT_AFTER" ]]; then
    info "Already up to date ($(git rev-parse --short HEAD))."
else
    info "Updated $(git rev-parse --short "$COMMIT_BEFORE") → $(git rev-parse --short "$COMMIT_AFTER")"
    git log --oneline "${COMMIT_BEFORE}..${COMMIT_AFTER}" | while read -r line; do
        info "  ${line}"
    done
fi

# ---------------------------------------------------------------------------
# Step 3 — Sync files to deployment directory
# ---------------------------------------------------------------------------
if [[ "$REPO_ROOT" != "$APP_DIR" ]]; then
    step "3 — Syncing files to ${APP_DIR}"
    rsync -a \
        --exclude='.git' --exclude='*.lgbm' --exclude='.env' \
        --exclude='venv/' --exclude='logs/' --exclude='backups/' \
        "${REPO_ROOT}/" "${APP_DIR}/"
    chown -R "${APP_USER}:${APP_USER}" "${APP_DIR}"
    chmod 755 "${APP_DIR}"
    info "Files synced to ${APP_DIR}."
else
    step "3 — File sync"
    info "Running from ${APP_DIR} — rsync skipped."
fi

# ---------------------------------------------------------------------------
# Step 4 — Install / update Python dependencies
# ---------------------------------------------------------------------------
step "4 — Python dependencies"
REQUIREMENTS="${APP_DIR}/requirements.txt"
if [[ -f "$REQUIREMENTS" ]]; then
    sudo -u "$APP_USER" "$VENV/bin/pip" install --quiet --upgrade pip
    sudo -u "$APP_USER" "$VENV/bin/pip" install --quiet -r "$REQUIREMENTS"
    info "Python dependencies up to date."
else
    warn "requirements.txt not found — skipping pip install."
fi

# ---------------------------------------------------------------------------
# Step 5 — Database migrations
# ---------------------------------------------------------------------------
step "5 — Database migrations"
ALEMBIC_INI="${APP_DIR}/alembic.ini"
if [[ -f "$ALEMBIC_INI" ]]; then
    (cd "$APP_DIR" && sudo -u "$APP_USER" "$VENV/bin/alembic" upgrade head)
    info "Database migrations applied."
else
    info "alembic.ini not found — skipping migrations (no migrations exist yet)."
fi

# ---------------------------------------------------------------------------
# Step 6 — Reload Nginx
# ---------------------------------------------------------------------------
step "6 — Reloading Nginx"
if command -v nginx &>/dev/null; then
    nginx -t && systemctl reload-or-restart nginx
    info "Nginx reloaded."
else
    warn "Nginx not installed — skipping reload."
fi

# ---------------------------------------------------------------------------
# Step 7 — Start services
# ---------------------------------------------------------------------------
step "7 — Starting services"
SERVICES_STOPPED=false
for SERVICE in ibkr-bot ibkr-web; do
    if [[ -f "/etc/systemd/system/${SERVICE}.service" ]]; then
        systemctl start "$SERVICE"
        info "${SERVICE} started."
    else
        warn "${SERVICE}.service not registered — skipping. Run deploy/setup.sh first."
    fi
done

# Disarm the error trap — update completed successfully
trap - EXIT

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
COMMIT_SHORT=$(git -C "$REPO_ROOT" rev-parse --short HEAD)
COMMIT_MSG=$(git -C "$REPO_ROOT" log -1 --format='%s' | cut -c1-40)
echo ""
echo "╔══════════════════════════════════════════════════════════════╗"
echo "║                    Update complete!                          ║"
echo "╠══════════════════════════════════════════════════════════════╣"
printf "║  Commit  : %-51s║\n" "${COMMIT_SHORT} — ${COMMIT_MSG}"
printf "║  App dir : %-51s║\n" "${APP_DIR}"
echo "╠══════════════════════════════════════════════════════════════╣"
echo "║  Check status:                                               ║"
echo "║    systemctl status ibkr-bot ibkr-web                        ║"
echo "║    journalctl -u ibkr-bot -f                                 ║"
echo "╚══════════════════════════════════════════════════════════════╝"
echo ""
