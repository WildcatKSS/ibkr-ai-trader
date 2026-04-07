#!/usr/bin/env bash
# =============================================================================
# deploy/update.sh — Update IBKR AI Trader to the latest version
#
# Run as root on the server:
#   sudo bash /opt/ibkr-trader/deploy/update.sh
#
# What it does:
#   1. Stops ibkr-bot and ibkr-web
#   2. Pulls latest code from GitHub (clones to temp dir if needed)
#   3. Syncs files to /opt/ibkr-trader
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
#
# Authentication
# --------------
# The script uses the SSH key of the root user by default.
# Alternatively set GITHUB_TOKEN to use HTTPS:
#   GITHUB_TOKEN=ghp_xxx sudo bash deploy/update.sh
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
# Configuration
# ---------------------------------------------------------------------------
APP_DIR="/opt/ibkr-trader"
APP_USER="trader"
VENV="${APP_DIR}/venv"
GITHUB_ORG="wildcatkss"
GITHUB_REPO_NAME="ibkr-ai-trader"
BRANCH="main"

# Build the remote URL.
# HTTPS with token: GITHUB_TOKEN=ghp_xxx sudo bash deploy/update.sh
# SSH (default):    uses root's ~/.ssh key — no token needed for public repos
if [[ -n "${GITHUB_TOKEN:-}" ]]; then
    REMOTE_URL="https://${GITHUB_TOKEN}@github.com/${GITHUB_ORG}/${GITHUB_REPO_NAME}.git"
    AUTH_METHOD="HTTPS (token)"
else
    REMOTE_URL="git@github.com:${GITHUB_ORG}/${GITHUB_REPO_NAME}.git"
    AUTH_METHOD="SSH"
fi

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
command -v rsync   &>/dev/null || error "rsync is not installed (apt install rsync)."

info "Remote: ${REMOTE_URL//${GITHUB_TOKEN:-TOKEN}/***}"
info "Auth:   ${AUTH_METHOD}"

# ---------------------------------------------------------------------------
# Trap: restart services if the update fails so the server is never left idle
# ---------------------------------------------------------------------------
SERVICES_STOPPED=false
WORK_DIR=""

_on_exit() {
    local exit_code=$?
    # Remove temp clone directory if we created one
    if [[ -n "$WORK_DIR" && -d "$WORK_DIR" && "$WORK_DIR" != "$APP_DIR" ]]; then
        rm -rf "$WORK_DIR"
    fi
    if [[ "$SERVICES_STOPPED" == true && $exit_code -ne 0 ]]; then
        warn "Update failed — restarting services with the previous version..."
        systemctl start ibkr-bot ibkr-web 2>/dev/null || true
    fi
}
trap _on_exit EXIT

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
# Step 2 — Fetch latest code from GitHub
# ---------------------------------------------------------------------------
step "2 — Fetching latest code from GitHub"

if git -C "$APP_DIR" rev-parse --git-dir &>/dev/null; then
    # APP_DIR is already a git repo — update the remote URL and pull.
    WORK_DIR="$APP_DIR"
    git -C "$APP_DIR" remote set-url origin "$REMOTE_URL" 2>/dev/null || \
        git -C "$APP_DIR" remote add origin "$REMOTE_URL"

    COMMIT_BEFORE=$(git -C "$APP_DIR" rev-parse HEAD 2>/dev/null || echo "none")
    git -C "$APP_DIR" fetch origin "$BRANCH"
    git -C "$APP_DIR" reset --hard "origin/${BRANCH}"
    COMMIT_AFTER=$(git -C "$APP_DIR" rev-parse HEAD)

    if [[ "$COMMIT_BEFORE" == "$COMMIT_AFTER" ]]; then
        info "Already up to date ($(git -C "$APP_DIR" rev-parse --short HEAD))."
    else
        info "Updated $(git -C "$APP_DIR" rev-parse --short "$COMMIT_BEFORE") → $(git -C "$APP_DIR" rev-parse --short "$COMMIT_AFTER")"
        git -C "$APP_DIR" log --oneline "${COMMIT_BEFORE}..${COMMIT_AFTER}" | while read -r line; do
            info "  ${line}"
        done
    fi
else
    # APP_DIR is not a git repo — clone to a temp directory and rsync.
    WORK_DIR="$(mktemp -d /tmp/ibkr-update-XXXXXX)"
    info "Cloning into temp directory ${WORK_DIR} ..."
    git clone --depth=1 --branch "$BRANCH" "$REMOTE_URL" "$WORK_DIR"
    COMMIT_AFTER=$(git -C "$WORK_DIR" rev-parse HEAD)
    info "Fetched commit $(git -C "$WORK_DIR" rev-parse --short HEAD): $(git -C "$WORK_DIR" log -1 --format='%s')"
fi

# ---------------------------------------------------------------------------
# Step 3 — Sync files to deployment directory
# ---------------------------------------------------------------------------
if [[ "$WORK_DIR" != "$APP_DIR" ]]; then
    step "3 — Syncing files to ${APP_DIR}"
    rsync -a \
        --exclude='.git' --exclude='*.lgbm' --exclude='.env' \
        --exclude='venv/' --exclude='logs/' --exclude='backups/' \
        "${WORK_DIR}/" "${APP_DIR}/"
    chown -R "${APP_USER}:${APP_USER}" "${APP_DIR}"
    chmod 755 "${APP_DIR}"
    info "Files synced to ${APP_DIR}."
else
    step "3 — File sync"
    info "Deployed directly into ${APP_DIR} — rsync skipped."
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
    info "alembic.ini not found — skipping migrations."
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

# Disarm the exit trap — update completed successfully
trap - EXIT

# Cleanup temp clone if used
if [[ -n "$WORK_DIR" && -d "$WORK_DIR" && "$WORK_DIR" != "$APP_DIR" ]]; then
    rm -rf "$WORK_DIR"
fi

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
COMMIT_SHORT=$(git -C "${WORK_DIR:-$APP_DIR}" rev-parse --short HEAD 2>/dev/null || echo "unknown")
COMMIT_MSG=$(git -C "${WORK_DIR:-$APP_DIR}" log -1 --format='%s' 2>/dev/null | cut -c1-40 || echo "")
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
