"""
Configuration loader for the Infrastructure Monitoring Agent.

This module provides functionality to load configuration from environment variables,
config files, and provide default values with comprehensive validation.
"""

import os
import json
from typing import Dict, List, Optional, Any
from pathlib import Path
from dotenv import load_dotenv

from .config_models import (
    Config,
    InfluxDBConnection,
    JIRAConnection,
    SLAParameters,
    WebServerConfig,
    SchedulingConfig
)


class ConfigLoader:
    """Loads and validates configuration from multiple sources."""
    
    def __init__(self, env_file: Optional[str] = None):
        """Initialize the config loader.
        
        Args:
            env_file: Path to .env file to load. If None, looks for .env in current directory.
        """
        self.env_file = env_file or ".env"
        if Path(self.env_file).exists():
            load_dotenv(self.env_file)
    
    def load_config(self, config_file: Optional[str] = None) -> Config:
        """Load configuration from environment variables and optional config file.
        
        Args:
            config_file: Path to JSON config file. If provided, values from this file
                        will override environment variables.
        
        Returns:
            Validated Config instance.
        
        Raises:
            ValueError: If configuration validation fails.
            FileNotFoundError: If specified config file doesn't exist.
        """
        # Start with environment-based configuration
        config_data = self._load_from_environment()
        
        # Override with config file if provided
        if config_file and Path(config_file).exists():
            file_config = self._load_from_file(config_file)
            config_data = self._merge_configs(config_data, file_config)
        
        # Create and validate config
        return Config.from_dict(config_data)
    
    def _load_from_environment(self) -> Dict[str, Any]:
        """Load configuration from environment variables."""
        # SLA Parameters
        sla_params = SLAParameters(
            uptime_threshold_percentage=float(os.getenv("SLA_THRESHOLD", "95.0")),
            downtime_alert_threshold_days=int(os.getenv("DOWNTIME_ALERT_THRESHOLD_DAYS", "3")),
            monitoring_window_hours=int(os.getenv("MONITORING_WINDOW_HOURS", "24"))
        )
        
        # Web Server Config
        web_server = WebServerConfig(
            host=os.getenv("WEB_HOST", "0.0.0.0"),
            port=int(os.getenv("WEB_PORT", "8000")),
            debug=os.getenv("WEB_DEBUG", "false").lower() == "true"
        )
        
        # Scheduling Config
        scheduling = SchedulingConfig(
            daily_monitoring_hour=int(os.getenv("MONITORING_SCHEDULE_HOUR", "6")),
            daily_monitoring_minute=int(os.getenv("MONITORING_SCHEDULE_MINUTE", "0")),
            timezone=os.getenv("MONITORING_TIMEZONE", "UTC")
        )
        
        # JIRA Connection (optional)
        jira_connection = None
        jira_url = os.getenv("JIRA_URL")
        if jira_url:
            jira_connection = JIRAConnection(
                url=jira_url,
                username=os.getenv("JIRA_USERNAME", ""),
                api_token=os.getenv("JIRA_API_TOKEN", ""),
                project_key=os.getenv("JIRA_PROJECT_KEY", "INFRA"),
                issue_type=os.getenv("JIRA_ISSUE_TYPE", "Bug")
            )
        
        # Vessel Databases
        vessel_databases = self._load_vessel_databases_from_env()
        
        return {
            'vessel_databases': vessel_databases,
            'jira_connection': jira_connection.to_dict() if jira_connection else None,
            'sla_parameters': sla_params.to_dict(),
            'web_server': web_server.to_dict(),
            'scheduling': scheduling.to_dict(),
            'database_path': os.getenv("DATABASE_PATH", "./monitoring_agent.db"),
            'log_level': os.getenv("LOG_LEVEL", "INFO"),
            'log_file': os.getenv("LOG_FILE", "monitoring_agent.log")
        }
    
    def _load_vessel_databases_from_env(self) -> Dict[str, Dict[str, Any]]:
        """Load vessel database configurations from environment variables.
        
        Supports two formats:
        1. Single InfluxDB for all vessels: INFLUXDB_URL, INFLUXDB_TOKEN, etc.
        2. Per-vessel configuration: VESSEL_001_INFLUXDB_URL, VESSEL_001_INFLUXDB_TOKEN, etc.
        """
        vessel_databases = {}
        
        # Check for vessel-specific configurations first
        vessel_configs = self._find_vessel_specific_configs()
        if vessel_configs:
            return vessel_configs
        
        # Fall back to single InfluxDB configuration for all vessels
        influxdb_url = os.getenv("INFLUXDB_URL")
        if influxdb_url:
            # Get vessel IDs from environment
            vessel_ids_str = os.getenv("VESSEL_IDS", "")
            if vessel_ids_str:
                vessel_ids = [vid.strip() for vid in vessel_ids_str.split(",") if vid.strip()]
            else:
                # Default test vessels if none specified
                vessel_ids = [f"vessel{i:03d}" for i in range(1, 67)]  # vessel001 to vessel066
            
            # Create same connection config for all vessels
            base_connection = {
                'url': influxdb_url,
                'token': os.getenv("INFLUXDB_TOKEN", ""),
                'org': os.getenv("INFLUXDB_ORG", ""),
                'bucket': os.getenv("INFLUXDB_BUCKET", "monitoring"),
                'timeout': int(os.getenv("INFLUXDB_TIMEOUT", "30"))
            }
            
            for vessel_id in vessel_ids:
                # Each vessel gets its own bucket (vessel_id + base bucket name)
                vessel_connection = base_connection.copy()
                vessel_connection['bucket'] = f"{vessel_id}_{base_connection['bucket']}"
                vessel_databases[vessel_id] = vessel_connection
        
        return vessel_databases
    
    def _find_vessel_specific_configs(self) -> Dict[str, Dict[str, Any]]:
        """Find vessel-specific InfluxDB configurations from environment variables.
        
        Looks for patterns like:
        VESSEL_001_INFLUXDB_URL, VESSEL_001_INFLUXDB_TOKEN, etc.
        """
        vessel_databases = {}
        
        # Find all vessel-specific environment variables
        vessel_prefixes = set()
        for key in os.environ:
            if key.startswith("VESSEL_") and "_INFLUXDB_URL" in key:
                prefix = key.replace("_INFLUXDB_URL", "")
                vessel_id = prefix.replace("VESSEL_", "").lower()
                vessel_prefixes.add((vessel_id, prefix))
        
        # Build configuration for each vessel
        for vessel_id, prefix in vessel_prefixes:
            url = os.getenv(f"{prefix}_INFLUXDB_URL")
            if url:
                vessel_databases[vessel_id] = {
                    'url': url,
                    'token': os.getenv(f"{prefix}_INFLUXDB_TOKEN", ""),
                    'org': os.getenv(f"{prefix}_INFLUXDB_ORG", ""),
                    'bucket': os.getenv(f"{prefix}_INFLUXDB_BUCKET", "monitoring"),
                    'timeout': int(os.getenv(f"{prefix}_INFLUXDB_TIMEOUT", "30"))
                }
        
        return vessel_databases
    
    def _load_from_file(self, config_file: str) -> Dict[str, Any]:
        """Load configuration from JSON file."""
        with open(config_file, 'r') as f:
            return json.load(f)
    
    def _merge_configs(self, base_config: Dict[str, Any], override_config: Dict[str, Any]) -> Dict[str, Any]:
        """Merge two configuration dictionaries, with override_config taking precedence."""
        merged = base_config.copy()
        
        for key, value in override_config.items():
            if key in merged and isinstance(merged[key], dict) and isinstance(value, dict):
                merged[key] = self._merge_configs(merged[key], value)
            else:
                merged[key] = value
        
        return merged
    
    def create_sample_config_file(self, file_path: str) -> None:
        """Create a sample configuration file with default values."""
        sample_config = {
            "vessel_databases": {
                "vessel001": {
                    "url": "http://vessel001-influxdb:8086",
                    "token": "your-influxdb-token-here",
                    "org": "fleet-monitoring",
                    "bucket": "vessel001_monitoring",
                    "timeout": 30
                },
                "vessel002": {
                    "url": "http://vessel002-influxdb:8086",
                    "token": "your-influxdb-token-here",
                    "org": "fleet-monitoring",
                    "bucket": "vessel002_monitoring",
                    "timeout": 30
                }
            },
            "jira_connection": {
                "url": "https://your-company.atlassian.net",
                "username": "monitoring-agent@company.com",
                "api_token": "your-jira-api-token-here",
                "project_key": "INFRA",
                "issue_type": "Bug"
            },
            "sla_parameters": {
                "uptime_threshold_percentage": 95.0,
                "downtime_alert_threshold_days": 3,
                "monitoring_window_hours": 24
            },
            "web_server": {
                "host": "0.0.0.0",
                "port": 8000,
                "debug": False
            },
            "scheduling": {
                "daily_monitoring_hour": 6,
                "daily_monitoring_minute": 0,
                "timezone": "UTC"
            },
            "database_path": "./monitoring_agent.db",
            "log_level": "INFO",
            "log_file": "monitoring_agent.log"
        }
        
        with open(file_path, 'w') as f:
            json.dump(sample_config, f, indent=2)


def load_config(config_file: Optional[str] = None, env_file: Optional[str] = None) -> Config:
    """Convenience function to load configuration.
    
    Args:
        config_file: Path to JSON config file (optional).
        env_file: Path to .env file (optional, defaults to .env).
    
    Returns:
        Validated Config instance.
    """
    loader = ConfigLoader(env_file)
    return loader.load_config(config_file)


def create_sample_config(file_path: str = "config.json") -> None:
    """Convenience function to create a sample configuration file.
    
    Args:
        file_path: Path where to create the sample config file.
    """
    loader = ConfigLoader()
    loader.create_sample_config_file(file_path)