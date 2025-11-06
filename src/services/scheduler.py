"""
Scheduling service for the Infrastructure Monitoring Agent.

This module provides background scheduling using APScheduler for daily vessel monitoring
and orchestration of the complete monitoring workflow.
"""

import logging
import asyncio
from datetime import datetime, time
from typing import Optional, Dict, Any, Callable
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.events import EVENT_JOB_EXECUTED, EVENT_JOB_ERROR, JobExecutionEvent

from ..config.config_models import Config, SchedulingConfig
from .data_collector import DataCollector
from .sla_analyzer import SLAAnalyzer
from .alert_manager import AlertManager
from .jira_service import JIRAService
from .database import DatabaseService


logger = logging.getLogger(__name__)


class MonitoringScheduler:
    """
    Background scheduler for daily vessel monitoring.
    
    This class manages the scheduling of automated monitoring tasks using APScheduler,
    providing configurable daily execution of the monitoring workflow and coordination
    of all monitoring services.
    """
    
    def __init__(self, config: Config):
        """
        Initialize the monitoring scheduler.
        
        Args:
            config: Application configuration containing scheduling parameters
        """
        self.config = config
        self.scheduling_config = config.scheduling
        
        # Initialize APScheduler
        self.scheduler = AsyncIOScheduler(timezone=self.scheduling_config.timezone)
        
        # Add job execution event listeners
        self.scheduler.add_listener(self._job_executed_listener, EVENT_JOB_EXECUTED)
        self.scheduler.add_listener(self._job_error_listener, EVENT_JOB_ERROR)
        
        # Track scheduler state
        self._is_running = False
        self._job_callbacks: Dict[str, Callable] = {}
        
        logger.info(
            f"Initialized MonitoringScheduler with timezone: {self.scheduling_config.timezone}, "
            f"daily monitoring at {self.scheduling_config.daily_monitoring_hour:02d}:"
            f"{self.scheduling_config.daily_monitoring_minute:02d}"
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
        Execute the complete daily monitoring workflow.
        
        This method serves as the entry point for the scheduled monitoring job
        and coordinates the execution of all monitoring services.
        """
        logger.info("Starting scheduled daily monitoring workflow")
        start_time = datetime.now()
        
        try:
            # Import and initialize the monitoring orchestrator
            from .monitoring_orchestrator import MonitoringOrchestrator
            
            orchestrator = MonitoringOrchestrator(self.config)
            
            # Execute the complete monitoring workflow
            workflow_result = await orchestrator.execute_daily_monitoring()
            
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
            'registered_callbacks': list(self._job_callbacks.keys())
        }