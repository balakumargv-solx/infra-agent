#!/bin/bash
# Infrastructure Monitoring Agent Startup Script

set -e

# Configuration
APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_DIR="${APP_DIR}/venv"
LOG_DIR="${APP_DIR}/logs"
DATA_DIR="${APP_DIR}/data"
PID_FILE="${APP_DIR}/monitoring_agent.pid"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Logging functions
log_info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Check if running as root
check_root() {
    if [[ $EUID -eq 0 ]]; then
        log_warn "Running as root is not recommended for security reasons"
        read -p "Continue anyway? (y/N): " -n 1 -r
        echo
        if [[ ! $REPLY =~ ^[Yy]$ ]]; then
            exit 1
        fi
    fi
}

# Create necessary directories
create_directories() {
    log_info "Creating necessary directories..."
    mkdir -p "$LOG_DIR" "$DATA_DIR"
    chmod 755 "$LOG_DIR" "$DATA_DIR"
}

# Check Python version
check_python() {
    log_info "Checking Python version..."
    if ! command -v python3 &> /dev/null; then
        log_error "Python 3 is not installed"
        exit 1
    fi
    
    PYTHON_VERSION=$(python3 -c 'import sys; print(".".join(map(str, sys.version_info[:2])))')
    REQUIRED_VERSION="3.11"
    
    if ! python3 -c "import sys; exit(0 if sys.version_info >= (3, 11) else 1)"; then
        log_error "Python $REQUIRED_VERSION or higher is required (found $PYTHON_VERSION)"
        exit 1
    fi
    
    log_info "Python version: $PYTHON_VERSION ✓"
}

# Setup virtual environment
setup_venv() {
    if [[ ! -d "$VENV_DIR" ]]; then
        log_info "Creating virtual environment..."
        python3 -m venv "$VENV_DIR"
    fi
    
    log_info "Activating virtual environment..."
    source "$VENV_DIR/bin/activate"
    
    log_info "Upgrading pip..."
    pip install --upgrade pip
    
    log_info "Installing dependencies..."
    pip install -r "$APP_DIR/requirements.txt"
}

# Check configuration
check_config() {
    log_info "Checking configuration..."
    
    if [[ ! -f "$APP_DIR/.env" ]]; then
        log_warn "No .env file found"
        if [[ -f "$APP_DIR/.env.example" ]]; then
            log_info "Copying .env.example to .env"
            cp "$APP_DIR/.env.example" "$APP_DIR/.env"
            log_warn "Please edit .env file with your configuration before starting"
            exit 1
        else
            log_error "No .env.example file found"
            exit 1
        fi
    fi
    
    # Check for required environment variables
    source "$APP_DIR/.env"
    
    REQUIRED_VARS=(
        "INFLUXDB_URL"
        "INFLUXDB_TOKEN"
        "JIRA_URL"
        "JIRA_USERNAME"
        "JIRA_API_TOKEN"
    )
    
    MISSING_VARS=()
    for var in "${REQUIRED_VARS[@]}"; do
        if [[ -z "${!var}" ]]; then
            MISSING_VARS+=("$var")
        fi
    done
    
    if [[ ${#MISSING_VARS[@]} -gt 0 ]]; then
        log_error "Missing required environment variables:"
        for var in "${MISSING_VARS[@]}"; do
            echo "  - $var"
        done
        log_error "Please configure these variables in .env file"
        exit 1
    fi
    
    log_info "Configuration check passed ✓"
}

# Check if application is already running
check_running() {
    if [[ -f "$PID_FILE" ]]; then
        PID=$(cat "$PID_FILE")
        if ps -p "$PID" > /dev/null 2>&1; then
            log_error "Application is already running (PID: $PID)"
            log_info "Use 'scripts/stop.sh' to stop it first"
            exit 1
        else
            log_warn "Stale PID file found, removing..."
            rm -f "$PID_FILE"
        fi
    fi
}

# Start the application
start_app() {
    log_info "Starting Infrastructure Monitoring Agent..."
    
    cd "$APP_DIR"
    source "$VENV_DIR/bin/activate"
    
    # Start application in background
    nohup python main.py > "$LOG_DIR/startup.log" 2>&1 &
    APP_PID=$!
    
    # Save PID
    echo $APP_PID > "$PID_FILE"
    
    # Wait a moment and check if it's still running
    sleep 3
    if ps -p $APP_PID > /dev/null 2>&1; then
        log_info "Application started successfully (PID: $APP_PID)"
        log_info "Web dashboard: http://localhost:${WEB_PORT:-8000}"
        log_info "Logs: $LOG_DIR/monitoring_agent.log"
        log_info "Use 'scripts/stop.sh' to stop the application"
    else
        log_error "Application failed to start"
        log_error "Check startup log: $LOG_DIR/startup.log"
        rm -f "$PID_FILE"
        exit 1
    fi
}

# Health check
health_check() {
    log_info "Performing health check..."
    
    # Wait for application to be ready
    for i in {1..30}; do
        if curl -s -f "http://localhost:${WEB_PORT:-8000}/health" > /dev/null 2>&1; then
            log_info "Health check passed ✓"
            return 0
        fi
        sleep 1
    done
    
    log_warn "Health check failed - application may still be starting"
    log_info "Check logs: $LOG_DIR/monitoring_agent.log"
}

# Main execution
main() {
    log_info "Infrastructure Monitoring Agent - Startup Script"
    log_info "=============================================="
    
    check_root
    create_directories
    check_python
    setup_venv
    check_config
    check_running
    start_app
    health_check
    
    log_info "Startup complete!"
}

# Handle script arguments
case "${1:-start}" in
    start)
        main
        ;;
    --help|-h)
        echo "Usage: $0 [start]"
        echo ""
        echo "Options:"
        echo "  start     Start the monitoring agent (default)"
        echo "  --help    Show this help message"
        exit 0
        ;;
    *)
        log_error "Unknown option: $1"
        echo "Use '$0 --help' for usage information"
        exit 1
        ;;
esac