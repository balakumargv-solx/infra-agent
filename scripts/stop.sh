#!/bin/bash
# Infrastructure Monitoring Agent Stop Script

set -e

# Configuration
APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
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

# Stop the application
stop_app() {
    log_info "Infrastructure Monitoring Agent - Stop Script"
    log_info "============================================"
    
    if [[ ! -f "$PID_FILE" ]]; then
        log_warn "PID file not found - application may not be running"
        
        # Try to find process by name
        PIDS=$(pgrep -f "python.*main.py" || true)
        if [[ -n "$PIDS" ]]; then
            log_info "Found running processes, attempting to stop..."
            for pid in $PIDS; do
                log_info "Stopping process $pid..."
                kill -TERM "$pid" 2>/dev/null || true
            done
            
            # Wait for graceful shutdown
            sleep 5
            
            # Force kill if still running
            for pid in $PIDS; do
                if ps -p "$pid" > /dev/null 2>&1; then
                    log_warn "Force killing process $pid..."
                    kill -KILL "$pid" 2>/dev/null || true
                fi
            done
        else
            log_info "No running processes found"
        fi
        
        return 0
    fi
    
    PID=$(cat "$PID_FILE")
    
    if ! ps -p "$PID" > /dev/null 2>&1; then
        log_warn "Process $PID is not running"
        rm -f "$PID_FILE"
        return 0
    fi
    
    log_info "Stopping application (PID: $PID)..."
    
    # Send SIGTERM for graceful shutdown
    kill -TERM "$PID"
    
    # Wait for graceful shutdown
    for i in {1..30}; do
        if ! ps -p "$PID" > /dev/null 2>&1; then
            log_info "Application stopped gracefully"
            rm -f "$PID_FILE"
            return 0
        fi
        sleep 1
    done
    
    # Force kill if still running
    log_warn "Graceful shutdown timeout, force killing..."
    kill -KILL "$PID" 2>/dev/null || true
    
    # Wait a bit more
    sleep 2
    
    if ps -p "$PID" > /dev/null 2>&1; then
        log_error "Failed to stop application"
        return 1
    else
        log_info "Application force stopped"
        rm -f "$PID_FILE"
        return 0
    fi
}

# Status check
status_check() {
    log_info "Infrastructure Monitoring Agent - Status Check"
    log_info "=============================================="
    
    if [[ ! -f "$PID_FILE" ]]; then
        log_info "Status: Not running (no PID file)"
        return 1
    fi
    
    PID=$(cat "$PID_FILE")
    
    if ps -p "$PID" > /dev/null 2>&1; then
        log_info "Status: Running (PID: $PID)"
        
        # Check if web server is responding
        if command -v curl &> /dev/null; then
            if curl -s -f "http://localhost:${WEB_PORT:-8000}/health" > /dev/null 2>&1; then
                log_info "Web server: Responding ✓"
            else
                log_warn "Web server: Not responding"
            fi
        fi
        
        return 0
    else
        log_warn "Status: Not running (stale PID file)"
        rm -f "$PID_FILE"
        return 1
    fi
}

# Restart the application
restart_app() {
    log_info "Infrastructure Monitoring Agent - Restart"
    log_info "========================================"
    
    # Stop if running
    if [[ -f "$PID_FILE" ]]; then
        stop_app
        sleep 2
    fi
    
    # Start
    log_info "Starting application..."
    "$APP_DIR/scripts/start.sh"
}

# Main execution
case "${1:-stop}" in
    stop)
        stop_app
        ;;
    status)
        status_check
        ;;
    restart)
        restart_app
        ;;
    --help|-h)
        echo "Usage: $0 [stop|status|restart]"
        echo ""
        echo "Commands:"
        echo "  stop      Stop the monitoring agent (default)"
        echo "  status    Check application status"
        echo "  restart   Restart the monitoring agent"
        echo "  --help    Show this help message"
        exit 0
        ;;
    *)
        log_error "Unknown command: $1"
        echo "Use '$0 --help' for usage information"
        exit 1
        ;;
esac