"""
Database service for the Infrastructure Monitoring Agent.

This module provides database operations for persistent storage of SLA violation
history, component status changes, and alert tracking using SQLite.
"""

import sqlite3
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple, Any
from contextlib import contextmanager
from pathlib import Path
import json

from ..models.data_models import SLAStatus, ComponentStatus
from ..models.enums import ComponentType, OperationalStatus
from .database_migrations import DatabaseMigration


logger = logging.getLogger(__name__)


class DatabaseService:
    """
    Service for managing persistent storage of monitoring data.
    
    This class handles SQLite database operations for storing SLA violation
    history, component status changes, and alert tracking information.
    """
    
    def __init__(self, database_path: str):
        """
        Initialize the database service.
        
        Args:
            database_path: Path to the SQLite database file
        """
        self.database_path = database_path
        self._ensure_database_directory()
        self._initialize_database()
        
        logger.info(f"Initialized database service with database: {database_path}")
    
    def _ensure_database_directory(self):
        """Ensure the database directory exists."""
        db_path = Path(self.database_path)
        db_path.parent.mkdir(parents=True, exist_ok=True)
    
    def _initialize_database(self):
        """Initialize database tables if they don't exist."""
        # Run migrations to ensure schema is up to date
        migration_manager = DatabaseMigration(self.database_path)
        migration_manager.migrate_to_latest()
        
        with self._get_connection() as conn:
            cursor = conn.cursor()
            
            # Create SLA violation history table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS sla_violation_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    vessel_id TEXT NOT NULL,
                    component_type TEXT NOT NULL,
                    violation_start TIMESTAMP NOT NULL,
                    violation_end TIMESTAMP,
                    uptime_percentage REAL NOT NULL,
                    violation_duration_seconds INTEGER,
                    is_resolved BOOLEAN DEFAULT FALSE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # Create component status history table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS component_status_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    vessel_id TEXT NOT NULL,
                    component_type TEXT NOT NULL,
                    uptime_percentage REAL NOT NULL,
                    current_status TEXT NOT NULL,
                    downtime_aging_seconds INTEGER NOT NULL,
                    last_ping_time TIMESTAMP NOT NULL,
                    recorded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # Create alert tracking table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS alert_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    vessel_id TEXT NOT NULL,
                    component_type TEXT NOT NULL,
                    alert_type TEXT NOT NULL,
                    severity TEXT NOT NULL,
                    message TEXT NOT NULL,
                    metadata TEXT,
                    is_resolved BOOLEAN DEFAULT FALSE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    resolved_at TIMESTAMP
                )
            """)
            
            # Create JIRA ticket tracking table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS jira_tickets (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ticket_key TEXT UNIQUE NOT NULL,
                    vessel_id TEXT NOT NULL,
                    component_type TEXT NOT NULL,
                    issue_summary TEXT NOT NULL,
                    ticket_status TEXT NOT NULL,
                    downtime_duration_seconds INTEGER NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    resolved_at TIMESTAMP,
                    alert_id INTEGER,
                    FOREIGN KEY (alert_id) REFERENCES alert_history (id)
                )
            """)
            
            # Create system state table for recovery
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS system_state (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    state_key TEXT UNIQUE NOT NULL,
                    state_value TEXT NOT NULL,
                    state_type TEXT NOT NULL,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # Create indexes for better query performance
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_sla_violation_vessel_component 
                ON sla_violation_history(vessel_id, component_type)
            """)
            
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_sla_violation_start 
                ON sla_violation_history(violation_start)
            """)
            
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_component_status_vessel_component 
                ON component_status_history(vessel_id, component_type)
            """)
            
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_component_status_recorded 
                ON component_status_history(recorded_at)
            """)
            
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_alert_vessel_component 
                ON alert_history(vessel_id, component_type)
            """)
            
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_jira_tickets_vessel_component 
                ON jira_tickets(vessel_id, component_type)
            """)
            
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_jira_tickets_status 
                ON jira_tickets(ticket_status)
            """)
            
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_system_state_key 
                ON system_state(state_key)
            """)
            
            conn.commit()
            logger.info("Database tables initialized successfully")
    
    @contextmanager
    def _get_connection(self):
        """Get a database connection with proper error handling."""
        conn = None
        try:
            conn = sqlite3.connect(
                self.database_path,
                detect_types=sqlite3.PARSE_DECLTYPES | sqlite3.PARSE_COLNAMES
            )
            conn.row_factory = sqlite3.Row  # Enable column access by name
            yield conn
        except Exception as e:
            if conn:
                conn.rollback()
            logger.error(f"Database error: {e}")
            raise
        finally:
            if conn:
                conn.close()
    
    def record_component_status(
        self,
        vessel_id: str,
        component_status: ComponentStatus,
        recorded_at: Optional[datetime] = None
    ) -> None:
        """
        Record component status in the history table.
        
        Args:
            vessel_id: ID of the vessel
            component_status: Current component status
            recorded_at: Timestamp when status was recorded (defaults to now)
        """
        if recorded_at is None:
            recorded_at = datetime.utcnow()
        
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO component_status_history 
                (vessel_id, component_type, uptime_percentage, current_status, 
                 downtime_aging_seconds, last_ping_time, recorded_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                vessel_id,
                component_status.component_type.value,
                component_status.uptime_percentage,
                component_status.current_status.value,
                int(component_status.downtime_aging.total_seconds()),
                component_status.last_ping_time,
                recorded_at
            ))
            conn.commit()
        
        logger.debug(
            f"Recorded status for {component_status.component_type.value} "
            f"on vessel {vessel_id}: {component_status.uptime_percentage:.2f}% uptime"
        )
    
    def record_sla_violation(
        self,
        vessel_id: str,
        component_type: ComponentType,
        violation_start: datetime,
        uptime_percentage: float,
        violation_duration: Optional[timedelta] = None
    ) -> int:
        """
        Record a new SLA violation.
        
        Args:
            vessel_id: ID of the vessel
            component_type: Type of component in violation
            violation_start: When the violation started
            uptime_percentage: Current uptime percentage
            violation_duration: Duration of the violation (if known)
            
        Returns:
            ID of the created violation record
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO sla_violation_history 
                (vessel_id, component_type, violation_start, uptime_percentage, 
                 violation_duration_seconds, is_resolved)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                vessel_id,
                component_type.value,
                violation_start,
                uptime_percentage,
                int(violation_duration.total_seconds()) if violation_duration else None,
                False
            ))
            violation_id = cursor.lastrowid
            conn.commit()
        
        logger.info(
            f"Recorded SLA violation for {component_type.value} on vessel {vessel_id} "
            f"starting at {violation_start}"
        )
        
        return violation_id
    
    def resolve_sla_violation(
        self,
        violation_id: int,
        violation_end: datetime,
        final_uptime_percentage: float
    ) -> None:
        """
        Mark an SLA violation as resolved.
        
        Args:
            violation_id: ID of the violation to resolve
            violation_end: When the violation was resolved
            final_uptime_percentage: Final uptime percentage when resolved
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            
            # Get violation start time to calculate total duration
            cursor.execute("""
                SELECT violation_start FROM sla_violation_history WHERE id = ?
            """, (violation_id,))
            row = cursor.fetchone()
            
            if row:
                violation_start = row['violation_start']
                total_duration = violation_end - violation_start
                
                cursor.execute("""
                    UPDATE sla_violation_history 
                    SET violation_end = ?, 
                        violation_duration_seconds = ?,
                        is_resolved = TRUE,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                """, (
                    violation_end,
                    int(total_duration.total_seconds()),
                    violation_id
                ))
                conn.commit()
                
                logger.info(
                    f"Resolved SLA violation {violation_id} after "
                    f"{total_duration.total_seconds() / 3600:.1f} hours"
                )
            else:
                logger.warning(f"SLA violation {violation_id} not found for resolution")
    
    def get_active_sla_violations(
        self,
        vessel_id: Optional[str] = None,
        component_type: Optional[ComponentType] = None
    ) -> List[Dict[str, Any]]:
        """
        Get currently active (unresolved) SLA violations.
        
        Args:
            vessel_id: Optional filter by vessel ID
            component_type: Optional filter by component type
            
        Returns:
            List of active violation records
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            
            query = """
                SELECT * FROM sla_violation_history 
                WHERE is_resolved = FALSE
            """
            params = []
            
            if vessel_id:
                query += " AND vessel_id = ?"
                params.append(vessel_id)
            
            if component_type:
                query += " AND component_type = ?"
                params.append(component_type.value)
            
            query += " ORDER BY violation_start DESC"
            
            cursor.execute(query, params)
            rows = cursor.fetchall()
            
            violations = []
            for row in rows:
                violation = dict(row)
                violation['component_type'] = ComponentType(violation['component_type'])
                violations.append(violation)
            
            return violations
    
    def get_violation_history(
        self,
        vessel_id: Optional[str] = None,
        component_type: Optional[ComponentType] = None,
        days_back: int = 30
    ) -> List[Dict[str, Any]]:
        """
        Get SLA violation history for analysis.
        
        Args:
            vessel_id: Optional filter by vessel ID
            component_type: Optional filter by component type
            days_back: Number of days of history to retrieve
            
        Returns:
            List of violation records
        """
        cutoff_date = datetime.utcnow() - timedelta(days=days_back)
        
        with self._get_connection() as conn:
            cursor = conn.cursor()
            
            query = """
                SELECT * FROM sla_violation_history 
                WHERE violation_start >= ?
            """
            params = [cutoff_date]
            
            if vessel_id:
                query += " AND vessel_id = ?"
                params.append(vessel_id)
            
            if component_type:
                query += " AND component_type = ?"
                params.append(component_type.value)
            
            query += " ORDER BY violation_start DESC"
            
            cursor.execute(query, params)
            rows = cursor.fetchall()
            
            violations = []
            for row in rows:
                violation = dict(row)
                violation['component_type'] = ComponentType(violation['component_type'])
                violations.append(violation)
            
            return violations
    
    def get_component_status_trends(
        self,
        vessel_id: str,
        component_type: ComponentType,
        days_back: int = 7
    ) -> List[Dict[str, Any]]:
        """
        Get component status trends over time.
        
        Args:
            vessel_id: ID of the vessel
            component_type: Type of component
            days_back: Number of days of history to retrieve
            
        Returns:
            List of status records showing trends
        """
        cutoff_date = datetime.utcnow() - timedelta(days=days_back)
        
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM component_status_history 
                WHERE vessel_id = ? AND component_type = ? AND recorded_at >= ?
                ORDER BY recorded_at ASC
            """, (vessel_id, component_type.value, cutoff_date))
            
            rows = cursor.fetchall()
            
            trends = []
            for row in rows:
                trend = dict(row)
                trend['component_type'] = ComponentType(trend['component_type'])
                trend['current_status'] = OperationalStatus(trend['current_status'])
                trend['downtime_aging'] = timedelta(seconds=trend['downtime_aging_seconds'])
                trends.append(trend)
            
            return trends
    
    def calculate_violation_duration_stats(
        self,
        vessel_id: Optional[str] = None,
        component_type: Optional[ComponentType] = None,
        days_back: int = 30
    ) -> Dict[str, Any]:
        """
        Calculate statistics about violation durations.
        
        Args:
            vessel_id: Optional filter by vessel ID
            component_type: Optional filter by component type
            days_back: Number of days to analyze
            
        Returns:
            Dictionary containing violation duration statistics
        """
        cutoff_date = datetime.utcnow() - timedelta(days=days_back)
        
        with self._get_connection() as conn:
            cursor = conn.cursor()
            
            query = """
                SELECT violation_duration_seconds 
                FROM sla_violation_history 
                WHERE violation_start >= ? AND violation_duration_seconds IS NOT NULL
            """
            params = [cutoff_date]
            
            if vessel_id:
                query += " AND vessel_id = ?"
                params.append(vessel_id)
            
            if component_type:
                query += " AND component_type = ?"
                params.append(component_type.value)
            
            cursor.execute(query, params)
            durations = [row[0] for row in cursor.fetchall()]
            
            if not durations:
                return {
                    'count': 0,
                    'average_duration_hours': 0.0,
                    'min_duration_hours': 0.0,
                    'max_duration_hours': 0.0,
                    'total_downtime_hours': 0.0
                }
            
            # Convert to hours for readability
            durations_hours = [d / 3600.0 for d in durations]
            
            return {
                'count': len(durations),
                'average_duration_hours': round(sum(durations_hours) / len(durations_hours), 2),
                'min_duration_hours': round(min(durations_hours), 2),
                'max_duration_hours': round(max(durations_hours), 2),
                'total_downtime_hours': round(sum(durations_hours), 2)
            }
    
    def record_alert(
        self,
        vessel_id: str,
        component_type: ComponentType,
        alert_type: str,
        severity: str,
        message: str,
        metadata: Optional[Dict[str, Any]] = None
    ) -> int:
        """
        Record an alert in the database.
        
        Args:
            vessel_id: ID of the vessel
            component_type: Type of component
            alert_type: Type of alert (e.g., 'sla_violation', 'persistent_downtime')
            severity: Alert severity level
            message: Alert message
            metadata: Optional additional metadata
            
        Returns:
            ID of the created alert record
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO alert_history 
                (vessel_id, component_type, alert_type, severity, message, metadata)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                vessel_id,
                component_type.value,
                alert_type,
                severity,
                message,
                json.dumps(metadata) if metadata else None
            ))
            alert_id = cursor.lastrowid
            conn.commit()
        
        logger.info(
            f"Recorded {alert_type} alert for {component_type.value} "
            f"on vessel {vessel_id}: {message}"
        )
        
        return alert_id
    
    def resolve_alert(self, alert_id: int) -> None:
        """
        Mark an alert as resolved.
        
        Args:
            alert_id: ID of the alert to resolve
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE alert_history 
                SET is_resolved = TRUE, resolved_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (alert_id,))
            conn.commit()
        
        logger.info(f"Resolved alert {alert_id}")
    
    def record_jira_ticket(
        self,
        ticket_key: str,
        vessel_id: str,
        component_type: ComponentType,
        issue_summary: str,
        ticket_status: str,
        downtime_duration: timedelta,
        alert_id: Optional[int] = None
    ) -> int:
        """
        Record a JIRA ticket in the database.
        
        Args:
            ticket_key: JIRA ticket key (e.g., 'INFRA-123')
            vessel_id: ID of the vessel
            component_type: Type of component
            issue_summary: Summary of the issue
            ticket_status: Current ticket status
            downtime_duration: Duration of downtime that triggered the ticket
            alert_id: Optional ID of associated alert
            
        Returns:
            ID of the created ticket record
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO jira_tickets 
                (ticket_key, vessel_id, component_type, issue_summary, ticket_status, 
                 downtime_duration_seconds, alert_id)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                ticket_key,
                vessel_id,
                component_type.value,
                issue_summary,
                ticket_status,
                int(downtime_duration.total_seconds()),
                alert_id
            ))
            ticket_id = cursor.lastrowid
            conn.commit()
        
        logger.info(
            f"Recorded JIRA ticket {ticket_key} for {component_type.value} "
            f"on vessel {vessel_id}"
        )
        
        return ticket_id
    
    def update_jira_ticket_status(
        self,
        ticket_key: str,
        new_status: str,
        resolved_at: Optional[datetime] = None
    ) -> None:
        """
        Update the status of a JIRA ticket.
        
        Args:
            ticket_key: JIRA ticket key
            new_status: New ticket status
            resolved_at: Optional timestamp when ticket was resolved
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            
            if resolved_at and new_status.lower() in ['resolved', 'closed', 'done']:
                cursor.execute("""
                    UPDATE jira_tickets 
                    SET ticket_status = ?, resolved_at = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE ticket_key = ?
                """, (new_status, resolved_at, ticket_key))
            else:
                cursor.execute("""
                    UPDATE jira_tickets 
                    SET ticket_status = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE ticket_key = ?
                """, (new_status, ticket_key))
            
            conn.commit()
        
        logger.info(f"Updated JIRA ticket {ticket_key} status to {new_status}")
    
    def get_existing_jira_tickets(
        self,
        vessel_id: str,
        component_type: ComponentType,
        active_only: bool = True
    ) -> List[Dict[str, Any]]:
        """
        Get existing JIRA tickets for a vessel and component.
        
        Args:
            vessel_id: ID of the vessel
            component_type: Type of component
            active_only: If True, only return unresolved tickets
            
        Returns:
            List of ticket records
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            
            query = """
                SELECT * FROM jira_tickets 
                WHERE vessel_id = ? AND component_type = ?
            """
            params = [vessel_id, component_type.value]
            
            if active_only:
                query += " AND resolved_at IS NULL"
            
            query += " ORDER BY created_at DESC"
            
            cursor.execute(query, params)
            rows = cursor.fetchall()
            
            tickets = []
            for row in rows:
                ticket = dict(row)
                ticket['component_type'] = ComponentType(ticket['component_type'])
                ticket['downtime_duration'] = timedelta(seconds=ticket['downtime_duration_seconds'])
                tickets.append(ticket)
            
            return tickets
    
    def set_system_state(
        self,
        state_key: str,
        state_value: Any,
        state_type: str = 'json'
    ) -> None:
        """
        Set a system state value for recovery purposes.
        
        Args:
            state_key: Unique key for the state
            state_value: Value to store
            state_type: Type of value ('json', 'string', 'datetime')
        """
        # Serialize value based on type
        if state_type == 'json':
            serialized_value = json.dumps(state_value)
        elif state_type == 'datetime':
            serialized_value = state_value.isoformat() if isinstance(state_value, datetime) else str(state_value)
        else:
            serialized_value = str(state_value)
        
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT OR REPLACE INTO system_state 
                (state_key, state_value, state_type, updated_at)
                VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            """, (state_key, serialized_value, state_type))
            conn.commit()
        
        logger.debug(f"Set system state {state_key} = {state_value}")
    
    def get_system_state(
        self,
        state_key: str,
        default_value: Any = None
    ) -> Any:
        """
        Get a system state value.
        
        Args:
            state_key: Key for the state
            default_value: Default value if key not found
            
        Returns:
            Deserialized state value or default
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT state_value, state_type FROM system_state WHERE state_key = ?
            """, (state_key,))
            row = cursor.fetchone()
            
            if not row:
                return default_value
            
            state_value, state_type = row
            
            # Deserialize based on type
            try:
                if state_type == 'json':
                    return json.loads(state_value)
                elif state_type == 'datetime':
                    return datetime.fromisoformat(state_value)
                else:
                    return state_value
            except (json.JSONDecodeError, ValueError) as e:
                logger.warning(f"Failed to deserialize state {state_key}: {e}")
                return default_value
    
    def get_system_recovery_info(self) -> Dict[str, Any]:
        """
        Get system recovery information for restart scenarios.
        
        Returns:
            Dictionary containing recovery information
        """
        recovery_info = {
            'last_monitoring_run': self.get_system_state('last_monitoring_run'),
            'active_violations': len(self.get_active_sla_violations()),
            'pending_tickets': len(self.get_pending_jira_tickets()),
            'system_health': self.get_system_state('system_health', 'unknown')
        }
        
        return recovery_info
    
    def get_pending_jira_tickets(self) -> List[Dict[str, Any]]:
        """
        Get all pending (unresolved) JIRA tickets.
        
        Returns:
            List of pending ticket records
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM jira_tickets 
                WHERE resolved_at IS NULL 
                ORDER BY created_at ASC
            """)
            rows = cursor.fetchall()
            
            tickets = []
            for row in rows:
                ticket = dict(row)
                ticket['component_type'] = ComponentType(ticket['component_type'])
                ticket['downtime_duration'] = timedelta(seconds=ticket['downtime_duration_seconds'])
                tickets.append(ticket)
            
            return tickets
    
    def cleanup_old_records(self, days_to_keep: int = 90) -> Dict[str, int]:
        """
        Clean up old records to prevent database growth.
        
        Args:
            days_to_keep: Number of days of records to keep
            
        Returns:
            Dictionary with counts of deleted records by table
        """
        cutoff_date = datetime.utcnow() - timedelta(days=days_to_keep)
        deleted_counts = {}
        
        with self._get_connection() as conn:
            cursor = conn.cursor()
            
            # Clean up old component status history
            cursor.execute("""
                DELETE FROM component_status_history WHERE recorded_at < ?
            """, (cutoff_date,))
            deleted_counts['component_status_history'] = cursor.rowcount
            
            # Clean up old resolved violations
            cursor.execute("""
                DELETE FROM sla_violation_history 
                WHERE is_resolved = TRUE AND updated_at < ?
            """, (cutoff_date,))
            deleted_counts['sla_violation_history'] = cursor.rowcount
            
            # Clean up old resolved alerts
            cursor.execute("""
                DELETE FROM alert_history 
                WHERE is_resolved = TRUE AND resolved_at < ?
            """, (cutoff_date,))
            deleted_counts['alert_history'] = cursor.rowcount
            
            # Clean up old resolved JIRA tickets
            cursor.execute("""
                DELETE FROM jira_tickets 
                WHERE resolved_at IS NOT NULL AND resolved_at < ?
            """, (cutoff_date,))
            deleted_counts['jira_tickets'] = cursor.rowcount
            
            # Clean up old system state (keep recent states)
            cursor.execute("""
                DELETE FROM system_state 
                WHERE updated_at < ? AND state_key NOT IN ('system_version', 'installation_date')
            """, (cutoff_date,))
            deleted_counts['system_state'] = cursor.rowcount
            
            conn.commit()
        
        total_deleted = sum(deleted_counts.values())
        logger.info(
            f"Cleaned up {total_deleted} old records older than {days_to_keep} days"
        )
        
        return deleted_counts