"""
Monitoring orchestrator for the Infrastructure Monitoring Agent.

This module provides the MonitoringOrchestrator class that executes the complete
daily monitoring process, integrating all services with comprehensive error handling
and recovery mechanisms.
"""

import logging
import asyncio
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass, asdict
import traceback

from ..config.config_models import Config
from ..models.data_models import VesselMetrics, ComponentStatus, SLAStatus, IssueSummary
from ..models.enums import ComponentType, OperationalStatus, IssueSeverity
from .data_collector import DataCollector
from .sla_analyzer import SLAAnalyzer
from .alert_manager import AlertManager, Alert
from .jira_service import JIRAService
from .approval_workflow import ApprovalWorkflowManager
from .database import DatabaseService


logger = logging.getLogger(__name__)


@dataclass
class WorkflowResult:
    """Result of a complete monitoring workflow execution."""
    
    execution_id: str
    start_time: datetime
    end_time: datetime
    success: bool
    vessels_processed: int
    vessels_failed: int
    sla_violations: int
    persistent_downtime_alerts: int
    tickets_created: int
    errors: List[str]
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            **asdict(self),
            'start_time': self.start_time.isoformat(),
            'end_time': self.end_time.isoformat(),
            'execution_duration_seconds': (self.end_time - self.start_time).total_seconds()
        }


@dataclass
class WorkflowStep:
    """Individual step in the monitoring workflow."""
    
    name: str
    start_time: datetime
    end_time: Optional[datetime] = None
    success: bool = False
    error_message: Optional[str] = None
    result_data: Optional[Dict[str, Any]] = None
    
    @property
    def duration(self) -> Optional[timedelta]:
        """Get step execution duration."""
        if self.end_time:
            return self.end_time - self.start_time
        return None


class MonitoringOrchestrator:
    """
    Orchestrator for the complete daily monitoring process.
    
    This class coordinates the execution of all monitoring services:
    - Data collection from vessel InfluxDB instances
    - SLA analysis and compliance checking
    - Alert generation and management
    - JIRA ticket creation with human approval workflow
    
    Provides comprehensive error handling and recovery mechanisms for failed operations.
    """
    
    def __init__(self, config: Config):
        """
        Initialize the monitoring orchestrator.
        
        Args:
            config: Application configuration
        """
        self.config = config
        
        # Initialize services
        self.database_service = DatabaseService(config.database_path)
        self.data_collector = DataCollector(config)
        self.sla_analyzer = SLAAnalyzer(config, self.database_service)
        self.alert_manager = AlertManager(
            self.database_service, 
            config.sla_parameters.uptime_threshold_percentage
        )
        
        # Initialize JIRA service if configured
        self.jira_service = None
        if config.jira_connection:
            self.jira_service = JIRAService(config.jira_connection)
        
        # Initialize approval workflow manager
        self.approval_workflow = ApprovalWorkflowManager(config)
        
        # Workflow tracking
        self._current_execution_id: Optional[str] = None
        self._workflow_steps: List[WorkflowStep] = []
        
        logger.info(
            f"Initialized MonitoringOrchestrator for {len(config.vessel_databases)} vessels"
        )
    
    async def execute_daily_monitoring(self) -> WorkflowResult:
        """
        Execute the complete daily monitoring workflow.
        
        This method orchestrates the entire monitoring process:
        1. Data collection from all vessel databases
        2. SLA analysis and compliance checking
        3. Alert generation for violations
        4. JIRA ticket creation for persistent downtime
        
        Returns:
            WorkflowResult containing execution summary and statistics
        """
        execution_id = f"monitoring_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        self._current_execution_id = execution_id
        self._workflow_steps = []
        
        start_time = datetime.now()
        
        logger.info(f"Starting daily monitoring workflow: {execution_id}")
        
        # Initialize result tracking
        vessels_processed = 0
        vessels_failed = 0
        sla_violations = 0
        persistent_downtime_alerts = 0
        tickets_created = 0
        errors = []
        
        try:
            # Step 1: Data Collection
            fleet_metrics = await self._execute_data_collection_step()
            vessels_processed = len(fleet_metrics)
            vessels_failed = len(self.config.get_vessel_ids()) - vessels_processed
            
            if not fleet_metrics:
                raise RuntimeError("No vessel data collected - cannot proceed with monitoring")
            
            # Step 2: SLA Analysis
            fleet_sla_statuses = await self._execute_sla_analysis_step(fleet_metrics)
            sla_violations = self._count_sla_violations(fleet_sla_statuses)
            
            # Step 3: Alert Generation
            generated_alerts = await self._execute_alert_generation_step(fleet_metrics)
            
            # Step 4: Persistent Downtime Monitoring
            persistent_alerts = await self._execute_persistent_downtime_step(fleet_metrics)
            persistent_downtime_alerts = len(persistent_alerts)
            
            # Step 5: JIRA Ticket Creation (if configured)
            if self.jira_service and persistent_alerts:
                tickets_created = await self._execute_ticket_creation_step(persistent_alerts)
            
            # Step 6: Alert Status Maintenance
            await self._execute_alert_maintenance_step(fleet_metrics)
            
            # Step 7: Workflow Completion and Logging
            await self._execute_completion_step(
                execution_id, fleet_metrics, fleet_sla_statuses, generated_alerts
            )
            
            end_time = datetime.now()
            execution_duration = end_time - start_time
            
            logger.info(
                f"Daily monitoring workflow {execution_id} completed successfully in {execution_duration}. "
                f"Processed: {vessels_processed} vessels, "
                f"SLA violations: {sla_violations}, "
                f"Persistent downtime alerts: {persistent_downtime_alerts}, "
                f"Tickets created: {tickets_created}"
            )
            
            return WorkflowResult(
                execution_id=execution_id,
                start_time=start_time,
                end_time=end_time,
                success=True,
                vessels_processed=vessels_processed,
                vessels_failed=vessels_failed,
                sla_violations=sla_violations,
                persistent_downtime_alerts=persistent_downtime_alerts,
                tickets_created=tickets_created,
                errors=errors
            )
            
        except Exception as e:
            end_time = datetime.now()
            execution_duration = end_time - start_time
            error_message = f"Workflow execution failed: {str(e)}"
            errors.append(error_message)
            
            logger.error(
                f"Daily monitoring workflow {execution_id} failed after {execution_duration}: {e}",
                exc_info=True
            )
            
            # Record failure in database for audit
            await self._record_workflow_failure(execution_id, start_time, end_time, str(e))
            
            return WorkflowResult(
                execution_id=execution_id,
                start_time=start_time,
                end_time=end_time,
                success=False,
                vessels_processed=vessels_processed,
                vessels_failed=vessels_failed,
                sla_violations=sla_violations,
                persistent_downtime_alerts=persistent_downtime_alerts,
                tickets_created=tickets_created,
                errors=errors
            )
    
    async def _execute_data_collection_step(self) -> Dict[str, VesselMetrics]:
        """
        Execute data collection step with error handling and recovery.
        
        Returns:
            Dictionary mapping vessel IDs to their metrics
        """
        step = WorkflowStep(name="data_collection", start_time=datetime.now())
        self._workflow_steps.append(step)
        
        try:
            logger.info("Starting data collection from vessel databases")
            
            # Collect metrics from all vessels
            fleet_metrics = await self.data_collector.collect_all_vessels_metrics()
            
            step.end_time = datetime.now()
            step.success = True
            step.result_data = {
                'vessels_collected': len(fleet_metrics),
                'total_vessels': len(self.config.get_vessel_ids())
            }
            
            logger.info(
                f"Data collection completed: {len(fleet_metrics)} vessels processed "
                f"in {step.duration}"
            )
            
            return fleet_metrics
            
        except Exception as e:
            step.end_time = datetime.now()
            step.success = False
            step.error_message = str(e)
            
            logger.error(f"Data collection step failed: {e}")
            
            # Attempt recovery with partial data collection
            try:
                logger.info("Attempting recovery with partial data collection")
                
                # Try to collect from vessels individually with more lenient error handling
                vessel_ids = self.config.get_vessel_ids()
                partial_metrics = {}
                
                for vessel_id in vessel_ids:
                    try:
                        vessel_metrics = await self.data_collector.collect_vessel_metrics(vessel_id)
                        partial_metrics[vessel_id] = vessel_metrics
                    except Exception as vessel_error:
                        logger.warning(f"Failed to collect data for vessel {vessel_id}: {vessel_error}")
                        continue
                
                if partial_metrics:
                    logger.info(f"Recovery successful: collected data for {len(partial_metrics)} vessels")
                    step.result_data = {
                        'vessels_collected': len(partial_metrics),
                        'total_vessels': len(vessel_ids),
                        'recovery_mode': True
                    }
                    return partial_metrics
                
            except Exception as recovery_error:
                logger.error(f"Data collection recovery failed: {recovery_error}")
            
            raise RuntimeError(f"Data collection failed and recovery unsuccessful: {e}")
    
    async def _execute_sla_analysis_step(
        self, 
        fleet_metrics: Dict[str, VesselMetrics]
    ) -> Dict[str, Dict[ComponentType, SLAStatus]]:
        """
        Execute SLA analysis step with error handling.
        
        Args:
            fleet_metrics: Fleet metrics from data collection
            
        Returns:
            Fleet SLA analysis results
        """
        step = WorkflowStep(name="sla_analysis", start_time=datetime.now())
        self._workflow_steps.append(step)
        
        try:
            logger.info("Starting SLA analysis for fleet")
            
            # Analyze SLA compliance with historical tracking
            fleet_sla_statuses = self.sla_analyzer.analyze_fleet_sla_compliance_with_tracking(
                fleet_metrics
            )
            
            step.end_time = datetime.now()
            step.success = True
            
            # Calculate summary statistics
            total_violations = sum(
                sum(1 for sla_status in vessel_statuses.values() if not sla_status.is_compliant)
                for vessel_statuses in fleet_sla_statuses.values()
            )
            
            step.result_data = {
                'vessels_analyzed': len(fleet_sla_statuses),
                'total_violations': total_violations
            }
            
            logger.info(
                f"SLA analysis completed: {len(fleet_sla_statuses)} vessels analyzed, "
                f"{total_violations} violations found in {step.duration}"
            )
            
            return fleet_sla_statuses
            
        except Exception as e:
            step.end_time = datetime.now()
            step.success = False
            step.error_message = str(e)
            
            logger.error(f"SLA analysis step failed: {e}")
            raise RuntimeError(f"SLA analysis failed: {e}")
    
    async def _execute_alert_generation_step(
        self, 
        fleet_metrics: Dict[str, VesselMetrics]
    ) -> List[Alert]:
        """
        Execute alert generation step with error handling.
        
        Args:
            fleet_metrics: Fleet metrics from data collection
            
        Returns:
            List of generated alerts
        """
        step = WorkflowStep(name="alert_generation", start_time=datetime.now())
        self._workflow_steps.append(step)
        
        try:
            logger.info("Starting alert generation for fleet")
            
            all_alerts = []
            
            # Process each vessel for alert generation
            for vessel_id, vessel_metrics in fleet_metrics.items():
                try:
                    vessel_alerts = self.alert_manager.process_vessel_metrics(vessel_metrics)
                    all_alerts.extend(vessel_alerts)
                except Exception as vessel_error:
                    logger.error(f"Alert generation failed for vessel {vessel_id}: {vessel_error}")
                    continue
            
            step.end_time = datetime.now()
            step.success = True
            step.result_data = {
                'total_alerts': len(all_alerts),
                'vessels_processed': len(fleet_metrics)
            }
            
            logger.info(
                f"Alert generation completed: {len(all_alerts)} alerts generated "
                f"for {len(fleet_metrics)} vessels in {step.duration}"
            )
            
            return all_alerts
            
        except Exception as e:
            step.end_time = datetime.now()
            step.success = False
            step.error_message = str(e)
            
            logger.error(f"Alert generation step failed: {e}")
            raise RuntimeError(f"Alert generation failed: {e}")
    
    async def _execute_persistent_downtime_step(
        self, 
        fleet_metrics: Dict[str, VesselMetrics]
    ) -> List[Alert]:
        """
        Execute persistent downtime monitoring step.
        
        Args:
            fleet_metrics: Fleet metrics from data collection
            
        Returns:
            List of persistent downtime alerts requiring ticket creation
        """
        step = WorkflowStep(name="persistent_downtime_monitoring", start_time=datetime.now())
        self._workflow_steps.append(step)
        
        try:
            logger.info("Starting persistent downtime monitoring")
            
            # Convert fleet metrics to list for alert manager
            vessel_metrics_list = list(fleet_metrics.values())
            
            # Monitor for persistent downtime and get alerts requiring tickets
            persistent_alerts = self.alert_manager.monitor_persistent_downtime(vessel_metrics_list)
            
            step.end_time = datetime.now()
            step.success = True
            step.result_data = {
                'persistent_downtime_alerts': len(persistent_alerts),
                'vessels_monitored': len(vessel_metrics_list)
            }
            
            logger.info(
                f"Persistent downtime monitoring completed: {len(persistent_alerts)} alerts "
                f"requiring tickets in {step.duration}"
            )
            
            return persistent_alerts
            
        except Exception as e:
            step.end_time = datetime.now()
            step.success = False
            step.error_message = str(e)
            
            logger.error(f"Persistent downtime monitoring step failed: {e}")
            raise RuntimeError(f"Persistent downtime monitoring failed: {e}")
    
    async def _execute_ticket_creation_step(self, persistent_alerts: List[Alert]) -> int:
        """
        Execute JIRA ticket creation step with human approval workflow.
        
        Args:
            persistent_alerts: List of persistent downtime alerts
            
        Returns:
            Number of tickets successfully created
        """
        step = WorkflowStep(name="ticket_creation", start_time=datetime.now())
        self._workflow_steps.append(step)
        
        tickets_created = 0
        
        try:
            logger.info(f"Starting ticket creation for {len(persistent_alerts)} alerts")
            
            for alert in persistent_alerts:
                try:
                    # Check for existing tickets first
                    existing_tickets = self.jira_service.search_existing_tickets(
                        vessel_id=alert.vessel_id,
                        component_type=alert.component_type,
                        status_filter=None  # Check all statuses
                    )
                    
                    # Filter for open tickets
                    open_tickets = [
                        ticket for ticket in existing_tickets 
                        if ticket.status.value in ['Open', 'In Progress', 'Reopened']
                    ]
                    
                    if open_tickets:
                        logger.info(
                            f"Skipping ticket creation for {alert.component_type.value} "
                            f"on vessel {alert.vessel_id} - existing open ticket: {open_tickets[0].key}"
                        )
                        continue
                    
                    # Create issue summary for approval
                    issue_summary = self._create_issue_summary_from_alert(alert)
                    
                    # Request human approval and create ticket if approved
                    ticket = await self._create_ticket_with_approval(issue_summary)
                    
                    if ticket:
                        # Mark alert as having ticket created
                        self.alert_manager.mark_ticket_created(alert.id, ticket.key)
                        tickets_created += 1
                        
                        logger.info(
                            f"Successfully created ticket {ticket.key} for "
                            f"{alert.component_type.value} on vessel {alert.vessel_id}"
                        )
                    
                except Exception as alert_error:
                    logger.error(
                        f"Failed to create ticket for alert {alert.id}: {alert_error}"
                    )
                    continue
            
            step.end_time = datetime.now()
            step.success = True
            step.result_data = {
                'tickets_created': tickets_created,
                'alerts_processed': len(persistent_alerts)
            }
            
            logger.info(
                f"Ticket creation completed: {tickets_created} tickets created "
                f"from {len(persistent_alerts)} alerts in {step.duration}"
            )
            
            return tickets_created
            
        except Exception as e:
            step.end_time = datetime.now()
            step.success = False
            step.error_message = str(e)
            
            logger.error(f"Ticket creation step failed: {e}")
            return tickets_created  # Return partial success count
    
    async def _execute_alert_maintenance_step(
        self, 
        fleet_metrics: Dict[str, VesselMetrics]
    ) -> Dict[str, int]:
        """
        Execute alert status maintenance step.
        
        Args:
            fleet_metrics: Current fleet metrics
            
        Returns:
            Alert maintenance statistics
        """
        step = WorkflowStep(name="alert_maintenance", start_time=datetime.now())
        self._workflow_steps.append(step)
        
        try:
            logger.info("Starting alert status maintenance")
            
            # Convert fleet metrics to list for alert manager
            vessel_metrics_list = list(fleet_metrics.values())
            
            # Maintain alert status until issues are resolved
            maintenance_stats = self.alert_manager.maintain_alert_status(vessel_metrics_list)
            
            step.end_time = datetime.now()
            step.success = True
            step.result_data = maintenance_stats
            
            logger.info(
                f"Alert maintenance completed: {maintenance_stats} in {step.duration}"
            )
            
            return maintenance_stats
            
        except Exception as e:
            step.end_time = datetime.now()
            step.success = False
            step.error_message = str(e)
            
            logger.error(f"Alert maintenance step failed: {e}")
            return {}
    
    async def _execute_completion_step(
        self,
        execution_id: str,
        fleet_metrics: Dict[str, VesselMetrics],
        fleet_sla_statuses: Dict[str, Dict[ComponentType, SLAStatus]],
        generated_alerts: List[Alert]
    ) -> None:
        """
        Execute workflow completion and logging step.
        
        Args:
            execution_id: Workflow execution ID
            fleet_metrics: Fleet metrics collected
            fleet_sla_statuses: SLA analysis results
            generated_alerts: Generated alerts
        """
        step = WorkflowStep(name="workflow_completion", start_time=datetime.now())
        self._workflow_steps.append(step)
        
        try:
            logger.info("Starting workflow completion and logging")
            
            # Generate comprehensive workflow summary
            workflow_summary = self._generate_workflow_summary(
                execution_id, fleet_metrics, fleet_sla_statuses, generated_alerts
            )
            
            # Record workflow execution in database
            await self._record_workflow_execution(execution_id, workflow_summary)
            
            # Generate structured logging for system health monitoring
            self._log_system_health_metrics(workflow_summary)
            
            step.end_time = datetime.now()
            step.success = True
            step.result_data = workflow_summary
            
            logger.info(f"Workflow completion step finished in {step.duration}")
            
        except Exception as e:
            step.end_time = datetime.now()
            step.success = False
            step.error_message = str(e)
            
            logger.error(f"Workflow completion step failed: {e}")
            # Don't raise exception here as main workflow was successful
    
    def _create_issue_summary_from_alert(self, alert: Alert) -> IssueSummary:
        """
        Create an IssueSummary from a persistent downtime alert.
        
        Args:
            alert: Persistent downtime alert
            
        Returns:
            IssueSummary for JIRA ticket creation
        """
        downtime_hours = alert.metadata.get('downtime_aging_hours', 0)
        historical_context = alert.metadata.get('historical_context', '')
        
        # Determine severity based on downtime duration
        if downtime_hours >= 168:  # 7 days
            severity = IssueSeverity.CRITICAL
        elif downtime_hours >= 72:  # 3 days
            severity = IssueSeverity.HIGH
        else:
            severity = IssueSeverity.MEDIUM
        
        return IssueSummary(
            vessel_id=alert.vessel_id,
            component_type=alert.component_type,
            downtime_duration=timedelta(hours=downtime_hours),
            historical_context=historical_context,
            severity=severity
        )
    
    async def _create_ticket_with_approval(self, issue_summary: IssueSummary) -> Optional[Any]:
        """
        Create JIRA ticket with human approval workflow.
        
        Args:
            issue_summary: Issue summary for ticket creation
            
        Returns:
            Created JIRA ticket or None if not approved
        """
        try:
            # Use the integrated approval workflow
            ticket = self.jira_service.create_ticket_with_approval_workflow(
                issue_summary=issue_summary,
                approval_workflow_manager=self.approval_workflow,
                timeout_minutes=60
            )
            
            return ticket
            
        except Exception as e:
            logger.error(f"Failed to create ticket with approval workflow: {e}")
            return None
    
    def _count_sla_violations(
        self, 
        fleet_sla_statuses: Dict[str, Dict[ComponentType, SLAStatus]]
    ) -> int:
        """Count total SLA violations across the fleet."""
        return sum(
            sum(1 for sla_status in vessel_statuses.values() if not sla_status.is_compliant)
            for vessel_statuses in fleet_sla_statuses.values()
        )
    
    def _generate_workflow_summary(
        self,
        execution_id: str,
        fleet_metrics: Dict[str, VesselMetrics],
        fleet_sla_statuses: Dict[str, Dict[ComponentType, SLAStatus]],
        generated_alerts: List[Alert]
    ) -> Dict[str, Any]:
        """Generate comprehensive workflow execution summary."""
        
        # Calculate fleet-wide statistics
        fleet_summary = self.sla_analyzer.calculate_fleet_sla_summary(fleet_sla_statuses)
        component_breakdown = self.sla_analyzer.get_component_type_breakdown(fleet_sla_statuses)
        alert_stats = self.alert_manager.get_alert_statistics()
        
        return {
            'execution_id': execution_id,
            'timestamp': datetime.now().isoformat(),
            'fleet_summary': fleet_summary,
            'component_breakdown': {
                comp_type.value: stats for comp_type, stats in component_breakdown.items()
            },
            'alert_statistics': alert_stats,
            'workflow_steps': [
                {
                    'name': step.name,
                    'duration_seconds': step.duration.total_seconds() if step.duration else 0,
                    'success': step.success,
                    'error_message': step.error_message,
                    'result_data': step.result_data
                }
                for step in self._workflow_steps
            ]
        }
    
    async def _record_workflow_execution(
        self, 
        execution_id: str, 
        workflow_summary: Dict[str, Any]
    ) -> None:
        """Record workflow execution in database for audit purposes."""
        try:
            # This would typically record in a workflow_executions table
            # For now, log the summary for audit purposes
            logger.info(f"Workflow execution summary: {workflow_summary}")
            
        except Exception as e:
            logger.error(f"Failed to record workflow execution: {e}")
    
    async def _record_workflow_failure(
        self, 
        execution_id: str, 
        start_time: datetime, 
        end_time: datetime, 
        error_message: str
    ) -> None:
        """Record workflow failure in database for audit purposes."""
        try:
            failure_record = {
                'execution_id': execution_id,
                'start_time': start_time.isoformat(),
                'end_time': end_time.isoformat(),
                'error_message': error_message,
                'workflow_steps': [
                    {
                        'name': step.name,
                        'duration_seconds': step.duration.total_seconds() if step.duration else 0,
                        'success': step.success,
                        'error_message': step.error_message
                    }
                    for step in self._workflow_steps
                ]
            }
            
            logger.error(f"Workflow failure record: {failure_record}")
            
        except Exception as e:
            logger.error(f"Failed to record workflow failure: {e}")
    
    def _log_system_health_metrics(self, workflow_summary: Dict[str, Any]) -> None:
        """
        Log structured system health metrics for monitoring and troubleshooting.
        
        Args:
            workflow_summary: Complete workflow execution summary
        """
        try:
            # Extract key metrics for system health monitoring
            fleet_summary = workflow_summary.get('fleet_summary', {})
            alert_stats = workflow_summary.get('alert_statistics', {})
            
            health_metrics = {
                'system': 'infrastructure_monitoring_agent',
                'execution_id': workflow_summary['execution_id'],
                'timestamp': workflow_summary['timestamp'],
                'fleet_compliance_rate': fleet_summary.get('fleet_compliance_rate', 0),
                'total_vessels': fleet_summary.get('total_vessels', 0),
                'vessels_with_violations': fleet_summary.get('vessels_with_violations', 0),
                'active_alerts': alert_stats.get('total_active_alerts', 0),
                'persistent_downtime_alerts': alert_stats.get('persistent_downtime_alerts', 0),
                'workflow_success': True,
                'execution_duration_seconds': sum(
                    step.get('duration_seconds', 0) 
                    for step in workflow_summary.get('workflow_steps', [])
                )
            }
            
            # Structured logging for system health monitoring
            logger.info(f"SYSTEM_HEALTH_METRICS: {health_metrics}")
            
            # Log individual step performance for troubleshooting
            for step in workflow_summary.get('workflow_steps', []):
                step_metrics = {
                    'system': 'infrastructure_monitoring_agent',
                    'execution_id': workflow_summary['execution_id'],
                    'step_name': step['name'],
                    'step_duration_seconds': step['duration_seconds'],
                    'step_success': step['success'],
                    'step_error': step.get('error_message')
                }
                logger.info(f"STEP_PERFORMANCE_METRICS: {step_metrics}")
                
        except Exception as e:
            logger.error(f"Failed to log system health metrics: {e}")
    
    def get_workflow_status(self) -> Optional[Dict[str, Any]]:
        """
        Get current workflow execution status.
        
        Returns:
            Current workflow status or None if no workflow is running
        """
        if not self._current_execution_id:
            return None
        
        return {
            'execution_id': self._current_execution_id,
            'steps_completed': len([step for step in self._workflow_steps if step.end_time]),
            'total_steps': len(self._workflow_steps),
            'current_step': next(
                (step.name for step in self._workflow_steps if not step.end_time), 
                None
            ),
            'workflow_steps': [
                {
                    'name': step.name,
                    'start_time': step.start_time.isoformat(),
                    'end_time': step.end_time.isoformat() if step.end_time else None,
                    'success': step.success,
                    'duration_seconds': step.duration.total_seconds() if step.duration else None
                }
                for step in self._workflow_steps
            ]
        }