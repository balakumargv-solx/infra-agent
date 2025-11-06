"""
Security and credential management for the Infrastructure Monitoring Agent.

This module provides secure credential management, API authentication,
and comprehensive audit logging for compliance and troubleshooting.
"""

import os
import hashlib
import secrets
import logging
from datetime import datetime, timedelta
from typing import Dict, Optional, Any, List
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
import base64
import json
from pathlib import Path

logger = logging.getLogger(__name__)


class CredentialManager:
    """
    Secure credential management system for sensitive data.
    
    This class handles encryption/decryption of credentials and secure
    storage of sensitive configuration data.
    """
    
    def __init__(self, master_key: Optional[str] = None):
        """
        Initialize the credential manager.
        
        Args:
            master_key: Optional master key for encryption (uses env var if not provided)
        """
        self.master_key = master_key or os.getenv('MONITORING_MASTER_KEY')
        if not self.master_key:
            # Generate a new master key if none provided
            self.master_key = base64.urlsafe_b64encode(os.urandom(32)).decode()
            logger.warning(
                "No master key provided. Generated new key. "
                "Set MONITORING_MASTER_KEY environment variable for production."
            )
        
        self._cipher_suite = self._create_cipher_suite()
    
    def _create_cipher_suite(self) -> Fernet:
        """Create encryption cipher suite from master key."""
        # Derive key from master key using PBKDF2
        salt = b'monitoring_agent_salt'  # Fixed salt for consistency
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=100000,
        )
        key = base64.urlsafe_b64encode(kdf.derive(self.master_key.encode()))
        return Fernet(key)
    
    def encrypt_credential(self, credential: str) -> str:
        """
        Encrypt a credential string.
        
        Args:
            credential: Plain text credential
            
        Returns:
            Encrypted credential as base64 string
        """
        encrypted_data = self._cipher_suite.encrypt(credential.encode())
        return base64.urlsafe_b64encode(encrypted_data).decode()
    
    def decrypt_credential(self, encrypted_credential: str) -> str:
        """
        Decrypt a credential string.
        
        Args:
            encrypted_credential: Encrypted credential as base64 string
            
        Returns:
            Decrypted plain text credential
        """
        try:
            encrypted_data = base64.urlsafe_b64decode(encrypted_credential.encode())
            decrypted_data = self._cipher_suite.decrypt(encrypted_data)
            return decrypted_data.decode()
        except Exception as e:
            logger.error(f"Failed to decrypt credential: {e}")
            raise ValueError("Invalid or corrupted credential")
    
    def get_influxdb_credentials(self, vessel_id: str) -> Dict[str, str]:
        """
        Get InfluxDB credentials for a specific vessel.
        
        Args:
            vessel_id: ID of the vessel
            
        Returns:
            Dictionary containing InfluxDB connection parameters
        """
        # Try vessel-specific environment variables first
        env_prefix = f"INFLUXDB_{vessel_id.upper()}"
        
        # Check for global InfluxDB configuration first
        global_url = os.getenv('INFLUXDB_URL')
        global_token = os.getenv('INFLUXDB_TOKEN')
        global_org = os.getenv('INFLUXDB_ORG')
        global_bucket = os.getenv('INFLUXDB_BUCKET')
        
        if global_url and global_token:
            # Use global InfluxDB configuration
            credentials = {
                'host': global_url,
                'port': 443 if global_url.startswith('https') else 8086,
                'database': global_bucket or 'monitoring',
                'username': global_org or 'monitoring',
                'password': global_token,
                'ssl': global_url.startswith('https'),
                'verify_ssl': True
            }
        else:
            # Fall back to vessel-specific configuration
            credentials = {
                'host': os.getenv(f"{env_prefix}_HOST", f"influxdb-{vessel_id}.example.com"),
                'port': int(os.getenv(f"{env_prefix}_PORT", "8086")),
                'database': os.getenv(f"{env_prefix}_DATABASE", f"vessel_{vessel_id}"),
                'username': os.getenv(f"{env_prefix}_USERNAME", "monitoring"),
                'password': os.getenv(f"{env_prefix}_PASSWORD", ""),
                'ssl': os.getenv(f"{env_prefix}_SSL", "true").lower() == "true",
                'verify_ssl': os.getenv(f"{env_prefix}_VERIFY_SSL", "true").lower() == "true"
            }
        
        # Decrypt password if it appears to be encrypted
        if credentials['password'].startswith('enc:'):
            try:
                credentials['password'] = self.decrypt_credential(
                    credentials['password'][4:]  # Remove 'enc:' prefix
                )
            except ValueError:
                logger.warning(f"Failed to decrypt password for vessel {vessel_id}")
                credentials['password'] = ""
        
        return credentials
    
    def get_jira_credentials(self) -> Dict[str, str]:
        """
        Get JIRA API credentials.
        
        Returns:
            Dictionary containing JIRA connection parameters
        """
        credentials = {
            'server': os.getenv('JIRA_SERVER', 'https://your-company.atlassian.net'),
            'username': os.getenv('JIRA_USERNAME', ''),
            'api_token': os.getenv('JIRA_API_TOKEN', ''),
            'project_key': os.getenv('JIRA_PROJECT_KEY', 'INFRA')
        }
        
        # Decrypt API token if encrypted
        if credentials['api_token'].startswith('enc:'):
            try:
                credentials['api_token'] = self.decrypt_credential(
                    credentials['api_token'][4:]
                )
            except ValueError:
                logger.warning("Failed to decrypt JIRA API token")
                credentials['api_token'] = ""
        
        return credentials
    
    def validate_credentials(self) -> Dict[str, bool]:
        """
        Validate that all required credentials are available.
        
        Returns:
            Dictionary indicating which credential sets are valid
        """
        validation_results = {}
        
        # Validate JIRA credentials
        jira_creds = self.get_jira_credentials()
        validation_results['jira'] = all([
            jira_creds['server'],
            jira_creds['username'],
            jira_creds['api_token']
        ])
        
        # Validate at least one vessel's InfluxDB credentials
        test_vessel_id = "001"  # Test with vessel 001
        influx_creds = self.get_influxdb_credentials(test_vessel_id)
        validation_results['influxdb'] = all([
            influx_creds['host'],
            influx_creds['username'],
            influx_creds['password']
        ])
        
        return validation_results


class APIAuthenticator:
    """
    API authentication manager for web dashboard access.
    
    This class handles API key generation, validation, and session management
    for the web dashboard interface.
    """
    
    def __init__(self, secret_key: Optional[str] = None):
        """
        Initialize the API authenticator.
        
        Args:
            secret_key: Secret key for token generation
        """
        self.secret_key = secret_key or os.getenv('API_SECRET_KEY', self._generate_secret_key())
        self.active_tokens: Dict[str, Dict[str, Any]] = {}
        self.token_expiry_hours = int(os.getenv('API_TOKEN_EXPIRY_HOURS', '24'))
    
    def _generate_secret_key(self) -> str:
        """Generate a random secret key."""
        return secrets.token_urlsafe(32)
    
    def generate_api_token(
        self,
        user_id: str,
        permissions: List[str] = None
    ) -> str:
        """
        Generate an API token for a user.
        
        Args:
            user_id: Unique identifier for the user
            permissions: List of permissions for the token
            
        Returns:
            Generated API token
        """
        if permissions is None:
            permissions = ['read', 'dashboard']
        
        token = secrets.token_urlsafe(32)
        expires_at = datetime.utcnow() + timedelta(hours=self.token_expiry_hours)
        
        self.active_tokens[token] = {
            'user_id': user_id,
            'permissions': permissions,
            'created_at': datetime.utcnow(),
            'expires_at': expires_at,
            'last_used': datetime.utcnow()
        }
        
        logger.info(f"Generated API token for user {user_id}")
        return token
    
    def validate_token(self, token: str) -> Optional[Dict[str, Any]]:
        """
        Validate an API token.
        
        Args:
            token: API token to validate
            
        Returns:
            Token information if valid, None otherwise
        """
        if token not in self.active_tokens:
            return None
        
        token_info = self.active_tokens[token]
        
        # Check if token has expired
        if datetime.utcnow() > token_info['expires_at']:
            del self.active_tokens[token]
            return None
        
        # Update last used timestamp
        token_info['last_used'] = datetime.utcnow()
        
        return token_info
    
    def revoke_token(self, token: str) -> bool:
        """
        Revoke an API token.
        
        Args:
            token: API token to revoke
            
        Returns:
            True if token was revoked, False if not found
        """
        if token in self.active_tokens:
            user_id = self.active_tokens[token]['user_id']
            del self.active_tokens[token]
            logger.info(f"Revoked API token for user {user_id}")
            return True
        return False
    
    def cleanup_expired_tokens(self) -> int:
        """
        Clean up expired tokens.
        
        Returns:
            Number of tokens cleaned up
        """
        current_time = datetime.utcnow()
        expired_tokens = [
            token for token, info in self.active_tokens.items()
            if current_time > info['expires_at']
        ]
        
        for token in expired_tokens:
            del self.active_tokens[token]
        
        if expired_tokens:
            logger.info(f"Cleaned up {len(expired_tokens)} expired tokens")
        
        return len(expired_tokens)
    
    def get_basic_auth_credentials(self) -> Optional[Dict[str, str]]:
        """
        Get basic authentication credentials from environment.
        
        Returns:
            Dictionary with username and password, or None if not configured
        """
        username = os.getenv('DASHBOARD_USERNAME')
        password = os.getenv('DASHBOARD_PASSWORD')
        
        if username and password:
            return {'username': username, 'password': password}
        
        return None


class AuditLogger:
    """
    Comprehensive audit logging for compliance and troubleshooting.
    
    This class provides structured logging for security events, API access,
    and system operations.
    """
    
    def __init__(self, log_file_path: Optional[str] = None):
        """
        Initialize the audit logger.
        
        Args:
            log_file_path: Path to audit log file
        """
        self.log_file_path = log_file_path or os.getenv(
            'AUDIT_LOG_PATH', 
            'logs/audit.log'
        )
        
        # Ensure log directory exists
        Path(self.log_file_path).parent.mkdir(parents=True, exist_ok=True)
        
        # Set up audit logger
        self.audit_logger = logging.getLogger('audit')
        self.audit_logger.setLevel(logging.INFO)
        
        # Create file handler if not already exists
        if not self.audit_logger.handlers:
            handler = logging.FileHandler(self.log_file_path)
            formatter = logging.Formatter(
                '%(asctime)s - %(levelname)s - %(message)s'
            )
            handler.setFormatter(formatter)
            self.audit_logger.addHandler(handler)
    
    def log_authentication_event(
        self,
        event_type: str,
        user_id: str,
        success: bool,
        details: Optional[Dict[str, Any]] = None
    ) -> None:
        """
        Log authentication events.
        
        Args:
            event_type: Type of authentication event
            user_id: User identifier
            success: Whether the event was successful
            details: Additional event details
        """
        event_data = {
            'event_type': 'authentication',
            'auth_event': event_type,
            'user_id': user_id,
            'success': success,
            'timestamp': datetime.utcnow().isoformat(),
            'details': details or {}
        }
        
        self.audit_logger.info(json.dumps(event_data))
    
    def log_api_access(
        self,
        endpoint: str,
        method: str,
        user_id: Optional[str],
        status_code: int,
        response_time_ms: float,
        ip_address: Optional[str] = None
    ) -> None:
        """
        Log API access events.
        
        Args:
            endpoint: API endpoint accessed
            method: HTTP method
            user_id: User identifier (if authenticated)
            status_code: HTTP status code
            response_time_ms: Response time in milliseconds
            ip_address: Client IP address
        """
        event_data = {
            'event_type': 'api_access',
            'endpoint': endpoint,
            'method': method,
            'user_id': user_id,
            'status_code': status_code,
            'response_time_ms': response_time_ms,
            'ip_address': ip_address,
            'timestamp': datetime.utcnow().isoformat()
        }
        
        self.audit_logger.info(json.dumps(event_data))
    
    def log_system_event(
        self,
        event_type: str,
        component: str,
        action: str,
        success: bool,
        details: Optional[Dict[str, Any]] = None
    ) -> None:
        """
        Log system events.
        
        Args:
            event_type: Type of system event
            component: System component involved
            action: Action performed
            success: Whether the action was successful
            details: Additional event details
        """
        event_data = {
            'event_type': 'system',
            'system_event': event_type,
            'component': component,
            'action': action,
            'success': success,
            'timestamp': datetime.utcnow().isoformat(),
            'details': details or {}
        }
        
        self.audit_logger.info(json.dumps(event_data))
    
    def log_security_event(
        self,
        event_type: str,
        severity: str,
        description: str,
        details: Optional[Dict[str, Any]] = None
    ) -> None:
        """
        Log security events.
        
        Args:
            event_type: Type of security event
            severity: Event severity (low, medium, high, critical)
            description: Event description
            details: Additional event details
        """
        event_data = {
            'event_type': 'security',
            'security_event': event_type,
            'severity': severity,
            'description': description,
            'timestamp': datetime.utcnow().isoformat(),
            'details': details or {}
        }
        
        self.audit_logger.warning(json.dumps(event_data))
    
    def log_data_access(
        self,
        data_type: str,
        vessel_id: Optional[str],
        user_id: Optional[str],
        action: str,
        success: bool
    ) -> None:
        """
        Log data access events for compliance.
        
        Args:
            data_type: Type of data accessed
            vessel_id: Vessel ID (if applicable)
            user_id: User identifier
            action: Action performed on data
            success: Whether the action was successful
        """
        event_data = {
            'event_type': 'data_access',
            'data_type': data_type,
            'vessel_id': vessel_id,
            'user_id': user_id,
            'action': action,
            'success': success,
            'timestamp': datetime.utcnow().isoformat()
        }
        
        self.audit_logger.info(json.dumps(event_data))


class SecurityManager:
    """
    Main security manager that coordinates all security components.
    """
    
    def __init__(self):
        """Initialize the security manager."""
        self.credential_manager = CredentialManager()
        self.api_authenticator = APIAuthenticator()
        self.audit_logger = AuditLogger()
        
        # Validate credentials on startup
        validation_results = self.credential_manager.validate_credentials()
        for service, is_valid in validation_results.items():
            if not is_valid:
                self.audit_logger.log_security_event(
                    'credential_validation_failed',
                    'high',
                    f'Invalid credentials for {service}',
                    {'service': service}
                )
                logger.warning(f"Invalid credentials for {service}")
    
    def get_credential_manager(self) -> CredentialManager:
        """Get the credential manager instance."""
        return self.credential_manager
    
    def get_api_authenticator(self) -> APIAuthenticator:
        """Get the API authenticator instance."""
        return self.api_authenticator
    
    def get_audit_logger(self) -> AuditLogger:
        """Get the audit logger instance."""
        return self.audit_logger
    
    def perform_security_check(self) -> Dict[str, Any]:
        """
        Perform a comprehensive security check.
        
        Returns:
            Dictionary containing security check results
        """
        results = {
            'timestamp': datetime.utcnow().isoformat(),
            'credential_validation': self.credential_manager.validate_credentials(),
            'active_tokens': len(self.api_authenticator.active_tokens),
            'expired_tokens_cleaned': self.api_authenticator.cleanup_expired_tokens(),
            'master_key_configured': bool(os.getenv('MONITORING_MASTER_KEY')),
            'api_secret_configured': bool(os.getenv('API_SECRET_KEY')),
            'audit_logging_enabled': True
        }
        
        # Log security check
        self.audit_logger.log_system_event(
            'security_check',
            'security_manager',
            'perform_security_check',
            True,
            results
        )
        
        return results


# Global security manager instance
_security_manager: Optional[SecurityManager] = None


def get_security_manager() -> SecurityManager:
    """
    Get the global security manager instance.
    
    Returns:
        SecurityManager instance
    """
    global _security_manager
    if _security_manager is None:
        _security_manager = SecurityManager()
    return _security_manager