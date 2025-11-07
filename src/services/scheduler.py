"""
Scheduling service for the Infrastructure Monitoring Agent.

This module provides background scheduling using APScheduler for daily vessel monitoring
and orchestration of the complete monitoring workflow.
"""

import logging
import asyncio
from datetime import datetime, time
from typing import Optional, Dict, Any, Callable, List, Tuple
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.events import EVENT_JOB_EXECUTED, EVENT_JOB_ERROR, JobExecutionEvent

from ..config.config_models import Config, SchedulingConfig
from ..models.data_models import SchedulerRunLog, VesselQueryResult, VesselMetrics
from .data_collector import DataCollector
from .sla_analyzer import SLAAnalyzer
from .alert_manager import AlertManager
from .jira_service import JIRAService
from .database import DatabaseService
from .scheduler_run_logger import SchedulerRunLogger


logger = logging.getLogger(__name__)


class MonitoringScheduler:
    """
    Background scheduler for daily vessel monitoring.
    
    This class manages the scheduling of automated monitoring tasks using APScheduler,
    providing configurable daily execution of the monitoring workflow and coordination
    of all monitoring services.
    """
    
    def __init__(self, config: Config, websocket_manager=None):
        """
        Initialize the monitoring scheduler.
        
        Args:
            config: Application configuration containing scheduling parameters
            websocket_manager: Optional WebSocket connection manager for real-time updates
        """
        self.config = config
        self.scheduling_config = config.scheduling
        self.websocket_manager = websocket_manager
        
        # Initialize APScheduler
        self.scheduler = AsyncIOScheduler(timezone=self.scheduling_config.timezone)
        
        # Add job execution event listeners
        self.scheduler.add_listener(self._job_executed_listener, EVENT_JOB_EXECUTED)
        self.scheduler.add_listener(self._job_error_listener, EVENT_JOB_ERROR)
        
        # Track scheduler state
        self._is_running = False
        self._job_callbacks: Dict[str, Callable] = {}
        
        # Initialize scheduler run logger
        self.run_logger = SchedulerRunLogger(config.database_path)
        
        # Retry configuration
        self.max_retry_attempts = 3
        self.base_retry_delay = 1.0  # Base delay in seconds for exponential backoff
        
        logger.info(
            f"Initialized MonitoringScheduler with timezone: {self.scheduling_config.timezone}, "
            f"daily monitoring at {self.scheduling_config.daily_monitoring_hour:02d}:"
            f"{self.scheduling_config.daily_monitoring_minute:02d}, "
            f"max retry attempts: {self.max_retry_attempts}"
        )
    
    def start(self) -> None:
        """
        Start the scheduler and configure monitoring jobs.
        
        This method starts the APScheduler and adds the daily monitoring job
        based on the configured schedule.
        """
        if self._is_running:
            logger.warning("Scheduler is already running")
            return
        
        try:
            # Start the scheduler
            self.scheduler.start()
            self._is_running = True
            
            # Add daily monitoring job
            self._schedule_daily_monitoring()
            
            logger.info("MonitoringScheduler started successfully")
            
        except Exception as e:
            logger.error(f"Failed to start MonitoringScheduler: {e}")
            raise
    
    def shutdown(self, wait: bool = True) -> None:
        """
        Shutdown the scheduler gracefully.
        
        Args:
            wait: Whether to wait for running jobs to complete
        """
        if not self._is_running:
            logger.warning("Scheduler is not running")
            return
        
        try:
            self.scheduler.shutdown(wait=wait)
            self._is_running = False
            logger.info("MonitoringScheduler shutdown completed")
            
        except Exception as e:
            logger.error(f"Error during scheduler shutdown: {e}")
    
    def _schedule_daily_monitoring(self) -> None:
        """
        Schedule the daily monitoring job.
        
        Creates a cron trigger for daily execution at the configured time
        and adds the monitoring workflow job to the scheduler.
        """
        # Create cron trigger for daily execution
        trigger = CronTrigger(
            hour=self.scheduling_config.daily_monitoring_hour,
            minute=self.scheduling_config.daily_monitoring_minute,
            timezone=self.scheduling_config.timezone
        )
        
        # Add the daily monitoring job
        job = self.scheduler.add_job(
            func=self._execute_daily_monitoring_workflow,
            trigger=trigger,
            id="daily_monitoring",
            name="Daily Vessel Monitoring Workflow",
            max_instances=1,  # Prevent overlapping executions
            coalesce=True,    # Combine missed executions
            misfire_grace_time=3600  # Allow 1 hour grace period for missed jobs
        )
        
        logger.info(f"Scheduled daily monitoring job: {job.id} at {trigger}")
    
    async def _execute_daily_monitoring_workflow(self) -> None:
        """
        Execute the complete daily monitoring workflow with retry logic.
        
        This method serves as the entry point for the scheduled monitoring job
        and coordinates the execution of all monitoring services with enhanced
        retry capabilities for failed vessel queries.
        """
        logger.info("Starting scheduled daily monitoring workflow with retry logic")
        start_time = datetime.now()
        
        try:
            # Execute enhanced monitoring with retry logic
            workflow_result = await self._execute_enhanced_monitoring_with_retry()
            
            execution_time = datetime.now() - start_time
            
            logger.info(
                f"Daily monitoring workflow completed successfully in {execution_time}. "
                f"Results: {workflow_result}"
            )
            
            # Trigger callback if registered
            if "daily_monitoring_completed" in self._job_callbacks:
                await self._job_callbacks["daily_monitoring_completed"](workflow_result)
                
        except Exception as e:
            execution_time = datetime.now() - start_time
            logger.error(
                f"Daily monitoring workflow failed after {execution_time}: {e}",
                exc_info=True
            )
            
            # Trigger error callback if registered
            if "daily_monitoring_error" in self._job_callbacks:
                await self._job_callbacks["daily_monitoring_error"](e)
            
            raise
    
    def schedule_custom_job(
        self,
        job_func: Callable,
        job_id: str,
        trigger_config: Dict[str, Any],
        job_name: Optional[str] = None
    ) -> None:
        """
        Schedule a custom monitoring job.
        
        Args:
            job_func: Function to execute
            job_id: Unique identifier for the job
            trigger_config: Trigger configuration (cron, interval, etc.)
            job_name: Optional human-readable job name
        """
        if not self._is_running:
            raise RuntimeError("Scheduler must be started before adding jobs")
        
        try:
            job = self.scheduler.add_job(
                func=job_func,
                id=job_id,
                name=job_name or job_id,
                **trigger_config
            )
            
            logger.info(f"Added custom job: {job.id} - {job.name}")
            
        except Exception as e:
            logger.error(f"Failed to add custom job {job_id}: {e}")
            raise
    
    def remove_job(self, job_id: str) -> None:
        """
        Remove a scheduled job.
        
        Args:
            job_id: ID of the job to remove
        """
        try:
            self.scheduler.remove_job(job_id)
            logger.info(f"Removed job: {job_id}")
            
        except Exception as e:
            logger.error(f"Failed to remove job {job_id}: {e}")
            raise
    
    def get_job_status(self, job_id: str) -> Optional[Dict[str, Any]]:
        """
        Get status information for a scheduled job.
        
        Args:
            job_id: ID of the job
            
        Returns:
            Job status information or None if job not found
        """
        try:
            job = self.scheduler.get_job(job_id)
            if job:
                return {
                    'id': job.id,
                    'name': job.name,
                    'next_run_time': job.next_run_time.isoformat() if job.next_run_time else None,
                    'trigger': str(job.trigger),
                    'max_instances': job.max_instances,
                    'coalesce': job.coalesce
                }
            return None
            
        except Exception as e:
            logger.error(f"Failed to get job status for {job_id}: {e}")
            return None
    
    def get_all_jobs_status(self) -> Dict[str, Dict[str, Any]]:
        """
        Get status information for all scheduled jobs.
        
        Returns:
            Dictionary mapping job IDs to their status information
        """
        jobs_status = {}
        
        try:
            for job in self.scheduler.get_jobs():
                jobs_status[job.id] = {
                    'id': job.id,
                    'name': job.name,
                    'next_run_time': job.next_run_time.isoformat() if job.next_run_time else None,
                    'trigger': str(job.trigger),
                    'max_instances': job.max_instances,
                    'coalesce': job.coalesce
                }
            
        except Exception as e:
            logger.error(f"Failed to get jobs status: {e}")
        
        return jobs_status
    
    def register_job_callback(self, event_name: str, callback: Callable) -> None:
        """
        Register a callback for job events.
        
        Args:
            event_name: Name of the event ('daily_monitoring_completed', 'daily_monitoring_error')
            callback: Async callback function to execute
        """
        self._job_callbacks[event_name] = callback
        logger.info(f"Registered callback for event: {event_name}")
    
    def unregister_job_callback(self, event_name: str) -> None:
        """
        Unregister a job event callback.
        
        Args:
            event_name: Name of the event to unregister
        """
        if event_name in self._job_callbacks:
            del self._job_callbacks[event_name]
            logger.info(f"Unregistered callback for event: {event_name}")
    
    def _job_executed_listener(self, event: JobExecutionEvent) -> None:
        """
        Handle job execution events.
        
        Args:
            event: Job execution event
        """
        logger.info(
            f"Job executed successfully: {event.job_id} "
            f"(runtime: {event.scheduled_run_time})"
        )
    
    def _job_error_listener(self, event: JobExecutionEvent) -> None:
        """
        Handle job error events.
        
        Args:
            event: Job execution event with error
        """
        logger.error(
            f"Job execution failed: {event.job_id} "
            f"(scheduled: {event.scheduled_run_time}) - {event.exception}",
            exc_info=event.traceback
        )
    
    def trigger_job_now(self, job_id: str) -> None:
        """
        Trigger a job to run immediately.
        
        Args:
            job_id: ID of the job to trigger
        """
        try:
            job = self.scheduler.get_job(job_id)
            if job:
                job.modify(next_run_time=datetime.now())
                logger.info(f"Triggered job to run now: {job_id}")
            else:
                logger.error(f"Job not found: {job_id}")
                
        except Exception as e:
            logger.error(f"Failed to trigger job {job_id}: {e}")
            raise
    
    def update_schedule(self, new_scheduling_config: SchedulingConfig) -> None:
        """
        Update the monitoring schedule configuration.
        
        Args:
            new_scheduling_config: New scheduling configuration
        """
        try:
            # Update configuration
            old_config = self.scheduling_config
            self.scheduling_config = new_scheduling_config
            self.config.scheduling = new_scheduling_config
            
            # Remove existing daily monitoring job
            if self.scheduler.get_job("daily_monitoring"):
                self.scheduler.remove_job("daily_monitoring")
            
            # Reschedule with new configuration
            self._schedule_daily_monitoring()
            
            logger.info(
                f"Updated monitoring schedule from "
                f"{old_config.daily_monitoring_hour:02d}:{old_config.daily_monitoring_minute:02d} "
                f"to {new_scheduling_config.daily_monitoring_hour:02d}:"
                f"{new_scheduling_config.daily_monitoring_minute:02d}"
            )
            
        except Exception as e:
            logger.error(f"Failed to update schedule: {e}")
            raise
    
    @property
    def is_running(self) -> bool:
        """Check if the scheduler is currently running."""
        return self._is_running
    
    def get_next_monitoring_time(self) -> Optional[datetime]:
        """
        Get the next scheduled monitoring time.
        
        Returns:
            Next monitoring execution time or None if not scheduled
        """
        job = self.scheduler.get_job("daily_monitoring")
        return job.next_run_time if job else None
    
    async def _execute_enhanced_monitoring_with_retry(self) -> Dict[str, Any]:
        """
        Execute enhanced monitoring workflow with vessel query retry logic.
        
        This method implements the retry mechanism for failed vessel queries
        with exponential backoff and comprehensive logging.
        
        Returns:
            Dictionary containing workflow execution results
        """
        # Initialize scheduler run log
        vessel_ids = self.config.get_vessel_ids()
        run_log = SchedulerRunLog.create_new(len(vessel_ids))
        
        logger.info(f"Starting enhanced monitoring run {run_log.run_id} for {len(vessel_ids)} vessels")
        
        # Log run start
        self.run_logger.log_run_start(run_log)
        
        # Emit WebSocket event for run start
        await self._emit_scheduler_run_start(run_log)
        
        try:
            # Initialize data collector
            data_collector = DataCollector(self.config)
            
            # Execute vessel queries with retry logic
            successful_vessels, failed_vessels, retry_attempts = await self._execute_vessel_queries_with_retry(
                data_collector, vessel_ids, run_log.run_id
            )
            
            # Update run log with results
            run_log.mark_completed(len(successful_vessels), len(failed_vessels), retry_attempts)
            self.run_logger.log_run_completion(run_log)
            
            # Emit WebSocket event for run completion
            await self._emit_scheduler_run_complete(run_log)
            
            # If we have successful vessel data, continue with the rest of the workflow
            if successful_vessels:
                logger.info(f"Proceeding with monitoring workflow for {len(successful_vessels)} successful vessels")
                
                # Import and initialize the monitoring orchestrator
                from .monitoring_orchestrator import MonitoringOrchestrator
                orchestrator = MonitoringOrchestrator(self.config)
                
                # Execute the complete monitoring workflow with successful vessel data
                workflow_result = await orchestrator.execute_daily_monitoring()
                
                # Add retry statistics to workflow result
                workflow_result_dict = workflow_result.to_dict()
                workflow_result_dict.update({
                    'scheduler_run_id': run_log.run_id,
                    'retry_attempts': retry_attempts,
                    'vessels_retried': len(failed_vessels),
                    'final_successful_vessels': len(successful_vessels),
                    'final_failed_vessels': len(failed_vessels)
                })
                
                return workflow_result_dict
            else:
                # No successful vessels - mark as failed
                error_msg = f"No vessel data collected after retry attempts - cannot proceed with monitoring"
                run_log.mark_failed(error_msg)
                self.run_logger.log_run_completion(run_log)
                
                # Emit WebSocket event for run failure
                await self._emit_scheduler_run_complete(run_log)
                
                raise RuntimeError(error_msg)
                
        except Exception as e:
            # Mark run as failed
            run_log.mark_failed(str(e))
            self.run_logger.log_run_completion(run_log)
            
            # Emit WebSocket event for run failure
            await self._emit_scheduler_run_complete(run_log)
            
            raise
    
    async def _execute_vessel_queries_with_retry(
        self, 
        data_collector: DataCollector, 
        vessel_ids: List[str], 
        run_id: str
    ) -> Tuple[Dict[str, VesselMetrics], List[str], int]:
        """
        Execute vessel queries with retry logic and exponential backoff.
        
        Args:
            data_collector: DataCollector instance for querying vessels
            vessel_ids: List of vessel IDs to query
            run_id: Scheduler run ID for logging
            
        Returns:
            Tuple of (successful_vessel_metrics, failed_vessel_ids, total_retry_attempts)
        """
        successful_vessels = {}
        failed_vessels = list(vessel_ids)  # Start with all vessels as potentially failed
        total_retry_attempts = 0
        
        # Attempt vessel queries with retry logic
        for attempt in range(1, self.max_retry_attempts + 1):
            if not failed_vessels:
                break  # All vessels successful
            
            logger.info(f"Attempt {attempt}/{self.max_retry_attempts}: querying {len(failed_vessels)} vessels")
            
            current_attempt_failed = []
            
            # Query each failed vessel
            for vessel_id in failed_vessels:
                vessel_start_time = datetime.utcnow()
                
                try:
                    # Query vessel metrics
                    vessel_metrics = await data_collector.collect_vessel_metrics(vessel_id)
                    
                    # Log successful query
                    query_duration = datetime.utcnow() - vessel_start_time
                    vessel_result = VesselQueryResult(
                        vessel_id=vessel_id,
                        attempt_number=attempt,
                        success=True,
                        query_duration=query_duration,
                        timestamp=datetime.utcnow()
                    )
                    self.run_logger.log_vessel_query_result(run_id, vessel_result)
                    
                    # Add to successful vessels
                    successful_vessels[vessel_id] = vessel_metrics
                    
                    # Emit progress update
                    await self._emit_vessel_query_progress(run_id, vessel_id, attempt, True, len(successful_vessels), len(failed_vessels))
                    
                    logger.debug(f"Successfully queried vessel {vessel_id} on attempt {attempt}")
                    
                except Exception as e:
                    # Handle specific error types with appropriate error handling
                    await self._handle_vessel_query_error(e, vessel_id, attempt)
                    
                    # Determine if error should be retried based on error type
                    should_retry = self._should_retry_vessel_query(e, attempt)
                    
                    # Log failed query
                    query_duration = datetime.utcnow() - vessel_start_time
                    vessel_result = VesselQueryResult(
                        vessel_id=vessel_id,
                        attempt_number=attempt,
                        success=False,
                        query_duration=query_duration,
                        error_message=str(e),
                        timestamp=datetime.utcnow()
                    )
                    self.run_logger.log_vessel_query_result(run_id, vessel_result)
                    
                    # Handle different error types appropriately
                    if should_retry and attempt < self.max_retry_attempts:
                        # Add to current attempt failed list for retry
                        current_attempt_failed.append(vessel_id)
                        logger.warning(f"Failed to query vessel {vessel_id} on attempt {attempt} (will retry): {e}")
                    else:
                        # Don't retry - log as permanent failure
                        if not should_retry:
                            logger.error(f"Permanent failure for vessel {vessel_id} on attempt {attempt} (no retry): {e}")
                        else:
                            logger.error(f"Max retries exceeded for vessel {vessel_id} on attempt {attempt}: {e}")
                    
                    # Emit progress update for failed vessel
                    await self._emit_vessel_query_progress(run_id, vessel_id, attempt, False, len(successful_vessels), len(failed_vessels))
            
            # Update failed vessels list for next attempt
            failed_vessels = current_attempt_failed
            
            # If this wasn't the last attempt and we have failed vessels, apply exponential backoff
            if attempt < self.max_retry_attempts and failed_vessels:
                backoff_delay = self.base_retry_delay * (2 ** (attempt - 1))  # 1s, 2s, 4s
                total_retry_attempts += len(failed_vessels)
                
                logger.info(f"Waiting {backoff_delay}s before retry attempt {attempt + 1} for {len(failed_vessels)} vessels")
                await asyncio.sleep(backoff_delay)
        
        logger.info(
            f"Vessel query execution completed: {len(successful_vessels)} successful, "
            f"{len(failed_vessels)} failed, {total_retry_attempts} total retry attempts"
        )
        
        return successful_vessels, failed_vessels, total_retry_attempts
    
    async def _handle_vessel_query_error(self, error: Exception, vessel_id: str, attempt: int) -> None:
        """
        Handle vessel query errors with appropriate error-specific handling.
        
        Args:
            error: Exception that occurred during vessel query
            vessel_id: ID of the vessel that failed
            attempt: Current attempt number
        """
        error_str = str(error).lower()
        error_type = type(error).__name__
        
        # Handle specific error types
        if any(keyword in error_str for keyword in ['timeout', 'timed out']):
            await self._handle_network_timeout_error(error, vessel_id, attempt)
        elif any(keyword in error_str for keyword in ['authentication', 'unauthorized', 'forbidden']):
            await self._handle_authentication_error(error, vessel_id)
        elif any(keyword in error_str for keyword in ['database', 'connection pool', 'too many connections']):
            await self._handle_database_connection_error(error, vessel_id)
        else:
            # Generic error handling
            logger.warning(f"Generic error for vessel {vessel_id} on attempt {attempt}: {error_type} - {error}")
    
    def _should_retry_vessel_query(self, error: Exception, attempt: int) -> bool:
        """
        Determine if a vessel query should be retried based on error type.
        
        Args:
            error: Exception that occurred during vessel query
            attempt: Current attempt number
            
        Returns:
            True if the query should be retried, False otherwise
        """
        error_str = str(error).lower()
        error_type = type(error).__name__
        
        # Network-related errors that should be retried
        retryable_errors = [
            'timeout',
            'connection',
            'network',
            'unreachable',
            'refused',
            'reset',
            'temporary',
            'unavailable'
        ]
        
        # Database-related errors that should be retried
        retryable_db_errors = [
            'database is locked',
            'database connection',
            'connection pool',
            'too many connections'
        ]
        
        # Authentication and configuration errors that should NOT be retried
        non_retryable_errors = [
            'authentication',
            'unauthorized',
            'forbidden',
            'invalid credentials',
            'permission denied',
            'configuration error',
            'invalid configuration',
            'ssl certificate',
            'certificate verify failed'
        ]
        
        # Check for non-retryable errors first
        for non_retryable in non_retryable_errors:
            if non_retryable in error_str:
                logger.info(f"Non-retryable error detected for attempt {attempt}: {non_retryable}")
                return False
        
        # Check for retryable network errors
        for retryable in retryable_errors:
            if retryable in error_str:
                logger.debug(f"Retryable network error detected for attempt {attempt}: {retryable}")
                return True
        
        # Check for retryable database errors
        for retryable_db in retryable_db_errors:
            if retryable_db in error_str:
                logger.debug(f"Retryable database error detected for attempt {attempt}: {retryable_db}")
                return True
        
        # Check specific exception types
        if error_type in ['TimeoutError', 'ConnectionError', 'OSError', 'socket.error']:
            logger.debug(f"Retryable exception type detected for attempt {attempt}: {error_type}")
            return True
        
        # Default to retry for unknown errors (conservative approach)
        logger.debug(f"Unknown error type for attempt {attempt}, defaulting to retry: {error_type}")
        return True
    
    async def _handle_database_connection_error(self, error: Exception, vessel_id: str) -> None:
        """
        Handle database connection errors gracefully.
        
        Args:
            error: Database connection error
            vessel_id: ID of the vessel that failed
        """
        logger.error(f"Database connection error for vessel {vessel_id}: {error}")
        
        # Try to reinitialize the database connection for this vessel
        try:
            # Clear cached client wrapper to force reconnection
            data_collector = DataCollector(self.config)
            if hasattr(data_collector, '_client_cache') and vessel_id in data_collector._client_cache:
                old_client = data_collector._client_cache[vessel_id]
                try:
                    old_client.close()
                except:
                    pass  # Ignore errors when closing old connection
                del data_collector._client_cache[vessel_id]
                logger.info(f"Cleared cached connection for vessel {vessel_id}")
        except Exception as cleanup_error:
            logger.warning(f"Failed to cleanup connection for vessel {vessel_id}: {cleanup_error}")
    
    async def _handle_authentication_error(self, error: Exception, vessel_id: str) -> None:
        """
        Handle authentication errors without retry.
        
        Args:
            error: Authentication error
            vessel_id: ID of the vessel that failed
        """
        logger.error(f"Authentication error for vessel {vessel_id} - configuration issue: {error}")
        
        # Log detailed error for troubleshooting
        vessel_config = self.config.get_vessel_connection(vessel_id)
        logger.error(
            f"Vessel {vessel_id} authentication failed. "
            f"Host: {vessel_config.host}, Port: {vessel_config.port}, "
            f"Database: {vessel_config.database}, Username: {vessel_config.username}"
        )
    
    async def _handle_network_timeout_error(self, error: Exception, vessel_id: str, attempt: int) -> None:
        """
        Handle network timeout errors with appropriate retry logic.
        
        Args:
            error: Network timeout error
            vessel_id: ID of the vessel that failed
            attempt: Current attempt number
        """
        logger.warning(f"Network timeout for vessel {vessel_id} on attempt {attempt}: {error}")
        
        # Log network diagnostics information
        vessel_config = self.config.get_vessel_connection(vessel_id)
        logger.info(
            f"Network timeout details for vessel {vessel_id}: "
            f"Host: {vessel_config.host}, Port: {vessel_config.port}"
        )
    
    def get_scheduler_stats(self) -> Dict[str, Any]:
        """
        Get scheduler statistics and status.
        
        Returns:
            Dictionary containing scheduler statistics
        """
        return {
            'is_running': self._is_running,
            'timezone': self.scheduling_config.timezone,
            'daily_monitoring_time': f"{self.scheduling_config.daily_monitoring_hour:02d}:{self.scheduling_config.daily_monitoring_minute:02d}",
            'next_monitoring_time': self.get_next_monitoring_time().isoformat() if self.get_next_monitoring_time() else None,
            'total_jobs': len(self.scheduler.get_jobs()),
            'registered_callbacks': list(self._job_callbacks.keys()),
            'max_retry_attempts': self.max_retry_attempts,
            'base_retry_delay': self.base_retry_delay
        }
    
    async def _emit_scheduler_run_start(self, run_log: SchedulerRunLog) -> None:
        """
        Emit WebSocket event when scheduler run starts.
        
        Args:
            run_log: SchedulerRunLog instance with run details
        """
        if not self.websocket_manager:
            return
        
        try:
            await self.websocket_manager.broadcast({
                "type": "scheduler_run_start",
                "data": {
                    "run_id": run_log.run_id,
                    "start_time": run_log.start_time.isoformat(),
                    "total_vessels": run_log.total_vessels,
                    "status": run_log.status
                }
            })
            logger.debug(f"Emitted scheduler run start event for {run_log.run_id}")
        except Exception as e:
            logger.warning(f"Failed to emit scheduler run start event: {e}")
    
    async def _emit_scheduler_run_complete(self, run_log: SchedulerRunLog) -> None:
        """
        Emit WebSocket event when scheduler run completes.
        
        Args:
            run_log: SchedulerRunLog instance with completion details
        """
        if not self.websocket_manager:
            return
        
        try:
            await self.websocket_manager.broadcast({
                "type": "scheduler_run_complete",
                "data": {
                    "run_id": run_log.run_id,
                    "start_time": run_log.start_time.isoformat(),
                    "end_time": run_log.end_time.isoformat() if run_log.end_time else None,
                    "status": run_log.status,
                    "total_vessels": run_log.total_vessels,
                    "successful_vessels": run_log.successful_vessels,
                    "failed_vessels": run_log.failed_vessels,
                    "retry_attempts": run_log.retry_attempts,
                    "duration_seconds": run_log.duration.total_seconds() if run_log.duration else None,
                    "error_message": run_log.error_message
                }
            })
            logger.debug(f"Emitted scheduler run complete event for {run_log.run_id}")
        except Exception as e:
            logger.warning(f"Failed to emit scheduler run complete event: {e}")
    
    async def _emit_vessel_query_progress(
        self, 
        run_id: str, 
        vessel_id: str, 
        attempt: int, 
        success: bool, 
        successful_count: int, 
        failed_count: int
    ) -> None:
        """
        Emit WebSocket event for vessel query progress updates.
        
        Args:
            run_id: Scheduler run ID
            vessel_id: ID of the vessel being queried
            attempt: Current attempt number
            success: Whether the query was successful
            successful_count: Current count of successful vessels
            failed_count: Current count of failed vessels
        """
        if not self.websocket_manager:
            return
        
        try:
            total_vessels = self.config.get_vessel_count() if hasattr(self.config, 'get_vessel_count') else len(self.config.vessel_databases)
            progress_percentage = ((successful_count + failed_count) / max(total_vessels, 1)) * 100
            
            await self.websocket_manager.broadcast({
                "type": "scheduler_run_progress",
                "data": {
                    "run_id": run_id,
                    "vessel_id": vessel_id,
                    "attempt": attempt,
                    "success": success,
                    "successful_vessels": successful_count,
                    "failed_vessels": failed_count,
                    "total_vessels": total_vessels,
                    "progress_percentage": round(progress_percentage, 1),
                    "timestamp": datetime.utcnow().isoformat()
                }
            })
            logger.debug(f"Emitted vessel query progress for {vessel_id} (attempt {attempt}): {success}")
        except Exception as e:
            logger.warning(f"Failed to emit vessel query progress event: {e}")