"""
Fleet Dashboard service for the Infrastructure Monitoring Agent.

This module provides the FleetDashboard class that aggregates status from all
66 vessels, calculates fleet-wide SLA status, and provides drill-down capability
for individual vessel metrics with SLA violation detection and highlighting.
"""

import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple, Any
from dataclasses import dataclass
from enum import Enum

from ..config.config_models import Config
from ..models.data_models import VesselMetrics, SLAStatus, ComponentStatus
from ..models.enums import ComponentType, OperationalStatus
from ..services.data_collector import DataCollector
from ..services.sla_analyzer import SLAAnalyzer, SLAViolation


logger = logging.getLogger(__name__)


class VesselStatusLevel(Enum):
    """Overall status levels for vessels"""
    OPERATIONAL = "operational"
    DEGRADED = "degraded"
    CRITICAL = "critical"
    OFFLINE = "offline"


class AlertSeverity(Enum):
    """Alert severity levels"""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass
class FleetOverview:
    """Fleet-wide overview data structure"""
    
    total_vessels: int
    vessels_online: int
    vessels_offline: int
    vessels_degraded: int
    vessels_critical: int
    fleet_compliance_rate: float
    average_uptime: float
    total_violations: int
    persistent_violations: int
    last_updated: datetime


@dataclass
class VesselSummary:
    """Summary data for a single vessel"""
    
    vessel_id: str
    status: VesselStatusLevel
    compliance_rate: float
    violations_count: int
    components_up: int
    components_total: int
    worst_component_uptime: float
    last_updated: datetime


@dataclass
class ComponentDetail:
    """Detailed information for a component"""
    
    component_type: ComponentType
    uptime_percentage: float
    current_status: OperationalStatus
    downtime_aging_hours: float
    is_sla_compliant: bool
    violation_duration_hours: Optional[float]
    last_ping_time: datetime
    alert_severity: AlertSeverity


@dataclass
class VesselDetail:
    """Detailed information for a vessel"""
    
    vessel_id: str
    overall_status: VesselStatusLevel
    compliance_rate: float
    components: List[ComponentDetail]
    violations: List[Dict[str, Any]]
    last_updated: datetime


class FleetDashboard:
    """
    Service for aggregating and presenting fleet-wide infrastructure status.
    
    This class provides methods to aggregate status from all 66 vessels,
    calculate fleet-wide SLA metrics, detect violations, and provide
    drill-down capabilities for individual vessel analysis.
    """
    
    def __init__(self, config: Config, data_collector: DataCollector, sla_analyzer: SLAAnalyzer):
        """
        Initialize the FleetDashboard service.
        
        Args:
            config: Application configuration
            data_collector: Data collection service
            sla_analyzer: SLA analysis service
        """
        self.config = config
        self.data_collector = data_collector
        self.sla_analyzer = sla_analyzer
        
        # Cache for fleet data
        self._fleet_cache: Optional[Dict[str, VesselMetrics]] = None
        self._fleet_sla_cache: Optional[Dict[str, Dict[ComponentType, SLAStatus]]] = None
        self._cache_timestamp: Optional[datetime] = None
        self._cache_ttl_minutes = 5  # Cache TTL in minutes
        
        logger.info(f"Initialized FleetDashboard for {len(config.vessel_databases)} vessels")
    
    async def get_fleet_overview(self, force_refresh: bool = False) -> FleetOverview:
        """
        Get fleet-wide overview with aggregated status and SLA metrics.
        
        Args:
            force_refresh: If True, bypass cache and collect fresh data
            
        Returns:
            FleetOverview containing aggregated fleet metrics
        """
        logger.info("Getting fleet overview")
        
        # Get fleet data (cached or fresh)
        fleet_metrics, fleet_sla_statuses = await self._get_fleet_data(force_refresh)
        
        if not fleet_metrics:
            return FleetOverview(
                total_vessels=len(self.config.vessel_databases),
                vessels_online=0,
                vessels_offline=len(self.config.vessel_databases),
                vessels_degraded=0,
                vessels_critical=0,
                fleet_compliance_rate=0.0,
                average_uptime=0.0,
                total_violations=0,
                persistent_violations=0,
                last_updated=datetime.utcnow()
            )
        
        # Calculate fleet statistics
        vessel_statuses = self._calculate_vessel_statuses(fleet_metrics, fleet_sla_statuses)
        
        # Count vessels by status
        status_counts = {status: 0 for status in VesselStatusLevel}
        for vessel_summary in vessel_statuses.values():
            status_counts[vessel_summary.status] += 1
        
        # Calculate fleet-wide metrics
        fleet_summary = self.data_collector.get_fleet_summary(fleet_metrics)
        sla_summary = self.sla_analyzer.calculate_fleet_sla_summary(fleet_sla_statuses)
        
        # Get violation counts
        violations = self.sla_analyzer.get_sla_violations(fleet_sla_statuses)
        persistent_violations = self.sla_analyzer.get_persistent_downtime_violations(violations)
        
        return FleetOverview(
            total_vessels=len(fleet_metrics),
            vessels_online=status_counts[VesselStatusLevel.OPERATIONAL],
            vessels_offline=status_counts[VesselStatusLevel.OFFLINE],
            vessels_degraded=status_counts[VesselStatusLevel.DEGRADED],
            vessels_critical=status_counts[VesselStatusLevel.CRITICAL],
            fleet_compliance_rate=sla_summary.get('fleet_compliance_rate', 0.0),
            average_uptime=fleet_summary.get('average_uptime', 0.0),
            total_violations=len(violations),
            persistent_violations=len(persistent_violations),
            last_updated=datetime.utcnow()
        )
    
    async def get_vessel_summaries(self, force_refresh: bool = False) -> Dict[str, VesselSummary]:
        """
        Get summary information for all vessels.
        
        Args:
            force_refresh: If True, bypass cache and collect fresh data
            
        Returns:
            Dictionary mapping vessel IDs to their summary information
        """
        logger.info("Getting vessel summaries")
        
        # Get fleet data (cached or fresh)
        fleet_metrics, fleet_sla_statuses = await self._get_fleet_data(force_refresh)
        
        return self._calculate_vessel_statuses(fleet_metrics, fleet_sla_statuses)
    
    async def get_vessel_details(self, vessel_id: str, force_refresh: bool = False) -> VesselDetail:
        """
        Get detailed information for a specific vessel.
        
        Args:
            vessel_id: ID of the vessel to get details for
            force_refresh: If True, bypass cache and collect fresh data
            
        Returns:
            VesselDetail containing comprehensive vessel information
            
        Raises:
            ValueError: If vessel ID is not found
        """
        logger.info(f"Getting details for vessel {vessel_id}")
        
        # Validate vessel ID
        if vessel_id not in self.config.get_vessel_ids():
            raise ValueError(f"Vessel {vessel_id} not found in configuration")
        
        # Collect vessel-specific data
        vessel_metrics = await self.data_collector.collect_vessel_metrics(vessel_id)
        vessel_sla_statuses = self.sla_analyzer.analyze_vessel_sla_compliance(vessel_metrics)
        
        # Calculate overall vessel status
        overall_status = self._calculate_vessel_status(vessel_sla_statuses)
        compliance_rate = self._calculate_compliance_rate(vessel_sla_statuses)
        
        # Build component details
        components = []
        violations = []
        
        for component_type, component_status in vessel_metrics.get_all_components().items():
            sla_status = vessel_sla_statuses[component_type]
            
            # Calculate alert severity
            alert_severity = self._calculate_alert_severity(component_status, sla_status)
            
            component_detail = ComponentDetail(
                component_type=component_type,
                uptime_percentage=component_status.uptime_percentage,
                current_status=component_status.current_status,
                downtime_aging_hours=component_status.downtime_aging.total_seconds() / 3600,
                is_sla_compliant=sla_status.is_compliant,
                violation_duration_hours=(
                    sla_status.violation_duration.total_seconds() / 3600 
                    if sla_status.violation_duration else None
                ),
                last_ping_time=component_status.last_ping_time,
                alert_severity=alert_severity
            )
            components.append(component_detail)
            
            # Add to violations if not compliant
            if not sla_status.is_compliant:
                violation_data = {
                    "component_type": component_type.value,
                    "uptime_percentage": component_status.uptime_percentage,
                    "downtime_aging_hours": component_status.downtime_aging.total_seconds() / 3600,
                    "violation_duration_hours": (
                        sla_status.violation_duration.total_seconds() / 3600 
                        if sla_status.violation_duration else 0
                    ),
                    "severity": alert_severity.value,
                    "requires_ticket": (
                        component_status.downtime_aging.days >= 
                        self.config.sla_parameters.downtime_alert_threshold_days
                    )
                }
                violations.append(violation_data)
        
        return VesselDetail(
            vessel_id=vessel_id,
            overall_status=overall_status,
            compliance_rate=compliance_rate,
            components=components,
            violations=violations,
            last_updated=vessel_metrics.timestamp
        )
    
    async def get_sla_violations(
        self, 
        vessel_id: Optional[str] = None,
        component_type: Optional[ComponentType] = None,
        persistent_only: bool = False,
        force_refresh: bool = False
    ) -> List[Dict[str, Any]]:
        """
        Get SLA violations with filtering and highlighting.
        
        Args:
            vessel_id: Optional filter by vessel ID
            component_type: Optional filter by component type
            persistent_only: If True, only return persistent violations
            force_refresh: If True, bypass cache and collect fresh data
            
        Returns:
            List of SLA violations with highlighting information
        """
        logger.info(f"Getting SLA violations (vessel_id={vessel_id}, component_type={component_type}, persistent_only={persistent_only})")
        
        # Get fleet data (cached or fresh)
        fleet_metrics, fleet_sla_statuses = await self._get_fleet_data(force_refresh)
        
        # Filter by vessel if specified
        if vessel_id:
            if vessel_id not in fleet_metrics:
                return []
            fleet_metrics = {vessel_id: fleet_metrics[vessel_id]}
            fleet_sla_statuses = {vessel_id: fleet_sla_statuses[vessel_id]}
        
        # Get violations
        all_violations = self.sla_analyzer.get_sla_violations(fleet_sla_statuses)
        
        if persistent_only:
            violations = self.sla_analyzer.get_persistent_downtime_violations(all_violations)
        else:
            violations = all_violations
        
        # Filter by component type if specified
        if component_type:
            violations = [v for v in violations if v.component_type == component_type]
        
        # Format violations with highlighting
        formatted_violations = []
        for violation in violations:
            vessel_metrics = fleet_metrics[violation.vessel_id]
            component_status = vessel_metrics.get_component_status(violation.component_type)
            
            # Get SLA status for additional context
            sla_status = fleet_sla_statuses[violation.vessel_id][violation.component_type]
            
            # Calculate severity and highlighting
            alert_severity = self._calculate_alert_severity(component_status, sla_status)
            
            violation_data = {
                "vessel_id": violation.vessel_id,
                "component_type": violation.component_type.value,
                "uptime_percentage": violation.uptime_percentage,
                "current_status": violation.current_status.value,
                "downtime_aging": {
                    "hours": violation.downtime_aging.total_seconds() / 3600,
                    "days": violation.downtime_aging.days,
                    "formatted": self._format_duration(violation.downtime_aging)
                },
                "violation_start": violation.violation_start.isoformat(),
                "severity": alert_severity.value,
                "highlight_class": self._get_highlight_class(alert_severity),
                "requires_ticket": (
                    violation.downtime_aging.days >= 
                    self.config.sla_parameters.downtime_alert_threshold_days
                ),
                "sla_threshold": self.config.sla_parameters.uptime_threshold_percentage,
                "last_ping": component_status.last_ping_time.isoformat()
            }
            formatted_violations.append(violation_data)
        
        # Sort by severity and downtime duration
        formatted_violations.sort(key=lambda x: (
            x["severity"] == "critical",
            x["severity"] == "high",
            x["downtime_aging"]["hours"]
        ), reverse=True)
        
        return formatted_violations
    
    async def get_component_breakdown(self, force_refresh: bool = False) -> Dict[str, Any]:
        """
        Get fleet-wide breakdown by component type.
        
        Args:
            force_refresh: If True, bypass cache and collect fresh data
            
        Returns:
            Component type breakdown with statistics
        """
        logger.info("Getting component breakdown")
        
        # Get fleet data (cached or fresh)
        fleet_metrics, fleet_sla_statuses = await self._get_fleet_data(force_refresh)
        
        # Get component breakdown from SLA analyzer
        component_breakdown = self.sla_analyzer.get_component_type_breakdown(fleet_sla_statuses)
        
        # Enhance with additional statistics
        enhanced_breakdown = {}
        for component_type, stats in component_breakdown.items():
            # Calculate additional metrics
            violations = []
            for vessel_id, vessel_sla_statuses in fleet_sla_statuses.items():
                if component_type in vessel_sla_statuses:
                    sla_status = vessel_sla_statuses[component_type]
                    if not sla_status.is_compliant:
                        violations.append({
                            "vessel_id": vessel_id,
                            "uptime_percentage": sla_status.uptime_percentage
                        })
            
            enhanced_breakdown[component_type.value] = {
                **stats,
                "violations": violations,
                "worst_uptime": min([v["uptime_percentage"] for v in violations]) if violations else 100.0,
                "status_distribution": self._get_component_status_distribution(
                    fleet_metrics, component_type
                )
            }
        
        return enhanced_breakdown
    
    def _get_component_status_distribution(
        self, 
        fleet_metrics: Dict[str, VesselMetrics], 
        component_type: ComponentType
    ) -> Dict[str, int]:
        """Get distribution of operational statuses for a component type"""
        distribution = {status.value: 0 for status in OperationalStatus}
        
        for vessel_metrics in fleet_metrics.values():
            component_status = vessel_metrics.get_component_status(component_type)
            distribution[component_status.current_status.value] += 1
        
        return distribution
    
    async def _get_fleet_data(
        self, 
        force_refresh: bool = False
    ) -> Tuple[Dict[str, VesselMetrics], Dict[str, Dict[ComponentType, SLAStatus]]]:
        """
        Get fleet data from cache or collect fresh data.
        
        Args:
            force_refresh: If True, bypass cache
            
        Returns:
            Tuple of (fleet_metrics, fleet_sla_statuses)
        """
        # Check cache validity
        if (not force_refresh and 
            self._fleet_cache is not None and 
            self._fleet_sla_cache is not None and
            self._cache_timestamp is not None and
            datetime.utcnow() - self._cache_timestamp < timedelta(minutes=self._cache_ttl_minutes)):
            
            logger.debug("Using cached fleet data")
            return self._fleet_cache, self._fleet_sla_cache
        
        # Collect fresh data
        logger.info("Collecting fresh fleet data")
        fleet_metrics = await self.data_collector.collect_all_vessels_metrics()
        fleet_sla_statuses = self.sla_analyzer.analyze_fleet_sla_compliance(fleet_metrics)
        
        # Update cache
        self._fleet_cache = fleet_metrics
        self._fleet_sla_cache = fleet_sla_statuses
        self._cache_timestamp = datetime.utcnow()
        
        return fleet_metrics, fleet_sla_statuses
    
    def _calculate_vessel_statuses(
        self,
        fleet_metrics: Dict[str, VesselMetrics],
        fleet_sla_statuses: Dict[str, Dict[ComponentType, SLAStatus]]
    ) -> Dict[str, VesselSummary]:
        """Calculate status summaries for all vessels"""
        vessel_summaries = {}
        
        for vessel_id, vessel_metrics in fleet_metrics.items():
            vessel_sla_statuses = fleet_sla_statuses.get(vessel_id, {})
            
            # Calculate vessel status
            vessel_status = self._calculate_vessel_status(vessel_sla_statuses)
            compliance_rate = self._calculate_compliance_rate(vessel_sla_statuses)
            
            # Count violations and components
            violations_count = sum(1 for sla_status in vessel_sla_statuses.values() if not sla_status.is_compliant)
            components_up = sum(
                1 for component_status in vessel_metrics.get_all_components().values()
                if component_status.current_status == OperationalStatus.UP
            )
            components_total = len(vessel_metrics.get_all_components())
            
            # Find worst component uptime
            worst_uptime = min(
                component_status.uptime_percentage
                for component_status in vessel_metrics.get_all_components().values()
            )
            
            vessel_summary = VesselSummary(
                vessel_id=vessel_id,
                status=vessel_status,
                compliance_rate=compliance_rate,
                violations_count=violations_count,
                components_up=components_up,
                components_total=components_total,
                worst_component_uptime=worst_uptime,
                last_updated=vessel_metrics.timestamp
            )
            vessel_summaries[vessel_id] = vessel_summary
        
        return vessel_summaries
    
    def _calculate_vessel_status(self, vessel_sla_statuses: Dict[ComponentType, SLAStatus]) -> VesselStatusLevel:
        """Calculate overall status for a vessel"""
        if not vessel_sla_statuses:
            return VesselStatusLevel.OFFLINE
        
        violations = sum(1 for sla_status in vessel_sla_statuses.values() if not sla_status.is_compliant)
        
        if violations == 0:
            return VesselStatusLevel.OPERATIONAL
        elif violations == 1:
            return VesselStatusLevel.DEGRADED
        else:
            return VesselStatusLevel.CRITICAL
    
    def _calculate_compliance_rate(self, vessel_sla_statuses: Dict[ComponentType, SLAStatus]) -> float:
        """Calculate SLA compliance rate for a vessel"""
        if not vessel_sla_statuses:
            return 0.0
        
        compliant_count = sum(1 for sla_status in vessel_sla_statuses.values() if sla_status.is_compliant)
        return round((compliant_count / len(vessel_sla_statuses)) * 100, 2)
    
    def _calculate_alert_severity(self, component_status: ComponentStatus, sla_status: SLAStatus) -> AlertSeverity:
        """Calculate alert severity for a component"""
        if sla_status.is_compliant:
            return AlertSeverity.LOW
        
        downtime_hours = component_status.downtime_aging.total_seconds() / 3600
        uptime_percentage = component_status.uptime_percentage
        
        # Critical: 3+ days downtime or very low uptime
        if downtime_hours >= 72 or uptime_percentage < 50:
            return AlertSeverity.CRITICAL
        
        # High: 1+ day downtime or below 80% uptime
        elif downtime_hours >= 24 or uptime_percentage < 80:
            return AlertSeverity.HIGH
        
        # Medium: 4+ hours downtime or below 90% uptime
        elif downtime_hours >= 4 or uptime_percentage < 90:
            return AlertSeverity.MEDIUM
        
        # Low: SLA violation but not severe
        else:
            return AlertSeverity.LOW
    
    def _get_highlight_class(self, severity: AlertSeverity) -> str:
        """Get CSS class for highlighting based on severity"""
        return {
            AlertSeverity.CRITICAL: "alert-critical",
            AlertSeverity.HIGH: "alert-high", 
            AlertSeverity.MEDIUM: "alert-medium",
            AlertSeverity.LOW: "alert-low"
        }[severity]
    
    def _format_duration(self, duration: timedelta) -> str:
        """Format timedelta in human-readable format"""
        total_seconds = int(duration.total_seconds())
        days = total_seconds // 86400
        hours = (total_seconds % 86400) // 3600
        minutes = (total_seconds % 3600) // 60
        
        parts = []
        if days > 0:
            parts.append(f"{days}d")
        if hours > 0:
            parts.append(f"{hours}h")
        if minutes > 0:
            parts.append(f"{minutes}m")
        
        return " ".join(parts) if parts else "< 1m"
    
    def clear_cache(self):
        """Clear the fleet data cache"""
        logger.info("Clearing fleet data cache")
        self._fleet_cache = None
        self._fleet_sla_cache = None
        self._cache_timestamp = None
    
    def get_cache_info(self) -> Dict[str, Any]:
        """Get information about the current cache state"""
        return {
            "is_cached": self._fleet_cache is not None,
            "cache_timestamp": self._cache_timestamp.isoformat() if self._cache_timestamp else None,
            "cache_age_minutes": (
                (datetime.utcnow() - self._cache_timestamp).total_seconds() / 60
                if self._cache_timestamp else None
            ),
            "cache_ttl_minutes": self._cache_ttl_minutes,
            "cached_vessels": len(self._fleet_cache) if self._fleet_cache else 0
        }