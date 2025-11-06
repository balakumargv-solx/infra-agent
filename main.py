#!/usr/bin/env python3
"""
Infrastructure Monitoring Agent
Main entry point for the application

This module serves as the main entry point for the Infrastructure Monitoring Agent.
It initializes all services, starts the web server and scheduler, and handles
graceful shutdown.
"""

import asyncio
import logging
import signal
import sys
import os
from pathlib import Path
from typing import Optional
import uvicorn
from contextlib import asynccontextmanager

# Add src to Python path
sys.path.insert(0, str(Path(__file__).parent / "src"))

from src.config.config_loader import ConfigLoader
from src.web.app import create_app
from src.services.scheduler import MonitoringScheduler
from src.services.security_manager import get_security_manager
from src.services.database_migrations import migrate_database

logger = logging.getLogger(__name__)


class ApplicationManager:
    """
    Application manager that handles startup, shutdown, and lifecycle management.
    """
    
    def __init__(self):
        self.config: Optional[ConfigLoader] = None
        self.scheduler: Optional[MonitoringScheduler] = None
        self.server: Optional[uvicorn.Server] = None
        self.shutdown_event = asyncio.Event()
        
    def setup_logging(self):
        """Configure application logging."""
        log_level = os.getenv('LOG_LEVEL', 'INFO').upper()
        log_file = os.getenv('LOG_FILE', 'monitoring_agent.log')
        
        # Create logs directory if it doesn't exist
        log_path = Path(log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        
        # Configure logging
        logging.basicConfig(
            level=getattr(logging, log_level),
            format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            handlers=[
                logging.StreamHandler(),
                logging.FileHandler(log_file)
            ]
        )
        
        # Set specific log levels for noisy libraries
        logging.getLogger('uvicorn.access').setLevel(logging.WARNING)
        logging.getLogger('apscheduler').setLevel(logging.WARNING)
        
        logger.info(f"Logging configured - Level: {log_level}, File: {log_file}")
    
    def setup_signal_handlers(self):
        """Setup signal handlers for graceful shutdown."""
        def signal_handler(signum, frame):
            logger.info(f"Received signal {signum}, initiating graceful shutdown...")
            self.shutdown_event.set()
        
        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)
    
    async def initialize_services(self):
        """Initialize all application services."""
        try:
            # Load configuration
            config_loader = ConfigLoader()
            self.config = config_loader.load_config()
            
            logger.info("Infrastructure Monitoring Agent starting up...")
            logger.info(f"Configuration loaded for {len(self.config.vessel_databases)} vessels")
            
            # Initialize database (run migrations)
            database_path = os.getenv('DATABASE_PATH', './monitoring_agent.db')
            logger.info(f"Initializing database: {database_path}")
            migrate_database(database_path, backup=True)
            
            # Initialize security manager
            security_manager = get_security_manager()
            security_check = security_manager.perform_security_check()
            logger.info(f"Security check completed: {security_check}")
            
            # Validate credentials
            credential_validation = security_check['credential_validation']
            for service, is_valid in credential_validation.items():
                if not is_valid:
                    logger.warning(f"Invalid credentials for {service} - some features may not work")
            
            # Initialize scheduler
            self.scheduler = MonitoringScheduler(self.config)
            
            logger.info("All services initialized successfully")
            
        except Exception as e:
            logger.error(f"Failed to initialize services: {e}")
            raise
    
    async def start_services(self):
        """Start all application services."""
        try:
            # Start scheduler
            if self.scheduler:
                self.scheduler.start()
                next_run = self.scheduler.get_next_monitoring_time()
                logger.info(f"Monitoring scheduler started - next run: {next_run}")
            
            # Create FastAPI application
            app = create_app(self.config)
            
            # Configure uvicorn server
            server_config = uvicorn.Config(
                app=app,
                host=self.config.web_server.host,
                port=self.config.web_server.port,
                log_level="warning",  # Reduce uvicorn log noise
                access_log=False,     # We handle access logging in middleware
                server_header=False,
                date_header=False
            )
            
            self.server = uvicorn.Server(server_config)
            
            logger.info(
                f"Web dashboard starting at http://{self.config.web_server.host}:{self.config.web_server.port}"
            )
            
            # Start server in background task
            server_task = asyncio.create_task(self.server.serve())
            
            # Wait for shutdown signal
            await self.shutdown_event.wait()
            
            # Graceful shutdown
            await self.shutdown_services()
            
            # Wait for server to finish
            if not server_task.done():
                server_task.cancel()
                try:
                    await server_task
                except asyncio.CancelledError:
                    pass
            
        except Exception as e:
            logger.error(f"Failed to start services: {e}")
            raise
    
    async def shutdown_services(self):
        """Gracefully shutdown all services."""
        logger.info("Shutting down services...")
        
        try:
            # Stop scheduler
            if self.scheduler:
                self.scheduler.shutdown()
                logger.info("Scheduler stopped")
            
            # Stop web server
            if self.server:
                self.server.should_exit = True
                logger.info("Web server stopped")
            
            # Perform final security audit
            security_manager = get_security_manager()
            audit_logger = security_manager.get_audit_logger()
            audit_logger.log_system_event(
                'application_shutdown',
                'main',
                'graceful_shutdown',
                True,
                {'reason': 'signal_received'}
            )
            
            logger.info("All services shut down successfully")
            
        except Exception as e:
            logger.error(f"Error during shutdown: {e}")
    
    async def run(self):
        """Run the complete application lifecycle."""
        try:
            self.setup_logging()
            self.setup_signal_handlers()
            
            await self.initialize_services()
            await self.start_services()
            
        except KeyboardInterrupt:
            logger.info("Application interrupted by user")
        except Exception as e:
            logger.error(f"Application failed: {e}")
            sys.exit(1)
        finally:
            logger.info("Application shutdown complete")


async def main():
    """Main application entry point."""
    app_manager = ApplicationManager()
    await app_manager.run()


def cli_main():
    """CLI entry point for the application."""
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nApplication interrupted by user")
        sys.exit(0)
    except Exception as e:
        print(f"Application failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    cli_main()