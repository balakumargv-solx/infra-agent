"""
Configuration settings for the Infrastructure Monitoring Agent.

This module provides backward compatibility with the old Settings class
while integrating with the new configuration system.
"""

import os
from typing import List, Dict
from pydantic import Field
from pydantic_settings import BaseSettings
from dotenv import load_dotenv

from .config_models import Config
from .config_loader import load_config

# Load environment variables from .env file
load_dotenv()


class Settings(BaseSettings):
    """Application settings loaded from environment variables (legacy compatibility)"""
    
    # Web server configuration
    web_host: str = Field(default="0.0.0.0", env="WEB_HOST")
    web_port: int = Field(default=8000, env="WEB_PORT")
    
    # SLA configuration
    sla_threshold: float = Field(default=95.0, env="SLA_THRESHOLD")
    downtime_alert_threshold_days: int = Field(default=3, env="DOWNTIME_ALERT_THRESHOLD_DAYS")
    
    # InfluxDB configuration
    influxdb_url: str = Field(default="http://localhost:8086", env="INFLUXDB_URL")
    influxdb_token: str = Field(default="", env="INFLUXDB_TOKEN")
    influxdb_org: str = Field(default="", env="INFLUXDB_ORG")
    influxdb_timeout: int = Field(default=30, env="INFLUXDB_TIMEOUT")
    
    # JIRA configuration
    jira_url: str = Field(default="", env="JIRA_URL")
    jira_username: str = Field(default="", env="JIRA_USERNAME")
    jira_api_token: str = Field(default="", env="JIRA_API_TOKEN")
    jira_project_key: str = Field(default="INFRA", env="JIRA_PROJECT_KEY")
    
    # Monitoring schedule
    monitoring_schedule_hour: int = Field(default=6, env="MONITORING_SCHEDULE_HOUR")
    monitoring_schedule_minute: int = Field(default=0, env="MONITORING_SCHEDULE_MINUTE")
    
    # Database configuration
    database_path: str = Field(default="./monitoring_agent.db", env="DATABASE_PATH")
    
    # Logging configuration
    log_level: str = Field(default="INFO", env="LOG_LEVEL")
    log_file: str = Field(default="monitoring_agent.log", env="LOG_FILE")
    
    # Vessel configuration
    vessel_ids: str = Field(default="", env="VESSEL_IDS")
    
    @property
    def vessel_databases(self) -> List[str]:
        """Get list of vessel IDs from comma-separated string"""
        if not self.vessel_ids:
            # Default to a few test vessels if not configured
            return ["vessel001", "vessel002", "vessel003"]
        return [vid.strip() for vid in self.vessel_ids.split(",") if vid.strip()]
    
    class Config:
        env_file = ".env"
        case_sensitive = False


# Convenience function to get the new configuration system
def get_config(config_file: str = None) -> Config:
    """Get the new configuration system instance."""
    return load_config(config_file)