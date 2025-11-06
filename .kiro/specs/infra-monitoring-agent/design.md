# Infrastructure Monitoring Agent Design

## Overview

The Infrastructure Monitoring Agent is a distributed monitoring system designed to ensure SLA compliance across a fleet of 66 vessels. The system operates as an automated agent that queries vessel-specific InfluxDB databases, calculates uptime metrics, manages alerting, and integrates with JIRA for issue tracking. The architecture prioritizes reliability, maintainability, and seamless integration with existing Grafana/InfluxDB infrastructure.

## Architecture

### High-Level Architecture

The system follows a modular, event-driven architecture implemented as a single Python application with clear separation of concerns:

```
┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐
│   Scheduler     │    │  Data Collector │    │  SLA Analyzer   │
│   (Daily Runs)  │───▶│  (InfluxDB)     │───▶│  (Uptime Calc)  │
└─────────────────┘    └─────────────────┘    └─────────────────┘
                                                        │
┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐
│ Python Web UI   │◀───│  Alert Manager  │◀───│  Ticket Manager │
│ (Flask/FastAPI) │    │  (Notifications)│    │     (JIRA)      │
└─────────────────┘    └─────────────────┘    └─────────────────┘
```

### Single Codebase Design

**Decision: Monolithic Python Application**
- Backend services and frontend web interface in single codebase
- FastAPI/Flask for web server and API endpoints
- HTML templates with JavaScript for interactive dashboard
- Background scheduler for automated monitoring tasks
- Rationale: Simplified deployment, maintenance, and development workflow

### Implementation Approach

**Decision: Traditional Python Implementation**
- Pure Python implementation using standard libraries and frameworks
- FastAPI/Flask for web services and API endpoints
- Standard Python libraries for scheduling, data processing, and integrations
- Rationale: Simplified development, maintenance, and deployment without AI framework complexity

### Core Components

1. **Data Collection Service**: Manages InfluxDB connections and data retrieval
2. **SLA Analysis Engine**: Calculates uptime percentages and downtime aging
3. **Alert Management System**: Handles threshold monitoring and notifications
4. **JIRA Integration Service**: Manages ticket lifecycle and human approval workflow
5. **Web Dashboard Service**: Provides Python-based web interface with real-time monitoring
6. **Configuration Manager**: Handles vessel database configurations and parameters

## Components and Interfaces

### Data Collection Service

**Purpose**: Query vessel-specific InfluxDB instances and retrieve infrastructure metrics

**Key Interfaces**:
```python
class DataCollector:
    def query_vessel_database(vessel_id: str) -> VesselMetrics
    def get_ping_status(vessel_id: str, component: ComponentType) -> PingData
    def calculate_24h_uptime(ping_data: PingData) -> float
```

**Design Decisions**:
- Concurrent querying of multiple vessels for performance
- Connection pooling for InfluxDB instances
- Retry logic with exponential backoff for network resilience

### SLA Analysis Engine

**Purpose**: Process collected data to determine SLA compliance and downtime aging

**Key Interfaces**:
```python
class SLAAnalyzer:
    def analyze_component_uptime(metrics: VesselMetrics) -> SLAStatus
    def calculate_downtime_aging(component_status: ComponentStatus) -> timedelta
    def check_sla_threshold(uptime_percentage: float) -> bool
```

**Design Decisions**:
- 95% SLA threshold as configurable parameter
- Historical data retention for trend analysis
- Component-specific status tracking (Access Point, Dashboard, Server)

### Alert Management System

**Purpose**: Generate and manage alerts based on SLA violations

**Key Interfaces**:
```python
class AlertManager:
    def generate_sla_alert(vessel_id: str, component: ComponentType) -> Alert
    def check_persistent_downtime(downtime_age: timedelta) -> bool
    def log_alert_history(alert: Alert) -> None
```

**Design Decisions**:
- 3-day threshold for ticket creation trigger
- Alert deduplication to prevent spam
- Comprehensive logging for audit trails

### JIRA Integration Service

**Purpose**: Manage automated ticket creation with human approval workflow

**Key Interfaces**:
```python
class JIRAService:
    def check_existing_tickets(vessel_id: str, component: ComponentType) -> List[Ticket]
    def request_human_approval(issue_summary: IssueSummary) -> ApprovalResponse
    def create_ticket(approved_issue: IssueSummary) -> Ticket
    def update_ticket_status(ticket_id: str, status: TicketStatus) -> None
```

**Design Decisions**:
- Human-in-the-loop approval process for ticket creation
- Duplicate ticket prevention through filter criteria
- Rich ticket descriptions with historical context

### Fleet Dashboard Service

**Purpose**: Provide centralized monitoring interface with drill-down capabilities

**Key Interfaces**:
```python
class FleetDashboard:
    def get_fleet_overview() -> FleetStatus
    def get_vessel_details(vessel_id: str) -> VesselDetails
    def highlight_sla_violations() -> List[ViolationAlert]
    def render_dashboard_html() -> str
    def serve_dashboard_api() -> FastAPI
```

**Design Decisions**:
- Simple Python-based web frontend (Flask/FastAPI + HTML/CSS/JavaScript)
- Real-time status updates via WebSocket or polling
- Visual indicators for SLA violations using CSS styling
- Self-contained dashboard without external dependencies

## Data Models

### Core Data Structures

```python
@dataclass
class VesselMetrics:
    vessel_id: str
    access_point_status: ComponentStatus
    dashboard_status: ComponentStatus
    server_status: ComponentStatus
    timestamp: datetime

@dataclass
class ComponentStatus:
    component_type: ComponentType
    uptime_percentage: float
    current_status: OperationalStatus
    downtime_aging: timedelta
    last_ping_time: datetime

@dataclass
class SLAStatus:
    vessel_id: str
    component_type: ComponentType
    is_compliant: bool
    uptime_percentage: float
    violation_duration: Optional[timedelta]

@dataclass
class IssueSummary:
    vessel_id: str
    component_type: ComponentType
    downtime_duration: timedelta
    historical_context: str
    severity: IssueSeverity
```

### Database Schema

**InfluxDB Query Patterns**:
- Time-series queries for 24-hour uptime calculations
- Real-time status queries for current operational state
- Historical data aggregation for trend analysis

**Local State Management**:
- SQLite database for alert history and ticket tracking
- Configuration storage for vessel database connections
- Audit logs for human approval decisions

## Error Handling

### Resilience Strategies

1. **Network Failures**:
   - Retry logic with exponential backoff
   - Circuit breaker pattern for failing InfluxDB instances
   - Graceful degradation when vessels are unreachable

2. **Data Quality Issues**:
   - Validation of InfluxDB query results
   - Handling of missing or incomplete ping data
   - Default values for calculation edge cases

3. **External Service Failures**:
   - JIRA API error handling and retry mechanisms
   - Grafana integration fallback options
   - Human approval workflow timeout handling

4. **System Recovery**:
   - Persistent state management for interrupted operations
   - Automatic recovery from partial failures
   - Comprehensive logging for troubleshooting

### Monitoring and Observability

- Health check endpoints for system status
- Metrics collection for performance monitoring
- Structured logging with correlation IDs
- Integration with existing monitoring infrastructure

## Testing Strategy

### Unit Testing
- Component-level testing for each service
- Mock InfluxDB responses for data collection testing
- SLA calculation accuracy verification
- JIRA integration API testing

### Integration Testing
- End-to-end workflow testing with test InfluxDB instances
- JIRA integration testing with sandbox environment
- Grafana dashboard integration validation
- Human approval workflow testing

### Performance Testing
- Load testing with 66 concurrent vessel queries
- Memory usage profiling for long-running operations
- Database query optimization validation
- Response time benchmarking for web dashboard

### Deployment Testing
- Configuration management testing
- Backward compatibility verification with existing systems
- Rollback procedure validation
- Production environment integration testing

## Integration Points

### Existing Infrastructure
- **InfluxDB**: Read-only access to vessel-specific databases
- **JIRA**: Ticket creation and management API integration
- **Vessel Networks**: Secure connectivity to maritime infrastructure

### API Design
- RESTful APIs for external system integration
- WebSocket connections for real-time dashboard updates
- Webhook endpoints for JIRA status updates
- Configuration APIs for vessel management
- Built-in web server serving both API endpoints and dashboard UI

### Security Considerations
- Secure credential management for database connections
- API authentication for JIRA integration
- Network security for vessel communications
- Audit logging for compliance requirements