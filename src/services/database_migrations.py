"""
Database migration utilities for the Infrastructure Monitoring Agent.

This module provides database migration functionality to handle schema updates
and data migrations for the SQLite database.
"""

import sqlite3
import logging
from datetime import datetime
from typing import List, Dict, Any
from pathlib import Path

logger = logging.getLogger(__name__)


class DatabaseMigration:
    """
    Database migration manager for handling schema updates.
    """
    
    def __init__(self, database_path: str):
        """
        Initialize the migration manager.
        
        Args:
            database_path: Path to the SQLite database file
        """
        self.database_path = database_path
        self.migrations = self._get_migrations()
    
    def _get_migrations(self) -> List[Dict[str, Any]]:
        """
        Define all database migrations.
        
        Returns:
            List of migration definitions
        """
        return [
            {
                'version': 1,
                'description': 'Initial schema creation',
                'sql': self._get_initial_schema_sql()
            },
            {
                'version': 2,
                'description': 'Add JIRA ticket tracking',
                'sql': self._get_jira_tracking_sql()
            },
            {
                'version': 3,
                'description': 'Add system state management',
                'sql': self._get_system_state_sql()
            },
            {
                'version': 4,
                'description': 'Add scheduler run logging tables',
                'sql': self._get_scheduler_run_logging_sql()
            }
        ]
    
    def _get_initial_schema_sql(self) -> List[str]:
        """Get SQL for initial schema creation."""
        return [
            """
            CREATE TABLE IF NOT EXISTS schema_version (
                version INTEGER PRIMARY KEY,
                applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                description TEXT
            )
            """,
            """
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
            """,
            """
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
            """,
            """
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
            """
        ]
    
    def _get_jira_tracking_sql(self) -> List[str]:
        """Get SQL for JIRA ticket tracking."""
        return [
            """
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
            """
        ]
    
    def _get_system_state_sql(self) -> List[str]:
        """Get SQL for system state management."""
        return [
            """
            CREATE TABLE IF NOT EXISTS system_state (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                state_key TEXT UNIQUE NOT NULL,
                state_value TEXT NOT NULL,
                state_type TEXT NOT NULL,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        ]
    
    def _get_scheduler_run_logging_sql(self) -> List[str]:
        """Get SQL for scheduler run logging tables."""
        return [
            """
            CREATE TABLE IF NOT EXISTS scheduler_runs (
                id TEXT PRIMARY KEY,
                start_time TIMESTAMP NOT NULL,
                end_time TIMESTAMP,
                total_vessels INTEGER NOT NULL,
                successful_vessels INTEGER DEFAULT 0,
                failed_vessels INTEGER DEFAULT 0,
                retry_attempts INTEGER DEFAULT 0,
                status TEXT NOT NULL CHECK (status IN ('running', 'completed', 'failed')),
                duration_seconds REAL,
                error_message TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS scheduler_vessel_results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                vessel_id TEXT NOT NULL,
                attempt_number INTEGER NOT NULL,
                success BOOLEAN NOT NULL,
                query_duration_seconds REAL,
                error_message TEXT,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (run_id) REFERENCES scheduler_runs (id) ON DELETE CASCADE
            )
            """
        ]
    
    def get_current_version(self) -> int:
        """
        Get the current database schema version.
        
        Returns:
            Current schema version number
        """
        try:
            with sqlite3.connect(self.database_path) as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT MAX(version) FROM schema_version
                """)
                result = cursor.fetchone()
                return result[0] if result[0] is not None else 0
        except sqlite3.OperationalError:
            # Table doesn't exist, database is at version 0
            return 0
    
    def apply_migration(self, migration: Dict[str, Any]) -> None:
        """
        Apply a single migration.
        
        Args:
            migration: Migration definition
        """
        version = migration['version']
        description = migration['description']
        sql_statements = migration['sql']
        
        logger.info(f"Applying migration {version}: {description}")
        
        with sqlite3.connect(self.database_path) as conn:
            cursor = conn.cursor()
            
            try:
                # Execute all SQL statements in the migration
                for sql in sql_statements:
                    cursor.execute(sql)
                
                # Record the migration
                cursor.execute("""
                    INSERT OR REPLACE INTO schema_version (version, description)
                    VALUES (?, ?)
                """, (version, description))
                
                conn.commit()
                logger.info(f"Successfully applied migration {version}")
                
            except Exception as e:
                conn.rollback()
                logger.error(f"Failed to apply migration {version}: {e}")
                raise
    
    def migrate_to_latest(self) -> None:
        """
        Migrate the database to the latest schema version.
        """
        current_version = self.get_current_version()
        latest_version = max(m['version'] for m in self.migrations)
        
        if current_version >= latest_version:
            logger.info(f"Database is already at latest version {current_version}")
            return
        
        logger.info(
            f"Migrating database from version {current_version} to {latest_version}"
        )
        
        # Apply migrations in order
        for migration in self.migrations:
            if migration['version'] > current_version:
                self.apply_migration(migration)
        
        # Create indexes after all migrations
        self._create_indexes()
        
        logger.info("Database migration completed successfully")
    
    def _create_indexes(self) -> None:
        """Create database indexes for performance."""
        indexes = [
            "CREATE INDEX IF NOT EXISTS idx_sla_violation_vessel_component ON sla_violation_history(vessel_id, component_type)",
            "CREATE INDEX IF NOT EXISTS idx_sla_violation_start ON sla_violation_history(violation_start)",
            "CREATE INDEX IF NOT EXISTS idx_component_status_vessel_component ON component_status_history(vessel_id, component_type)",
            "CREATE INDEX IF NOT EXISTS idx_component_status_recorded ON component_status_history(recorded_at)",
            "CREATE INDEX IF NOT EXISTS idx_alert_vessel_component ON alert_history(vessel_id, component_type)",
            "CREATE INDEX IF NOT EXISTS idx_jira_tickets_vessel_component ON jira_tickets(vessel_id, component_type)",
            "CREATE INDEX IF NOT EXISTS idx_jira_tickets_status ON jira_tickets(ticket_status)",
            "CREATE INDEX IF NOT EXISTS idx_system_state_key ON system_state(state_key)",
            "CREATE INDEX IF NOT EXISTS idx_scheduler_runs_start_time ON scheduler_runs(start_time)",
            "CREATE INDEX IF NOT EXISTS idx_scheduler_runs_status ON scheduler_runs(status)",
            "CREATE INDEX IF NOT EXISTS idx_scheduler_vessel_results_run_id ON scheduler_vessel_results(run_id)",
            "CREATE INDEX IF NOT EXISTS idx_scheduler_vessel_results_vessel_id ON scheduler_vessel_results(vessel_id)",
            "CREATE INDEX IF NOT EXISTS idx_scheduler_vessel_results_timestamp ON scheduler_vessel_results(timestamp)"
        ]
        
        with sqlite3.connect(self.database_path) as conn:
            cursor = conn.cursor()
            for index_sql in indexes:
                try:
                    cursor.execute(index_sql)
                except sqlite3.OperationalError as e:
                    logger.warning(f"Failed to create index: {e}")
            conn.commit()
        
        logger.info("Database indexes created successfully")
    
    def backup_database(self, backup_path: str) -> None:
        """
        Create a backup of the database before migration.
        
        Args:
            backup_path: Path for the backup file
        """
        import shutil
        
        if Path(self.database_path).exists():
            shutil.copy2(self.database_path, backup_path)
            logger.info(f"Database backed up to {backup_path}")
        else:
            logger.warning("Database file does not exist, no backup created")
    
    def validate_schema(self) -> bool:
        """
        Validate that the database schema is correct.
        
        Returns:
            True if schema is valid, False otherwise
        """
        try:
            with sqlite3.connect(self.database_path) as conn:
                cursor = conn.cursor()
                
                # Check that all expected tables exist
                expected_tables = [
                    'schema_version',
                    'sla_violation_history',
                    'component_status_history',
                    'alert_history',
                    'jira_tickets',
                    'system_state',
                    'scheduler_runs',
                    'scheduler_vessel_results'
                ]
                
                cursor.execute("""
                    SELECT name FROM sqlite_master WHERE type='table'
                """)
                existing_tables = [row[0] for row in cursor.fetchall()]
                
                missing_tables = set(expected_tables) - set(existing_tables)
                if missing_tables:
                    logger.error(f"Missing tables: {missing_tables}")
                    return False
                
                # Check schema version
                current_version = self.get_current_version()
                latest_version = max(m['version'] for m in self.migrations)
                
                if current_version < latest_version:
                    logger.error(
                        f"Schema version {current_version} is behind latest {latest_version}"
                    )
                    return False
                
                logger.info("Database schema validation passed")
                return True
                
        except Exception as e:
            logger.error(f"Schema validation failed: {e}")
            return False


def migrate_database(database_path: str, backup: bool = True) -> None:
    """
    Convenience function to migrate a database to the latest version.
    
    Args:
        database_path: Path to the database file
        backup: Whether to create a backup before migration
    """
    migration_manager = DatabaseMigration(database_path)
    
    if backup:
        backup_path = f"{database_path}.backup.{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        migration_manager.backup_database(backup_path)
    
    migration_manager.migrate_to_latest()
    
    if not migration_manager.validate_schema():
        raise RuntimeError("Database schema validation failed after migration")