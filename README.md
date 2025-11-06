# Infrastructure Monitoring Agent

An automated system for monitoring SLA compliance across a fleet of 66 vessels. The system tracks uptime for Access Points, Dashboards, and Servers by querying vessel-specific InfluxDB databases and provides automated alerting and JIRA integration.

## Features

- Daily automated monitoring of 66 vessel infrastructure components
- SLA compliance tracking with 95% uptime threshold
- Automated alerting for SLA violations
- JIRA integration with human approval workflow
- Centralized web dashboard for fleet-wide monitoring
- Integration with existing Grafana and InfluxDB infrastructure

## Quick Start

1. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

2. Configure environment variables (see `.env.example`)

3. Run the application:
   ```bash
   python main.py
   ```

4. Access the web dashboard at `http://localhost:8000`

## Project Structure

```
├── src/
│   ├── models/          # Data models and enums
│   ├── services/        # Core business logic services
│   ├── web/            # Web interface and API endpoints
│   └── config/         # Configuration management
├── tests/              # Test files
├── main.py            # Application entry point
└── requirements.txt   # Python dependencies
```

## Configuration

The application uses environment variables for configuration. Copy `.env.example` to `.env` and update the values:

- `INFLUXDB_*`: InfluxDB connection settings
- `JIRA_*`: JIRA API credentials
- `WEB_*`: Web server configuration
- `SLA_THRESHOLD`: SLA compliance threshold (default: 95%)

## Development

Install development dependencies:
```bash
pip install -r requirements.txt
pip install -e .[dev]
```

Run tests:
```bash
pytest
```

Format code:
```bash
black src/ tests/
```

Type checking:
```bash
mypy src/
```