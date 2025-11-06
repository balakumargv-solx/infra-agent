"""
Data collection service for the Infrastructure Monitoring Agent.

This module provides the DataCollector class that queries all vessel-specific
InfluxDB instances, calculates uptime percentages, and determines operational
status for infrastructure components across the fleet.
"""

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Set
from concurrent.futures import ThreadPoolExecutor, as_completed
import time

from ..config.config_models import Config, InfluxDBConnection
from ..models.data_models import VesselMetrics, ComponentStatus
from ..models.enums import ComponentType, OperationalStatus
from .influxdb_client import InfluxDBClientWrapper, PingData


logger = logging.getLogger(__name__)


class DataCollector:
    """
    Service for collecting infrastructure metrics from all vessel databases.
    
    This class manages concurrent querying of multiple vessel-specific InfluxDB
    instances, calculates uptime percentages, and determines operational status
    for Access Points, Dashboards, and Servers across the fleet.
    """
    
    def __init__(self, config: Config, max_concurrent_vessels: int = 10):
        """
        Initialize the DataCollector service.
        
        Args:
            config: Application configuration containing vessel database connections
            max_concurrent_vessels: Maximum number of vessels to query concurrently
        """
        self.config = config
        self.max_concurrent_vessels = max_concurrent_vessels
        self.monitoring_window_hours = config.sla_parameters.monitoring_window_hours
        
        # Cache for InfluxDB client wrappers
        self._client_cache: Dict[str, InfluxDBClientWrapper] = {}
        
        logger.info(
            f"Initialized DataCollector for {len(config.vessel_databases)} vessels "
            f"with {max_concurrent_vessels} max concurrent connections"
        )
    
    def _get_client_wrapper(self, vessel_id: str) -> InfluxDBClientWrapper:
        """
        Get or create an InfluxDB client wrapper for a vessel.
        
        Args:
            vessel_id: ID of the vessel
            
        Returns:
            InfluxDBClientWrapper instance for the vessel
            
        Raises:
            ValueError: If vessel configuration is not found
        """
        if vessel_id not in self._client_cache:
            connection = self.config.get_vessel_connection(vessel_id)
            self._client_cache[vessel_id] = InfluxDBClientWrapper(connection, vessel_id)
        
        return self._client_cache[vessel_id]
    
    async def collect_vessel_metrics(self, vessel_id: str) -> VesselMetrics:
        """
        Collect complete metrics for a single vessel.
        
        Args:
            vessel_id: ID of the vessel to collect metrics for
            
        Returns:
            VesselMetrics containing status for all components
            
        Raises:
            Exception: If data collection fails for the vessel
        """
        logger.info(f"Collecting metrics for vessel {vessel_id}")
        start_time = time.time()
        
        try:
            client_wrapper = self._get_client_wrapper(vessel_id)
            
            # Collect data for all component types concurrently
            tasks = []
            for component_type in ComponentType:
                task = self._collect_component_status(
                    client_wrapper, vessel_id, component_type
                )
                tasks.append(task)
            
            # Wait for all component data collection to complete
            component_statuses = await asyncio.gather(*tasks)
            
            # Map results to specific components
            status_map = {status.component_type: status for status in component_statuses}
            
            # Create VesselMetrics object
            vessel_metrics = VesselMetrics(
                vessel_id=vessel_id,
                access_point_status=status_map[ComponentType.ACCESS_POINT],
                dashboard_status=status_map[ComponentType.DASHBOARD],
                server_status=status_map[ComponentType.SERVER],
                timestamp=datetime.utcnow()
            )
            
            collection_time = time.time() - start_time
            logger.info(
                f"Successfully collected metrics for vessel {vessel_id} "
                f"in {collection_time:.2f}s"
            )
            
            return vessel_metrics
            
        except Exception as e:
            collection_time = time.time() - start_time
            logger.error(
                f"Failed to collect metrics for vessel {vessel_id} "
                f"after {collection_time:.2f}s: {e}"
            )
            raise
    
    async def _collect_component_status(
        self,
        client_wrapper: InfluxDBClientWrapper,
        vessel_id: str,
        component_type: ComponentType
    ) -> ComponentStatus:
        """
        Collect status for a specific component on a vessel.
        
        Args:
            client_wrapper: InfluxDB client wrapper for the vessel
            vessel_id: ID of the vessel
            component_type: Type of component to collect status for
            
        Returns:
            ComponentStatus for the specified component
        """
        try:
            # Query ping data for the component
            ping_data = await client_wrapper.query_ping_status(
                component_type=component_type,
                hours_back=self.monitoring_window_hours
            )
            
            # Calculate metrics
            uptime_percentage = ping_data.get_uptime_percentage(self.monitoring_window_hours)
            current_status = ping_data.get_current_status()
            downtime_aging = ping_data.calculate_downtime_aging()
            last_ping_time = ping_data.get_last_ping_time() or datetime.utcnow()
            
            component_status = ComponentStatus(
                component_type=component_type,
                uptime_percentage=uptime_percentage,
                current_status=current_status,
                downtime_aging=downtime_aging,
                last_ping_time=last_ping_time
            )
            
            logger.debug(
                f"Component {component_type.value} on vessel {vessel_id}: "
                f"{uptime_percentage:.2f}% uptime, status {current_status.value}"
            )
            
            return component_status
            
        except Exception as e:
            logger.error(
                f"Failed to collect status for {component_type.value} "
                f"on vessel {vessel_id}: {e}"
            )
            
            # Return a status indicating unknown state
            return ComponentStatus(
                component_type=component_type,
                uptime_percentage=0.0,
                current_status=OperationalStatus.UNKNOWN,
                downtime_aging=timedelta(0),
                last_ping_time=datetime.utcnow()
            )
    
    async def collect_all_vessels_metrics(
        self,
        vessel_ids: Optional[List[str]] = None
    ) -> Dict[str, VesselMetrics]:
        """
        Collect metrics for all vessels or a specified subset.
        
        Args:
            vessel_ids: Optional list of specific vessel IDs to collect.
                       If None, collects for all configured vessels.
            
        Returns:
            Dictionary mapping vessel IDs to their VesselMetrics
        """
        if vessel_ids is None:
            vessel_ids = self.config.get_vessel_ids()
        
        logger.info(f"Starting collection for {len(vessel_ids)} vessels")
        start_time = time.time()
        
        # Use semaphore to limit concurrent connections
        semaphore = asyncio.Semaphore(self.max_concurrent_vessels)
        
        async def collect_with_semaphore(vessel_id: str) -> tuple[str, Optional[VesselMetrics]]:
            async with semaphore:
                try:
                    metrics = await self.collect_vessel_metrics(vessel_id)
                    return vessel_id, metrics
                except Exception as e:
                    logger.error(f"Failed to collect metrics for vessel {vessel_id}: {e}")
                    return vessel_id, None
        
        # Create tasks for all vessels
        tasks = [collect_with_semaphore(vessel_id) for vessel_id in vessel_ids]
        
        # Execute all tasks concurrently
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Process results
        vessel_metrics = {}
        successful_collections = 0
        failed_collections = 0
        
        for result in results:
            if isinstance(result, Exception):
                logger.error(f"Unexpected error in vessel collection: {result}")
                failed_collections += 1
                continue
            
            vessel_id, metrics = result
            if metrics is not None:
                vessel_metrics[vessel_id] = metrics
                successful_collections += 1
            else:
                failed_collections += 1
        
        collection_time = time.time() - start_time
        logger.info(
            f"Completed collection for {len(vessel_ids)} vessels in {collection_time:.2f}s: "
            f"{successful_collections} successful, {failed_collections} failed"
        )
        
        return vessel_metrics
    
    async def test_vessel_connections(
        self,
        vessel_ids: Optional[List[str]] = None
    ) -> Dict[str, bool]:
        """
        Test connections to vessel databases.
        
        Args:
            vessel_ids: Optional list of specific vessel IDs to test.
                       If None, tests all configured vessels.
            
        Returns:
            Dictionary mapping vessel IDs to connection test results
        """
        if vessel_ids is None:
            vessel_ids = self.config.get_vessel_ids()
        
        logger.info(f"Testing connections for {len(vessel_ids)} vessels")
        
        # Use semaphore to limit concurrent connections
        semaphore = asyncio.Semaphore(self.max_concurrent_vessels)
        
        async def test_with_semaphore(vessel_id: str) -> tuple[str, bool]:
            async with semaphore:
                try:
                    client_wrapper = self._get_client_wrapper(vessel_id)
                    result = await client_wrapper.test_connection()
                    return vessel_id, result
                except Exception as e:
                    logger.error(f"Connection test failed for vessel {vessel_id}: {e}")
                    return vessel_id, False
        
        # Create tasks for all vessels
        tasks = [test_with_semaphore(vessel_id) for vessel_id in vessel_ids]
        
        # Execute all tasks concurrently
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Process results
        connection_results = {}
        for result in results:
            if isinstance(result, Exception):
                logger.error(f"Unexpected error in connection test: {result}")
                continue
            
            vessel_id, success = result
            connection_results[vessel_id] = success
        
        successful_connections = sum(connection_results.values())
        logger.info(
            f"Connection test completed: {successful_connections}/{len(vessel_ids)} successful"
        )
        
        return connection_results
    
    def get_fleet_summary(self, vessel_metrics: Dict[str, VesselMetrics]) -> Dict[str, any]:
        """
        Generate a summary of fleet-wide metrics.
        
        Args:
            vessel_metrics: Dictionary of vessel metrics
            
        Returns:
            Dictionary containing fleet summary statistics
        """
        if not vessel_metrics:
            return {
                'total_vessels': 0,
                'vessels_online': 0,
                'average_uptime': 0.0,
                'components_below_sla': 0,
                'total_components': 0
            }
        
        total_vessels = len(vessel_metrics)
        vessels_online = 0
        total_uptime = 0.0
        components_below_sla = 0
        total_components = 0
        sla_threshold = self.config.sla_parameters.uptime_threshold_percentage
        
        for vessel_id, metrics in vessel_metrics.items():
            vessel_online = True
            vessel_uptime_sum = 0.0
            vessel_components = 0
            
            for component_status in [
                metrics.access_point_status,
                metrics.dashboard_status,
                metrics.server_status
            ]:
                vessel_uptime_sum += component_status.uptime_percentage
                vessel_components += 1
                total_components += 1
                
                if component_status.uptime_percentage < sla_threshold:
                    components_below_sla += 1
                
                if component_status.current_status != OperationalStatus.UP:
                    vessel_online = False
            
            if vessel_online:
                vessels_online += 1
            
            # Add vessel's average uptime to total
            vessel_avg_uptime = vessel_uptime_sum / vessel_components if vessel_components > 0 else 0.0
            total_uptime += vessel_avg_uptime
        
        average_uptime = total_uptime / total_vessels if total_vessels > 0 else 0.0
        
        return {
            'total_vessels': total_vessels,
            'vessels_online': vessels_online,
            'average_uptime': round(average_uptime, 2),
            'components_below_sla': components_below_sla,
            'total_components': total_components,
            'sla_compliance_rate': round(
                ((total_components - components_below_sla) / total_components * 100) 
                if total_components > 0 else 0.0, 2
            )
        }
    
    def close_all_connections(self):
        """Close all cached InfluxDB client connections."""
        logger.info(f"Closing {len(self._client_cache)} InfluxDB client connections")
        
        for vessel_id, client_wrapper in self._client_cache.items():
            try:
                client_wrapper.close()
            except Exception as e:
                logger.warning(f"Error closing connection for vessel {vessel_id}: {e}")
        
        self._client_cache.clear()
        logger.info("All InfluxDB client connections closed")
    
    def __enter__(self):
        """Context manager entry."""
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        self.close_all_connections()
    
    async def __aenter__(self):
        """Async context manager entry."""
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        self.close_all_connections()