"""
JIRA Integration Service for Infrastructure Monitoring Agent.

This module provides JIRA API integration for automated ticket creation,
duplicate prevention, and ticket lifecycle management with human approval workflow.
"""

import logging
import json
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass, asdict
from enum import Enum
import requests
from requests.auth import HTTPBasicAuth
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from ..models.data_models import IssueSummary, ComponentType
from ..models.enums import IssueSeverity
from ..config.config_models import JIRAConnection


class TicketStatus(Enum):
    """JIRA ticket status enumeration."""
    OPEN = "Open"
    IN_PROGRESS = "In Progress"
    RESOLVED = "Resolved"
    CLOSED = "Closed"
    REOPENED = "Reopened"


class ApprovalStatus(Enum):
    """Human approval status enumeration."""
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    TIMEOUT = "timeout"


@dataclass
class JIRATicket:
    """JIRA ticket representation."""
    
    key: str
    id: str
    summary: str
    description: str
    status: TicketStatus
    created: datetime
    updated: datetime
    vessel_id: str
    component_type: ComponentType
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        data = asdict(self)
        data['created'] = self.created.isoformat()
        data['updated'] = self.updated.isoformat()
        data['component_type'] = self.component_type.value
        data['status'] = self.status.value
        return data
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'JIRATicket':
        """Create instance from dictionary."""
        data = data.copy()
        data['created'] = datetime.fromisoformat(data['created'])
        data['updated'] = datetime.fromisoformat(data['updated'])
        data['component_type'] = ComponentType(data['component_type'])
        data['status'] = TicketStatus(data['status'])
        return cls(**data)


@dataclass
class ApprovalRequest:
    """Human approval request for ticket creation."""
    
    request_id: str
    issue_summary: IssueSummary
    status: ApprovalStatus
    requested_at: datetime
    responded_at: Optional[datetime] = None
    approver: Optional[str] = None
    comments: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        data = asdict(self)
        data['issue_summary'] = self.issue_summary.to_dict()
        data['requested_at'] = self.requested_at.isoformat()
        data['status'] = self.status.value
        if self.responded_at:
            data['responded_at'] = self.responded_at.isoformat()
        return data
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'ApprovalRequest':
        """Create instance from dictionary."""
        data = data.copy()
        data['issue_summary'] = IssueSummary.from_dict(data['issue_summary'])
        data['requested_at'] = datetime.fromisoformat(data['requested_at'])
        data['status'] = ApprovalStatus(data['status'])
        if data.get('responded_at'):
            data['responded_at'] = datetime.fromisoformat(data['responded_at'])
        return cls(**data)


class JIRAServiceError(Exception):
    """Base exception for JIRA service errors."""
    pass


class JIRAAuthenticationError(JIRAServiceError):
    """JIRA authentication error."""
    pass


class JIRAAPIError(JIRAServiceError):
    """JIRA API error."""
    pass


class JIRAService:
    """
    JIRA integration service for automated ticket management.
    
    Provides functionality for:
    - Searching existing tickets with vessel and component filters
    - Creating tickets with comprehensive issue descriptions
    - Managing ticket lifecycle and status tracking
    - Human approval workflow for ticket creation
    """
    
    def __init__(self, jira_connection: JIRAConnection):
        """
        Initialize JIRA service with connection configuration.
        
        Args:
            jira_connection: JIRA connection configuration
        """
        self.connection = jira_connection
        self.logger = logging.getLogger(__name__)
        
        # Setup HTTP session with retry strategy
        self.session = requests.Session()
        retry_strategy = Retry(
            total=3,
            backoff_factor=1,
            status_forcelist=[429, 500, 502, 503, 504],
        )
        adapter = HTTPAdapter(max_retries=retry_strategy)
        self.session.mount("http://", adapter)
        self.session.mount("https://", adapter)
        
        # Setup authentication
        self.session.auth = HTTPBasicAuth(
            self.connection.username,
            self.connection.api_token
        )
        
        # Setup headers
        self.session.headers.update({
            'Accept': 'application/json',
            'Content-Type': 'application/json'
        })
        
        # In-memory storage for approval requests (in production, use database)
        self._approval_requests: Dict[str, ApprovalRequest] = {}
        
        self.logger.info(f"JIRA service initialized for {self.connection.url}")
    
    def test_connection(self) -> bool:
        """
        Test JIRA connection and authentication.
        
        Returns:
            True if connection is successful, False otherwise
        """
        try:
            response = self.session.get(f"{self.connection.url}/rest/api/2/myself")
            response.raise_for_status()
            
            user_info = response.json()
            self.logger.info(f"JIRA connection successful. User: {user_info.get('displayName')}")
            return True
            
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 401:
                self.logger.error("JIRA authentication failed")
                raise JIRAAuthenticationError("Invalid JIRA credentials")
            else:
                self.logger.error(f"JIRA API error: {e}")
                raise JIRAAPIError(f"JIRA API error: {e}")
        except Exception as e:
            self.logger.error(f"JIRA connection test failed: {e}")
            return False
    
    def search_existing_tickets(
        self, 
        vessel_id: str, 
        component_type: ComponentType,
        status_filter: Optional[List[TicketStatus]] = None
    ) -> List[JIRATicket]:
        """
        Search for existing tickets using vessel and component filter criteria.
        
        Args:
            vessel_id: Vessel identifier
            component_type: Component type to search for
            status_filter: Optional list of ticket statuses to filter by
            
        Returns:
            List of matching JIRA tickets
            
        Raises:
            JIRAServiceError: If search fails
        """
        try:
            # Build JQL query
            jql_parts = [
                f'project = "{self.connection.project_key}"',
                f'summary ~ "Vessel {vessel_id}"',
                f'summary ~ "{component_type.value.title()}"'
            ]
            
            if status_filter:
                status_names = [status.value for status in status_filter]
                status_clause = ', '.join(f'"{status}"' for status in status_names)
                jql_parts.append(f'status in ({status_clause})')
            
            jql = ' AND '.join(jql_parts)
            
            # Execute search
            search_url = f"{self.connection.url}/rest/api/2/search"
            params = {
                'jql': jql,
                'fields': 'key,id,summary,description,status,created,updated',
                'maxResults': 100
            }
            
            response = self.session.get(search_url, params=params)
            response.raise_for_status()
            
            search_results = response.json()
            tickets = []
            
            for issue in search_results.get('issues', []):
                try:
                    ticket = self._parse_jira_issue(issue, vessel_id, component_type)
                    tickets.append(ticket)
                except Exception as e:
                    self.logger.warning(f"Failed to parse JIRA issue {issue.get('key')}: {e}")
            
            self.logger.info(f"Found {len(tickets)} existing tickets for vessel {vessel_id}, component {component_type.value}")
            return tickets
            
        except requests.exceptions.RequestException as e:
            self.logger.error(f"JIRA search failed: {e}")
            raise JIRAServiceError(f"Failed to search JIRA tickets: {e}")
    
    def request_human_approval(
        self, 
        issue_summary: IssueSummary,
        timeout_minutes: int = 60
    ) -> str:
        """
        Create approval request for human operator review.
        
        Args:
            issue_summary: Issue summary requiring approval
            timeout_minutes: Timeout for approval request
            
        Returns:
            Request ID for tracking approval status
        """
        request_id = f"approval_{issue_summary.vessel_id}_{issue_summary.component_type.value}_{int(time.time())}"
        
        approval_request = ApprovalRequest(
            request_id=request_id,
            issue_summary=issue_summary,
            status=ApprovalStatus.PENDING,
            requested_at=datetime.now()
        )
        
        self._approval_requests[request_id] = approval_request
        
        # Log approval request for human operator
        self.logger.info(
            f"APPROVAL REQUEST {request_id}: "
            f"Vessel {issue_summary.vessel_id} - {issue_summary.component_type.value.title()} "
            f"down for {issue_summary._format_duration()}. "
            f"Severity: {issue_summary.severity.value.title()}"
        )
        
        # In a real implementation, this would trigger notification to human operators
        # (email, Slack, dashboard alert, etc.)
        
        return request_id
    
    def check_approval_status(self, request_id: str) -> ApprovalRequest:
        """
        Check status of approval request.
        
        Args:
            request_id: Approval request ID
            
        Returns:
            Current approval request status
            
        Raises:
            JIRAServiceError: If request ID not found
        """
        if request_id not in self._approval_requests:
            raise JIRAServiceError(f"Approval request {request_id} not found")
        
        approval_request = self._approval_requests[request_id]
        
        # Check for timeout
        if (approval_request.status == ApprovalStatus.PENDING and 
            datetime.now() - approval_request.requested_at > timedelta(hours=1)):
            approval_request.status = ApprovalStatus.TIMEOUT
            approval_request.responded_at = datetime.now()
            self.logger.warning(f"Approval request {request_id} timed out")
        
        return approval_request
    
    def submit_approval_response(
        self, 
        request_id: str, 
        approved: bool, 
        approver: str,
        comments: Optional[str] = None
    ) -> ApprovalRequest:
        """
        Submit human approval response.
        
        Args:
            request_id: Approval request ID
            approved: Whether the request is approved
            approver: Name/ID of the approver
            comments: Optional approval comments
            
        Returns:
            Updated approval request
            
        Raises:
            JIRAServiceError: If request ID not found or already responded
        """
        if request_id not in self._approval_requests:
            raise JIRAServiceError(f"Approval request {request_id} not found")
        
        approval_request = self._approval_requests[request_id]
        
        if approval_request.status != ApprovalStatus.PENDING:
            raise JIRAServiceError(f"Approval request {request_id} already responded to")
        
        approval_request.status = ApprovalStatus.APPROVED if approved else ApprovalStatus.REJECTED
        approval_request.responded_at = datetime.now()
        approval_request.approver = approver
        approval_request.comments = comments
        
        self.logger.info(
            f"Approval request {request_id} {'approved' if approved else 'rejected'} "
            f"by {approver}"
        )
        
        return approval_request
    
    def create_ticket(self, approved_issue: IssueSummary) -> JIRATicket:
        """
        Create JIRA ticket with comprehensive issue description.
        
        Args:
            approved_issue: Approved issue summary for ticket creation
            
        Returns:
            Created JIRA ticket
            
        Raises:
            JIRAServiceError: If ticket creation fails
        """
        try:
            # Prepare ticket data
            ticket_data = {
                'fields': {
                    'project': {'key': self.connection.project_key},
                    'summary': approved_issue.get_title(),
                    'description': approved_issue.get_description(),
                    'issuetype': {'name': self.connection.issue_type},
                    'priority': self._get_jira_priority(approved_issue.severity),
                    'labels': [
                        f'vessel-{approved_issue.vessel_id}',
                        f'component-{approved_issue.component_type.value}',
                        'infrastructure-monitoring',
                        'automated'
                    ]
                }
            }
            
            # Create ticket
            create_url = f"{self.connection.url}/rest/api/2/issue"
            response = self.session.post(create_url, json=ticket_data)
            response.raise_for_status()
            
            created_issue = response.json()
            
            # Fetch full ticket details
            ticket_key = created_issue['key']
            ticket_details = self._get_ticket_details(ticket_key)
            
            self.logger.info(f"Created JIRA ticket {ticket_key} for vessel {approved_issue.vessel_id}")
            
            return ticket_details
            
        except requests.exceptions.RequestException as e:
            self.logger.error(f"JIRA ticket creation failed: {e}")
            raise JIRAServiceError(f"Failed to create JIRA ticket: {e}")
    
    def update_ticket_status(self, ticket_key: str, new_status: TicketStatus) -> JIRATicket:
        """
        Update JIRA ticket status.
        
        Args:
            ticket_key: JIRA ticket key
            new_status: New ticket status
            
        Returns:
            Updated JIRA ticket
            
        Raises:
            JIRAServiceError: If status update fails
        """
        try:
            # Get available transitions
            transitions_url = f"{self.connection.url}/rest/api/2/issue/{ticket_key}/transitions"
            response = self.session.get(transitions_url)
            response.raise_for_status()
            
            transitions = response.json()['transitions']
            
            # Find transition to desired status
            target_transition = None
            for transition in transitions:
                if transition['to']['name'] == new_status.value:
                    target_transition = transition
                    break
            
            if not target_transition:
                available_statuses = [t['to']['name'] for t in transitions]
                raise JIRAServiceError(
                    f"Cannot transition ticket {ticket_key} to {new_status.value}. "
                    f"Available transitions: {available_statuses}"
                )
            
            # Execute transition
            transition_data = {
                'transition': {'id': target_transition['id']}
            }
            
            response = self.session.post(transitions_url, json=transition_data)
            response.raise_for_status()
            
            # Fetch updated ticket details
            updated_ticket = self._get_ticket_details(ticket_key)
            
            self.logger.info(f"Updated ticket {ticket_key} status to {new_status.value}")
            
            return updated_ticket
            
        except requests.exceptions.RequestException as e:
            self.logger.error(f"JIRA ticket status update failed: {e}")
            raise JIRAServiceError(f"Failed to update ticket status: {e}")
    
    def get_ticket_details(self, ticket_key: str) -> JIRATicket:
        """
        Get detailed information for a JIRA ticket.
        
        Args:
            ticket_key: JIRA ticket key
            
        Returns:
            JIRA ticket details
            
        Raises:
            JIRAServiceError: If ticket retrieval fails
        """
        return self._get_ticket_details(ticket_key)
    
    def _get_ticket_details(self, ticket_key: str) -> JIRATicket:
        """Internal method to fetch ticket details."""
        try:
            ticket_url = f"{self.connection.url}/rest/api/2/issue/{ticket_key}"
            params = {
                'fields': 'key,id,summary,description,status,created,updated'
            }
            
            response = self.session.get(ticket_url, params=params)
            response.raise_for_status()
            
            issue_data = response.json()
            
            # Extract vessel ID and component type from summary
            summary = issue_data['fields']['summary']
            vessel_id = self._extract_vessel_id_from_summary(summary)
            component_type = self._extract_component_type_from_summary(summary)
            
            return self._parse_jira_issue(issue_data, vessel_id, component_type)
            
        except requests.exceptions.RequestException as e:
            self.logger.error(f"Failed to fetch ticket details for {ticket_key}: {e}")
            raise JIRAServiceError(f"Failed to fetch ticket details: {e}")
    
    def _parse_jira_issue(
        self, 
        issue_data: Dict[str, Any], 
        vessel_id: str, 
        component_type: ComponentType
    ) -> JIRATicket:
        """Parse JIRA issue data into JIRATicket object."""
        fields = issue_data['fields']
        
        return JIRATicket(
            key=issue_data['key'],
            id=issue_data['id'],
            summary=fields['summary'],
            description=fields.get('description', ''),
            status=TicketStatus(fields['status']['name']),
            created=datetime.fromisoformat(fields['created'].replace('Z', '+00:00')),
            updated=datetime.fromisoformat(fields['updated'].replace('Z', '+00:00')),
            vessel_id=vessel_id,
            component_type=component_type
        )
    
    def _get_jira_priority(self, severity: IssueSeverity) -> Dict[str, str]:
        """Map issue severity to JIRA priority."""
        priority_map = {
            IssueSeverity.LOW: 'Low',
            IssueSeverity.MEDIUM: 'Medium',
            IssueSeverity.HIGH: 'High',
            IssueSeverity.CRITICAL: 'Highest'
        }
        return {'name': priority_map.get(severity, 'Medium')}
    
    def _extract_vessel_id_from_summary(self, summary: str) -> str:
        """Extract vessel ID from ticket summary."""
        # Simple extraction - assumes format "Vessel {vessel_id} - ..."
        import re
        match = re.search(r'Vessel (\w+)', summary)
        return match.group(1) if match else 'unknown'
    
    def _extract_component_type_from_summary(self, summary: str) -> ComponentType:
        """Extract component type from ticket summary."""
        summary_lower = summary.lower()
        if 'access point' in summary_lower or 'access_point' in summary_lower:
            return ComponentType.ACCESS_POINT
        elif 'dashboard' in summary_lower:
            return ComponentType.DASHBOARD
        elif 'server' in summary_lower:
            return ComponentType.SERVER
        else:
            return ComponentType.SERVER  # Default fallback
    
    def get_approval_requests(self, status: Optional[ApprovalStatus] = None) -> List[ApprovalRequest]:
        """
        Get all approval requests, optionally filtered by status.
        
        Args:
            status: Optional status filter
            
        Returns:
            List of approval requests
        """
        requests = list(self._approval_requests.values())
        
        if status:
            requests = [req for req in requests if req.status == status]
        
        return requests
    
    def create_ticket_with_approval_workflow(
        self, 
        issue_summary: IssueSummary,
        approval_workflow_manager,
        timeout_minutes: Optional[int] = None
    ) -> Optional[JIRATicket]:
        """
        Create JIRA ticket with integrated approval workflow.
        
        Args:
            issue_summary: Issue summary for ticket creation
            approval_workflow_manager: Approval workflow manager instance
            timeout_minutes: Custom approval timeout
            
        Returns:
            Created JIRA ticket if approved, None if rejected/timeout
            
        Raises:
            JIRAServiceError: If ticket creation fails after approval
        """
        try:
            # Request approval
            request_id = approval_workflow_manager.request_ticket_approval(
                issue_summary=issue_summary,
                timeout_minutes=timeout_minutes
            )
            
            self.logger.info(f"Requested approval for ticket creation: {request_id}")
            
            # Wait for approval decision
            approval_status = approval_workflow_manager.wait_for_approval(
                request_id=request_id,
                max_wait_minutes=timeout_minutes
            )
            
            if approval_status == ApprovalStatus.APPROVED:
                # Create ticket
                ticket = self.create_ticket(issue_summary)
                self.logger.info(f"Created approved ticket {ticket.key} for request {request_id}")
                return ticket
            
            elif approval_status == ApprovalStatus.REJECTED:
                self.logger.info(f"Ticket creation rejected for request {request_id}")
                return None
            
            else:  # TIMEOUT
                self.logger.warning(f"Ticket creation timed out for request {request_id}")
                return None
                
        except Exception as e:
            self.logger.error(f"Failed to create ticket with approval workflow: {e}")
            raise JIRAServiceError(f"Ticket creation with approval failed: {e}")
    
    def cleanup_old_approval_requests(self, max_age_hours: int = 24) -> int:
        """
        Clean up old approval requests.
        
        Args:
            max_age_hours: Maximum age of requests to keep
            
        Returns:
            Number of requests cleaned up
        """
        cutoff_time = datetime.now() - timedelta(hours=max_age_hours)
        
        old_requests = [
            req_id for req_id, req in self._approval_requests.items()
            if req.requested_at < cutoff_time
        ]
        
        for req_id in old_requests:
            del self._approval_requests[req_id]
        
        self.logger.info(f"Cleaned up {len(old_requests)} old approval requests")
        return len(old_requests)