# Infrastructure Monitoring Agent - Deployment Guide

This guide provides comprehensive instructions for deploying the Infrastructure Monitoring Agent in various environments.

## Table of Contents

1. [Prerequisites](#prerequisites)
2. [Environment Configuration](#environment-configuration)
3. [Local Development](#local-development)
4. [Docker Deployment](#docker-deployment)
5. [Production Deployment](#production-deployment)
6. [Security Configuration](#security-configuration)
7. [Monitoring and Maintenance](#monitoring-and-maintenance)
8. [Troubleshooting](#troubleshooting)

## Prerequisites

### System Requirements

- Python 3.11 or higher
- Docker and Docker Compose (for containerized deployment)
- Access to InfluxDB instances for vessel data
- JIRA instance with API access
- Minimum 512MB RAM, 1GB recommended
- 1GB disk space for logs and database

### Network Requirements

- Outbound HTTPS access to InfluxDB instances
- Outbound HTTPS access to JIRA API
- Inbound access on port 8000 (configurable) for web dashboard
- Optional: Slack webhook access for notifications

## Environment Configuration

### 1. Copy Environment Template

```bash
cp .env.example .env
```

### 2. Configure Required Settings

Edit `.env` file with your specific configuration:

```bash
# Security Configuration (REQUIRED)
MONITORING_MASTER_KEY=your-32-character-master-key-here
API_SECRET_KEY=your-api-secret-key-for-dashboard
DASHBOARD_USERNAME=admin
DASHBOARD_PASSWORD=your-secure-password

# InfluxDB Configuration (REQUIRED)
INFLUXDB_URL=https://your-influxdb-server.com
INFLUXDB_TOKEN=your-influxdb-token
INFLUXDB_ORG=your-organization
INFLUXDB_BUCKET=your-bucket

# JIRA Configuration (REQUIRED)
JIRA_URL=https://your-company.atlassian.net/
JIRA_USERNAME=your-email@company.com
JIRA_API_TOKEN=your-jira-api-token
JIRA_PROJECT_KEY=INFRA

# Vessel Configuration (REQUIRED)
VESSEL_IDS=vessel001,vessel002,vessel003,...
```

### 3. Generate Security Keys

```bash
# Generate master key
python -c "import secrets; print(secrets.token_urlsafe(32))"

# Generate API secret key
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

## Local Development

### 1. Install Dependencies

```bash
# Create virtual environment
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
```

### 2. Initialize Database

```bash
# Database will be automatically initialized on first run
python main.py
```

### 3. Access Dashboard

- Web Dashboard: http://localhost:8000
- API Documentation: http://localhost:8000/docs
- Health Check: http://localhost:8000/health

## Docker Deployment

### 1. Build and Run with Docker Compose

```bash
# Build and start services
docker-compose up -d

# View logs
docker-compose logs -f

# Stop services
docker-compose down
```

### 2. Build Custom Image

```bash
# Build image
docker build -t infra-monitoring-agent .

# Run container
docker run -d \
  --name monitoring-agent \
  -p 8000:8000 \
  --env-file .env \
  -v $(pwd)/data:/app/data \
  -v $(pwd)/logs:/app/logs \
  infra-monitoring-agent
```

### 3. Docker Health Checks

```bash
# Check container health
docker ps
docker inspect monitoring-agent | grep Health -A 10
```

## Production Deployment

### 1. System Service (systemd)

Create service file `/etc/systemd/system/monitoring-agent.service`:

```ini
[Unit]
Description=Infrastructure Monitoring Agent
After=network.target

[Service]
Type=simple
User=monitoring
Group=monitoring
WorkingDirectory=/opt/monitoring-agent
Environment=PATH=/opt/monitoring-agent/venv/bin
ExecStart=/opt/monitoring-agent/venv/bin/python main.py
Restart=always
RestartSec=10

# Security settings
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/opt/monitoring-agent/data /opt/monitoring-agent/logs

[Install]
WantedBy=multi-user.target
```

Enable and start service:

```bash
sudo systemctl daemon-reload
sudo systemctl enable monitoring-agent
sudo systemctl start monitoring-agent
sudo systemctl status monitoring-agent
```

### 2. Reverse Proxy (Nginx)

Create Nginx configuration `/etc/nginx/sites-available/monitoring-agent`:

```nginx
server {
    listen 80;
    server_name monitoring.your-domain.com;
    
    # Redirect to HTTPS
    return 301 https://$server_name$request_uri;
}

server {
    listen 443 ssl http2;
    server_name monitoring.your-domain.com;
    
    # SSL configuration
    ssl_certificate /path/to/your/certificate.crt;
    ssl_certificate_key /path/to/your/private.key;
    
    # Security headers
    add_header X-Frame-Options DENY;
    add_header X-Content-Type-Options nosniff;
    add_header X-XSS-Protection "1; mode=block";
    
    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        
        # WebSocket support
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
    }
    
    # Static files
    location /static/ {
        proxy_pass http://127.0.0.1:8000/static/;
        expires 1y;
        add_header Cache-Control "public, immutable";
    }
}
```

### 3. Process Management (Supervisor)

Create supervisor configuration `/etc/supervisor/conf.d/monitoring-agent.conf`:

```ini
[program:monitoring-agent]
command=/opt/monitoring-agent/venv/bin/python main.py
directory=/opt/monitoring-agent
user=monitoring
group=monitoring
autostart=true
autorestart=true
redirect_stderr=true
stdout_logfile=/var/log/supervisor/monitoring-agent.log
environment=PATH="/opt/monitoring-agent/venv/bin"
```

## Security Configuration

### 1. Credential Encryption

Encrypt sensitive credentials:

```python
from src.services.security_manager import CredentialManager

# Initialize credential manager
cred_manager = CredentialManager()

# Encrypt password
encrypted_password = cred_manager.encrypt_credential("your-password")
print(f"Encrypted: enc:{encrypted_password}")

# Use in environment variable
# JIRA_API_TOKEN=enc:gAAAAABhZ1234567890abcdef...
```

### 2. API Authentication

Generate API tokens:

```bash
# Using curl with basic auth
curl -X POST http://localhost:8000/api/auth/token \
  -u admin:your-password

# Response includes bearer token
{
  "access_token": "your-bearer-token",
  "token_type": "bearer",
  "expires_in": 86400
}

# Use token for API calls
curl -H "Authorization: Bearer your-bearer-token" \
  http://localhost:8000/api/fleet-overview
```

### 3. Firewall Configuration

```bash
# Allow only necessary ports
sudo ufw allow 22/tcp    # SSH
sudo ufw allow 80/tcp    # HTTP (redirect to HTTPS)
sudo ufw allow 443/tcp   # HTTPS
sudo ufw enable
```

## Monitoring and Maintenance

### 1. Log Management

```bash
# View application logs
tail -f logs/monitoring_agent.log

# View audit logs
tail -f logs/audit.log

# Rotate logs (add to crontab)
0 0 * * * /usr/sbin/logrotate /etc/logrotate.d/monitoring-agent
```

### 2. Database Maintenance

```bash
# Backup database
cp data/monitoring_agent.db data/monitoring_agent.db.backup.$(date +%Y%m%d)

# Clean old records (automated, but can be run manually)
python -c "
from src.services.database import DatabaseService
db = DatabaseService('data/monitoring_agent.db')
deleted = db.cleanup_old_records(days_to_keep=90)
print(f'Cleaned up {sum(deleted.values())} old records')
"
```

### 3. Health Monitoring

```bash
# Check application health
curl http://localhost:8000/health

# Check authentication status
curl -u admin:password http://localhost:8000/api/auth/status

# Monitor system resources
docker stats monitoring-agent  # For Docker deployment
```

### 4. Performance Monitoring

Key metrics to monitor:

- Response time for API endpoints
- Database size and query performance
- Memory usage and CPU utilization
- Number of active vessels being monitored
- SLA violation trends
- JIRA ticket creation rate

## Troubleshooting

### Common Issues

#### 1. Database Connection Errors

```bash
# Check database file permissions
ls -la data/monitoring_agent.db

# Reinitialize database
rm data/monitoring_agent.db
python main.py  # Will recreate database
```

#### 2. InfluxDB Connection Issues

```bash
# Test InfluxDB connectivity
curl -H "Authorization: Token your-token" \
  "https://your-influxdb-server.com/api/v2/ping"

# Check vessel configuration
python -c "
from src.config.config_loader import ConfigLoader
config = ConfigLoader().load_config()
print(f'Configured vessels: {len(config.vessel_databases)}')
"
```

#### 3. JIRA Integration Problems

```bash
# Test JIRA API access
curl -u your-email@company.com:your-api-token \
  "https://your-company.atlassian.net/rest/api/2/myself"

# Check JIRA project permissions
curl -u your-email@company.com:your-api-token \
  "https://your-company.atlassian.net/rest/api/2/project/INFRA"
```

#### 4. Authentication Issues

```bash
# Reset API tokens
python -c "
from src.services.security_manager import get_security_manager
auth = get_security_manager().get_api_authenticator()
auth.cleanup_expired_tokens()
print('Expired tokens cleaned up')
"
```

### Log Analysis

```bash
# Find authentication failures
grep "authentication_failed" logs/audit.log

# Find system errors
grep "ERROR" logs/monitoring_agent.log

# Monitor API access patterns
grep "api_access" logs/audit.log | tail -20
```

### Performance Optimization

1. **Database Optimization**:
   - Regular cleanup of old records
   - Index optimization for frequently queried data
   - Consider database vacuum operations

2. **Memory Management**:
   - Monitor memory usage during vessel data collection
   - Adjust concurrent query limits if needed
   - Consider implementing data pagination for large fleets

3. **Network Optimization**:
   - Use connection pooling for InfluxDB connections
   - Implement retry logic with exponential backoff
   - Monitor network latency to vessel databases

## Support

For additional support:

1. Check application logs in `logs/monitoring_agent.log`
2. Review audit logs in `logs/audit.log`
3. Verify configuration in `.env` file
4. Test individual components using the API endpoints
5. Monitor system resources and network connectivity

## Version Updates

When updating to a new version:

1. Stop the application
2. Backup the database and configuration
3. Update the code/container image
4. Run database migrations (automatic on startup)
5. Restart the application
6. Verify functionality with health checks