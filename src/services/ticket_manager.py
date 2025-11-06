"""
Integrated Ticket Management Service with Slack Approval Workflow.

This module provides a high-level service that combines JIRA integration
with Slack-based human approval workflow for automated ticket management.
"""

import logging
import threading
from typing import Optional, List, Tuple
from datetime import datetime

from ..models.data_models import IssueSummary, ComponentType
from ..models.enums import IssueSeverity
from ..config.config_models import Config
from .jira_service import JIRAService, JIRATicket, TicketStatus
from .approval_workflow import (
    ApprovalWorkflow, 
    ApprovalWorkflowManager, 
    ApprovalWorkflowConfig,
    SlackConfig,
    NotificationChannel
)
from .slack_webhook_handler import SlackWebhookHandler
from .ticket_lifecycle_manager import (
    TicketLifecycleManager, 
    DuplicatePreventionRule,
    TicketLifecycleStatus
)


class TicketManagerError(Exception):
    """Base exception for ticket manager errors."""
    pass


class TicketManager:
    """
    Integrated ticket management service.
    
    Combines JIRA integration with Slack approval workflow to provide
    automated ticket creation with human oversight and approval.
    """
    
    def __init__(self, config: Config):
        """
        Initialize ticket manager with configuration.
        
        Args:
            config: System configuration including JIRA and Slack settings
        """
        self.config = config
        self.logger = logging.getLogger(__name__)
        
        # Initialize JIRA service
        if not config.jira_connection:
            raise TicketManagerError("JIRA connection configuration required")
        
        self.jira_service = JIRAService(config.jira_connection)
        
        # Initialize ticket lifecycle manager
        duplicate_rules = DuplicatePreventionRule(
            time_window_hours=24,
            allow_severity_escalation=True,
            max_tickets_per_component=3
        )
        self.lifecycle_manager = TicketLifecycleManager(
            database_path=config.database_path,
            duplicate_rules=duplicate_rules
        )
        
        # Initialize approval workflow
        self._setup_approval_workflow()
        
        # Initialize Slack webhook handler if Slack is configured
        self.slack_handler: Optional[SlackWebhookHandler] = None
        if config.slack_config:
            self._setup_slack_integration()
        
        self.logger.info("Ticket manager initialized")
    
    def _setup_approval_workflow(self):
        """Setup approval workflow with appropriate configuration."""
        # Configure notification channels
        notification_channels = [NotificationChannel.LOG]
        
        if self.config.slack_config:
            notification_channels.append(NotificationChannel.SLACK)
        
        # Create workflow configuration
        workflow_config = ApprovalWorkflowConfig(
            default_timeout_minutes=60,
            max_pending_requests=100,
            notification_channels=notification_channels,
            audit_log_path="approval_audit.log",
            auto_cleanup_hours=24
        )
        
        # Add Slack config if available
        if self.config.slack_config:
            workflow_config.slack_config = SlackConfig(
                webhook_url=self.config.slack_config.webhook_url,
                channel=self.config.slack_config.channel,
                username=self.config.slack_config.username,
                icon_emoji=self.config.slack_config.icon_emoji
            )
        
        # Create workflow and manager
        self.approval_workflow = ApprovalWorkflow(workflow_config)
        self.approval_manager = ApprovalWorkflowManager(self.approval_workflow)
    
    def _setup_slack_integration(self):
        """Setup Slack webhook handler for interactive approvals."""
        try:
            self.slack_handler = SlackWebhookHandler(
                approval_workflow=self.approval_workflow,
                slack_signing_secret=self.config.slack_config.signing_secret
            )
            
            # Start Slack webhook server in background thread
            webhook_thread = threading.Thread(
                target=self._run_slack_webhook_server,
                daemon=True
            )
            webhook_thread.start()
            
            self.logger.info("Slack integration initialized")
            
        except Exception as e:
            self.logger.error(f"Failed to setup Slack integration: {e}")
            self.slack_handler = None
    
    def _run_slack_webhook_server(self):
        """Run Slack webhook server in background thread."""
        try:
            self.slack_handler.run(
                host='0.0.0.0',
                port=self.config.slack_config.webhook_port,
                debug=False
            )
        except Exception as e:
            self.logger.error(f"Slack webhook server error: {e}")
    
    def check_existing_tickets(
        self, 
        vessel_id: str, 
        component_type: ComponentType
    ) -> List[JIRATicket]:
        """
        Check for existing tickets for vessel and component.
        
        Args:
            vessel_id: Vessel identifier
            component_type: Component type
            
        Returns:
            List of existing tickets
        """
        try:
            # Search for open tickets only
            open_statuses = [TicketStatus.OPEN, TicketStatus.IN_PROGRESS, TicketStatus.REOPENED]
            
            tickets = self.jira_service.search_existing_tickets(
                vessel_id=vessel_id,
                component_type=component_type,
                status_filter=open_statuses
            )
            
            self.logger.info(f"Found {len(tickets)} existing tickets for {vessel_id} {component_type.value}")
            return tickets
            
        except Exception as e:
            self.logger.error(f"Error checking existing tickets: {e}")
            raise TicketManagerError(f"Failed to check existing tickets: {e}")
    
    def check_for_duplicates_with_rules(
        self, 
        vessel_id: str, 
        component_type: ComponentType,
        issue_severity: IssueSeverity
    ) -> Tuple[bool, List]:
        """
        Check for duplicate tickets using advanced prevention rules.
        
        Args:
            vessel_id: Vessel identifier
            component_type: Component type
            issue_severity: Issue severity
            
        Returns:
            Tuple of (is_duplicate, existing_ticket_records)
        """
        try:
            return self.lifecycle_manager.check_for_duplicates(
                vessel_id=vessel_id,
                component_type=component_type,
                issue_severity=issue_severity
            )
        except Exception as e:
            self.logger.error(f"Error checking for duplicates with rules: {e}")
            return False, []
    
    def create_ticket_with_approval(
        self, 
        issue_summary: IssueSummary,
        timeout_minutes: Optional[int] = None,
        skip_duplicate_check: bool = False,
        alert_id: Optional[str] = None
    ) -> Optional[JIRATicket]:
        """
        Create JIRA ticket with human approval workflow and lifecycle tracking.
        
        Args:
            issue_summary: Issue summary for ticket creation
            timeout_minutes: Custom approval timeout
            skip_duplicate_check: Skip checking for existing tickets
            alert_id: Optional alert ID to link with ticket
            
        Returns:
            Created JIRA ticket if approved, None if rejected/timeout
            
        Raises:
            TicketManagerError: If ticket creation process fails
        """
        try:
            # Advanced duplicate checking with rules
            if not skip_duplicate_check:
                is_duplicate, existing_records = self.check_for_duplicates_with_rules(
                    vessel_id=issue_summary.vessel_id,
                    component_type=issue_summary.component_type,
                    issue_severity=issue_summary.severity
                )
                
                if is_duplicate:
                    self.logger.info(
                        f"Duplicate ticket detected for {issue_summary.vessel_id} "
                        f"{issue_summary.component_type.value}. Found {len(existing_records)} existing tickets."
                    )
                    
                    # Link alert to existing ticket if provided
                    if alert_id and existing_records:
                        latest_ticket = existing_records[0]  # Most recent ticket
                        self.link_ticket_to_alert(latest_ticket.jira_key, alert_id)
                    
                    return None
            
            # Create ticket with approval workflow
            ticket = self.jira_service.create_ticket_with_approval_workflow(
                issue_summary=issue_summary,
                approval_workflow_manager=self.approval_manager,
                timeout_minutes=timeout_minutes
            )
            
            if ticket:
                # Create lifecycle record
                ticket_record = self.lifecycle_manager.create_ticket_record(
                    jira_ticket=ticket,
                    issue_summary=issue_summary
                )
                
                # Link to alert if provided
                if alert_id:
                    self.link_ticket_to_alert(ticket.key, alert_id)
                
                self.logger.info(f"Successfully created ticket {ticket.key} with lifecycle tracking")
            else:
                self.logger.info("Ticket creation was not approved or timed out")
            
            return ticket
            
        except Exception as e:
            self.logger.error(f"Error creating ticket with approval: {e}")
            raise TicketManagerError(f"Failed to create ticket: {e}")
    
    def update_ticket_status(
        self, 
        ticket_key: str, 
        new_status: TicketStatus,
        resolution_notes: Optional[str] = None
    ) -> JIRATicket:
        """
        Update JIRA ticket status with lifecycle tracking.
        
        Args:
            ticket_key: JIRA ticket key
            new_status: New ticket status
            resolution_notes: Optional resolution notes
            
        Returns:
            Updated JIRA ticket
        """
        try:
            # Update JIRA ticket
            updated_ticket = self.jira_service.update_ticket_status(ticket_key, new_status)
            
            # Update lifecycle status
            lifecycle_status_map = {
                TicketStatus.OPEN: TicketLifecycleStatus.CREATED,
                TicketStatus.IN_PROGRESS: TicketLifecycleStatus.IN_PROGRESS,
                TicketStatus.RESOLVED: TicketLifecycleStatus.RESOLVED,
                TicketStatus.CLOSED: TicketLifecycleStatus.CLOSED,
                TicketStatus.REOPENED: TicketLifecycleStatus.REOPENED
            }
            
            if new_status in lifecycle_status_map:
                self.lifecycle_manager.update_ticket_lifecycle_status(
                    ticket_key=ticket_key,
                    new_status=lifecycle_status_map[new_status],
                    resolution_notes=resolution_notes
                )
            
            return updated_ticket
            
        except Exception as e:
            self.logger.error(f"Error updating ticket status: {e}")
            raise TicketManagerError(f"Failed to update ticket status: {e}")
    
    def link_ticket_to_alert(self, ticket_key: str, alert_id: str) -> bool:
        """
        Link ticket to alert for tracking.
        
        Args:
            ticket_key: JIRA ticket key
            alert_id: Alert identifier
            
        Returns:
            True if linked successfully
        """
        try:
            return self.lifecycle_manager.link_ticket_to_alert(ticket_key, alert_id)
        except Exception as e:
            self.logger.error(f"Error linking ticket to alert: {e}")
            return False
    
    def get_ticket_details(self, ticket_key: str) -> JIRATicket:
        """
        Get detailed information for a JIRA ticket.
        
        Args:
            ticket_key: JIRA ticket key
            
        Returns:
            JIRA ticket details
        """
        try:
            return self.jira_service.get_ticket_details(ticket_key)
        except Exception as e:
            self.logger.error(f"Error getting ticket details: {e}")
            raise TicketManagerError(f"Failed to get ticket details: {e}")
    
    def get_approval_statistics(self) -> dict:
        """
        Get approval workflow statistics.
        
        Returns:
            Dictionary with approval statistics
        """
        try:
            return self.approval_workflow.get_approval_statistics()
        except Exception as e:
            self.logger.error(f"Error getting approval statistics: {e}")
            return {"error": str(e)}
    
    def get_pending_approvals(self) -> list:
        """
        Get list of pending approval requests.
        
        Returns:
            List of pending approval requests
        """
        try:
            pending_requests = self.approval_workflow.get_pending_requests()
            return [
                {
                    "request_id": req.request_id,
                    "vessel_id": req.issue_summary.vessel_id,
                    "component_type": req.issue_summary.component_type.value,
                    "severity": req.issue_summary.severity.value,
                    "downtime_duration": req.issue_summary._format_duration(),
                    "requested_at": req.requested_at.isoformat(),
                    "status": req.status.value
                }
                for req in pending_requests
            ]
        except Exception as e:
            self.logger.error(f"Error getting pending approvals: {e}")
            return []
    
    def get_tickets_by_vessel_component(
        self, 
        vessel_id: str, 
        component_type: ComponentType,
        include_closed: bool = False
    ) -> List[dict]:
        """
        Get tickets for specific vessel and component.
        
        Args:
            vessel_id: Vessel identifier
            component_type: Component type
            include_closed: Include closed/resolved tickets
            
        Returns:
            List of ticket information
        """
        try:
            status_filter = None
            if not include_closed:
                status_filter = [
                    TicketLifecycleStatus.CREATED,
                    TicketLifecycleStatus.LINKED_TO_ALERT,
                    TicketLifecycleStatus.IN_PROGRESS,
                    TicketLifecycleStatus.REOPENED
                ]
            
            ticket_records = self.lifecycle_manager.get_tickets_by_vessel_component(
                vessel_id=vessel_id,
                component_type=component_type,
                status_filter=status_filter
            )
            
            return [
                {
                    "jira_key": record.jira_key,
                    "vessel_id": record.vessel_id,
                    "component_type": record.component_type.value,
                    "severity": record.issue_severity.value,
                    "lifecycle_status": record.lifecycle_status.value,
                    "created_at": record.created_at.isoformat(),
                    "updated_at": record.updated_at.isoformat(),
                    "alert_count": len(record.alert_ids),
                    "downtime_hours": round(record.downtime_duration_seconds / 3600, 2)
                }
                for record in ticket_records
            ]
            
        except Exception as e:
            self.logger.error(f"Error getting tickets by vessel/component: {e}")
            return []
    
    def get_tickets_by_alert(self, alert_id: str) -> List[dict]:
        """
        Get tickets linked to specific alert.
        
        Args:
            alert_id: Alert identifier
            
        Returns:
            List of linked ticket information
        """
        try:
            ticket_records = self.lifecycle_manager.get_tickets_by_alert(alert_id)
            
            return [
                {
                    "jira_key": record.jira_key,
                    "vessel_id": record.vessel_id,
                    "component_type": record.component_type.value,
                    "severity": record.issue_severity.value,
                    "lifecycle_status": record.lifecycle_status.value,
                    "created_at": record.created_at.isoformat()
                }
                for record in ticket_records
            ]
            
        except Exception as e:
            self.logger.error(f"Error getting tickets by alert: {e}")
            return []
    
    def get_ticket_lifecycle_statistics(self) -> dict:
        """
        Get comprehensive ticket lifecycle statistics.
        
        Returns:
            Dictionary with lifecycle statistics
        """
        try:
            return self.lifecycle_manager.get_lifecycle_statistics()
        except Exception as e:
            self.logger.error(f"Error getting lifecycle statistics: {e}")
            return {"error": str(e)}
    
    def test_connections(self) -> dict:
        """
        Test all external service connections.
        
        Returns:
            Dictionary with connection test results
        """
        results = {}
        
        # Test JIRA connection
        try:
            results["jira"] = self.jira_service.test_connection()
        except Exception as e:
            results["jira"] = False
            results["jira_error"] = str(e)
        
        # Test Slack integration
        if self.slack_handler:
            results["slack"] = True
            results["slack_webhook_port"] = self.config.slack_config.webhook_port
        else:
            results["slack"] = False
            results["slack_error"] = "Slack not configured"
        
        return results
    
    def cleanup(self):
        """Cleanup resources and background threads."""
        try:
            # Cleanup approval workflow
            if hasattr(self.approval_workflow, '_cleanup_thread'):
                # The cleanup thread is daemon, so it will stop when main thread stops
                pass
            
            self.logger.info("Ticket manager cleanup completed")
            
        except Exception as e:
            self.logger.error(f"Error during cleanup: {e}")


def create_ticket_manager(config: Config) -> TicketManager:
    """
    Factory function to create ticket manager.
    
    Args:
        config: System configuration
        
    Returns:
        Configured ticket manager
    """
    return TicketManager(config)