"""
Scheduler Run Logger service for the Infrastructure Monitoring Agent.

This module provides logging functionality for scheduler execution history,
tracking run status, vessel query results, and retry attempts.
"""

import sqlite3
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
from contextlib import contextmanager

from ..models.data_models import SchedulerRunLog, VesselQueryResult, SchedulerRunDetails


logger = logging.getLogger(__name__)


class SchedulerRunLogger:
    """
    Service for logging and tracking scheduler run execution history.
    
    This class handles database operations for storing scheduler run logs,
    vessel query results, and retry attempt tracking.
    """
    
    def __init__(self, database_path: str):
        """
        Initialize the scheduler run logger.
        
        Args:
            database_path: Path to the SQLite database file
        """
        self.database_path = database_path
        logger.info(f"Initialized scheduler run logger with database: {database_path}")
    
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
            logger.error(f"Database error in scheduler run logger: {e}")
            raise
        finally:
            if conn:
                conn.close()
    
    def log_run_start(self, run_log: SchedulerRunLog) -> None:
        """
        Log the start of a scheduler run.
        
        Args:
            run_log: SchedulerRunLog instance with run details
        """
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT INTO scheduler_runs 
                    (id, start_time, total_vessels, successful_vessels, failed_vessels, 
                     retry_attempts, status, duration_seconds, error_message)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    run_log.run_id,
                    run_log.start_time,
                    run_log.total_vessels,
                    run_log.successful_vessels,
                    run_log.failed_vessels,
                    run_log.retry_attempts,
                    run_log.status,
                    run_log.duration.total_seconds() if run_log.duration else None,
                    run_log.error_message
                ))
                conn.commit()
            
            logger.info(
                f"Logged scheduler run start: {run_log.run_id} with {run_log.total_vessels} vessels"
            )
        except Exception as e:
            logger.error(f"Failed to log scheduler run start: {e}")
            raise
    
    def log_vessel_query_result(self, run_id: str, vessel_result: VesselQueryResult) -> None:
        """
        Log the result of querying a single vessel.
        
        Args:
            run_id: ID of the scheduler run
            vessel_result: VesselQueryResult instance with query details
        """
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT INTO scheduler_vessel_results 
                    (run_id, vessel_id, attempt_number, success, query_duration_seconds, 
                     error_message, timestamp)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (
                    run_id,
                    vessel_result.vessel_id,
                    vessel_result.attempt_number,
                    vessel_result.success,
                    vessel_result.query_duration.total_seconds(),
                    vessel_result.error_message,
                    vessel_result.timestamp
                ))
                conn.commit()
            
            status = "succeeded" if vessel_result.success else "failed"
            logger.debug(
                f"Logged vessel query result: {vessel_result.vessel_id} {status} "
                f"(attempt {vessel_result.attempt_number})"
            )
        except Exception as e:
            logger.error(f"Failed to log vessel query result: {e}")
            raise
    
    def log_run_completion(self, run_log: SchedulerRunLog) -> None:
        """
        Log the completion of a scheduler run.
        
        Args:
            run_log: Updated SchedulerRunLog instance with completion details
        """
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    UPDATE scheduler_runs 
                    SET end_time = ?, successful_vessels = ?, failed_vessels = ?, 
                        retry_attempts = ?, status = ?, duration_seconds = ?, error_message = ?
                    WHERE id = ?
                """, (
                    run_log.end_time,
                    run_log.successful_vessels,
                    run_log.failed_vessels,
                    run_log.retry_attempts,
                    run_log.status,
                    run_log.duration.total_seconds() if run_log.duration else None,
                    run_log.error_message,
                    run_log.run_id
                ))
                conn.commit()
            
            logger.info(
                f"Logged scheduler run completion: {run_log.run_id} - "
                f"{run_log.successful_vessels} successful, {run_log.failed_vessels} failed"
            )
        except Exception as e:
            logger.error(f"Failed to log scheduler run completion: {e}")
            raise
    
    def get_recent_runs(self, limit: int = 20) -> List[SchedulerRunLog]:
        """
        Get recent scheduler runs.
        
        Args:
            limit: Maximum number of runs to retrieve
            
        Returns:
            List of SchedulerRunLog instances
        """
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT id, start_time, end_time, total_vessels, successful_vessels, 
                           failed_vessels, retry_attempts, status, duration_seconds, error_message
                    FROM scheduler_runs 
                    ORDER BY start_time DESC 
                    LIMIT ?
                """, (limit,))
                
                rows = cursor.fetchall()
                runs = []
                
                for row in rows:
                    run_data = {
                        'run_id': row['id'],
                        'start_time': row['start_time'],
                        'end_time': row['end_time'],
                        'total_vessels': row['total_vessels'],
                        'successful_vessels': row['successful_vessels'],
                        'failed_vessels': row['failed_vessels'],
                        'retry_attempts': row['retry_attempts'],
                        'status': row['status'],
                        'duration': timedelta(seconds=row['duration_seconds']) if row['duration_seconds'] else None,
                        'error_message': row['error_message']
                    }
                    runs.append(SchedulerRunLog(**run_data))
                
                return runs
        except Exception as e:
            logger.error(f"Failed to get recent runs: {e}")
            raise
    
    def get_run_details(self, run_id: str) -> Optional[SchedulerRunDetails]:
        """
        Get detailed information about a specific scheduler run.
        
        Args:
            run_id: ID of the scheduler run
            
        Returns:
            SchedulerRunDetails instance or None if not found
        """
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                
                # Get run summary
                cursor.execute("""
                    SELECT id, start_time, end_time, total_vessels, successful_vessels, 
                           failed_vessels, retry_attempts, status, duration_seconds, error_message
                    FROM scheduler_runs 
                    WHERE id = ?
                """, (run_id,))
                
                run_row = cursor.fetchone()
                if not run_row:
                    return None
                
                run_summary = SchedulerRunLog(
                    run_id=run_row['id'],
                    start_time=run_row['start_time'],
                    end_time=run_row['end_time'],
                    total_vessels=run_row['total_vessels'],
                    successful_vessels=run_row['successful_vessels'],
                    failed_vessels=run_row['failed_vessels'],
                    retry_attempts=run_row['retry_attempts'],
                    status=run_row['status'],
                    duration=timedelta(seconds=run_row['duration_seconds']) if run_row['duration_seconds'] else None,
                    error_message=run_row['error_message']
                )
                
                # Get vessel results
                cursor.execute("""
                    SELECT vessel_id, attempt_number, success, query_duration_seconds, 
                           error_message, timestamp
                    FROM scheduler_vessel_results 
                    WHERE run_id = ?
                    ORDER BY timestamp ASC
                """, (run_id,))
                
                vessel_results = []
                retry_summary = {}
                
                for result_row in cursor.fetchall():
                    vessel_result = VesselQueryResult(
                        vessel_id=result_row['vessel_id'],
                        attempt_number=result_row['attempt_number'],
                        success=result_row['success'],
                        query_duration=timedelta(seconds=result_row['query_duration_seconds']),
                        error_message=result_row['error_message'],
                        timestamp=result_row['timestamp']
                    )
                    vessel_results.append(vessel_result)
                    
                    # Track retry counts
                    vessel_id = result_row['vessel_id']
                    if vessel_id not in retry_summary:
                        retry_summary[vessel_id] = 0
                    if result_row['attempt_number'] > 1:
                        retry_summary[vessel_id] = max(
                            retry_summary[vessel_id], 
                            result_row['attempt_number'] - 1
                        )
                
                return SchedulerRunDetails(
                    run_summary=run_summary,
                    vessel_results=vessel_results,
                    retry_summary=retry_summary
                )
                
        except Exception as e:
            logger.error(f"Failed to get run details for {run_id}: {e}")
            raise
    
    def get_run_statistics(self, days_back: int = 30) -> Dict[str, Any]:
        """
        Get statistics about scheduler runs over a time period.
        
        Args:
            days_back: Number of days to analyze
            
        Returns:
            Dictionary containing run statistics
        """
        try:
            cutoff_date = datetime.utcnow() - timedelta(days=days_back)
            
            with self._get_connection() as conn:
                cursor = conn.cursor()
                
                # Get basic run statistics
                cursor.execute("""
                    SELECT 
                        COUNT(*) as total_runs,
                        SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) as successful_runs,
                        SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) as failed_runs,
                        AVG(duration_seconds) as avg_duration_seconds,
                        AVG(successful_vessels) as avg_successful_vessels,
                        AVG(failed_vessels) as avg_failed_vessels,
                        AVG(retry_attempts) as avg_retry_attempts
                    FROM scheduler_runs 
                    WHERE start_time >= ?
                """, (cutoff_date,))
                
                stats_row = cursor.fetchone()
                
                # Get vessel failure statistics
                cursor.execute("""
                    SELECT 
                        vessel_id,
                        COUNT(*) as total_attempts,
                        SUM(CASE WHEN success = 1 THEN 1 ELSE 0 END) as successful_attempts
                    FROM scheduler_vessel_results svr
                    JOIN scheduler_runs sr ON svr.run_id = sr.id
                    WHERE sr.start_time >= ?
                    GROUP BY vessel_id
                """, (cutoff_date,))
                
                vessel_stats = cursor.fetchall()
                
                # Calculate vessel reliability
                vessel_reliability = {}
                for vessel_row in vessel_stats:
                    vessel_id = vessel_row['vessel_id']
                    success_rate = vessel_row['successful_attempts'] / vessel_row['total_attempts']
                    vessel_reliability[vessel_id] = {
                        'success_rate': round(success_rate * 100, 2),
                        'total_attempts': vessel_row['total_attempts'],
                        'successful_attempts': vessel_row['successful_attempts']
                    }
                
                return {
                    'period_days': days_back,
                    'total_runs': stats_row['total_runs'] or 0,
                    'successful_runs': stats_row['successful_runs'] or 0,
                    'failed_runs': stats_row['failed_runs'] or 0,
                    'success_rate_percent': round(
                        (stats_row['successful_runs'] or 0) / max(stats_row['total_runs'] or 1, 1) * 100, 2
                    ),
                    'average_duration_minutes': round(
                        (stats_row['avg_duration_seconds'] or 0) / 60, 2
                    ),
                    'average_successful_vessels': round(stats_row['avg_successful_vessels'] or 0, 1),
                    'average_failed_vessels': round(stats_row['avg_failed_vessels'] or 0, 1),
                    'average_retry_attempts': round(stats_row['avg_retry_attempts'] or 0, 1),
                    'vessel_reliability': vessel_reliability
                }
                
        except Exception as e:
            logger.error(f"Failed to get run statistics: {e}")
            raise
    
    def cleanup_old_runs(self, days_to_keep: int = 90) -> int:
        """
        Clean up old scheduler run records.
        
        Args:
            days_to_keep: Number of days of records to keep
            
        Returns:
            Number of deleted run records
        """
        try:
            cutoff_date = datetime.utcnow() - timedelta(days=days_to_keep)
            
            with self._get_connection() as conn:
                cursor = conn.cursor()
                
                # Delete old vessel results (will cascade due to foreign key)
                cursor.execute("""
                    DELETE FROM scheduler_vessel_results 
                    WHERE run_id IN (
                        SELECT id FROM scheduler_runs WHERE start_time < ?
                    )
                """, (cutoff_date,))
                vessel_results_deleted = cursor.rowcount
                
                # Delete old runs
                cursor.execute("""
                    DELETE FROM scheduler_runs WHERE start_time < ?
                """, (cutoff_date,))
                runs_deleted = cursor.rowcount
                
                conn.commit()
                
                logger.info(
                    f"Cleaned up {runs_deleted} old scheduler runs and "
                    f"{vessel_results_deleted} vessel results older than {days_to_keep} days"
                )
                
                return runs_deleted
                
        except Exception as e:
            logger.error(f"Failed to cleanup old runs: {e}")
            raise
    
    def get_active_run(self) -> Optional[SchedulerRunLog]:
        """
        Get the currently active (running) scheduler run if any.
        
        Returns:
            SchedulerRunLog instance or None if no active run
        """
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT id, start_time, end_time, total_vessels, successful_vessels, 
                           failed_vessels, retry_attempts, status, duration_seconds, error_message
                    FROM scheduler_runs 
                    WHERE status = 'running'
                    ORDER BY start_time DESC 
                    LIMIT 1
                """)
                
                row = cursor.fetchone()
                if not row:
                    return None
                
                return SchedulerRunLog(
                    run_id=row['id'],
                    start_time=row['start_time'],
                    end_time=row['end_time'],
                    total_vessels=row['total_vessels'],
                    successful_vessels=row['successful_vessels'],
                    failed_vessels=row['failed_vessels'],
                    retry_attempts=row['retry_attempts'],
                    status=row['status'],
                    duration=timedelta(seconds=row['duration_seconds']) if row['duration_seconds'] else None,
                    error_message=row['error_message']
                )
                
        except Exception as e:
            logger.error(f"Failed to get active run: {e}")
            raise