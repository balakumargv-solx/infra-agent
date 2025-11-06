"""
Enums for the Infrastructure Monitoring Agent.

This module defines the core enumerations used throughout the system
for component types, operational status, and issue severity levels.
"""

from enum import Enum


class ComponentType(str, Enum):
    """Types of infrastructure components monitored on each vessel."""
    
    ACCESS_POINT = "access_point"
    DASHBOARD = "dashboard"
    SERVER = "server"


class OperationalStatus(str, Enum):
    """Current operational status of infrastructure components."""
    
    UP = "up"
    DOWN = "down"
    UNKNOWN = "unknown"


class IssueSeverity(str, Enum):
    """Severity levels for infrastructure issues."""
    
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class AlertType(str, Enum):
    """Types of alerts that can be generated."""
    
    SLA_VIOLATION = "sla_violation"
    PERSISTENT_DOWNTIME = "persistent_downtime"
    COMPONENT_RECOVERY = "component_recovery"


class AlertSeverity(str, Enum):
    """Severity levels for alerts."""
    
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"