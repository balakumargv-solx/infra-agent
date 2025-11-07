"""
Core data models for the Infrastructure Monitoring Agent.

This module defines the main data structures used throughout the system
for vessel metrics, component status, SLA tracking, and issue management.
"""

from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List
from dataclasses import dataclass, asdict
from pydantic import BaseModel, Field, validator
import uuid

from .enums import ComponentType, OperationalStatus, IssueSeverity


@dataclass
class DeviceStatus:
    """Status information for an individual device (IP address)."""
    
    ip_address: str
    uptime_percentage: float
    current_status: OperationalStatus
    downtime_aging: timedelta
    last_ping_time: datetime
    has_data: bool  # True if we have recent data, False if no data available
    ping_count: int  # Number of ping records found
    successful_pings: int  # Number of successful pings
    
    def __post_init__(self):
        """Validate data after initialization."""
        if not 0 <= self.uptime_percentage <= 100:
            raise ValueError("Uptime percentage must be between 0 and 100")
        
        if self.downtime_aging.total_seconds() < 0:
            raise ValueError("Downtime aging cannot be negative")
        
        if self.ping_count < 0 or self.successful_pings < 0:
            raise ValueError("Ping counts cannot be negative")
        
        if self.successful_pings > self.ping_count:
            raise ValueError("Successful pings cannot exceed total ping count")
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            'ip_address': self.ip_address,
            'uptime_percentage': self.uptime_percentage,
            'current_status': self.current_status.value,
            'downtime_aging': self.downtime_aging.total_seconds(),
            'last_ping_time': self.last_ping_time.isoformat(),
            'has_data': self.has_data,
            'ping_count': self.ping_count,
            'successful_pings': self.successful_pings
        }


@dataclass
class ComponentStatus:
    """Status information for a single infrastructure component."""
    
    component_type: ComponentType
    uptime_percentage: float
    current_status: OperationalStatus
    downtime_aging: timedelta
    last_ping_time: datetime
    devices: List[DeviceStatus]  # Individual device statuses
    has_data: bool  # True if component has any data
    
    def __post_init__(self):
        """Validate data after initialization."""
        if not 0 <= self.uptime_percentage <= 100:
            raise ValueError("Uptime percentage must be between 0 and 100")
        
        if self.downtime_aging.total_seconds() < 0:
            raise ValueError("Downtime aging cannot be negative")
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            'component_type': self.component_type.value,
            'uptime_percentage': self.uptime_percentage,
            'current_status': self.current_status.value,
            'downtime_aging': self.downtime_aging.total_seconds(),
            'last_ping_time': self.last_ping_time.isoformat(),
            'devices': [device.to_dict() for device in self.devices],
            'has_data': self.has_data
        }
    
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


@dataclass
class VesselQueryResult:
    """Result of querying a single vessel during scheduler run."""
    
    vessel_id: str
    attempt_number: int
    success: bool
    query_duration: timedelta
    error_message: Optional[str] = None
    timestamp: Optional[datetime] = None
    
    def __post_init__(self):
        """Validate data after initialization."""
        if not self.vessel_id or not self.vessel_id.strip():
            raise ValueError("Vessel ID cannot be empty")
        
        if self.attempt_number < 1:
            raise ValueError("Attempt number must be at least 1")
        
        if self.query_duration.total_seconds() < 0:
            raise ValueError("Query duration cannot be negative")
        
        if self.timestamp is None:
            self.timestamp = datetime.utcnow()
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            'vessel_id': self.vessel_id,
            'attempt_number': self.attempt_number,
            'success': self.success,
            'query_duration_seconds': self.query_duration.total_seconds(),
            'error_message': self.error_message,
            'timestamp': self.timestamp.isoformat() if self.timestamp else None
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'VesselQueryResult':
        """Create instance from dictionary."""
        data = data.copy()
        data['query_duration'] = timedelta(seconds=data['query_duration_seconds'])
        if data.get('timestamp'):
            data['timestamp'] = datetime.fromisoformat(data['timestamp'])
        data.pop('query_duration_seconds', None)
        return cls(**data)


@dataclass
class SchedulerRunLog:
    """Log record for a scheduler run execution."""
    
    run_id: str
    start_time: datetime
    total_vessels: int
    end_time: Optional[datetime] = None
    successful_vessels: int = 0
    failed_vessels: int = 0
    retry_attempts: int = 0
    status: str = 'running'  # 'running', 'completed', 'failed'
    duration: Optional[timedelta] = None
    error_message: Optional[str] = None
    
    def __post_init__(self):
        """Validate data after initialization."""
        if not self.run_id or not self.run_id.strip():
            raise ValueError("Run ID cannot be empty")
        
        if self.total_vessels < 0:
            raise ValueError("Total vessels cannot be negative")
        
        if self.successful_vessels < 0 or self.failed_vessels < 0:
            raise ValueError("Vessel counts cannot be negative")
        
        if self.retry_attempts < 0:
            raise ValueError("Retry attempts cannot be negative")
        
        if self.status not in ['running', 'completed', 'failed']:
            raise ValueError("Status must be 'running', 'completed', or 'failed'")
        
        # Calculate duration if end_time is set
        if self.end_time and not self.duration:
            self.duration = self.end_time - self.start_time
    
    @classmethod
    def create_new(cls, total_vessels: int) -> 'SchedulerRunLog':
        """Create a new scheduler run log with generated ID."""
        return cls(
            run_id=str(uuid.uuid4()),
            start_time=datetime.utcnow(),
            total_vessels=total_vessels
        )
    
    def mark_completed(self, successful_vessels: int, failed_vessels: int, retry_attempts: int = 0) -> None:
        """Mark the run as completed with final counts."""
        self.end_time = datetime.utcnow()
        self.successful_vessels = successful_vessels
        self.failed_vessels = failed_vessels
        self.retry_attempts = retry_attempts
        self.status = 'completed' if failed_vessels == 0 else 'failed'
        self.duration = self.end_time - self.start_time
    
    def mark_failed(self, error_message: str) -> None:
        """Mark the run as failed with error message."""
        self.end_time = datetime.utcnow()
        self.status = 'failed'
        self.error_message = error_message
        self.duration = self.end_time - self.start_time
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            'run_id': self.run_id,
            'start_time': self.start_time.isoformat(),
            'end_time': self.end_time.isoformat() if self.end_time else None,
            'total_vessels': self.total_vessels,
            'successful_vessels': self.successful_vessels,
            'failed_vessels': self.failed_vessels,
            'retry_attempts': self.retry_attempts,
            'status': self.status,
            'duration_seconds': self.duration.total_seconds() if self.duration else None,
            'error_message': self.error_message
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'SchedulerRunLog':
        """Create instance from dictionary."""
        data = data.copy()
        data['start_time'] = datetime.fromisoformat(data['start_time'])
        if data.get('end_time'):
            data['end_time'] = datetime.fromisoformat(data['end_time'])
        if data.get('duration_seconds') is not None:
            data['duration'] = timedelta(seconds=data['duration_seconds'])
        data.pop('duration_seconds', None)
        return cls(**data)


@dataclass
class SchedulerRunDetails:
    """Detailed information about a scheduler run including vessel results."""
    
    run_summary: SchedulerRunLog
    vessel_results: List[VesselQueryResult]
    retry_summary: Dict[str, int]  # vessel_id -> retry_count
    
    def __post_init__(self):
        """Validate data after initialization."""
        if not isinstance(self.vessel_results, list):
            raise ValueError("Vessel results must be a list")
        
        if not isinstance(self.retry_summary, dict):
            raise ValueError("Retry summary must be a dictionary")
    
    def get_vessel_result_by_id(self, vessel_id: str) -> List[VesselQueryResult]:
        """Get all query results for a specific vessel."""
        return [result for result in self.vessel_results if result.vessel_id == vessel_id]
    
    def get_failed_vessels(self) -> List[str]:
        """Get list of vessel IDs that failed all attempts."""
        failed_vessels = set()
        successful_vessels = set()
        
        for result in self.vessel_results:
            if result.success:
                successful_vessels.add(result.vessel_id)
            else:
                failed_vessels.add(result.vessel_id)
        
        # Return vessels that failed and never succeeded
        return list(failed_vessels - successful_vessels)
    
    def get_retry_statistics(self) -> Dict[str, Any]:
        """Get statistics about retry attempts."""
        total_retries = sum(self.retry_summary.values())
        vessels_with_retries = len([count for count in self.retry_summary.values() if count > 0])
        
        return {
            'total_retry_attempts': total_retries,
            'vessels_requiring_retries': vessels_with_retries,
            'average_retries_per_vessel': total_retries / len(self.retry_summary) if self.retry_summary else 0,
            'max_retries_for_vessel': max(self.retry_summary.values()) if self.retry_summary else 0
        }
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            'run_summary': self.run_summary.to_dict(),
            'vessel_results': [result.to_dict() for result in self.vessel_results],
            'retry_summary': self.retry_summary
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'SchedulerRunDetails':
        """Create instance from dictionary."""
        data = data.copy()
        data['run_summary'] = SchedulerRunLog.from_dict(data['run_summary'])
        data['vessel_results'] = [VesselQueryResult.from_dict(result) for result in data['vessel_results']]
        return cls(**data)


# Pydantic models for API validation
class VesselQueryResultModel(BaseModel):
    """Pydantic model for VesselQueryResult validation in APIs."""
    
    vessel_id: str = Field(min_length=1)
    attempt_number: int = Field(ge=1)
    success: bool
    query_duration_seconds: float = Field(ge=0)
    error_message: Optional[str] = None
    timestamp: Optional[datetime] = None


class SchedulerRunLogModel(BaseModel):
    """Pydantic model for SchedulerRunLog validation in APIs."""
    
    run_id: str = Field(min_length=1)
    start_time: datetime
    end_time: Optional[datetime] = None
    total_vessels: int = Field(ge=0)
    successful_vessels: int = Field(ge=0)
    failed_vessels: int = Field(ge=0)
    retry_attempts: int = Field(ge=0)
    status: str = Field(pattern='^(running|completed|failed)$')
    duration_seconds: Optional[float] = Field(None, ge=0)
    error_message: Optional[str] = None
    
    @validator('end_time')
    def validate_end_time(cls, v, values):
        if v and 'start_time' in values and v < values['start_time']:
            raise ValueError('end_time must be after start_time')
        return v


class SchedulerRunDetailsModel(BaseModel):
    """Pydantic model for SchedulerRunDetails validation in APIs."""
    
    run_summary: SchedulerRunLogModel
    vessel_results: List[VesselQueryResultModel]
    retry_summary: Dict[str, int]