#!/bin/bash
# Infrastructure Monitoring Agent Installation Script

set -e

# Configuration
INSTALL_DIR="/opt/monitoring-agent"
SERVICE_USER="monitoring"
SERVICE_GROUP="monitoring"
SYSTEMD_SERVICE="monitoring-agent"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
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

log_step() {
    echo -e "${BLUE}[STEP]${NC} $1"
}

# Check if running as root
check_root() {
    if [[ $EUID -ne 0 ]]; then
        log_error "This script must be run as root"
        log_info "Please run: sudo $0"
        exit 1
    fi
}

# Detect OS
detect_os() {
    if [[ -f /etc/os-release ]]; then
        . /etc/os-release
        OS=$ID
        OS_VERSION=$VERSION_ID
    else
        log_error "Cannot detect operating system"
        exit 1
    fi
    
    log_info "Detected OS: $OS $OS_VERSION"
}

# Install system dependencies
install_dependencies() {
    log_step "Installing system dependencies..."
    
    case $OS in
        ubuntu|debian)
            apt-get update
            apt-get install -y \
                python3 \
                python3-pip \
                python3-venv \
                curl \
                wget \
                git \
                supervisor \
                nginx \
                logrotate
            ;;
        centos|rhel|fedora)
            if command -v dnf &> /dev/null; then
                dnf install -y \
                    python3 \
                    python3-pip \
                    curl \
                    wget \
                    git \
                    supervisor \
                    nginx \
                    logrotate
            else
                yum install -y \
                    python3 \
                    python3-pip \
                    curl \
                    wget \
                    git \
                    supervisor \
                    nginx \
                    logrotate
            fi
            ;;
        *)
            log_error "Unsupported operating system: $OS"
            exit 1
            ;;
    esac
    
    log_info "System dependencies installed ✓"
}

# Create service user
create_user() {
    log_step "Creating service user..."
    
    if ! id "$SERVICE_USER" &>/dev/null; then
        useradd --system --shell /bin/false --home-dir "$INSTALL_DIR" --create-home "$SERVICE_USER"
        log_info "Created user: $SERVICE_USER"
    else
        log_info "User $SERVICE_USER already exists"
    fi
}

# Install application
install_app() {
    log_step "Installing application..."
    
    # Create installation directory
    mkdir -p "$INSTALL_DIR"
    
    # Copy application files
    cp -r . "$INSTALL_DIR/"
    
    # Set ownership
    chown -R "$SERVICE_USER:$SERVICE_GROUP" "$INSTALL_DIR"
    
    # Create necessary directories
    mkdir -p "$INSTALL_DIR/logs" "$INSTALL_DIR/data"
    chown "$SERVICE_USER:$SERVICE_GROUP" "$INSTALL_DIR/logs" "$INSTALL_DIR/data"
    chmod 755 "$INSTALL_DIR/logs" "$INSTALL_DIR/data"
    
    log_info "Application installed to $INSTALL_DIR ✓"
}

# Setup Python environment
setup_python() {
    log_step "Setting up Python environment..."
    
    # Switch to service user for venv creation
    sudo -u "$SERVICE_USER" python3 -m venv "$INSTALL_DIR/venv"
    
    # Install dependencies
    sudo -u "$SERVICE_USER" "$INSTALL_DIR/venv/bin/pip" install --upgrade pip
    sudo -u "$SERVICE_USER" "$INSTALL_DIR/venv/bin/pip" install -r "$INSTALL_DIR/requirements.txt"
    
    log_info "Python environment setup complete ✓"
}

# Configure environment
configure_env() {
    log_step "Configuring environment..."
    
    if [[ ! -f "$INSTALL_DIR/.env" ]]; then
        if [[ -f "$INSTALL_DIR/.env.example" ]]; then
            cp "$INSTALL_DIR/.env.example" "$INSTALL_DIR/.env"
            chown "$SERVICE_USER:$SERVICE_GROUP" "$INSTALL_DIR/.env"
            chmod 600 "$INSTALL_DIR/.env"
            log_warn "Created .env file from template - please configure it before starting"
        else
            log_error "No .env.example file found"
            exit 1
        fi
    else
        log_info ".env file already exists"
    fi
}

# Setup systemd service
setup_systemd() {
    log_step "Setting up systemd service..."
    
    cat > "/etc/systemd/system/$SYSTEMD_SERVICE.service" << EOF
[Unit]
Description=Infrastructure Monitoring Agent
After=network.target

[Service]
Type=simple
User=$SERVICE_USER
Group=$SERVICE_GROUP
WorkingDirectory=$INSTALL_DIR
Environment=PATH=$INSTALL_DIR/venv/bin
ExecStart=$INSTALL_DIR/venv/bin/python main.py
Restart=always
RestartSec=10

# Security settings
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=$INSTALL_DIR/data $INSTALL_DIR/logs

[Install]
WantedBy=multi-user.target
EOF
    
    # Reload systemd
    systemctl daemon-reload
    
    log_info "Systemd service created ✓"
}

# Setup log rotation
setup_logrotate() {
    log_step "Setting up log rotation..."
    
    cat > "/etc/logrotate.d/$SYSTEMD_SERVICE" << EOF
$INSTALL_DIR/logs/*.log {
    daily
    missingok
    rotate 30
    compress
    delaycompress
    notifempty
    create 644 $SERVICE_USER $SERVICE_GROUP
    postrotate
        systemctl reload $SYSTEMD_SERVICE || true
    endscript
}
EOF
    
    log_info "Log rotation configured ✓"
}

# Setup nginx (optional)
setup_nginx() {
    if [[ "${SETUP_NGINX:-yes}" == "yes" ]]; then
        log_step "Setting up nginx reverse proxy..."
        
        cat > "/etc/nginx/sites-available/$SYSTEMD_SERVICE" << EOF
server {
    listen 80;
    server_name _;
    
    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        
        # WebSocket support
        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";
    }
    
    location /static/ {
        proxy_pass http://127.0.0.1:8000/static/;
        expires 1y;
        add_header Cache-Control "public, immutable";
    }
}
EOF
        
        # Enable site
        if [[ -d "/etc/nginx/sites-enabled" ]]; then
            ln -sf "/etc/nginx/sites-available/$SYSTEMD_SERVICE" "/etc/nginx/sites-enabled/"
        fi
        
        # Test nginx configuration
        nginx -t
        
        log_info "Nginx configuration created ✓"
        log_warn "Please configure SSL certificates for production use"
    fi
}

# Setup firewall
setup_firewall() {
    if command -v ufw &> /dev/null && [[ "${SETUP_FIREWALL:-no}" == "yes" ]]; then
        log_step "Configuring firewall..."
        
        ufw allow ssh
        ufw allow 80/tcp
        ufw allow 443/tcp
        ufw --force enable
        
        log_info "Firewall configured ✓"
    fi
}

# Final setup
final_setup() {
    log_step "Completing installation..."
    
    # Enable service
    systemctl enable "$SYSTEMD_SERVICE"
    
    # Start nginx if configured
    if [[ "${SETUP_NGINX:-yes}" == "yes" ]]; then
        systemctl enable nginx
        systemctl restart nginx
    fi
    
    log_info "Installation completed successfully! ✓"
    echo
    log_info "Next steps:"
    echo "1. Configure $INSTALL_DIR/.env with your settings"
    echo "2. Start the service: systemctl start $SYSTEMD_SERVICE"
    echo "3. Check status: systemctl status $SYSTEMD_SERVICE"
    echo "4. View logs: journalctl -u $SYSTEMD_SERVICE -f"
    echo
    if [[ "${SETUP_NGINX:-yes}" == "yes" ]]; then
        echo "Web dashboard will be available at: http://your-server-ip/"
    else
        echo "Web dashboard will be available at: http://your-server-ip:8000/"
    fi
}

# Uninstall function
uninstall() {
    log_step "Uninstalling Infrastructure Monitoring Agent..."
    
    # Stop and disable service
    systemctl stop "$SYSTEMD_SERVICE" 2>/dev/null || true
    systemctl disable "$SYSTEMD_SERVICE" 2>/dev/null || true
    
    # Remove systemd service
    rm -f "/etc/systemd/system/$SYSTEMD_SERVICE.service"
    systemctl daemon-reload
    
    # Remove nginx configuration
    rm -f "/etc/nginx/sites-available/$SYSTEMD_SERVICE"
    rm -f "/etc/nginx/sites-enabled/$SYSTEMD_SERVICE"
    
    # Remove logrotate configuration
    rm -f "/etc/logrotate.d/$SYSTEMD_SERVICE"
    
    # Remove installation directory
    if [[ -d "$INSTALL_DIR" ]]; then
        read -p "Remove installation directory $INSTALL_DIR? (y/N): " -n 1 -r
        echo
        if [[ $REPLY =~ ^[Yy]$ ]]; then
            rm -rf "$INSTALL_DIR"
            log_info "Installation directory removed"
        fi
    fi
    
    # Remove user
    read -p "Remove service user $SERVICE_USER? (y/N): " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        userdel "$SERVICE_USER" 2>/dev/null || true
        log_info "Service user removed"
    fi
    
    log_info "Uninstallation completed"
}

# Main installation
main() {
    log_info "Infrastructure Monitoring Agent - Installation Script"
    log_info "=================================================="
    
    check_root
    detect_os
    install_dependencies
    create_user
    install_app
    setup_python
    configure_env
    setup_systemd
    setup_logrotate
    setup_nginx
    setup_firewall
    final_setup
}

# Handle script arguments
case "${1:-install}" in
    install)
        main
        ;;
    uninstall)
        uninstall
        ;;
    --help|-h)
        echo "Usage: $0 [install|uninstall]"
        echo ""
        echo "Commands:"
        echo "  install     Install the monitoring agent (default)"
        echo "  uninstall   Remove the monitoring agent"
        echo "  --help      Show this help message"
        echo ""
        echo "Environment variables:"
        echo "  SETUP_NGINX=no      Skip nginx setup"
        echo "  SETUP_FIREWALL=yes  Configure firewall"
        exit 0
        ;;
    *)
        log_error "Unknown command: $1"
        echo "Use '$0 --help' for usage information"
        exit 1
        ;;
esac