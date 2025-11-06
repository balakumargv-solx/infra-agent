"""
Ticket Lifecycle Management for Infrastructure Monitoring Agent.

This module provides comprehensive ticket lifecycle tracking, duplicate prevention,
and integration with alert system for complete ticket management.
"""

import logging
import sqlite3
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass, asdict
from enum import Enum
import json

from ..models.data_models import IssueSummary, ComponentType
from ..models.enums import IssueSeverity
from .jira_service import JIRATicket, TicketStatus


class TicketLifecycleStatus(Enum):
    """Ticket lifecycle status enumeration."""
    CREATED = "created"
    LINKED_TO_ALERT = "linked_to_alert"
    IN_PROGRESS = "in_progress"
    RESOLVED = "resolved"
    CLOSED = "closed"
    REOPENED = "reopened"


@dataclass
class TicketRecord:
    """Internal ticket record for lifecycle tracking."""
    
    id: Optional[int]
    jira_key: str
    jira_id: str
    vessel_id: str
    component_type: ComponentType
    issue_severity: IssueSeverity
    lifecycle_status: TicketLifecycleStatus
    created_at: datetime
    updated_at: datetime
    alert_ids: List[str]  # Associated alert IDs
    downtime_duration_seconds: float
    historical_context: str
    resolution_notes: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        data = asdict(self)
        data['created_at'] = self.created_at.isoformat()
        data['updated_at'] = self.updated_at.isoformat()
        data['component_type'] = self.component_type.value
        data['issue_severity'] = self.issue_severity.value
        data['lifecycle_status'] = self.lifecycle_status.value
        data['alert_ids'] = json.dumps(self.alert_ids)
        return data
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'TicketRecord':
        """Create instance from dictionary."""
        data = data.copy()
        data['created_at'] = datetime.fromisoformat(data['created_at'])
        data['updated_at'] = datetime.fromisoformat(data['updated_at'])
        data['component_type'] = ComponentType(data['component_type'])
        data['issue_severity'] = IssueSeverity(data['issue_severity'])
        data['lifecycle_status'] = TicketLifecycleStatus(data['lifecycle_status'])
        data['alert_ids'] = json.loads(data['alert_ids']) if data['alert_ids'] else []
        return cls(**data)


class DuplicatePreventionRule:
    """Rules for preventing duplicate ticket creation."""
    
    def __init__(
        self,
        time_window_hours: int = 24,
        allow_severity_escalation: bool = True,
        max_tickets_per_component: int = 3
    ):
        """
        Initialize duplicate prevention rules.
        
        Args:
            time_window_hours: Time window for duplicate detection
            allow_severity_escalation: Allow new tickets for higher severity
            max_tickets_per_component: Maximum open tickets per component
        """
        self.time_window_hours = time_window_hours
        self.allow_severity_escalation = allow_severity_escalation
        self.max_tickets_per_component = max_tickets_per_component


class TicketLifecycleManager:
    """
    Comprehensive ticket lifecycle management system.
    
    Provides:
    - Duplicate prevention with configurable rules
    - Ticket lifecycle tracking from creation to resolution
    - Integration with alert system
    - Historical analysis and reporting
    """
    
    def __init__(self, database_path: str, duplicate_rules: Optional[DuplicatePreventionRule] = None):
        """
        Initialize ticket lifecycle manager.
        
        Args:
            database_path: Path to SQLite database
            duplicate_rules: Rules for duplicate prevention
        """
        self.database_path = database_path
        self.duplicate_rules = duplicate_rules or DuplicatePreventionRule()
        self.logger = logging.getLogger(__name__)
        
        # Initialize database
        self._init_database()
        
        self.logger.info("Ticket lifecycle manager initialized")
    
    def _init_database(self):
        """Initialize SQLite database schema."""
        try:
            with sqlite3.connect(self.database_path) as conn:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS ticket_records (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        jira_key TEXT NOT NULL UNIQUE,
                        jira_id TEXT NOT NULL,
                        vessel_id TEXT NOT NULL,
                        component_type TEXT NOT NULL,
                        issue_severity TEXT NOT NULL,
                        lifecycle_status TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        alert_ids TEXT,
                        downtime_duration_seconds REAL NOT NULL,
                        historical_context TEXT,
                        resolution_notes TEXT,
                        INDEX(vessel_id, component_type),
                        INDEX(lifecycle_status),
                        INDEX(created_at)
                    )
                """)
                
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS alert_ticket_links (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        alert_id TEXT NOT NULL,
                        ticket_id INTEGER NOT NULL,
                        linked_at TEXT NOT NULL,
                        FOREIGN KEY(ticket_id) REFERENCES ticket_records(id),
                        UNIQUE(alert_id, ticket_id)
                    )
                """)
                
                conn.commit()
                
        except Exception as e:
            self.logger.error(f"Failed to initialize database: {e}")
            raise
    
    def check_for_duplicates(
        self, 
        vessel_id: str, 
        component_type: ComponentType,
        issue_severity: IssueSeverity
    ) -> Tuple[bool, List[TicketRecord]]:
        """
        Check for duplicate tickets based on prevention rules.
        
        Args:
            vessel_id: Vessel identifier
            component_type: Component type
            issue_severity: Issue severity
            
        Returns:
            Tuple of (is_duplicate, existing_tickets)
        """
        try:
            # Get existing tickets within time window
            cutoff_time = datetime.now() - timedelta(hours=self.duplicate_rules.time_window_hours)
            
            with sqlite3.connect(self.database_path) as conn:
                conn.row_factory = sqlite3.Row
                
                # Find existing open tickets for same vessel/component
                cursor = conn.execute("""
                    SELECT * FROM ticket_records 
                    WHERE vessel_id = ? 
                    AND component_type = ? 
                    AND lifecycle_status IN ('created', 'linked_to_alert', 'in_progress', 'reopened')
                    AND created_at > ?
                    ORDER BY created_at DESC
                """, (vessel_id, component_type.value, cutoff_time.isoformat()))
                
                existing_tickets = [
                    TicketRecord.from_dict(dict(row)) 
                    for row in cursor.fetchall()
                ]
            
            # Apply duplicate prevention rules
            if not existing_tickets:
                return False, []
            
            # Check maximum tickets per component
            if len(existing_tickets) >= self.duplicate_rules.max_tickets_per_component:
                self.logger.info(
                    f"Maximum tickets ({self.duplicate_rules.max_tickets_per_component}) "
                    f"reached for {vessel_id} {component_type.value}"
                )
                return True, existing_tickets
            
            # Check severity escalation rule
            if self.duplicate_rules.allow_severity_escalation:
                # Allow new ticket if severity is higher than existing ones
                max_existing_severity = max(
                    ticket.issue_severity 
                    for ticket in existing_tickets
                )
                
                severity_order = {
                    IssueSeverity.LOW: 1,
                    IssueSeverity.MEDIUM: 2,
                    IssueSeverity.HIGH: 3,
                    IssueSeverity.CRITICAL: 4
                }
                
                if severity_order[issue_severity] > severity_order[max_existing_severity]:
                    self.logger.info(
                        f"Allowing new ticket due to severity escalation: "
                        f"{issue_severity.value} > {max_existing_severity.value}"
                    )
                    return False, existing_tickets
            
            # Default: consider as duplicate
            return True, existing_tickets
            
        except Exception as e:
            self.logger.error(f"Error checking for duplicates: {e}")
            return False, []
    
    def create_ticket_record(
        self, 
        jira_ticket: JIRATicket, 
        issue_summary: IssueSummary
    ) -> TicketRecord:
        """
        Create ticket record for lifecycle tracking.
        
        Args:
            jira_ticket: Created JIRA ticket
            issue_summary: Original issue summary
            
        Returns:
            Created ticket record
        """
        try:
            ticket_record = TicketRecord(
                id=None,
                jira_key=jira_ticket.key,
                jira_id=jira_ticket.id,
                vessel_id=issue_summary.vessel_id,
                component_type=issue_summary.component_type,
                issue_severity=issue_summary.severity,
                lifecycle_status=TicketLifecycleStatus.CREATED,
                created_at=datetime.now(),
                updated_at=datetime.now(),
                alert_ids=[],
                downtime_duration_seconds=issue_summary.downtime_duration.total_seconds(),
                historical_context=issue_summary.historical_context
            )
            
            # Save to database
            with sqlite3.connect(self.database_path) as conn:
                cursor = conn.execute("""
                    INSERT INTO ticket_records (
                        jira_key, jira_id, vessel_id, component_type, issue_severity,
                        lifecycle_status, created_at, updated_at, alert_ids,
                        downtime_duration_seconds, historical_context
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    ticket_record.jira_key,
                    ticket_record.jira_id,
                    ticket_record.vessel_id,
                    ticket_record.component_type.value,
                    ticket_record.issue_severity.value,
                    ticket_record.lifecycle_status.value,
                    ticket_record.created_at.isoformat(),
                    ticket_record.updated_at.isoformat(),
                    json.dumps(ticket_record.alert_ids),
                    ticket_record.downtime_duration_seconds,
                    ticket_record.historical_context
                ))
                
                ticket_record.id = cursor.lastrowid
                conn.commit()
            
            self.logger.info(f"Created ticket record for {ticket_record.jira_key}")
            return ticket_record
            
        except Exception as e:
            self.logger.error(f"Error creating ticket record: {e}")
            raise
    
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
            with sqlite3.connect(self.database_path) as conn:
                # Get ticket record
                cursor = conn.execute(
                    "SELECT id, alert_ids FROM ticket_records WHERE jira_key = ?",
                    (ticket_key,)
                )
                row = cursor.fetchone()
                
                if not row:
                    self.logger.warning(f"Ticket record not found: {ticket_key}")
                    return False
                
                ticket_id, alert_ids_json = row
                alert_ids = json.loads(alert_ids_json) if alert_ids_json else []
                
                # Add alert ID if not already linked
                if alert_id not in alert_ids:
                    alert_ids.append(alert_id)
                    
                    # Update ticket record
                    conn.execute("""
                        UPDATE ticket_records 
                        SET alert_ids = ?, 
                            lifecycle_status = ?,
                            updated_at = ?
                        WHERE jira_key = ?
                    """, (
                        json.dumps(alert_ids),
                        TicketLifecycleStatus.LINKED_TO_ALERT.value,
                        datetime.now().isoformat(),
                        ticket_key
                    ))
                    
                    # Create alert-ticket link
                    conn.execute("""
                        INSERT OR IGNORE INTO alert_ticket_links 
                        (alert_id, ticket_id, linked_at) 
                        VALUES (?, ?, ?)
                    """, (alert_id, ticket_id, datetime.now().isoformat()))
                    
                    conn.commit()
                    
                    self.logger.info(f"Linked ticket {ticket_key} to alert {alert_id}")
                
                return True
                
        except Exception as e:
            self.logger.error(f"Error linking ticket to alert: {e}")
            return False
    
    def update_ticket_lifecycle_status(
        self, 
        ticket_key: str, 
        new_status: TicketLifecycleStatus,
        resolution_notes: Optional[str] = None
    ) -> bool:
        """
        Update ticket lifecycle status.
        
        Args:
            ticket_key: JIRA ticket key
            new_status: New lifecycle status
            resolution_notes: Optional resolution notes
            
        Returns:
            True if updated successfully
        """
        try:
            with sqlite3.connect(self.database_path) as conn:
                update_data = [
                    new_status.value,
                    datetime.now().isoformat(),
                    ticket_key
                ]
                
                if resolution_notes:
                    conn.execute("""
                        UPDATE ticket_records 
                        SET lifecycle_status = ?, updated_at = ?, resolution_notes = ?
                        WHERE jira_key = ?
                    """, [new_status.value, datetime.now().isoformat(), resolution_notes, ticket_key])
                else:
                    conn.execute("""
                        UPDATE ticket_records 
                        SET lifecycle_status = ?, updated_at = ?
                        WHERE jira_key = ?
                    """, update_data)
                
                conn.commit()
                
                if conn.total_changes > 0:
                    self.logger.info(f"Updated ticket {ticket_key} status to {new_status.value}")
                    return True
                else:
                    self.logger.warning(f"Ticket record not found: {ticket_key}")
                    return False
                
        except Exception as e:
            self.logger.error(f"Error updating ticket lifecycle status: {e}")
            return False
    
    def get_ticket_record(self, ticket_key: str) -> Optional[TicketRecord]:
        """
        Get ticket record by JIRA key.
        
        Args:
            ticket_key: JIRA ticket key
            
        Returns:
            Ticket record or None if not found
        """
        try:
            with sqlite3.connect(self.database_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.execute(
                    "SELECT * FROM ticket_records WHERE jira_key = ?",
                    (ticket_key,)
                )
                row = cursor.fetchone()
                
                if row:
                    return TicketRecord.from_dict(dict(row))
                return None
                
        except Exception as e:
            self.logger.error(f"Error getting ticket record: {e}")
            return None
    
    def get_tickets_by_vessel_component(
        self, 
        vessel_id: str, 
        component_type: ComponentType,
        status_filter: Optional[List[TicketLifecycleStatus]] = None
    ) -> List[TicketRecord]:
        """
        Get tickets for specific vessel and component.
        
        Args:
            vessel_id: Vessel identifier
            component_type: Component type
            status_filter: Optional status filter
            
        Returns:
            List of matching ticket records
        """
        try:
            with sqlite3.connect(self.database_path) as conn:
                conn.row_factory = sqlite3.Row
                
                if status_filter:
                    status_values = [status.value for status in status_filter]
                    placeholders = ','.join('?' * len(status_values))
                    query = f"""
                        SELECT * FROM ticket_records 
                        WHERE vessel_id = ? AND component_type = ? 
                        AND lifecycle_status IN ({placeholders})
                        ORDER BY created_at DESC
                    """
                    params = [vessel_id, component_type.value] + status_values
                else:
                    query = """
                        SELECT * FROM ticket_records 
                        WHERE vessel_id = ? AND component_type = ?
                        ORDER BY created_at DESC
                    """
                    params = [vessel_id, component_type.value]
                
                cursor = conn.execute(query, params)
                return [TicketRecord.from_dict(dict(row)) for row in cursor.fetchall()]
                
        except Exception as e:
            self.logger.error(f"Error getting tickets by vessel/component: {e}")
            return []
    
    def get_tickets_by_alert(self, alert_id: str) -> List[TicketRecord]:
        """
        Get tickets linked to specific alert.
        
        Args:
            alert_id: Alert identifier
            
        Returns:
            List of linked ticket records
        """
        try:
            with sqlite3.connect(self.database_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.execute("""
                    SELECT tr.* FROM ticket_records tr
                    JOIN alert_ticket_links atl ON tr.id = atl.ticket_id
                    WHERE atl.alert_id = ?
                    ORDER BY tr.created_at DESC
                """, (alert_id,))
                
                return [TicketRecord.from_dict(dict(row)) for row in cursor.fetchall()]
                
        except Exception as e:
            self.logger.error(f"Error getting tickets by alert: {e}")
            return []
    
    def get_lifecycle_statistics(self) -> Dict[str, Any]:
        """
        Get ticket lifecycle statistics.
        
        Returns:
            Dictionary with lifecycle statistics
        """
        try:
            with sqlite3.connect(self.database_path) as conn:
                # Count tickets by status
                cursor = conn.execute("""
                    SELECT lifecycle_status, COUNT(*) as count
                    FROM ticket_records
                    GROUP BY lifecycle_status
                """)
                status_counts = {row[0]: row[1] for row in cursor.fetchall()}
                
                # Count tickets by vessel
                cursor = conn.execute("""
                    SELECT vessel_id, COUNT(*) as count
                    FROM ticket_records
                    GROUP BY vessel_id
                    ORDER BY count DESC
                    LIMIT 10
                """)
                top_vessels = [{"vessel_id": row[0], "count": row[1]} for row in cursor.fetchall()]
                
                # Count tickets by component type
                cursor = conn.execute("""
                    SELECT component_type, COUNT(*) as count
                    FROM ticket_records
                    GROUP BY component_type
                """)
                component_counts = {row[0]: row[1] for row in cursor.fetchall()}
                
                # Average resolution time for resolved tickets
                cursor = conn.execute("""
                    SELECT AVG(
                        (julianday(updated_at) - julianday(created_at)) * 24 * 60
                    ) as avg_resolution_minutes
                    FROM ticket_records
                    WHERE lifecycle_status IN ('resolved', 'closed')
                """)
                avg_resolution = cursor.fetchone()[0] or 0
                
                return {
                    "total_tickets": sum(status_counts.values()),
                    "status_counts": status_counts,
                    "top_vessels": top_vessels,
                    "component_counts": component_counts,
                    "average_resolution_minutes": round(avg_resolution, 2)
                }
                
        except Exception as e:
            self.logger.error(f"Error getting lifecycle statistics: {e}")
            return {"error": str(e)}
    
    def cleanup_old_records(self, max_age_days: int = 90) -> int:
        """
        Clean up old ticket records.
        
        Args:
            max_age_days: Maximum age of records to keep
            
        Returns:
            Number of records cleaned up
        """
        try:
            cutoff_date = datetime.now() - timedelta(days=max_age_days)
            
            with sqlite3.connect(self.database_path) as conn:
                # Delete old alert-ticket links first
                conn.execute("""
                    DELETE FROM alert_ticket_links 
                    WHERE ticket_id IN (
                        SELECT id FROM ticket_records 
                        WHERE created_at < ? AND lifecycle_status IN ('resolved', 'closed')
                    )
                """, (cutoff_date.isoformat(),))
                
                # Delete old ticket records
                cursor = conn.execute("""
                    DELETE FROM ticket_records 
                    WHERE created_at < ? AND lifecycle_status IN ('resolved', 'closed')
                """, (cutoff_date.isoformat(),))
                
                deleted_count = cursor.rowcount
                conn.commit()
                
                self.logger.info(f"Cleaned up {deleted_count} old ticket records")
                return deleted_count
                
        except Exception as e:
            self.logger.error(f"Error cleaning up old records: {e}")
            return 0