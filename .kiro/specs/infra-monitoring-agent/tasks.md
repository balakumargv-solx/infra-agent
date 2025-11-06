# Implementation Plan

- [x] 1. Set up project structure and core interfaces
  - Create directory structure: src/models/, src/services/, src/web/, src/config/, tests/
  - Set up Python package with __init__.py files and requirements.txt
  - Create main.py entry point and basic project configuration
  - _Requirements: 5.3, 5.5_

Don- [x] 2. Implement core data models and enums
  - [x] 2.1 Create data model classes and enums
    - Implement ComponentType, OperationalStatus, and IssueSeverity enums
    - Create VesselMetrics, ComponentStatus, SLAStatus, and IssueSummary dataclasses
    - Add data validation methods and serialization support
    - _Requirements: 1.2, 1.3, 1.4, 2.1_

  - [x] 2.2 Implement configuration management system
    - Create Config dataclass for vessel database connections and SLA parameters
    - Implement configuration loading from environment variables and config files
    - Add validation for InfluxDB connection parameters and 95% SLA threshold
    - _Requirements: 5.5, 1.1, 2.1_

  - [ ]* 2.3 Write unit tests for data models
    - Create unit tests for data model validation and serialization
    - Test configuration loading and validation logic
    - _Requirements: 1.2, 1.3, 1.4_

- [x] 3. Create InfluxDB data collection service
  - [x] 3.1 Implement InfluxDB client wrapper
    - Create InfluxDBClient class with connection pooling and authentication
    - Implement query methods for retrieving ping status data from vessel databases
    - Add retry logic with exponential backoff for network resilience
    - _Requirements: 1.1, 1.2_

  - [x] 3.2 Implement DataCollector service
    - Create DataCollector class that queries all 66 vessel-specific InfluxDB instances
    - Implement 24-hour uptime percentage calculation for Access Points, Dashboards, and Servers
    - Add concurrent querying capability to handle multiple vessels efficiently
    - Implement current operational status determination for each component
    - _Requirements: 1.1, 1.2, 1.3, 1.4_

  - [ ]* 3.3 Write unit tests for data collection
    - Create mock InfluxDB responses for testing
    - Test uptime calculation accuracy and edge cases
    - _Requirements: 1.1, 1.2, 1.3_

- [x] 4. Implement SLA analysis engine
  - [x] 4.1 Create SLAAnalyzer service
    - Implement SLAAnalyzer class that processes VesselMetrics data
    - Add uptime percentage validation against 95% SLA threshold
    - Implement downtime aging calculation for components currently in down state
    - Create SLA compliance status determination for each component
    - _Requirements: 1.3, 1.5, 2.1_

  - [x] 4.2 Implement historical data tracking
    - Add logic to track component status changes over time
    - Implement persistent storage for SLA violation history
    - Create methods for calculating violation duration and trends
    - _Requirements: 1.4, 2.2, 2.4_

  - [ ]* 4.3 Write unit tests for SLA analysis
    - Test SLA calculation accuracy with various uptime scenarios
    - Verify downtime aging calculations and threshold detection
    - _Requirements: 1.3, 1.5, 2.1_

- [x] 5. Create alert management system
  - [x] 5.1 Implement AlertManager service
    - Create AlertManager class that monitors SLA violations across all vessels
    - Implement alert generation when component uptime falls below 95% threshold
    - Add alert deduplication logic to prevent duplicate notifications
    - Implement alert status tracking and persistence
    - _Requirements: 2.1, 2.4_

  - [x] 5.2 Implement persistent downtime monitoring
    - Add logic to detect when downtime aging exceeds 3-day threshold
    - Create trigger mechanism for JIRA ticket creation process
    - Implement comprehensive alert logging for audit trails and historical tracking
    - Add alert status maintenance until issues are resolved
    - _Requirements: 2.2, 2.3_

  - [ ]* 5.3 Write unit tests for alert management
    - Test alert generation triggers and deduplication logic
    - Verify persistent downtime detection and logging
    - _Requirements: 2.1, 2.2, 2.3_

- [x] 6. Implement JIRA integration service
  - [x] 6.1 Create JIRAService client
    - Implement JIRA API client with authentication and error handling
    - Create methods for searching existing tickets using vessel and component filter criteria
    - Add ticket creation functionality with comprehensive issue descriptions
    - Implement ticket status tracking and update capabilities
    - _Requirements: 3.1, 3.4, 3.5_

  - [x] 6.2 Implement human approval workflow
    - Create approval request system that presents issue summaries to human operators
    - Implement approval response handling and validation logic
    - Add timeout handling and retry mechanisms for approval requests
    - Create approval decision logging for audit purposes
    - _Requirements: 3.3, 3.4_

  - [x] 6.3 Implement duplicate prevention and ticket management
    - Add logic to check for existing tickets before creating new ones
    - Create detailed ticket descriptions including vessel ID, component type, downtime duration, and historical context
    - Implement ticket lifecycle tracking from creation to resolution
    - Add integration with alert system to link tickets to alerts
    - _Requirements: 3.2, 3.4, 3.5_

  - [ ]* 6.4 Write unit tests for JIRA integration
    - Mock JIRA API responses for testing
    - Test approval workflow and ticket creation logic
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5_

- [x] 7. Create web dashboard service
  - [x] 7.1 Set up FastAPI web server and API endpoints
    - Implement FastAPI application with REST endpoints for fleet monitoring
    - Create API routes: /fleet-overview, /vessel/{id}/details, /sla-violations
    - Add WebSocket endpoint for real-time status updates
    - Implement basic error handling and response formatting
    - _Requirements: 4.1, 4.2, 4.5_

  - [x] 7.2 Implement FleetDashboard service class
    - Create FleetDashboard class that aggregates status from all 66 vessels
    - Implement fleet-wide SLA status calculation and vessel detail retrieval
    - Add SLA violation detection and highlighting logic
    - Create drill-down capability for individual vessel metrics
    - _Requirements: 4.1, 4.2, 4.3, 4.4_

  - [x] 7.3 Create HTML templates and frontend interface
    - Design responsive HTML template for fleet overview showing all vessels
    - Create vessel detail page template with component-specific metrics
    - Implement CSS styling with visual indicators for SLA violations (red/green status)
    - Add JavaScript for real-time updates and interactive drill-down features
    - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5_

  - [ ]* 7.4 Write unit tests for web dashboard
    - Test API endpoints and data serialization
    - Verify dashboard data aggregation and filtering
    - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5_

- [x] 8. Implement scheduling and orchestration
  - [x] 8.1 Create daily monitoring scheduler
    - Implement background scheduler using APScheduler for daily vessel monitoring
    - Add configurable scheduling for daily execution of monitoring workflow
    - Create orchestration logic that coordinates DataCollector, SLAAnalyzer, AlertManager, and JIRAService
    - _Requirements: 1.1, 2.2_

  - [x] 8.2 Implement main monitoring workflow
    - Create MonitoringOrchestrator class that executes the complete daily monitoring process
    - Integrate all services: data collection → SLA analysis → alert generation → JIRA ticket creation
    - Add comprehensive error handling and recovery mechanisms for failed operations
    - Implement structured logging for system health monitoring and troubleshooting
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 2.1, 2.2, 2.3, 5.4_

  - [ ]* 8.3 Write integration tests for monitoring workflow
    - Test end-to-end monitoring process with mock data
    - Verify error handling and recovery mechanisms
    - _Requirements: 1.1, 2.2, 5.4_

- [x] 9. Implement system integration and deployment
  - [x] 9.1 Add SQLite database for persistent state management
    - Implement SQLite database schema for alert history and ticket tracking
    - Create database models for storing SLA violations, alerts, and JIRA ticket references
    - Add database migration and initialization scripts
    - Implement persistent state management for system recovery after failures
    - _Requirements: 2.2, 3.5, 5.4_

  - [x] 9.2 Implement security and credential management
    - Add secure credential management for InfluxDB and JIRA API connections
    - Implement environment variable-based configuration for sensitive data
    - Add basic API authentication for web dashboard access
    - Implement comprehensive audit logging for compliance and troubleshooting
    - _Requirements: 5.4, 6.4, 6.5_

  - [x] 9.3 Create deployment configuration and main application entry point
    - Create main.py application entry point that starts web server and scheduler
    - Add requirements.txt with all necessary Python dependencies
    - Create Docker configuration for containerized deployment
    - Add environment-specific configuration files and deployment documentation
    - _Requirements: 5.3, 5.4, 5.5_

  - [ ]* 9.4 Write system integration tests
    - Test integration with real InfluxDB instances using test data
    - Verify JIRA integration with sandbox environment
    - Test complete system deployment and configuration
    - _Requirements: 6.1, 6.2, 6.3, 6.4, 6.5_