"""
InfluxDB client wrapper for the Infrastructure Monitoring Agent.

This module provides a robust InfluxDB client with connection pooling,
authentication, retry logic, and methods for retrieving ping status data
from vessel databases. Works with InfluxDB 1.8.x using InfluxQL.
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass
import time
import random
import requests
from urllib.parse import urlencode

from ..config.config_models import InfluxDBConnection
from ..models.enums import ComponentType, OperationalStatus


logger = logging.getLogger(__name__)


@dataclass
class DevicePingData:
    """Raw ping data for a single device/IP address."""
    
    ip_address: str
    timestamps: List[datetime]
    ping_success: List[bool]
    
    def get_uptime_percentage(self, window_hours: int = 24) -> float:
        """Calculate uptime percentage for the given time window."""
        if not self.ping_success:
            return 0.0
        
        # Filter data to the specified time window
        cutoff_time = datetime.now(timezone.utc) - timedelta(hours=window_hours)
        filtered_data = [
            success for timestamp, success in zip(self.timestamps, self.ping_success)
            if timestamp >= cutoff_time
        ]
        
        if not filtered_data:
            return 0.0
        
        successful_pings = sum(filtered_data)
        total_pings = len(filtered_data)
        
        return (successful_pings / total_pings) * 100.0
    
    def get_current_status(self) -> OperationalStatus:
        """Determine current operational status based on most recent ping."""
        if not self.ping_success or not self.timestamps:
            return OperationalStatus.UNKNOWN
        
        # Get the most recent ping result
        latest_success = self.ping_success[-1]
        return OperationalStatus.UP if latest_success else OperationalStatus.DOWN
    
    def calculate_downtime_aging(self) -> timedelta:
        """Calculate how long the device has been down."""
        if not self.timestamps or not self.ping_success:
            return timedelta(0)
        
        # Find the last successful ping
        for i in range(len(self.ping_success) - 1, -1, -1):
            if self.ping_success[i]:
                # Calculate time since last successful ping
                return datetime.now(timezone.utc) - self.timestamps[i]
        
        # If no successful pings found, return time since first ping
        return datetime.now(timezone.utc) - self.timestamps[0]
    
    def get_last_ping_time(self) -> Optional[datetime]:
        """Get the timestamp of the most recent ping."""
        return self.timestamps[-1] if self.timestamps else None
    
    def has_recent_data(self, hours: int = 2) -> bool:
        """Check if we have data within the specified hours."""
        if not self.timestamps:
            return False
        
        cutoff_time = datetime.now(timezone.utc) - timedelta(hours=hours)
        return self.timestamps[-1] >= cutoff_time


@dataclass
class PingData:
    """Raw ping data retrieved from InfluxDB with per-device tracking."""
    
    component_type: ComponentType
    devices: List[DevicePingData]  # Data for individual devices
    vessel_id: str
    
    def get_uptime_percentage(self, window_hours: int = 24) -> float:
        """Calculate average uptime percentage across all devices."""
        if not self.devices:
            return 0.0
        
        # Calculate average uptime across all devices
        total_uptime = 0.0
        device_count = 0
        
        for device in self.devices:
            device_uptime = device.get_uptime_percentage(window_hours)
            total_uptime += device_uptime
            device_count += 1
        
        if device_count == 0:
            return 0.0
        
        return total_uptime / device_count
    
    def get_current_status(self) -> OperationalStatus:
        """Determine current operational status based on device statuses."""
        if not self.devices:
            return OperationalStatus.UNKNOWN
        
        # Component is UP if at least 50% of devices are UP
        up_devices = sum(1 for device in self.devices if device.get_current_status() == OperationalStatus.UP)
        total_devices = len(self.devices)
        
        if up_devices == 0:
            return OperationalStatus.DOWN
        elif up_devices >= total_devices * 0.5:
            return OperationalStatus.UP
        else:
            return OperationalStatus.DOWN
    
    def get_last_ping_time(self) -> Optional[datetime]:
        """Get the timestamp of the most recent ping across all devices."""
        if not self.devices:
            return None
        
        latest_time = None
        for device in self.devices:
            if device.timestamps:
                device_latest = max(device.timestamps)
                if latest_time is None or device_latest > latest_time:
                    latest_time = device_latest
        
        return latest_time
    
    def calculate_downtime_aging(self) -> timedelta:
        """Calculate the maximum downtime aging among all devices."""
        if not self.devices:
            return timedelta(0)
        
        # Return the maximum downtime aging among all devices
        max_downtime = timedelta(0)
        for device in self.devices:
            device_downtime = device.calculate_downtime_aging()
            if device_downtime > max_downtime:
                max_downtime = device_downtime
        
        return max_downtime


class InfluxDBClientWrapper:
    """
    Wrapper for InfluxDB 1.8 client with retry logic and specialized methods 
    for vessel infrastructure monitoring using InfluxQL.
    """
    
    def __init__(self, connection: InfluxDBConnection, vessel_id: str, max_retries: int = 3):
        """
        Initialize the InfluxDB client wrapper.
        
        Args:
            connection: InfluxDB connection configuration
            vessel_id: ID of the vessel (used as database name)
            max_retries: Maximum number of retry attempts for failed operations
        """
        self.connection = connection
        self.vessel_id = vessel_id
        self.database_name = vessel_id  # In InfluxDB 1.8, each vessel is a database
        self.max_retries = max_retries
        
        # Retry configuration
        self.base_delay = 1.0  # Base delay in seconds
        self.max_delay = 60.0  # Maximum delay in seconds
        self.backoff_factor = 2.0  # Exponential backoff factor
        
        # Component type to IP mapping (this should be configurable per vessel)
        self.component_ip_mapping = self._get_default_component_mapping()
        
        logger.info(f"Initialized InfluxDB 1.8 client wrapper for vessel {vessel_id} at {connection.url}")
    
    def _get_default_component_mapping(self) -> Dict[ComponentType, List[str]]:
        """
        Get default IP address mapping for component types based on actual vessel configuration.
        """
        return {
            ComponentType.SERVER: [
                "8.8.8.8"  # External connectivity test (represents server connectivity)
            ],
            ComponentType.DASHBOARD: [
                "192.168.1.43",  # Dashboard server 1
                "192.168.1.44",  # Dashboard server 2  
                "192.168.1.45"   # Dashboard server 3
            ],
            ComponentType.ACCESS_POINT: [
                # All other 192.168.1.x IPs are access points
                "192.168.1.1", "192.168.1.2", "192.168.1.3", "192.168.1.4", "192.168.1.5",
                "192.168.1.6", "192.168.1.7", "192.168.1.8", "192.168.1.9", "192.168.1.10",
                "192.168.1.11", "192.168.1.12", "192.168.1.13", "192.168.1.22", "192.168.1.23",
                "192.168.1.24"
            ]
        }
    
    def set_component_ip_mapping(self, mapping: Dict[ComponentType, List[str]]):
        """Set custom IP mapping for component types."""
        self.component_ip_mapping = mapping
    
    def _calculate_delay(self, attempt: int) -> float:
        """Calculate exponential backoff delay with jitter."""
        delay = min(
            self.base_delay * (self.backoff_factor ** attempt),
            self.max_delay
        )
        # Add jitter to prevent thundering herd
        jitter = random.uniform(0.1, 0.3) * delay
        return delay + jitter
    
    async def _retry_operation(self, operation, *args, **kwargs):
        """
        Execute an operation with exponential backoff retry logic.
        
        Args:
            operation: The operation to retry
            *args, **kwargs: Arguments to pass to the operation
            
        Returns:
            Result of the operation
            
        Raises:
            Exception: If all retry attempts fail
        """
        last_exception = None
        
        for attempt in range(self.max_retries + 1):
            try:
                return await operation(*args, **kwargs)
            except requests.RequestException as e:
                last_exception = e
                
                if attempt == self.max_retries:
                    logger.error(
                        f"Operation failed after {self.max_retries + 1} attempts: {e}"
                    )
                    break
                
                delay = self._calculate_delay(attempt)
                logger.warning(
                    f"Operation failed (attempt {attempt + 1}/{self.max_retries + 1}), "
                    f"retrying in {delay:.2f}s: {e}"
                )
                await asyncio.sleep(delay)
            except Exception as e:
                # Non-HTTP errors are not retried
                logger.error(f"Non-retryable error in operation: {e}")
                raise
        
        raise last_exception
    
    async def _execute_query_http(self, query: str) -> Dict[str, Any]:
        """
        Execute an InfluxQL query against the vessel database.
        
        Args:
            query: InfluxQL query string
            
        Returns:
            Query result as dictionary
            
        Raises:
            requests.RequestException: If query fails
        """
        import httpx
        
        url = f"{self.connection.url}/query"
        headers = {
            'Authorization': f'Token {self.connection.token}',
            'Content-Type': 'application/x-www-form-urlencoded'
        }
        
        params = {
            'db': self.database_name,
            'q': query
        }
        
        logger.debug(f"Executing query on {self.database_name}: {query}")
        
        async with httpx.AsyncClient(timeout=self.connection.timeout) as client:
            response = await client.get(url, headers=headers, params=params)
            
            if response.status_code != 200:
                logger.error(f"Query failed with status {response.status_code}: {response.text}")
                raise requests.RequestException(
                    f"Query failed with status {response.status_code}: {response.text}"
                )
            
            return response.json()
    
    async def query_ping_status(
        self,
        component_type: ComponentType,
        hours_back: int = 24
    ) -> PingData:
        """
        Query ping status data for a specific component type.
        
        Args:
            component_type: Type of component to query
            hours_back: Number of hours of historical data to retrieve
            
        Returns:
            PingData containing timestamps and ping success status
            
        Raises:
            Exception: If query fails after all retries
        """
        
        async def _execute_query():
            # Get IP addresses for this component type
            ip_addresses = self.component_ip_mapping.get(component_type, [])
            
            if not ip_addresses:
                logger.warning(f"No IP addresses configured for component type {component_type.value}")
                return PingData(
                    component_type=component_type,
                    devices=[],
                    vessel_id=self.vessel_id
                )
            
            # Build InfluxQL query for the component's IP addresses
            ip_conditions = " OR ".join([f"url = '{ip}'" for ip in ip_addresses])
            
            query = f'''
            SELECT time, url, result_code, percent_packet_loss 
            FROM ping 
            WHERE time > now() - {hours_back}h 
            AND ({ip_conditions})
            ORDER BY time ASC
            '''
            
            logger.debug(f"Executing query for {component_type.value}: {query}")
            
            # Execute query
            result = await self._execute_query_http(query)
            
            # Group data by IP address
            device_data = {}
            
            if 'results' in result and result['results']:
                if 'series' in result['results'][0]:
                    for series in result['results'][0]['series']:
                        columns = series.get('columns', [])
                        values = series.get('values', [])
                        
                        for value_row in values:
                            record = dict(zip(columns, value_row))
                            
                            # Parse timestamp
                            timestamp_str = record.get('time')
                            ip_address = record.get('url')
                            
                            if timestamp_str and ip_address:
                                timestamp = datetime.fromisoformat(timestamp_str.replace('Z', '+00:00'))
                                
                                # Determine success: result_code == 0 and packet_loss < 100%
                                result_code = record.get('result_code', 1)
                                packet_loss = record.get('percent_packet_loss', 100)
                                success = (result_code == 0) and (packet_loss < 100)
                                
                                # Group by IP address
                                if ip_address not in device_data:
                                    device_data[ip_address] = {
                                        'timestamps': [],
                                        'ping_success': []
                                    }
                                
                                device_data[ip_address]['timestamps'].append(timestamp)
                                device_data[ip_address]['ping_success'].append(success)
            
            # Create DevicePingData objects for each IP
            devices = []
            for ip_address in ip_addresses:  # Include all configured IPs, even if no data
                if ip_address in device_data:
                    device_ping_data = DevicePingData(
                        ip_address=ip_address,
                        timestamps=device_data[ip_address]['timestamps'],
                        ping_success=device_data[ip_address]['ping_success']
                    )
                else:
                    # No data for this IP - create empty device data
                    device_ping_data = DevicePingData(
                        ip_address=ip_address,
                        timestamps=[],
                        ping_success=[]
                    )
                
                devices.append(device_ping_data)
            
            total_records = sum(len(device.timestamps) for device in devices)
            logger.info(
                f"Retrieved {total_records} ping records for "
                f"{self.vessel_id}/{component_type.value} across {len(devices)} devices"
            )
            
            return PingData(
                component_type=component_type,
                devices=devices,
                vessel_id=self.vessel_id
            )
        
        return await self._retry_operation(_execute_query)
    
    async def test_connection(self) -> bool:
        """
        Test the InfluxDB connection.
        
        Returns:
            True if connection is successful, False otherwise
        """
        try:
            async def _test():
                # Simple query to test connection and database access
                query = 'SHOW MEASUREMENTS LIMIT 1'
                result = await self._execute_query_http(query)
                return True
            
            await self._retry_operation(_test)
            logger.info(f"Connection test successful for {self.connection.url}/{self.database_name}")
            return True
            
        except Exception as e:
            logger.error(f"Connection test failed for {self.connection.url}/{self.database_name}: {e}")
            return False
    
    async def get_latest_ping_time(
        self,
        component_type: ComponentType
    ) -> Optional[datetime]:
        """
        Get the timestamp of the most recent ping for a component.
        
        Args:
            component_type: Type of component to query
            
        Returns:
            Timestamp of the most recent ping, or None if no data found
        """
        
        async def _execute_query():
            # Get IP addresses for this component type
            ip_addresses = self.component_ip_mapping.get(component_type, [])
            
            if not ip_addresses:
                return None
            
            # Build InfluxQL query for the component's IP addresses
            ip_conditions = " OR ".join([f"url = '{ip}'" for ip in ip_addresses])
            
            query = f'''
            SELECT time 
            FROM ping 
            WHERE time > now() - 7d 
            AND ({ip_conditions})
            ORDER BY time DESC 
            LIMIT 1
            '''
            
            result = await self._execute_query_http(query)
            
            if 'results' in result and result['results']:
                if 'series' in result['results'][0]:
                    for series in result['results'][0]['series']:
                        values = series.get('values', [])
                        if values:
                            timestamp_str = values[0][0]  # First column is time
                            return datetime.fromisoformat(timestamp_str.replace('Z', '+00:00'))
            
            return None
        
        return await self._retry_operation(_execute_query)
    
    def close(self):
        """Close the InfluxDB client connection (no-op for HTTP client)."""
        logger.info(f"Closed InfluxDB client for {self.connection.url}/{self.database_name}")
    
    def __enter__(self):
        """Context manager entry."""
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        self.close()
    
    async def __aenter__(self):
        """Async context manager entry."""
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        self.close()