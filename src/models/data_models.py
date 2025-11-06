"""
Core data models for the Infrastructure Monitoring Agent.

This module defines the main data structures used throughout the system
for vessel metrics, component status, SLA tracking, and issue management.
"""

from datetime import datetime, timedelta
from typing import Optional, Dict, Any
from dataclasses import dataclass, asdict
from pydantic import BaseModel, Field, validator

from .enums import ComponentType, OperationalStatus, IssueSeverity


@dataclass
class ComponentStatus:
    """Status information for a single infrastructure component."""
    
    component_type: ComponentType
    uptime_percentage: float
    current_status: OperationalStatus
    downtime_aging: timedelta
    last_ping_time: datetime
    
    def __post_init__(self):
        """Validate data after initialization."""
        if not 0 <= self.uptime_percentage <= 100:
            raise ValueError("Uptime percentage must be between 0 and 100")
        
        if self.downtime_aging.total_seconds() < 0:
            raise ValueError("Downtime aging cannot be negative")
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        data = asdict(self)
        data['downtime_aging'] = self.downtime_aging.total_seconds()
        data['last_ping_time'] = self.last_ping_time.isoformat()
        return data
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'ComponentStatus':
        """Create instance from dictionary."""
        data = data.copy()
        data['component_type'] = ComponentType(data['component_type'])
        data['current_status'] = OperationalStatus(data['current_status'])
        data['downtime_aging'] = timedelta(seconds=data['downtime_aging'])
        data['last_ping_time'] = datetime.fromisoformat(data['last_ping_time'])
        return cls(**data)


@dataclass
class VesselMetrics:
    """Complete metrics for all infrastructure components on a vessel."""
    
    vessel_id: str
    access_point_status: ComponentStatus
    dashboard_status: ComponentStatus
    server_status: ComponentStatus
    timestamp: datetime
    
    def __post_init__(self):
        """Validate data after initialization."""
        if not self.vessel_id or not self.vessel_id.strip():
            raise ValueError("Vessel ID cannot be empty")
        
        # Ensure all components have the correct type
        expected_types = {
            'access_point_status': ComponentType.ACCESS_POINT,
            'dashboard_status': ComponentType.DASHBOARD,
            'server_status': ComponentType.SERVER
        }
        
        for attr_name, expected_type in expected_types.items():
            component = getattr(self, attr_name)
            if component.component_type != expected_type:
                raise ValueError(f"{attr_name} must have component_type {expected_type}")
    
    def get_component_status(self, component_type: ComponentType) -> ComponentStatus:
        """Get status for a specific component type."""
        component_map = {
            ComponentType.ACCESS_POINT: self.access_point_status,
            ComponentType.DASHBOARD: self.dashboard_status,
            ComponentType.SERVER: self.server_status
        }
        return component_map[component_type]
    
    def get_all_components(self) -> Dict[ComponentType, ComponentStatus]:
        """Get all component statuses as a dictionary."""
        return {
            ComponentType.ACCESS_POINT: self.access_point_status,
            ComponentType.DASHBOARD: self.dashboard_status,
            ComponentType.SERVER: self.server_status
        }
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            'vessel_id': self.vessel_id,
            'access_point_status': self.access_point_status.to_dict(),
            'dashboard_status': self.dashboard_status.to_dict(),
            'server_status': self.server_status.to_dict(),
            'timestamp': self.timestamp.isoformat()
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'VesselMetrics':
        """Create instance from dictionary."""
        data = data.copy()
        data['access_point_status'] = ComponentStatus.from_dict(data['access_point_status'])
        data['dashboard_status'] = ComponentStatus.from_dict(data['dashboard_status'])
        data['server_status'] = ComponentStatus.from_dict(data['server_status'])
        data['timestamp'] = datetime.fromisoformat(data['timestamp'])
        return cls(**data)


@dataclass
class SLAStatus:
    """SLA compliance status for a specific component."""
    
    vessel_id: str
    component_type: ComponentType
    is_compliant: bool
    uptime_percentage: float
    violation_duration: Optional[timedelta] = None
    
    def __post_init__(self):
        """Validate data after initialization."""
        if not self.vessel_id or not self.vessel_id.strip():
            raise ValueError("Vessel ID cannot be empty")
        
        if not 0 <= self.uptime_percentage <= 100:
            raise ValueError("Uptime percentage must be between 0 and 100")
        
        if self.violation_duration and self.violation_duration.total_seconds() < 0:
            raise ValueError("Violation duration cannot be negative")
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        data = asdict(self)
        if self.violation_duration:
            data['violation_duration'] = self.violation_duration.total_seconds()
        return data
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'SLAStatus':
        """Create instance from dictionary."""
        data = data.copy()
        data['component_type'] = ComponentType(data['component_type'])
        if data.get('violation_duration') is not None:
            data['violation_duration'] = timedelta(seconds=data['violation_duration'])
        return cls(**data)


@dataclass
class IssueSummary:
    """Summary of an infrastructure issue for ticket creation."""
    
    vessel_id: str
    component_type: ComponentType
    downtime_duration: timedelta
    historical_context: str
    severity: IssueSeverity
    
    def __post_init__(self):
        """Validate data after initialization."""
        if not self.vessel_id or not self.vessel_id.strip():
            raise ValueError("Vessel ID cannot be empty")
        
        if self.downtime_duration.total_seconds() < 0:
            raise ValueError("Downtime duration cannot be negative")
        
        if not self.historical_context or not self.historical_context.strip():
            raise ValueError("Historical context cannot be empty")
    
    def get_title(self) -> str:
        """Generate a descriptive title for the issue."""
        return f"Vessel {self.vessel_id} - {self.component_type.value.title()} Down for {self._format_duration()}"
    
    def get_description(self) -> str:
        """Generate a detailed description for the issue."""
        return (
            f"Infrastructure Issue Report\n\n"
            f"Vessel ID: {self.vessel_id}\n"
            f"Component: {self.component_type.value.title()}\n"
            f"Downtime Duration: {self._format_duration()}\n"
            f"Severity: {self.severity.value.title()}\n\n"
            f"Historical Context:\n{self.historical_context}"
        )
    
    def _format_duration(self) -> str:
        """Format downtime duration in a human-readable format."""
        total_seconds = int(self.downtime_duration.total_seconds())
        days = total_seconds // 86400
        hours = (total_seconds % 86400) // 3600
        minutes = (total_seconds % 3600) // 60
        
        parts = []
        if days > 0:
            parts.append(f"{days} day{'s' if days != 1 else ''}")
        if hours > 0:
            parts.append(f"{hours} hour{'s' if hours != 1 else ''}")
        if minutes > 0:
            parts.append(f"{minutes} minute{'s' if minutes != 1 else ''}")
        
        if not parts:
            return "less than 1 minute"
        
        return ", ".join(parts)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        data = asdict(self)
        data['downtime_duration'] = self.downtime_duration.total_seconds()
        return data
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'IssueSummary':
        """Create instance from dictionary."""
        data = data.copy()
        data['component_type'] = ComponentType(data['component_type'])
        data['severity'] = IssueSeverity(data['severity'])
        data['downtime_duration'] = timedelta(seconds=data['downtime_duration'])
        return cls(**data)


# Pydantic models for API validation
class ComponentStatusModel(BaseModel):
    """Pydantic model for ComponentStatus validation in APIs."""
    
    component_type: ComponentType
    uptime_percentage: float = Field(ge=0, le=100)
    current_status: OperationalStatus
    downtime_aging_seconds: float = Field(ge=0)
    last_ping_time: datetime
    
    class Config:
        use_enum_values = True


class VesselMetricsModel(BaseModel):
    """Pydantic model for VesselMetrics validation in APIs."""
    
    vessel_id: str = Field(min_length=1)
    access_point_status: ComponentStatusModel
    dashboard_status: ComponentStatusModel
    server_status: ComponentStatusModel
    timestamp: datetime
    
    @validator('access_point_status')
    def validate_access_point_type(cls, v):
        if v.component_type != ComponentType.ACCESS_POINT:
            raise ValueError('access_point_status must have component_type ACCESS_POINT')
        return v
    
    @validator('dashboard_status')
    def validate_dashboard_type(cls, v):
        if v.component_type != ComponentType.DASHBOARD:
            raise ValueError('dashboard_status must have component_type DASHBOARD')
        return v
    
    @validator('server_status')
    def validate_server_type(cls, v):
        if v.component_type != ComponentType.SERVER:
            raise ValueError('server_status must have component_type SERVER')
        return v


class SLAStatusModel(BaseModel):
    """Pydantic model for SLAStatus validation in APIs."""
    
    vessel_id: str = Field(min_length=1)
    component_type: ComponentType
    is_compliant: bool
    uptime_percentage: float = Field(ge=0, le=100)
    violation_duration_seconds: Optional[float] = Field(None, ge=0)
    
    class Config:
        use_enum_values = True


class IssueSummaryModel(BaseModel):
    """Pydantic model for IssueSummary validation in APIs."""
    
    vessel_id: str = Field(min_length=1)
    component_type: ComponentType
    downtime_duration_seconds: float = Field(ge=0)
    historical_context: str = Field(min_length=1)
    severity: IssueSeverity
    
    class Config:
        use_enum_values = True