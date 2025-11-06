"""
Human Approval Workflow for Infrastructure Monitoring Agent.

This module provides a comprehensive human approval system for JIRA ticket creation,
including request presentation, response handling, timeout management, and audit logging.
"""

import logging
import json
import uuid
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, Callable
from dataclasses import dataclass, asdict
from enum import Enum
from pathlib import Path
import threading
import time
import requests

from ..models.data_models import IssueSummary
from .jira_service import ApprovalRequest, ApprovalStatus


class NotificationChannel(Enum):
    """Available notification channels for approval requests."""
    LOG = "log"
    EMAIL = "email"
    SLACK = "slack"
    WEBHOOK = "webhook"
    CONSOLE = "console"


@dataclass
class SlackConfig:
    """Slack integration configuration."""
    
    webhook_url: str
    channel: str = "#infrastructure-alerts"
    username: str = "Infrastructure Monitor"
    icon_emoji: str = ":warning:"
    
    def __post_init__(self):
        if not self.webhook_url or not self.webhook_url.strip():
            raise ValueError("Slack webhook URL cannot be empty")


@dataclass
class ApprovalWorkflowConfig:
    """Configuration for approval workflow."""
    
    default_timeout_minutes: int = 60
    max_pending_requests: int = 100
    notification_channels: List[NotificationChannel] = None
    audit_log_path: str = "approval_audit.log"
    auto_cleanup_hours: int = 24
    slack_config: Optional[SlackConfig] = None
    
    def __post_init__(self):
        if self.notification_channels is None:
            self.notification_channels = [NotificationChannel.LOG]


@dataclass
class ApprovalDecision:
    """Detailed approval decision with audit information."""
    
    request_id: str
    decision: ApprovalStatus
    approver_id: str
    approver_name: str
    decision_time: datetime
    comments: Optional[str] = None
    decision_method: str = "manual"  # manual, timeout, auto
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        data = asdict(self)
        data['decision'] = self.decision.value
        data['decision_time'] = self.decision_time.isoformat()
        return data
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'ApprovalDecision':
        """Create instance from dictionary."""
        data = data.copy()
        data['decision'] = ApprovalStatus(data['decision'])
        data['decision_time'] = datetime.fromisoformat(data['decision_time'])
        return cls(**data)


class ApprovalWorkflow:
    """
    Human approval workflow manager.
    
    Provides comprehensive approval workflow including:
    - Request presentation to human operators
    - Multiple notification channels
    - Timeout handling and retry mechanisms
    - Audit logging for compliance
    - Batch approval capabilities
    """
    
    def __init__(self, config: ApprovalWorkflowConfig):
        """
        Initialize approval workflow.
        
        Args:
            config: Workflow configuration
        """
        self.config = config
        self.logger = logging.getLogger(__name__)
        
        # In-memory storage for active requests
        self._pending_requests: Dict[str, ApprovalRequest] = {}
        self._completed_requests: Dict[str, ApprovalRequest] = {}
        self._approval_decisions: Dict[str, ApprovalDecision] = {}
        
        # Notification handlers
        self._notification_handlers: Dict[NotificationChannel, Callable] = {
            NotificationChannel.LOG: self._notify_via_log,
            NotificationChannel.CONSOLE: self._notify_via_console,
            NotificationChannel.SLACK: self._notify_via_slack,
        }
        
        # Setup audit logging
        self._setup_audit_logging()
        
        # Start background cleanup thread
        self._cleanup_thread = threading.Thread(target=self._cleanup_worker, daemon=True)
        self._cleanup_thread.start()
        
        self.logger.info("Approval workflow initialized")
    
    def submit_approval_request(
        self, 
        issue_summary: IssueSummary,
        priority: str = "normal",
        timeout_minutes: Optional[int] = None
    ) -> str:
        """
        Submit new approval request.
        
        Args:
            issue_summary: Issue requiring approval
            priority: Request priority (low, normal, high, urgent)
            timeout_minutes: Custom timeout, uses default if None
            
        Returns:
            Request ID for tracking
            
        Raises:
            ValueError: If too many pending requests
        """
        if len(self._pending_requests) >= self.config.max_pending_requests:
            raise ValueError(f"Too many pending requests ({len(self._pending_requests)})")
        
        request_id = str(uuid.uuid4())
        timeout_minutes = timeout_minutes or self.config.default_timeout_minutes
        
        approval_request = ApprovalRequest(
            request_id=request_id,
            issue_summary=issue_summary,
            status=ApprovalStatus.PENDING,
            requested_at=datetime.now()
        )
        
        self._pending_requests[request_id] = approval_request
        
        # Send notifications
        self._send_notifications(approval_request, priority)
        
        # Log audit entry
        self._log_audit_event("request_submitted", {
            "request_id": request_id,
            "vessel_id": issue_summary.vessel_id,
            "component_type": issue_summary.component_type.value,
            "severity": issue_summary.severity.value,
            "priority": priority,
            "timeout_minutes": timeout_minutes
        })
        
        self.logger.info(f"Submitted approval request {request_id} for vessel {issue_summary.vessel_id}")
        
        return request_id
    
    def get_pending_requests(self) -> List[ApprovalRequest]:
        """
        Get all pending approval requests.
        
        Returns:
            List of pending requests sorted by submission time
        """
        requests = list(self._pending_requests.values())
        return sorted(requests, key=lambda r: r.requested_at)
    
    def get_request_details(self, request_id: str) -> Optional[ApprovalRequest]:
        """
        Get detailed information for a specific request.
        
        Args:
            request_id: Request identifier
            
        Returns:
            Request details or None if not found
        """
        # Check pending requests first
        if request_id in self._pending_requests:
            return self._pending_requests[request_id]
        
        # Check completed requests
        if request_id in self._completed_requests:
            return self._completed_requests[request_id]
        
        return None
    
    def submit_approval_decision(
        self,
        request_id: str,
        approved: bool,
        approver_id: str,
        approver_name: str,
        comments: Optional[str] = None
    ) -> ApprovalDecision:
        """
        Submit approval decision for a request.
        
        Args:
            request_id: Request identifier
            approved: Whether request is approved
            approver_id: Unique identifier of approver
            approver_name: Display name of approver
            comments: Optional approval comments
            
        Returns:
            Approval decision record
            
        Raises:
            ValueError: If request not found or already decided
        """
        if request_id not in self._pending_requests:
            raise ValueError(f"Request {request_id} not found or already processed")
        
        request = self._pending_requests[request_id]
        
        # Update request status
        request.status = ApprovalStatus.APPROVED if approved else ApprovalStatus.REJECTED
        request.responded_at = datetime.now()
        request.approver = approver_name
        request.comments = comments
        
        # Create decision record
        decision = ApprovalDecision(
            request_id=request_id,
            decision=request.status,
            approver_id=approver_id,
            approver_name=approver_name,
            decision_time=datetime.now(),
            comments=comments,
            decision_method="manual"
        )
        
        # Move to completed requests
        self._completed_requests[request_id] = request
        self._approval_decisions[request_id] = decision
        del self._pending_requests[request_id]
        
        # Log audit entry
        self._log_audit_event("decision_submitted", {
            "request_id": request_id,
            "decision": decision.decision.value,
            "approver_id": approver_id,
            "approver_name": approver_name,
            "comments": comments,
            "vessel_id": request.issue_summary.vessel_id,
            "component_type": request.issue_summary.component_type.value
        })
        
        self.logger.info(
            f"Approval decision submitted for {request_id}: "
            f"{'APPROVED' if approved else 'REJECTED'} by {approver_name}"
        )
        
        return decision
    
    def check_timeouts(self) -> List[str]:
        """
        Check for timed out requests and mark them accordingly.
        
        Returns:
            List of request IDs that timed out
        """
        timed_out_requests = []
        current_time = datetime.now()
        
        for request_id, request in list(self._pending_requests.items()):
            timeout_threshold = request.requested_at + timedelta(minutes=self.config.default_timeout_minutes)
            
            if current_time > timeout_threshold:
                # Mark as timed out
                request.status = ApprovalStatus.TIMEOUT
                request.responded_at = current_time
                
                # Create timeout decision record
                decision = ApprovalDecision(
                    request_id=request_id,
                    decision=ApprovalStatus.TIMEOUT,
                    approver_id="system",
                    approver_name="System (Timeout)",
                    decision_time=current_time,
                    comments="Request timed out without response",
                    decision_method="timeout"
                )
                
                # Move to completed requests
                self._completed_requests[request_id] = request
                self._approval_decisions[request_id] = decision
                del self._pending_requests[request_id]
                
                timed_out_requests.append(request_id)
                
                # Log audit entry
                self._log_audit_event("request_timeout", {
                    "request_id": request_id,
                    "vessel_id": request.issue_summary.vessel_id,
                    "component_type": request.issue_summary.component_type.value,
                    "timeout_minutes": self.config.default_timeout_minutes
                })
        
        if timed_out_requests:
            self.logger.warning(f"Marked {len(timed_out_requests)} requests as timed out")
        
        return timed_out_requests
    
    def get_approval_statistics(self) -> Dict[str, Any]:
        """
        Get approval workflow statistics.
        
        Returns:
            Dictionary with workflow statistics
        """
        total_requests = len(self._completed_requests) + len(self._pending_requests)
        
        # Count decisions by type
        decision_counts = {
            "approved": 0,
            "rejected": 0,
            "timeout": 0,
            "pending": len(self._pending_requests)
        }
        
        for decision in self._approval_decisions.values():
            if decision.decision == ApprovalStatus.APPROVED:
                decision_counts["approved"] += 1
            elif decision.decision == ApprovalStatus.REJECTED:
                decision_counts["rejected"] += 1
            elif decision.decision == ApprovalStatus.TIMEOUT:
                decision_counts["timeout"] += 1
        
        # Calculate average response time for completed requests
        response_times = []
        for request in self._completed_requests.values():
            if request.responded_at and request.status != ApprovalStatus.TIMEOUT:
                response_time = (request.responded_at - request.requested_at).total_seconds() / 60
                response_times.append(response_time)
        
        avg_response_time = sum(response_times) / len(response_times) if response_times else 0
        
        return {
            "total_requests": total_requests,
            "decision_counts": decision_counts,
            "average_response_time_minutes": round(avg_response_time, 2),
            "oldest_pending_request": self._get_oldest_pending_request_age()
        }
    
    def format_request_for_display(self, request: ApprovalRequest) -> str:
        """
        Format approval request for human-readable display.
        
        Args:
            request: Approval request to format
            
        Returns:
            Formatted request string
        """
        issue = request.issue_summary
        
        return f"""
APPROVAL REQUEST: {request.request_id}
=====================================
Vessel ID: {issue.vessel_id}
Component: {issue.component_type.value.title()}
Severity: {issue.severity.value.title()}
Downtime Duration: {issue._format_duration()}
Requested: {request.requested_at.strftime('%Y-%m-%d %H:%M:%S')}

Issue Description:
{issue.get_description()}

Historical Context:
{issue.historical_context}

Status: {request.status.value.upper()}
=====================================
"""
    
    def _send_notifications(self, request: ApprovalRequest, priority: str):
        """Send notifications through configured channels."""
        for channel in self.config.notification_channels:
            if channel in self._notification_handlers:
                try:
                    self._notification_handlers[channel](request, priority)
                except Exception as e:
                    self.logger.error(f"Failed to send notification via {channel.value}: {e}")
    
    def _notify_via_log(self, request: ApprovalRequest, priority: str):
        """Send notification via logging."""
        message = f"APPROVAL REQUIRED [{priority.upper()}]: {request.request_id} - " \
                 f"Vessel {request.issue_summary.vessel_id} " \
                 f"{request.issue_summary.component_type.value.title()} down for " \
                 f"{request.issue_summary._format_duration()}"
        
        if priority in ["high", "urgent"]:
            self.logger.warning(message)
        else:
            self.logger.info(message)
    
    def _notify_via_console(self, request: ApprovalRequest, priority: str):
        """Send notification via console output."""
        formatted_request = self.format_request_for_display(request)
        print(f"\n{'='*50}")
        print(f"APPROVAL REQUIRED [{priority.upper()}]")
        print(formatted_request)
        print(f"{'='*50}\n")
    
    def _notify_via_slack(self, request: ApprovalRequest, priority: str):
        """Send notification via Slack with interactive approval buttons."""
        if not self.config.slack_config:
            self.logger.warning("Slack notification requested but no Slack config provided")
            return
        
        try:
            issue = request.issue_summary
            
            # Determine color based on priority and severity
            color_map = {
                "urgent": "#ff0000",    # Red
                "high": "#ff8c00",      # Orange
                "normal": "#ffff00",    # Yellow
                "low": "#00ff00"        # Green
            }
            color = color_map.get(priority, "#ffff00")
            
            # Create Slack message with interactive buttons
            slack_payload = {
                "channel": self.config.slack_config.channel,
                "username": self.config.slack_config.username,
                "icon_emoji": self.config.slack_config.icon_emoji,
                "attachments": [
                    {
                        "color": color,
                        "title": f"🚨 Infrastructure Alert - Approval Required [{priority.upper()}]",
                        "title_link": f"#approval-{request.request_id}",
                        "fields": [
                            {
                                "title": "Vessel ID",
                                "value": issue.vessel_id,
                                "short": True
                            },
                            {
                                "title": "Component",
                                "value": issue.component_type.value.title(),
                                "short": True
                            },
                            {
                                "title": "Severity",
                                "value": issue.severity.value.title(),
                                "short": True
                            },
                            {
                                "title": "Downtime Duration",
                                "value": issue._format_duration(),
                                "short": True
                            },
                            {
                                "title": "Request ID",
                                "value": request.request_id,
                                "short": False
                            },
                            {
                                "title": "Historical Context",
                                "value": issue.historical_context[:500] + ("..." if len(issue.historical_context) > 500 else ""),
                                "short": False
                            }
                        ],
                        "actions": [
                            {
                                "type": "button",
                                "text": "✅ Approve Ticket",
                                "style": "primary",
                                "name": "approve",
                                "value": request.request_id,
                                "confirm": {
                                    "title": "Approve JIRA Ticket Creation",
                                    "text": f"Create JIRA ticket for Vessel {issue.vessel_id} {issue.component_type.value.title()} issue?",
                                    "ok_text": "Yes, Create Ticket",
                                    "dismiss_text": "Cancel"
                                }
                            },
                            {
                                "type": "button",
                                "text": "❌ Reject",
                                "style": "danger",
                                "name": "reject",
                                "value": request.request_id,
                                "confirm": {
                                    "title": "Reject JIRA Ticket Creation",
                                    "text": "Are you sure you want to reject this ticket creation request?",
                                    "ok_text": "Yes, Reject",
                                    "dismiss_text": "Cancel"
                                }
                            },
                            {
                                "type": "button",
                                "text": "ℹ️ More Details",
                                "name": "details",
                                "value": request.request_id
                            }
                        ],
                        "footer": "Infrastructure Monitoring Agent",
                        "ts": int(request.requested_at.timestamp())
                    }
                ]
            }
            
            # Send to Slack
            response = requests.post(
                self.config.slack_config.webhook_url,
                json=slack_payload,
                timeout=10
            )
            response.raise_for_status()
            
            self.logger.info(f"Sent Slack notification for approval request {request.request_id}")
            
        except Exception as e:
            self.logger.error(f"Failed to send Slack notification: {e}")
    
    def handle_slack_interaction(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Handle Slack interactive button responses.
        
        Args:
            payload: Slack interaction payload
            
        Returns:
            Response message for Slack
        """
        try:
            # Parse Slack payload
            user = payload.get('user', {})
            user_id = user.get('id', 'unknown')
            user_name = user.get('name', 'Unknown User')
            
            actions = payload.get('actions', [])
            if not actions:
                return {"text": "No action specified"}
            
            action = actions[0]
            action_name = action.get('name')
            request_id = action.get('value')
            
            if action_name == "approve":
                decision = self.submit_approval_decision(
                    request_id=request_id,
                    approved=True,
                    approver_id=user_id,
                    approver_name=user_name,
                    comments="Approved via Slack"
                )
                
                return {
                    "text": f"✅ Ticket creation approved by {user_name}",
                    "response_type": "in_channel",
                    "replace_original": True,
                    "attachments": [
                        {
                            "color": "good",
                            "text": f"JIRA ticket creation approved for request {request_id}",
                            "footer": f"Approved by {user_name} at {decision.decision_time.strftime('%Y-%m-%d %H:%M:%S')}"
                        }
                    ]
                }
            
            elif action_name == "reject":
                decision = self.submit_approval_decision(
                    request_id=request_id,
                    approved=False,
                    approver_id=user_id,
                    approver_name=user_name,
                    comments="Rejected via Slack"
                )
                
                return {
                    "text": f"❌ Ticket creation rejected by {user_name}",
                    "response_type": "in_channel",
                    "replace_original": True,
                    "attachments": [
                        {
                            "color": "danger",
                            "text": f"JIRA ticket creation rejected for request {request_id}",
                            "footer": f"Rejected by {user_name} at {decision.decision_time.strftime('%Y-%m-%d %H:%M:%S')}"
                        }
                    ]
                }
            
            elif action_name == "details":
                request = self.get_request_details(request_id)
                if not request:
                    return {"text": "Request not found"}
                
                detailed_info = self.format_request_for_display(request)
                
                return {
                    "text": f"Detailed information for request {request_id}:",
                    "response_type": "ephemeral",
                    "attachments": [
                        {
                            "color": "#36a64f",
                            "text": f"```{detailed_info}```",
                            "mrkdwn_in": ["text"]
                        }
                    ]
                }
            
            else:
                return {"text": f"Unknown action: {action_name}"}
                
        except ValueError as e:
            self.logger.error(f"Slack interaction error: {e}")
            return {
                "text": f"Error processing request: {str(e)}",
                "response_type": "ephemeral"
            }
        except Exception as e:
            self.logger.error(f"Unexpected error handling Slack interaction: {e}")
            return {
                "text": "An unexpected error occurred. Please try again.",
                "response_type": "ephemeral"
            }
    
    def _setup_audit_logging(self):
        """Setup audit logging for approval decisions."""
        self.audit_logger = logging.getLogger(f"{__name__}.audit")
        
        # Create audit log handler if it doesn't exist
        audit_handler = logging.FileHandler(self.config.audit_log_path)
        audit_formatter = logging.Formatter(
            '%(asctime)s - %(levelname)s - %(message)s'
        )
        audit_handler.setFormatter(audit_formatter)
        
        self.audit_logger.addHandler(audit_handler)
        self.audit_logger.setLevel(logging.INFO)
    
    def _log_audit_event(self, event_type: str, event_data: Dict[str, Any]):
        """Log audit event."""
        audit_entry = {
            "event_type": event_type,
            "timestamp": datetime.now().isoformat(),
            "data": event_data
        }
        
        self.audit_logger.info(json.dumps(audit_entry))
    
    def _get_oldest_pending_request_age(self) -> Optional[float]:
        """Get age of oldest pending request in minutes."""
        if not self._pending_requests:
            return None
        
        oldest_request = min(self._pending_requests.values(), key=lambda r: r.requested_at)
        age_minutes = (datetime.now() - oldest_request.requested_at).total_seconds() / 60
        return round(age_minutes, 2)
    
    def _cleanup_worker(self):
        """Background worker for cleanup tasks."""
        while True:
            try:
                # Check for timeouts
                self.check_timeouts()
                
                # Clean up old completed requests
                self._cleanup_old_requests()
                
                # Sleep for 5 minutes before next check
                time.sleep(300)
                
            except Exception as e:
                self.logger.error(f"Cleanup worker error: {e}")
                time.sleep(60)  # Wait 1 minute before retrying
    
    def _cleanup_old_requests(self):
        """Clean up old completed requests."""
        cutoff_time = datetime.now() - timedelta(hours=self.config.auto_cleanup_hours)
        
        old_request_ids = [
            req_id for req_id, req in self._completed_requests.items()
            if req.requested_at < cutoff_time
        ]
        
        for req_id in old_request_ids:
            del self._completed_requests[req_id]
            if req_id in self._approval_decisions:
                del self._approval_decisions[req_id]
        
        if old_request_ids:
            self.logger.info(f"Cleaned up {len(old_request_ids)} old approval requests")


class ApprovalWorkflowManager:
    """
    High-level manager for approval workflow integration.
    
    Provides simplified interface for integration with JIRA service
    and other system components.
    """
    
    def __init__(self, workflow: ApprovalWorkflow):
        """
        Initialize workflow manager.
        
        Args:
            workflow: Approval workflow instance
        """
        self.workflow = workflow
        self.logger = logging.getLogger(__name__)
    
    def request_ticket_approval(
        self, 
        issue_summary: IssueSummary,
        timeout_minutes: Optional[int] = None
    ) -> str:
        """
        Request approval for JIRA ticket creation.
        
        Args:
            issue_summary: Issue requiring ticket creation
            timeout_minutes: Custom timeout
            
        Returns:
            Request ID for tracking
        """
        # Determine priority based on severity and downtime duration
        priority = self._determine_priority(issue_summary)
        
        return self.workflow.submit_approval_request(
            issue_summary=issue_summary,
            priority=priority,
            timeout_minutes=timeout_minutes
        )
    
    def wait_for_approval(
        self, 
        request_id: str, 
        poll_interval_seconds: int = 30,
        max_wait_minutes: Optional[int] = None
    ) -> ApprovalStatus:
        """
        Wait for approval decision with polling.
        
        Args:
            request_id: Request to wait for
            poll_interval_seconds: How often to check status
            max_wait_minutes: Maximum time to wait
            
        Returns:
            Final approval status
        """
        start_time = datetime.now()
        max_wait_time = timedelta(minutes=max_wait_minutes) if max_wait_minutes else None
        
        while True:
            request = self.workflow.get_request_details(request_id)
            
            if not request:
                raise ValueError(f"Request {request_id} not found")
            
            if request.status != ApprovalStatus.PENDING:
                return request.status
            
            # Check if we've exceeded max wait time
            if max_wait_time and (datetime.now() - start_time) > max_wait_time:
                self.logger.warning(f"Stopped waiting for approval {request_id} - max wait time exceeded")
                return ApprovalStatus.TIMEOUT
            
            time.sleep(poll_interval_seconds)
    
    def _determine_priority(self, issue_summary: IssueSummary) -> str:
        """Determine request priority based on issue characteristics."""
        # High priority for critical severity or long downtime
        if (issue_summary.severity.value == "critical" or 
            issue_summary.downtime_duration.days >= 7):
            return "urgent"
        elif (issue_summary.severity.value == "high" or 
              issue_summary.downtime_duration.days >= 5):
            return "high"
        elif issue_summary.downtime_duration.days >= 3:
            return "normal"
        else:
            return "low"