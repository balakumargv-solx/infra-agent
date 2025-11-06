"""
Configuration data models for the Infrastructure Monitoring Agent.

This module defines the configuration structures for vessel database connections,
SLA parameters, and system settings with comprehensive validation.
"""

import os
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse
import json


@dataclass
class InfluxDBConnection:
    """Configuration for a single InfluxDB connection."""
    
    url: str
    token: str
    org: str
    bucket: str
    timeout: int = 30
    
    def __post_init__(self):
        """Validate InfluxDB connection parameters."""
        if not self.url or not self.url.strip():
            raise ValueError("InfluxDB URL cannot be empty")
        
        # Validate URL format
        parsed = urlparse(self.url)
        if not parsed.scheme or not parsed.netloc:
            raise ValueError(f"Invalid InfluxDB URL format: {self.url}")
        
        if not self.token or not self.token.strip():
            raise ValueError("InfluxDB token cannot be empty")
        
        if not self.org or not self.org.strip():
            raise ValueError("InfluxDB organization cannot be empty")
        
        if not self.bucket or not self.bucket.strip():
            raise ValueError("InfluxDB bucket cannot be empty")
        
        if self.timeout <= 0:
            raise ValueError("InfluxDB timeout must be positive")
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            'url': self.url,
            'token': self.token,
            'org': self.org,
            'bucket': self.bucket,
            'timeout': self.timeout
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'InfluxDBConnection':
        """Create instance from dictionary."""
        return cls(**data)


@dataclass
class JIRAConnection:
    """Configuration for JIRA integration."""
    
    url: str
    username: str
    api_token: str
    project_key: str
    issue_type: str = "Bug"
    
    def __post_init__(self):
        """Validate JIRA connection parameters."""
        if not self.url or not self.url.strip():
            raise ValueError("JIRA URL cannot be empty")
        
        # Validate URL format
        parsed = urlparse(self.url)
        if not parsed.scheme or not parsed.netloc:
            raise ValueError(f"Invalid JIRA URL format: {self.url}")
        
        if not self.username or not self.username.strip():
            raise ValueError("JIRA username cannot be empty")
        
        if not self.api_token or not self.api_token.strip():
            raise ValueError("JIRA API token cannot be empty")
        
        if not self.project_key or not self.project_key.strip():
            raise ValueError("JIRA project key cannot be empty")
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            'url': self.url,
            'username': self.username,
            'api_token': self.api_token,
            'project_key': self.project_key,
            'issue_type': self.issue_type
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'JIRAConnection':
        """Create instance from dictionary."""
        return cls(**data)


@dataclass
class SLAParameters:
    """SLA monitoring parameters and thresholds."""
    
    uptime_threshold_percentage: float = 95.0
    downtime_alert_threshold_days: int = 3
    monitoring_window_hours: int = 24
    
    def __post_init__(self):
        """Validate SLA parameters."""
        if not 0 < self.uptime_threshold_percentage <= 100:
            raise ValueError("SLA uptime threshold must be between 0 and 100")
        
        if self.downtime_alert_threshold_days <= 0:
            raise ValueError("Downtime alert threshold must be positive")
        
        if self.monitoring_window_hours <= 0:
            raise ValueError("Monitoring window must be positive")
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            'uptime_threshold_percentage': self.uptime_threshold_percentage,
            'downtime_alert_threshold_days': self.downtime_alert_threshold_days,
            'monitoring_window_hours': self.monitoring_window_hours
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'SLAParameters':
        """Create instance from dictionary."""
        return cls(**data)


@dataclass
class WebServerConfig:
    """Web server configuration."""
    
    host: str = "0.0.0.0"
    port: int = 8000
    debug: bool = False
    
    def __post_init__(self):
        """Validate web server configuration."""
        if not self.host or not self.host.strip():
            raise ValueError("Web server host cannot be empty")
        
        if not 1 <= self.port <= 65535:
            raise ValueError("Web server port must be between 1 and 65535")
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            'host': self.host,
            'port': self.port,
            'debug': self.debug
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'SLAParameters':
        """Create instance from dictionary."""
        return cls(**data)


@dataclass
class SchedulingConfig:
    """Monitoring schedule configuration."""
    
    daily_monitoring_hour: int = 6
    daily_monitoring_minute: int = 0
    timezone: str = "UTC"
    
    def __post_init__(self):
        """Validate scheduling configuration."""
        if not 0 <= self.daily_monitoring_hour <= 23:
            raise ValueError("Daily monitoring hour must be between 0 and 23")
        
        if not 0 <= self.daily_monitoring_minute <= 59:
            raise ValueError("Daily monitoring minute must be between 0 and 59")
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            'daily_monitoring_hour': self.daily_monitoring_hour,
            'daily_monitoring_minute': self.daily_monitoring_minute,
            'timezone': self.timezone
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'SchedulingConfig':
        """Create instance from dictionary."""
        return cls(**data)


@dataclass
class SlackConfig:
    """Slack integration configuration."""
    
    webhook_url: str
    signing_secret: Optional[str] = None
    channel: str = "#infrastructure-alerts"
    username: str = "Infrastructure Monitor"
    icon_emoji: str = ":warning:"
    webhook_port: int = 5000
    
    def __post_init__(self):
        """Validate Slack configuration."""
        if not self.webhook_url or not self.webhook_url.strip():
            raise ValueError("Slack webhook URL cannot be empty")
        
        if not 1 <= self.webhook_port <= 65535:
            raise ValueError("Slack webhook port must be between 1 and 65535")
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            'webhook_url': self.webhook_url,
            'signing_secret': self.signing_secret,
            'channel': self.channel,
            'username': self.username,
            'icon_emoji': self.icon_emoji,
            'webhook_port': self.webhook_port
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'SlackConfig':
        """Create instance from dictionary."""
        return cls(**data)


@dataclass
class Config:
    """Main configuration class for the Infrastructure Monitoring Agent."""
    
    # Vessel database connections
    vessel_databases: Dict[str, InfluxDBConnection] = field(default_factory=dict)
    
    # External service connections
    jira_connection: Optional[JIRAConnection] = None
    slack_config: Optional[SlackConfig] = None
    
    # SLA monitoring parameters
    sla_parameters: SLAParameters = field(default_factory=SLAParameters)
    
    # Web server configuration
    web_server: WebServerConfig = field(default_factory=WebServerConfig)
    
    # Scheduling configuration
    scheduling: SchedulingConfig = field(default_factory=SchedulingConfig)
    
    # Database and logging
    database_path: str = "./monitoring_agent.db"
    log_level: str = "INFO"
    log_file: str = "monitoring_agent.log"
    
    def __post_init__(self):
        """Validate configuration after initialization."""
        if not self.vessel_databases:
            raise ValueError("At least one vessel database must be configured")
        
        # Validate database path
        if not self.database_path or not self.database_path.strip():
            raise ValueError("Database path cannot be empty")
        
        # Validate log level
        valid_log_levels = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
        if self.log_level.upper() not in valid_log_levels:
            raise ValueError(f"Log level must be one of: {valid_log_levels}")
    
    def get_vessel_ids(self) -> List[str]:
        """Get list of configured vessel IDs."""
        return list(self.vessel_databases.keys())
    
    def get_vessel_connection(self, vessel_id: str) -> InfluxDBConnection:
        """Get InfluxDB connection for a specific vessel."""
        if vessel_id not in self.vessel_databases:
            raise ValueError(f"No database configuration found for vessel: {vessel_id}")
        return self.vessel_databases[vessel_id]
    
    def add_vessel_database(self, vessel_id: str, connection: InfluxDBConnection) -> None:
        """Add a vessel database configuration."""
        if not vessel_id or not vessel_id.strip():
            raise ValueError("Vessel ID cannot be empty")
        self.vessel_databases[vessel_id] = connection
    
    def remove_vessel_database(self, vessel_id: str) -> None:
        """Remove a vessel database configuration."""
        if vessel_id in self.vessel_databases:
            del self.vessel_databases[vessel_id]
    
    def validate_connections(self) -> Dict[str, bool]:
        """Validate all configured connections (basic validation)."""
        results = {}
        
        # Validate vessel database connections
        for vessel_id, connection in self.vessel_databases.items():
            try:
                # Basic validation - just check if parameters are set
                connection.__post_init__()
                results[f"vessel_{vessel_id}"] = True
            except Exception as e:
                results[f"vessel_{vessel_id}"] = False
        
        # Validate JIRA connection
        if self.jira_connection:
            try:
                self.jira_connection.__post_init__()
                results["jira"] = True
            except Exception as e:
                results["jira"] = False
        
        # Validate Slack connection
        if self.slack_config:
            try:
                self.slack_config.__post_init__()
                results["slack"] = True
            except Exception as e:
                results["slack"] = False
        
        return results
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            'vessel_databases': {
                vessel_id: conn.to_dict() 
                for vessel_id, conn in self.vessel_databases.items()
            },
            'jira_connection': self.jira_connection.to_dict() if self.jira_connection else None,
            'slack_config': self.slack_config.to_dict() if self.slack_config else None,
            'sla_parameters': self.sla_parameters.to_dict(),
            'web_server': self.web_server.to_dict(),
            'scheduling': self.scheduling.to_dict(),
            'database_path': self.database_path,
            'log_level': self.log_level,
            'log_file': self.log_file
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'Config':
        """Create instance from dictionary."""
        data = data.copy()
        
        # Convert vessel databases
        vessel_databases = {}
        for vessel_id, conn_data in data.get('vessel_databases', {}).items():
            vessel_databases[vessel_id] = InfluxDBConnection.from_dict(conn_data)
        data['vessel_databases'] = vessel_databases
        
        # Convert JIRA connection
        if data.get('jira_connection'):
            data['jira_connection'] = JIRAConnection.from_dict(data['jira_connection'])
        
        # Convert Slack config
        if data.get('slack_config'):
            data['slack_config'] = SlackConfig.from_dict(data['slack_config'])
        
        # Convert SLA parameters
        if data.get('sla_parameters'):
            data['sla_parameters'] = SLAParameters.from_dict(data['sla_parameters'])
        
        # Convert web server config
        if data.get('web_server'):
            data['web_server'] = WebServerConfig.from_dict(data['web_server'])
        
        # Convert scheduling config
        if data.get('scheduling'):
            data['scheduling'] = SchedulingConfig.from_dict(data['scheduling'])
        
        return cls(**data)
    
    def save_to_file(self, file_path: str) -> None:
        """Save configuration to JSON file."""
        config_data = self.to_dict()
        with open(file_path, 'w') as f:
            json.dump(config_data, f, indent=2)
    
    @classmethod
    def load_from_file(cls, file_path: str) -> 'Config':
        """Load configuration from JSON file."""
        with open(file_path, 'r') as f:
            data = json.load(f)
        return cls.from_dict(data)