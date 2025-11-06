"""
SLA analysis service for the Infrastructure Monitoring Agent.

This module provides the SLAAnalyzer class that processes VesselMetrics data,
validates uptime percentages against SLA thresholds, calculates downtime aging,
and determines SLA compliance status for infrastructure components with
historical data tracking capabilities.
"""

import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Set, Tuple
from dataclasses import dataclass

from ..config.config_models import Config, SLAParameters
from ..models.data_models import VesselMetrics, ComponentStatus, SLAStatus
from ..models.enums import ComponentType, OperationalStatus
from .database import DatabaseService


logger = logging.getLogger(__name__)


@dataclass
class SLAViolation:
    """Represents an SLA violation for tracking purposes."""
    
    vessel_id: str
    component_type: ComponentType
    violation_start: datetime
    uptime_percentage: float
    downtime_aging: timedelta
    current_status: OperationalStatus


class SLAAnalyzer:
    """
    Service for analyzing SLA compliance across vessel infrastructure components.
    
    This class processes VesselMetrics data to determine SLA compliance status,
    calculate downtime aging, identify components that require attention
    based on configured SLA thresholds, and maintains historical tracking
    of violations and status changes.
    """
    
    def __init__(self, config: Config, database_service: Optional[DatabaseService] = None):
        """
        Initialize the SLAAnalyzer service.
        
        Args:
            config: Application configuration containing SLA parameters
            database_service: Optional database service for historical tracking
        """
        self.config = config
        self.sla_params = config.sla_parameters
        self.db_service = database_service or DatabaseService(config.database_path)
        
        # Cache for tracking active violations
        self._active_violations: Dict[Tuple[str, ComponentType], int] = {}
        
        logger.info(
            f"Initialized SLAAnalyzer with {self.sla_params.uptime_threshold_percentage}% "
            f"SLA threshold and {self.sla_params.downtime_alert_threshold_days} day "
            f"downtime alert threshold"
        )
    
    def analyze_vessel_sla_compliance(self, vessel_metrics: VesselMetrics) -> Dict[ComponentType, SLAStatus]:
        """
        Analyze SLA compliance for all components on a single vessel.
        
        Args:
            vessel_metrics: Complete metrics for the vessel
            
        Returns:
            Dictionary mapping component types to their SLA status
        """
        logger.debug(f"Analyzing SLA compliance for vessel {vessel_metrics.vessel_id}")
        
        sla_statuses = {}
        
        # Analyze each component type
        for component_type, component_status in vessel_metrics.get_all_components().items():
            sla_status = self._analyze_component_sla_compliance(
                vessel_metrics.vessel_id,
                component_status
            )
            sla_statuses[component_type] = sla_status
            
            # Log SLA violations
            if not sla_status.is_compliant:
                logger.warning(
                    f"SLA violation detected: Vessel {vessel_metrics.vessel_id} "
                    f"{component_type.value} at {sla_status.uptime_percentage:.2f}% uptime "
                    f"(threshold: {self.sla_params.uptime_threshold_percentage}%)"
                )
        
        return sla_statuses
    
    def _analyze_component_sla_compliance(
        self,
        vessel_id: str,
        component_status: ComponentStatus
    ) -> SLAStatus:
        """
        Analyze SLA compliance for a single component.
        
        Args:
            vessel_id: ID of the vessel
            component_status: Status information for the component
            
        Returns:
            SLAStatus indicating compliance and violation details
        """
        # Check if uptime meets SLA threshold
        is_compliant = self._check_sla_threshold(component_status.uptime_percentage)
        
        # Calculate violation duration if not compliant
        violation_duration = None
        if not is_compliant:
            violation_duration = self._calculate_violation_duration(component_status)
        
        sla_status = SLAStatus(
            vessel_id=vessel_id,
            component_type=component_status.component_type,
            is_compliant=is_compliant,
            uptime_percentage=component_status.uptime_percentage,
            violation_duration=violation_duration
        )
        
        logger.debug(
            f"Component {component_status.component_type.value} on vessel {vessel_id}: "
            f"{'COMPLIANT' if is_compliant else 'VIOLATION'} "
            f"({component_status.uptime_percentage:.2f}% uptime)"
        )
        
        return sla_status
    
    def _check_sla_threshold(self, uptime_percentage: float) -> bool:
        """
        Check if uptime percentage meets SLA threshold.
        
        Args:
            uptime_percentage: Uptime percentage to check
            
        Returns:
            True if uptime meets or exceeds SLA threshold
        """
        return uptime_percentage >= self.sla_params.uptime_threshold_percentage
    
    def _calculate_violation_duration(self, component_status: ComponentStatus) -> timedelta:
        """
        Calculate how long a component has been in violation.
        
        For components currently down, this returns the downtime aging.
        For components that are up but below SLA threshold, this estimates
        violation duration based on the monitoring window.
        
        Args:
            component_status: Status information for the component
            
        Returns:
            Duration of the SLA violation
        """
        if component_status.current_status != OperationalStatus.UP:
            # Component is currently down, use downtime aging
            return component_status.downtime_aging
        else:
            # Component is up but below SLA threshold
            # Estimate violation duration based on uptime percentage
            monitoring_window = timedelta(hours=self.sla_params.monitoring_window_hours)
            downtime_percentage = 100.0 - component_status.uptime_percentage
            estimated_downtime = monitoring_window * (downtime_percentage / 100.0)
            return estimated_downtime
    
    def analyze_fleet_sla_compliance(
        self,
        fleet_metrics: Dict[str, VesselMetrics]
    ) -> Dict[str, Dict[ComponentType, SLAStatus]]:
        """
        Analyze SLA compliance for the entire fleet.
        
        Args:
            fleet_metrics: Dictionary mapping vessel IDs to their metrics
            
        Returns:
            Nested dictionary mapping vessel IDs to component SLA statuses
        """
        logger.info(f"Analyzing SLA compliance for {len(fleet_metrics)} vessels")
        
        fleet_sla_statuses = {}
        total_violations = 0
        
        for vessel_id, vessel_metrics in fleet_metrics.items():
            try:
                vessel_sla_statuses = self.analyze_vessel_sla_compliance(vessel_metrics)
                fleet_sla_statuses[vessel_id] = vessel_sla_statuses
                
                # Count violations for this vessel
                vessel_violations = sum(
                    1 for sla_status in vessel_sla_statuses.values()
                    if not sla_status.is_compliant
                )
                total_violations += vessel_violations
                
            except Exception as e:
                logger.error(f"Failed to analyze SLA compliance for vessel {vessel_id}: {e}")
                # Continue with other vessels
                continue
        
        logger.info(
            f"Fleet SLA analysis completed: {total_violations} total violations "
            f"across {len(fleet_sla_statuses)} vessels"
        )
        
        return fleet_sla_statuses
    
    def get_sla_violations(
        self,
        fleet_sla_statuses: Dict[str, Dict[ComponentType, SLAStatus]]
    ) -> List[SLAViolation]:
        """
        Extract all SLA violations from fleet analysis results.
        
        Args:
            fleet_sla_statuses: Fleet SLA analysis results
            
        Returns:
            List of SLA violations requiring attention
        """
        violations = []
        
        for vessel_id, vessel_sla_statuses in fleet_sla_statuses.items():
            for component_type, sla_status in vessel_sla_statuses.items():
                if not sla_status.is_compliant:
                    # Create violation record
                    violation = SLAViolation(
                        vessel_id=vessel_id,
                        component_type=component_type,
                        violation_start=datetime.utcnow() - (sla_status.violation_duration or timedelta(0)),
                        uptime_percentage=sla_status.uptime_percentage,
                        downtime_aging=sla_status.violation_duration or timedelta(0),
                        current_status=OperationalStatus.DOWN  # Default assumption for violations
                    )
                    violations.append(violation)
        
        logger.info(f"Identified {len(violations)} SLA violations requiring attention")
        return violations
    
    def get_persistent_downtime_violations(
        self,
        violations: List[SLAViolation]
    ) -> List[SLAViolation]:
        """
        Filter violations to find those exceeding the downtime alert threshold.
        
        Args:
            violations: List of all SLA violations
            
        Returns:
            List of violations that have exceeded the downtime alert threshold
        """
        threshold_days = self.sla_params.downtime_alert_threshold_days
        threshold_duration = timedelta(days=threshold_days)
        
        persistent_violations = [
            violation for violation in violations
            if violation.downtime_aging >= threshold_duration
        ]
        
        logger.info(
            f"Found {len(persistent_violations)} violations exceeding "
            f"{threshold_days} day downtime threshold"
        )
        
        return persistent_violations
    
    def calculate_fleet_sla_summary(
        self,
        fleet_sla_statuses: Dict[str, Dict[ComponentType, SLAStatus]]
    ) -> Dict[str, any]:
        """
        Calculate summary statistics for fleet-wide SLA compliance.
        
        Args:
            fleet_sla_statuses: Fleet SLA analysis results
            
        Returns:
            Dictionary containing fleet SLA summary statistics
        """
        if not fleet_sla_statuses:
            return {
                'total_vessels': 0,
                'total_components': 0,
                'compliant_components': 0,
                'violation_components': 0,
                'fleet_compliance_rate': 0.0,
                'average_uptime': 0.0,
                'vessels_with_violations': 0
            }
        
        total_vessels = len(fleet_sla_statuses)
        total_components = 0
        compliant_components = 0
        violation_components = 0
        total_uptime = 0.0
        vessels_with_violations = 0
        
        for vessel_id, vessel_sla_statuses in fleet_sla_statuses.items():
            vessel_has_violations = False
            
            for component_type, sla_status in vessel_sla_statuses.items():
                total_components += 1
                total_uptime += sla_status.uptime_percentage
                
                if sla_status.is_compliant:
                    compliant_components += 1
                else:
                    violation_components += 1
                    vessel_has_violations = True
            
            if vessel_has_violations:
                vessels_with_violations += 1
        
        # Calculate rates and averages
        fleet_compliance_rate = (
            (compliant_components / total_components * 100) 
            if total_components > 0 else 0.0
        )
        average_uptime = total_uptime / total_components if total_components > 0 else 0.0
        
        summary = {
            'total_vessels': total_vessels,
            'total_components': total_components,
            'compliant_components': compliant_components,
            'violation_components': violation_components,
            'fleet_compliance_rate': round(fleet_compliance_rate, 2),
            'average_uptime': round(average_uptime, 2),
            'vessels_with_violations': vessels_with_violations,
            'vessels_fully_compliant': total_vessels - vessels_with_violations
        }
        
        logger.info(
            f"Fleet SLA Summary: {fleet_compliance_rate:.2f}% compliance rate, "
            f"{violation_components} violations across {vessels_with_violations} vessels"
        )
        
        return summary
    
    def get_component_type_breakdown(
        self,
        fleet_sla_statuses: Dict[str, Dict[ComponentType, SLAStatus]]
    ) -> Dict[ComponentType, Dict[str, any]]:
        """
        Get SLA compliance breakdown by component type.
        
        Args:
            fleet_sla_statuses: Fleet SLA analysis results
            
        Returns:
            Dictionary mapping component types to their compliance statistics
        """
        component_breakdown = {}
        
        for component_type in ComponentType:
            total_count = 0
            compliant_count = 0
            total_uptime = 0.0
            violation_count = 0
            
            for vessel_id, vessel_sla_statuses in fleet_sla_statuses.items():
                if component_type in vessel_sla_statuses:
                    sla_status = vessel_sla_statuses[component_type]
                    total_count += 1
                    total_uptime += sla_status.uptime_percentage
                    
                    if sla_status.is_compliant:
                        compliant_count += 1
                    else:
                        violation_count += 1
            
            if total_count > 0:
                compliance_rate = (compliant_count / total_count) * 100
                average_uptime = total_uptime / total_count
            else:
                compliance_rate = 0.0
                average_uptime = 0.0
            
            component_breakdown[component_type] = {
                'total_count': total_count,
                'compliant_count': compliant_count,
                'violation_count': violation_count,
                'compliance_rate': round(compliance_rate, 2),
                'average_uptime': round(average_uptime, 2)
            }
        
        return component_breakdown
    
    def analyze_vessel_sla_compliance_with_tracking(
        self,
        vessel_metrics: VesselMetrics
    ) -> Dict[ComponentType, SLAStatus]:
        """
        Analyze SLA compliance for a vessel with historical tracking.
        
        This method extends the basic SLA analysis to include:
        - Recording component status changes in the database
        - Tracking SLA violations over time
        - Managing violation lifecycle (start/resolve)
        
        Args:
            vessel_metrics: Complete metrics for the vessel
            
        Returns:
            Dictionary mapping component types to their SLA status
        """
        logger.debug(f"Analyzing SLA compliance with tracking for vessel {vessel_metrics.vessel_id}")
        
        # Perform basic SLA analysis
        sla_statuses = self.analyze_vessel_sla_compliance(vessel_metrics)
        
        # Record component statuses in database
        for component_type, component_status in vessel_metrics.get_all_components().items():
            self.db_service.record_component_status(
                vessel_metrics.vessel_id,
                component_status,
                vessel_metrics.timestamp
            )
        
        # Track SLA violations
        for component_type, sla_status in sla_statuses.items():
            self._track_sla_violation_lifecycle(
                vessel_metrics.vessel_id,
                component_type,
                sla_status,
                vessel_metrics.timestamp
            )
        
        return sla_statuses
    
    def _track_sla_violation_lifecycle(
        self,
        vessel_id: str,
        component_type: ComponentType,
        sla_status: SLAStatus,
        timestamp: datetime
    ) -> None:
        """
        Track the lifecycle of SLA violations (start/resolve).
        
        Args:
            vessel_id: ID of the vessel
            component_type: Type of component
            sla_status: Current SLA status
            timestamp: When the status was recorded
        """
        violation_key = (vessel_id, component_type)
        
        if not sla_status.is_compliant:
            # Component is in violation
            if violation_key not in self._active_violations:
                # New violation - record it
                violation_id = self.db_service.record_sla_violation(
                    vessel_id=vessel_id,
                    component_type=component_type,
                    violation_start=timestamp - (sla_status.violation_duration or timedelta(0)),
                    uptime_percentage=sla_status.uptime_percentage,
                    violation_duration=sla_status.violation_duration
                )
                self._active_violations[violation_key] = violation_id
                
                logger.info(
                    f"Started tracking SLA violation for {component_type.value} "
                    f"on vessel {vessel_id} (violation ID: {violation_id})"
                )
        else:
            # Component is compliant
            if violation_key in self._active_violations:
                # Violation resolved - update record
                violation_id = self._active_violations[violation_key]
                self.db_service.resolve_sla_violation(
                    violation_id=violation_id,
                    violation_end=timestamp,
                    final_uptime_percentage=sla_status.uptime_percentage
                )
                del self._active_violations[violation_key]
                
                logger.info(
                    f"Resolved SLA violation for {component_type.value} "
                    f"on vessel {vessel_id} (violation ID: {violation_id})"
                )
    
    def analyze_fleet_sla_compliance_with_tracking(
        self,
        fleet_metrics: Dict[str, VesselMetrics]
    ) -> Dict[str, Dict[ComponentType, SLAStatus]]:
        """
        Analyze SLA compliance for the entire fleet with historical tracking.
        
        Args:
            fleet_metrics: Dictionary mapping vessel IDs to their metrics
            
        Returns:
            Nested dictionary mapping vessel IDs to component SLA statuses
        """
        logger.info(f"Analyzing SLA compliance with tracking for {len(fleet_metrics)} vessels")
        
        fleet_sla_statuses = {}
        total_violations = 0
        
        for vessel_id, vessel_metrics in fleet_metrics.items():
            try:
                vessel_sla_statuses = self.analyze_vessel_sla_compliance_with_tracking(vessel_metrics)
                fleet_sla_statuses[vessel_id] = vessel_sla_statuses
                
                # Count violations for this vessel
                vessel_violations = sum(
                    1 for sla_status in vessel_sla_statuses.values()
                    if not sla_status.is_compliant
                )
                total_violations += vessel_violations
                
            except Exception as e:
                logger.error(f"Failed to analyze SLA compliance for vessel {vessel_id}: {e}")
                # Continue with other vessels
                continue
        
        logger.info(
            f"Fleet SLA analysis with tracking completed: {total_violations} total violations "
            f"across {len(fleet_sla_statuses)} vessels"
        )
        
        return fleet_sla_statuses
    
    def get_violation_trends(
        self,
        vessel_id: Optional[str] = None,
        component_type: Optional[ComponentType] = None,
        days_back: int = 30
    ) -> Dict[str, any]:
        """
        Analyze SLA violation trends over time.
        
        Args:
            vessel_id: Optional filter by vessel ID
            component_type: Optional filter by component type
            days_back: Number of days to analyze
            
        Returns:
            Dictionary containing trend analysis results
        """
        logger.info(f"Analyzing violation trends for {days_back} days")
        
        # Get violation history
        violations = self.db_service.get_violation_history(
            vessel_id=vessel_id,
            component_type=component_type,
            days_back=days_back
        )
        
        if not violations:
            return {
                'total_violations': 0,
                'average_duration_hours': 0.0,
                'violations_by_day': {},
                'violations_by_component': {},
                'trend_direction': 'stable'
            }
        
        # Analyze violations by day
        violations_by_day = {}
        for violation in violations:
            day_key = violation['violation_start'].date().isoformat()
            violations_by_day[day_key] = violations_by_day.get(day_key, 0) + 1
        
        # Analyze violations by component type
        violations_by_component = {}
        total_duration_hours = 0.0
        duration_count = 0
        
        for violation in violations:
            comp_type = violation['component_type'].value
            violations_by_component[comp_type] = violations_by_component.get(comp_type, 0) + 1
            
            if violation['violation_duration_seconds']:
                total_duration_hours += violation['violation_duration_seconds'] / 3600.0
                duration_count += 1
        
        # Calculate trend direction (simple comparison of first vs last week)
        trend_direction = self._calculate_trend_direction(violations, days_back)
        
        return {
            'total_violations': len(violations),
            'average_duration_hours': round(
                total_duration_hours / duration_count if duration_count > 0 else 0.0, 2
            ),
            'violations_by_day': violations_by_day,
            'violations_by_component': violations_by_component,
            'trend_direction': trend_direction,
            'analysis_period_days': days_back
        }
    
    def _calculate_trend_direction(self, violations: List[Dict], days_back: int) -> str:
        """
        Calculate trend direction based on violation frequency.
        
        Args:
            violations: List of violation records
            days_back: Total analysis period
            
        Returns:
            Trend direction: 'improving', 'worsening', or 'stable'
        """
        if len(violations) < 2 or days_back < 14:
            return 'stable'
        
        # Split violations into first and second half of the period
        cutoff_date = datetime.utcnow() - timedelta(days=days_back // 2)
        
        recent_violations = [
            v for v in violations 
            if v['violation_start'] >= cutoff_date
        ]
        older_violations = [
            v for v in violations 
            if v['violation_start'] < cutoff_date
        ]
        
        recent_count = len(recent_violations)
        older_count = len(older_violations)
        
        # Calculate rate per day
        recent_rate = recent_count / (days_back // 2)
        older_rate = older_count / (days_back // 2)
        
        if recent_rate > older_rate * 1.2:  # 20% increase threshold
            return 'worsening'
        elif recent_rate < older_rate * 0.8:  # 20% decrease threshold
            return 'improving'
        else:
            return 'stable'
    
    def get_component_status_trends(
        self,
        vessel_id: str,
        component_type: ComponentType,
        days_back: int = 7
    ) -> Dict[str, any]:
        """
        Get detailed status trends for a specific component.
        
        Args:
            vessel_id: ID of the vessel
            component_type: Type of component
            days_back: Number of days to analyze
            
        Returns:
            Dictionary containing component trend analysis
        """
        trends = self.db_service.get_component_status_trends(
            vessel_id=vessel_id,
            component_type=component_type,
            days_back=days_back
        )
        
        if not trends:
            return {
                'data_points': 0,
                'average_uptime': 0.0,
                'uptime_trend': 'stable',
                'status_changes': 0,
                'current_streak_hours': 0.0
            }
        
        # Calculate statistics
        uptimes = [t['uptime_percentage'] for t in trends]
        average_uptime = sum(uptimes) / len(uptimes)
        
        # Count status changes
        status_changes = 0
        for i in range(1, len(trends)):
            if trends[i]['current_status'] != trends[i-1]['current_status']:
                status_changes += 1
        
        # Calculate uptime trend
        uptime_trend = self._calculate_uptime_trend(uptimes)
        
        # Calculate current streak
        current_streak_hours = self._calculate_current_streak(trends)
        
        return {
            'data_points': len(trends),
            'average_uptime': round(average_uptime, 2),
            'uptime_trend': uptime_trend,
            'status_changes': status_changes,
            'current_streak_hours': current_streak_hours,
            'analysis_period_days': days_back
        }
    
    def _calculate_uptime_trend(self, uptimes: List[float]) -> str:
        """Calculate uptime trend direction."""
        if len(uptimes) < 4:
            return 'stable'
        
        # Compare first and last quarters
        quarter_size = len(uptimes) // 4
        first_quarter_avg = sum(uptimes[:quarter_size]) / quarter_size
        last_quarter_avg = sum(uptimes[-quarter_size:]) / quarter_size
        
        if last_quarter_avg > first_quarter_avg + 2.0:  # 2% improvement threshold
            return 'improving'
        elif last_quarter_avg < first_quarter_avg - 2.0:  # 2% degradation threshold
            return 'degrading'
        else:
            return 'stable'
    
    def _calculate_current_streak(self, trends: List[Dict]) -> float:
        """Calculate current up/down streak in hours."""
        if not trends:
            return 0.0
        
        # Get the most recent status
        current_status = trends[-1]['current_status']
        streak_start = None
        
        # Find when the current streak started
        for i in range(len(trends) - 1, -1, -1):
            if trends[i]['current_status'] == current_status:
                streak_start = trends[i]['recorded_at']
            else:
                break
        
        if streak_start:
            streak_duration = datetime.utcnow() - streak_start
            return round(streak_duration.total_seconds() / 3600.0, 1)
        
        return 0.0
    
    def generate_historical_report(
        self,
        vessel_id: Optional[str] = None,
        days_back: int = 30
    ) -> Dict[str, any]:
        """
        Generate a comprehensive historical analysis report.
        
        Args:
            vessel_id: Optional filter by vessel ID
            days_back: Number of days to analyze
            
        Returns:
            Dictionary containing comprehensive historical analysis
        """
        logger.info(f"Generating historical report for {days_back} days")
        
        # Get violation trends
        violation_trends = self.get_violation_trends(
            vessel_id=vessel_id,
            days_back=days_back
        )
        
        # Get violation duration statistics
        duration_stats = self.db_service.calculate_violation_duration_stats(
            vessel_id=vessel_id,
            days_back=days_back
        )
        
        # Get component-specific trends if vessel is specified
        component_trends = {}
        if vessel_id:
            for component_type in ComponentType:
                component_trends[component_type.value] = self.get_component_status_trends(
                    vessel_id=vessel_id,
                    component_type=component_type,
                    days_back=days_back
                )
        
        return {
            'analysis_period_days': days_back,
            'vessel_id': vessel_id,
            'violation_trends': violation_trends,
            'duration_statistics': duration_stats,
            'component_trends': component_trends,
            'generated_at': datetime.utcnow().isoformat()
        }
    
    def cleanup_old_data(self, days_to_keep: int = 90) -> Dict[str, int]:
        """
        Clean up old historical data.
        
        Args:
            days_to_keep: Number of days of data to retain
            
        Returns:
            Dictionary with counts of cleaned up records
        """
        logger.info(f"Cleaning up historical data older than {days_to_keep} days")
        return self.db_service.cleanup_old_records(days_to_keep)
    
    def update_sla_parameters(self, new_params: SLAParameters) -> None:
        """
        Update SLA parameters for the analyzer.
        
        Args:
            new_params: New SLA parameters to use
        """
        old_threshold = self.sla_params.uptime_threshold_percentage
        old_alert_days = self.sla_params.downtime_alert_threshold_days
        
        self.sla_params = new_params
        self.config.sla_parameters = new_params
        
        logger.info(
            f"Updated SLA parameters: threshold {old_threshold}% -> "
            f"{new_params.uptime_threshold_percentage}%, "
            f"alert threshold {old_alert_days} -> {new_params.downtime_alert_threshold_days} days"
        )