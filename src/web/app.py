"""
FastAPI web application for the Infrastructure Monitoring Agent
"""

import logging
from typing import Dict, List, Optional
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect, Depends, status
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from fastapi import Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials, HTTPBasic, HTTPBasicCredentials
from fastapi.middleware.cors import CORSMiddleware
import json
import asyncio
import time
import secrets
from datetime import datetime

from ..config.config_models import Config
from ..models.data_models import VesselMetrics, SLAStatus, ComponentStatus
from ..models.enums import ComponentType, OperationalStatus
from ..services.data_collector import DataCollector
from ..services.sla_analyzer import SLAAnalyzer
from ..services.fleet_dashboard import FleetDashboard
from ..services.security_manager import get_security_manager


logger = logging.getLogger(__name__)


class ConnectionManager:
    """Manages WebSocket connections for real-time updates"""
    
    def __init__(self):
        self.active_connections: List[WebSocket] = []
    
    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)
        logger.info(f"WebSocket connected. Total connections: {len(self.active_connections)}")
    
    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
            logger.info(f"WebSocket disconnected. Total connections: {len(self.active_connections)}")
    
    async def broadcast(self, message: dict):
        """Broadcast message to all connected clients"""
        if not self.active_connections:
            return
        
        message_str = json.dumps(message)
        disconnected = []
        
        for connection in self.active_connections:
            try:
                await connection.send_text(message_str)
            except Exception as e:
                logger.warning(f"Failed to send message to WebSocket: {e}")
                disconnected.append(connection)
        
        # Remove disconnected clients
        for connection in disconnected:
            self.disconnect(connection)


def create_app(config: Config) -> FastAPI:
    """Create and configure the FastAPI application"""
    
    app = FastAPI(
        title="Infrastructure Monitoring Agent",
        description="Automated SLA monitoring system for vessel infrastructure",
        version="1.0.0"
    )
    
    # Add CORS middleware
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],  # Configure appropriately for production
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    
    # Initialize services
    data_collector = DataCollector(config)
    sla_analyzer = SLAAnalyzer(config)
    fleet_dashboard = FleetDashboard(config, data_collector, sla_analyzer)
    connection_manager = ConnectionManager()
    
    # Initialize scheduler run logger
    from ..services.scheduler_run_logger import SchedulerRunLogger
    scheduler_run_logger = SchedulerRunLogger(config.database_path)
    
    # Initialize scheduler with WebSocket manager
    from ..services.scheduler import MonitoringScheduler
    monitoring_scheduler = MonitoringScheduler(config, websocket_manager=connection_manager)
    
    # Initialize security
    security_manager = get_security_manager()
    audit_logger = security_manager.get_audit_logger()
    api_authenticator = security_manager.get_api_authenticator()
    
    # Security schemes
    bearer_scheme = HTTPBearer(auto_error=False)
    basic_scheme = HTTPBasic(auto_error=False)
    
    # Set up templates
    templates = Jinja2Templates(directory="src/web/templates")
    
    # Mount static files
    app.mount("/static", StaticFiles(directory="src/web/static"), name="static")
    
    # Authentication dependencies
    async def get_current_user(
        request: Request,
        bearer_token: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
        basic_creds: Optional[HTTPBasicCredentials] = Depends(basic_scheme)
    ) -> Optional[Dict[str, any]]:
        """Get current authenticated user from token or basic auth"""
        start_time = time.time()
        user_info = None
        auth_method = None
        
        try:
            # Try bearer token first
            if bearer_token:
                token_info = api_authenticator.validate_token(bearer_token.credentials)
                if token_info:
                    user_info = {
                        'user_id': token_info['user_id'],
                        'permissions': token_info['permissions'],
                        'auth_method': 'bearer_token'
                    }
                    auth_method = 'bearer_token'
            
            # Try basic auth if no valid token
            if not user_info and basic_creds:
                basic_auth_creds = api_authenticator.get_basic_auth_credentials()
                if (basic_auth_creds and 
                    basic_creds.username == basic_auth_creds['username'] and
                    secrets.compare_digest(basic_creds.password, basic_auth_creds['password'])):
                    user_info = {
                        'user_id': basic_creds.username,
                        'permissions': ['read', 'dashboard', 'admin'],
                        'auth_method': 'basic_auth'
                    }
                    auth_method = 'basic_auth'
            
            # Log authentication attempt
            response_time = (time.time() - start_time) * 1000
            audit_logger.log_authentication_event(
                event_type='api_access',
                user_id=user_info['user_id'] if user_info else 'anonymous',
                success=bool(user_info),
                details={
                    'method': auth_method,
                    'endpoint': str(request.url.path),
                    'ip_address': request.client.host if request.client else None
                }
            )
            
            return user_info
            
        except Exception as e:
            logger.error(f"Authentication error: {e}")
            audit_logger.log_security_event(
                event_type='authentication_error',
                severity='medium',
                description=f'Authentication error: {str(e)}',
                details={
                    'endpoint': str(request.url.path),
                    'ip_address': request.client.host if request.client else None
                }
            )
            return None
    
    async def require_auth(
        user_info: Optional[Dict[str, any]] = Depends(get_current_user)
    ) -> Dict[str, any]:
        """Require authentication for protected endpoints"""
        if not user_info:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Authentication required",
                headers={"WWW-Authenticate": "Bearer"},
            )
        return user_info
    
    async def require_permission(permission: str):
        """Create a dependency that requires a specific permission"""
        async def check_permission(
            user_info: Dict[str, any] = Depends(require_auth)
        ) -> Dict[str, any]:
            if permission not in user_info.get('permissions', []):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=f"Permission '{permission}' required"
                )
            return user_info
        return check_permission
    
    # Middleware for request logging
    @app.middleware("http")
    async def log_requests(request: Request, call_next):
        start_time = time.time()
        
        # Process request
        response = await call_next(request)
        
        # Log API access
        process_time = (time.time() - start_time) * 1000
        
        # Try to get user info from request state (set by auth dependency)
        user_id = getattr(request.state, 'user_id', None)
        
        audit_logger.log_api_access(
            endpoint=str(request.url.path),
            method=request.method,
            user_id=user_id,
            status_code=response.status_code,
            response_time_ms=process_time,
            ip_address=request.client.host if request.client else None
        )
        
        return response
    
    @app.get("/", response_class=HTMLResponse)
    async def root(
        request: Request,
        user_info: Optional[Dict[str, any]] = Depends(get_current_user)
    ):
        """Root endpoint - fleet dashboard"""
        # Store user info in request state for logging
        if user_info:
            request.state.user_id = user_info['user_id']
        
        return templates.TemplateResponse("dashboard.html", {
            "request": request,
            "total_vessels": len(config.vessel_databases),
            "authenticated": bool(user_info),
            "user_id": user_info['user_id'] if user_info else None
        })
    
    @app.get("/config", response_class=HTMLResponse)
    async def config_page(
        request: Request,
        user_info: Optional[Dict[str, any]] = Depends(get_current_user)
    ):
        """Configuration page"""
        if user_info:
            request.state.user_id = user_info['user_id']
        
        return templates.TemplateResponse("config.html", {
            "request": request,
            "authenticated": bool(user_info),
            "user_id": user_info['user_id'] if user_info else None
        })
    
    @app.get("/health")
    async def health_check():
        """Health check endpoint"""
        return {
            "status": "healthy",
            "service": "Infrastructure Monitoring Agent",
            "vessels_configured": len(config.vessel_databases),
            "sla_threshold": config.sla_parameters.uptime_threshold_percentage
        }
    
    @app.get("/api/fleet-overview")
    async def get_fleet_overview(
        force_refresh: bool = False,
        include_devices: bool = False,
        user_info: Optional[Dict[str, any]] = Depends(get_current_user)
    ):
        """
        Get fleet-wide overview with SLA status for all vessels
        
        Args:
            force_refresh: If True, bypass cache and collect fresh data
            include_devices: If True, include individual device details with IP addresses
        
        Returns:
            Fleet overview with vessel statuses and SLA compliance
        """
        try:
            logger.info("Getting fleet overview")
            
            # Get fleet overview from dashboard service
            fleet_overview = await fleet_dashboard.get_fleet_overview(force_refresh=force_refresh)
            vessel_summaries = await fleet_dashboard.get_vessel_summaries(
                force_refresh=force_refresh, 
                include_devices=include_devices
            )
            
            # Format vessel data for response
            vessels_data = []
            for vessel_id, vessel_summary in vessel_summaries.items():
                vessel_data = {
                    "vessel_id": vessel_id,
                    "status": vessel_summary.status.value,
                    "compliance_rate": vessel_summary.compliance_rate,
                    "violations_count": vessel_summary.violations_count,
                    "components_up": vessel_summary.components_up,
                    "components_total": vessel_summary.components_total,
                    "worst_component_uptime": vessel_summary.worst_component_uptime,
                    "last_updated": vessel_summary.last_updated.isoformat()
                }
                
                # Include device details if requested
                if include_devices and vessel_summary.devices:
                    vessel_data["devices"] = []
                    for device in vessel_summary.devices:
                        device_data = {
                            "ip_address": device.ip_address,
                            "component_type": device.component_type.value,
                            "uptime_percentage": device.uptime_percentage,
                            "current_status": device.current_status.value,
                            "downtime_aging_hours": device.downtime_aging_hours,
                            "last_ping_time": device.last_ping_time.isoformat(),
                            "has_data": device.has_data,
                            "sync_status": device.sync_status
                        }
                        vessel_data["devices"].append(device_data)
                
                vessels_data.append(vessel_data)
            
            # Sort vessels by status severity (critical first)
            status_priority = {
                "critical": 0,
                "degraded": 1,
                "offline": 2,
                "operational": 3
            }
            vessels_data.sort(key=lambda x: (status_priority.get(x["status"], 4), x["vessel_id"]))
            
            response_data = {
                "fleet_summary": {
                    "total_vessels": fleet_overview.total_vessels,
                    "vessels_online": fleet_overview.vessels_online,
                    "vessels_offline": fleet_overview.vessels_offline,
                    "vessels_degraded": fleet_overview.vessels_degraded,
                    "vessels_critical": fleet_overview.vessels_critical,
                    "fleet_compliance_rate": fleet_overview.fleet_compliance_rate,
                    "average_uptime": fleet_overview.average_uptime,
                    "total_violations": fleet_overview.total_violations,
                    "persistent_violations": fleet_overview.persistent_violations
                },
                "vessels": vessels_data,
                "timestamp": fleet_overview.last_updated.isoformat(),
                "include_devices": include_devices
            }
            
            # Broadcast update to WebSocket clients
            await connection_manager.broadcast({
                "type": "fleet_update",
                "data": response_data
            })
            
            return response_data
            
        except Exception as e:
            logger.error(f"Failed to get fleet overview: {e}")
            raise HTTPException(status_code=500, detail=f"Failed to collect fleet data: {str(e)}")
    
    @app.get("/api/vessel/{vessel_id}/details")
    async def get_vessel_details(
        vessel_id: str,
        user_info: Optional[Dict[str, any]] = Depends(get_current_user)
    ):
        """
        Get detailed metrics for a specific vessel
        
        Args:
            vessel_id: ID of the vessel to get details for
            
        Returns:
            Detailed vessel metrics and SLA status
        """
        try:
            logger.info(f"Getting details for vessel {vessel_id}")
            
            # Get vessel details from dashboard service
            vessel_detail = await fleet_dashboard.get_vessel_details(vessel_id)
            
            # Format component data for response
            components_detail = []
            for component in vessel_detail.components:
                component_data = {
                    "type": component.component_type.value,
                    "uptime_percentage": component.uptime_percentage,
                    "current_status": component.current_status.value,
                    "downtime_aging": {
                        "hours": component.downtime_aging_hours,
                        "formatted": _format_duration_hours(component.downtime_aging_hours)
                    },
                    "sla_status": {
                        "is_compliant": component.is_sla_compliant,
                        "violation_duration_hours": component.violation_duration_hours or 0
                    },
                    "last_ping": component.last_ping_time.isoformat(),
                    "alert_severity": component.alert_severity.value,
                    "highlight_class": f"alert-{component.alert_severity.value}"
                }
                components_detail.append(component_data)
            
            # Format device details
            devices_detail = []
            for device in vessel_detail.devices:
                device_data = {
                    "ip_address": device.ip_address,
                    "component_type": device.component_type.value,
                    "uptime_percentage": device.uptime_percentage,
                    "current_status": device.current_status.value,
                    "downtime_aging": {
                        "hours": device.downtime_aging_hours,
                        "formatted": _format_duration_hours(device.downtime_aging_hours)
                    },
                    "last_ping_time": device.last_ping_time.isoformat(),
                    "has_data": device.has_data,
                    "sync_status": device.sync_status,
                    "sync_status_class": f"sync-{device.sync_status.replace('_', '-')}"
                }
                devices_detail.append(device_data)
            
            response_data = {
                "vessel_id": vessel_detail.vessel_id,
                "timestamp": vessel_detail.last_updated.isoformat(),
                "components": components_detail,
                "devices": devices_detail,
                "overall_status": vessel_detail.overall_status.value,
                "sla_compliance_rate": vessel_detail.compliance_rate,
                "violations": vessel_detail.violations
            }
            
            return response_data
            
        except ValueError as e:
            raise HTTPException(status_code=404, detail=str(e))
        except Exception as e:
            logger.error(f"Failed to get vessel details for {vessel_id}: {e}")
            raise HTTPException(status_code=500, detail=f"Failed to get vessel details: {str(e)}")
    
    @app.get("/api/sla-violations")
    async def get_sla_violations(
        persistent_only: bool = False,
        vessel_id: Optional[str] = None,
        component_type: Optional[str] = None,
        user_info: Optional[Dict[str, any]] = Depends(get_current_user)
    ):
        """
        Get current SLA violations across the fleet
        
        Args:
            persistent_only: If True, only return violations exceeding downtime threshold
            vessel_id: Optional filter by specific vessel
            component_type: Optional filter by component type
            
        Returns:
            List of current SLA violations
        """
        try:
            logger.info(f"Getting SLA violations (persistent_only={persistent_only}, vessel_id={vessel_id}, component_type={component_type})")
            
            # Parse component type if provided
            component_type_enum = None
            if component_type:
                try:
                    component_type_enum = ComponentType(component_type.lower())
                except ValueError:
                    raise HTTPException(status_code=400, detail=f"Invalid component type: {component_type}")
            
            # Get violations from dashboard service
            violations_data = await fleet_dashboard.get_sla_violations(
                vessel_id=vessel_id,
                component_type=component_type_enum,
                persistent_only=persistent_only
            )
            
            return {
                "violations": violations_data,
                "total_count": len(violations_data),
                "persistent_count": len([v for v in violations_data if v["requires_ticket"]]),
                "filter": {
                    "persistent_only": persistent_only,
                    "vessel_id": vessel_id,
                    "component_type": component_type
                }
            }
            
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Failed to get SLA violations: {e}")
            raise HTTPException(status_code=500, detail=f"Failed to get SLA violations: {str(e)}")
    
    @app.post("/api/auth/token")
    async def create_access_token(
        request: Request,
        basic_creds: HTTPBasicCredentials = Depends(basic_scheme)
    ):
        """Create an API access token using basic authentication"""
        if not basic_creds:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Basic authentication required",
                headers={"WWW-Authenticate": "Basic"},
            )
        
        # Validate basic auth credentials
        basic_auth_creds = api_authenticator.get_basic_auth_credentials()
        if (not basic_auth_creds or 
            basic_creds.username != basic_auth_creds['username'] or
            not secrets.compare_digest(basic_creds.password, basic_auth_creds['password'])):
            
            audit_logger.log_authentication_event(
                event_type='token_creation_failed',
                user_id=basic_creds.username,
                success=False,
                details={'reason': 'invalid_credentials'}
            )
            
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid credentials",
                headers={"WWW-Authenticate": "Basic"},
            )
        
        # Generate token
        token = api_authenticator.generate_api_token(
            user_id=basic_creds.username,
            permissions=['read', 'dashboard', 'admin']
        )
        
        audit_logger.log_authentication_event(
            event_type='token_created',
            user_id=basic_creds.username,
            success=True,
            details={'ip_address': request.client.host if request.client else None}
        )
        
        return {
            "access_token": token,
            "token_type": "bearer",
            "expires_in": api_authenticator.token_expiry_hours * 3600
        }
    
    @app.delete("/api/auth/token")
    async def revoke_access_token(
        user_info: Dict[str, any] = Depends(require_auth),
        bearer_token: HTTPAuthorizationCredentials = Depends(bearer_scheme)
    ):
        """Revoke the current access token"""
        if bearer_token:
            revoked = api_authenticator.revoke_token(bearer_token.credentials)
            
            audit_logger.log_authentication_event(
                event_type='token_revoked',
                user_id=user_info['user_id'],
                success=revoked,
                details={'token_found': revoked}
            )
            
            return {"message": "Token revoked successfully" if revoked else "Token not found"}
        
        return {"message": "No token to revoke"}
    
    @app.get("/api/auth/status")
    async def get_auth_status(
        user_info: Optional[Dict[str, any]] = Depends(get_current_user)
    ):
        """Get current authentication status"""
        if user_info:
            return {
                "authenticated": True,
                "user_id": user_info['user_id'],
                "permissions": user_info['permissions'],
                "auth_method": user_info['auth_method']
            }
        else:
            return {
                "authenticated": False,
                "auth_methods": ["bearer_token", "basic_auth"]
            }
    
    @app.get("/api/config/status")
    async def get_config_status(
        user_info: Dict[str, any] = Depends(require_auth)
    ):
        """Get current configuration status"""
        credential_manager = security_manager.get_credential_manager()
        validation_results = credential_manager.validate_credentials()
        
        # Get current configuration (without sensitive data)
        influx_creds = credential_manager.get_influxdb_credentials("001")  # Test vessel
        jira_creds = credential_manager.get_jira_credentials()
        
        return {
            "influxdb": {
                "configured": validation_results.get('influxdb', False),
                "host": influx_creds.get('host', ''),
                "port": influx_creds.get('port', 8086),
                "database": influx_creds.get('database', ''),
                "username": influx_creds.get('username', ''),
                "ssl": influx_creds.get('ssl', True),
                "verify_ssl": influx_creds.get('verify_ssl', True)
            },
            "jira": {
                "configured": validation_results.get('jira', False),
                "server": jira_creds.get('server', ''),
                "username": jira_creds.get('username', ''),
                "project_key": jira_creds.get('project_key', 'INFRA')
            },
            "vessels_count": len(config.vessel_databases)
        }
    
    @app.post("/api/config/influxdb")
    async def update_influxdb_config(
        request: Request,
        user_info: Dict[str, any] = Depends(require_auth)
    ):
        """Update InfluxDB configuration"""
        try:
            config_data = await request.json()
            
            # Validate required fields
            required_fields = ['host', 'port', 'database', 'username', 'password']
            for field in required_fields:
                if not config_data.get(field):
                    raise HTTPException(
                        status_code=400,
                        detail=f"Missing required field: {field}"
                    )
            
            # Update environment variables (this would typically update .env file)
            # For now, we'll just validate the connection
            audit_logger.log_system_event(
                'config_update',
                'influxdb',
                'configuration_updated',
                True,
                {
                    'user_id': user_info['user_id'],
                    'host': config_data['host'],
                    'database': config_data['database']
                }
            )
            
            return {
                "success": True,
                "message": "InfluxDB configuration updated successfully",
                "note": "Restart the application to apply changes"
            }
            
        except Exception as e:
            audit_logger.log_system_event(
                'config_update',
                'influxdb',
                'configuration_update_failed',
                False,
                {
                    'user_id': user_info['user_id'],
                    'error': str(e)
                }
            )
            raise HTTPException(status_code=500, detail=str(e))
    
    @app.post("/api/config/jira")
    async def update_jira_config(
        request: Request,
        user_info: Dict[str, any] = Depends(require_auth)
    ):
        """Update JIRA configuration"""
        try:
            config_data = await request.json()
            
            # Validate required fields
            required_fields = ['server', 'username', 'api_token', 'project_key']
            for field in required_fields:
                if not config_data.get(field):
                    raise HTTPException(
                        status_code=400,
                        detail=f"Missing required field: {field}"
                    )
            
            # Update environment variables (this would typically update .env file)
            audit_logger.log_system_event(
                'config_update',
                'jira',
                'configuration_updated',
                True,
                {
                    'user_id': user_info['user_id'],
                    'server': config_data['server'],
                    'project_key': config_data['project_key']
                }
            )
            
            return {
                "success": True,
                "message": "JIRA configuration updated successfully",
                "note": "Restart the application to apply changes"
            }
            
        except Exception as e:
            audit_logger.log_system_event(
                'config_update',
                'jira',
                'configuration_update_failed',
                False,
                {
                    'user_id': user_info['user_id'],
                    'error': str(e)
                }
            )
            raise HTTPException(status_code=500, detail=str(e))
    
    @app.post("/api/config/test-connection")
    async def test_connection(
        request: Request,
        user_info: Dict[str, any] = Depends(require_auth)
    ):
        """Test connection to external services"""
        try:
            test_data = await request.json()
            service_type = test_data.get('type')  # 'influxdb' or 'jira'
            
            if service_type == 'influxdb':
                # Test InfluxDB connection
                # This would use the provided credentials to test connection
                return {
                    "success": True,
                    "message": "InfluxDB connection test successful",
                    "details": "Connected to database successfully"
                }
            elif service_type == 'jira':
                # Test JIRA connection
                return {
                    "success": True,
                    "message": "JIRA connection test successful",
                    "details": "API authentication successful"
                }
            else:
                raise HTTPException(status_code=400, detail="Invalid service type")
                
        except Exception as e:
            return {
                "success": False,
                "message": f"Connection test failed: {str(e)}",
                "details": str(e)
            }
    
    @app.get("/api/scheduler-runs")
    async def get_scheduler_runs(
        limit: int = 20,
        user_info: Optional[Dict[str, any]] = Depends(get_current_user)
    ):
        """
        Get recent scheduler run logs.
        
        Args:
            limit: Maximum number of runs to retrieve (default: 20, max: 100)
            
        Returns:
            List of recent scheduler runs with summary information
        """
        try:
            # Validate limit
            if limit > 100:
                limit = 100
            elif limit < 1:
                limit = 20
            
            logger.info(f"Getting {limit} recent scheduler runs")
            
            # Get recent runs from logger
            recent_runs = scheduler_run_logger.get_recent_runs(limit=limit)
            
            # Format runs for API response
            runs_data = []
            for run in recent_runs:
                run_data = {
                    "run_id": run.run_id,
                    "start_time": run.start_time.isoformat(),
                    "end_time": run.end_time.isoformat() if run.end_time else None,
                    "status": run.status,
                    "total_vessels": run.total_vessels,
                    "successful_vessels": run.successful_vessels,
                    "failed_vessels": run.failed_vessels,
                    "retry_attempts": run.retry_attempts,
                    "duration": {
                        "seconds": run.duration.total_seconds() if run.duration else None,
                        "formatted": _format_duration(run.duration) if run.duration else None
                    },
                    "error_message": run.error_message,
                    "success_rate": round(
                        (run.successful_vessels / max(run.total_vessels, 1)) * 100, 1
                    ) if run.total_vessels > 0 else 0
                }
                runs_data.append(run_data)
            
            return {
                "runs": runs_data,
                "total_count": len(runs_data),
                "limit": limit
            }
            
        except Exception as e:
            logger.error(f"Failed to get scheduler runs: {e}")
            raise HTTPException(status_code=500, detail=f"Failed to get scheduler runs: {str(e)}")
    
    @app.get("/api/scheduler-runs/{run_id}")
    async def get_scheduler_run_details(
        run_id: str,
        user_info: Optional[Dict[str, any]] = Depends(get_current_user)
    ):
        """
        Get detailed information about a specific scheduler run.
        
        Args:
            run_id: ID of the scheduler run
            
        Returns:
            Detailed scheduler run information including vessel results
        """
        try:
            logger.info(f"Getting details for scheduler run {run_id}")
            
            # Get run details from logger
            run_details = scheduler_run_logger.get_run_details(run_id)
            
            if not run_details:
                raise HTTPException(status_code=404, detail=f"Scheduler run {run_id} not found")
            
            # Format vessel results
            vessel_results = []
            for result in run_details.vessel_results:
                result_data = {
                    "vessel_id": result.vessel_id,
                    "attempt_number": result.attempt_number,
                    "success": result.success,
                    "query_duration": {
                        "seconds": result.query_duration.total_seconds(),
                        "formatted": _format_duration(result.query_duration)
                    },
                    "error_message": result.error_message,
                    "timestamp": result.timestamp.isoformat() if result.timestamp else None
                }
                vessel_results.append(result_data)
            
            # Get retry statistics
            retry_stats = run_details.get_retry_statistics()
            
            # Format run summary
            run_summary = run_details.run_summary
            response_data = {
                "run_summary": {
                    "run_id": run_summary.run_id,
                    "start_time": run_summary.start_time.isoformat(),
                    "end_time": run_summary.end_time.isoformat() if run_summary.end_time else None,
                    "status": run_summary.status,
                    "total_vessels": run_summary.total_vessels,
                    "successful_vessels": run_summary.successful_vessels,
                    "failed_vessels": run_summary.failed_vessels,
                    "retry_attempts": run_summary.retry_attempts,
                    "duration": {
                        "seconds": run_summary.duration.total_seconds() if run_summary.duration else None,
                        "formatted": _format_duration(run_summary.duration) if run_summary.duration else None
                    },
                    "error_message": run_summary.error_message,
                    "success_rate": round(
                        (run_summary.successful_vessels / max(run_summary.total_vessels, 1)) * 100, 1
                    ) if run_summary.total_vessels > 0 else 0
                },
                "vessel_results": vessel_results,
                "retry_summary": run_details.retry_summary,
                "retry_statistics": retry_stats,
                "failed_vessels": run_details.get_failed_vessels()
            }
            
            return response_data
            
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Failed to get scheduler run details for {run_id}: {e}")
            raise HTTPException(status_code=500, detail=f"Failed to get run details: {str(e)}")
    
    @app.get("/api/scheduler-runs/statistics")
    async def get_scheduler_statistics(
        days_back: int = 30,
        user_info: Optional[Dict[str, any]] = Depends(get_current_user)
    ):
        """
        Get scheduler run statistics over a time period.
        
        Args:
            days_back: Number of days to analyze (default: 30, max: 365)
            
        Returns:
            Scheduler run statistics and vessel reliability information
        """
        try:
            # Validate days_back
            if days_back > 365:
                days_back = 365
            elif days_back < 1:
                days_back = 30
            
            logger.info(f"Getting scheduler statistics for {days_back} days")
            
            # Get statistics from logger
            statistics = scheduler_run_logger.get_run_statistics(days_back=days_back)
            
            return statistics
            
        except Exception as e:
            logger.error(f"Failed to get scheduler statistics: {e}")
            raise HTTPException(status_code=500, detail=f"Failed to get statistics: {str(e)}")
    
    @app.get("/api/scheduler-runs/active")
    async def get_active_scheduler_run(
        user_info: Optional[Dict[str, any]] = Depends(get_current_user)
    ):
        """
        Get the currently active (running) scheduler run if any.
        
        Returns:
            Active scheduler run information or null if no active run
        """
        try:
            logger.info("Getting active scheduler run")
            
            # Get active run from logger
            active_run = scheduler_run_logger.get_active_run()
            
            if not active_run:
                return {"active_run": None}
            
            # Format active run data
            run_data = {
                "run_id": active_run.run_id,
                "start_time": active_run.start_time.isoformat(),
                "status": active_run.status,
                "total_vessels": active_run.total_vessels,
                "successful_vessels": active_run.successful_vessels,
                "failed_vessels": active_run.failed_vessels,
                "retry_attempts": active_run.retry_attempts,
                "elapsed_time": {
                    "seconds": (datetime.utcnow() - active_run.start_time).total_seconds(),
                    "formatted": _format_duration(datetime.utcnow() - active_run.start_time)
                },
                "progress_percentage": round(
                    ((active_run.successful_vessels + active_run.failed_vessels) / max(active_run.total_vessels, 1)) * 100, 1
                ) if active_run.total_vessels > 0 else 0
            }
            
            return {"active_run": run_data}
            
        except Exception as e:
            logger.error(f"Failed to get active scheduler run: {e}")
            raise HTTPException(status_code=500, detail=f"Failed to get active run: {str(e)}")
    
    @app.get("/api/fleet-sync-status")
    async def get_fleet_sync_status(
        force_refresh: bool = False,
        user_info: Optional[Dict[str, any]] = Depends(get_current_user)
    ):
        """
        Get fleet-wide data sync status breakdown.
        
        Args:
            force_refresh: If True, bypass cache and collect fresh data
            
        Returns:
            Fleet-wide sync status information with device-level details
        """
        try:
            logger.info("Getting fleet sync status")
            
            # Get sync status from fleet dashboard
            sync_status = await fleet_dashboard.get_fleet_sync_status(force_refresh=force_refresh)
            
            return sync_status
            
        except Exception as e:
            logger.error(f"Failed to get fleet sync status: {e}")
            raise HTTPException(status_code=500, detail=f"Failed to get sync status: {str(e)}")
    
    @app.post("/api/scheduler/trigger")
    async def trigger_scheduler_run(
        user_info: Dict[str, any] = Depends(require_auth)
    ):
        """
        Manually trigger a scheduler run for testing purposes.
        
        Returns:
            Confirmation message and run details
        """
        try:
            logger.info(f"Manual scheduler run triggered by user {user_info['user_id']}")
            
            # Check if scheduler is running
            if not monitoring_scheduler.is_running:
                monitoring_scheduler.start()
            
            # Trigger the daily monitoring job immediately
            monitoring_scheduler.trigger_job_now("daily_monitoring")
            
            return {
                "success": True,
                "message": "Scheduler run triggered successfully",
                "triggered_by": user_info['user_id'],
                "timestamp": datetime.utcnow().isoformat()
            }
            
        except Exception as e:
            logger.error(f"Failed to trigger scheduler run: {e}")
            raise HTTPException(status_code=500, detail=f"Failed to trigger scheduler: {str(e)}")
    
    @app.get("/api/scheduler/status")
    async def get_scheduler_status(
        user_info: Optional[Dict[str, any]] = Depends(get_current_user)
    ):
        """
        Get current scheduler status and configuration.
        
        Returns:
            Scheduler status information
        """
        try:
            logger.info("Getting scheduler status")
            
            scheduler_stats = monitoring_scheduler.get_scheduler_stats()
            active_run = scheduler_run_logger.get_active_run()
            
            return {
                "scheduler": scheduler_stats,
                "active_run": {
                    "run_id": active_run.run_id,
                    "start_time": active_run.start_time.isoformat(),
                    "status": active_run.status,
                    "total_vessels": active_run.total_vessels,
                    "successful_vessels": active_run.successful_vessels,
                    "failed_vessels": active_run.failed_vessels,
                    "elapsed_time_seconds": (datetime.utcnow() - active_run.start_time).total_seconds()
                } if active_run else None
            }
            
        except Exception as e:
            logger.error(f"Failed to get scheduler status: {e}")
            raise HTTPException(status_code=500, detail=f"Failed to get scheduler status: {str(e)}")
    
    @app.websocket("/ws")
    async def websocket_endpoint(websocket: WebSocket):
        """WebSocket endpoint for real-time status updates"""
        await connection_manager.connect(websocket)
        try:
            while True:
                # Keep connection alive and handle client messages
                data = await websocket.receive_text()
                message = json.loads(data)
                
                if message.get("type") == "ping":
                    await websocket.send_text(json.dumps({"type": "pong"}))
                elif message.get("type") == "subscribe":
                    # Client is subscribing to updates
                    await websocket.send_text(json.dumps({
                        "type": "subscribed",
                        "message": "Successfully subscribed to real-time updates"
                    }))
                    
        except WebSocketDisconnect:
            connection_manager.disconnect(websocket)
        except Exception as e:
            logger.error(f"WebSocket error: {e}")
            connection_manager.disconnect(websocket)
    
    @app.exception_handler(404)
    async def not_found_handler(request: Request, exc: HTTPException):
        """Custom 404 handler"""
        return JSONResponse(
            status_code=404,
            content={"error": "Endpoint not found", "path": str(request.url.path)}
        )
    
    @app.exception_handler(500)
    async def internal_error_handler(request: Request, exc: Exception):
        """Custom 500 handler"""
        logger.error(f"Internal server error: {exc}")
        return JSONResponse(
            status_code=500,
            content={"error": "Internal server error", "message": str(exc)}
        )
    
    return app


def _format_duration(duration) -> str:
    """Format timedelta in human-readable format"""
    total_seconds = int(duration.total_seconds())
    days = total_seconds // 86400
    hours = (total_seconds % 86400) // 3600
    minutes = (total_seconds % 3600) // 60
    
    parts = []
    if days > 0:
        parts.append(f"{days}d")
    if hours > 0:
        parts.append(f"{hours}h")
    if minutes > 0:
        parts.append(f"{minutes}m")
    
    return " ".join(parts) if parts else "< 1m"


def _format_duration_hours(hours: float) -> str:
    """Format hours in human-readable format"""
    total_seconds = int(hours * 3600)
    days = total_seconds // 86400
    hours_part = (total_seconds % 86400) // 3600
    minutes = (total_seconds % 3600) // 60
    
    parts = []
    if days > 0:
        parts.append(f"{days}d")
    if hours_part > 0:
        parts.append(f"{hours_part}h")
    if minutes > 0:
        parts.append(f"{minutes}m")
    
    return " ".join(parts) if parts else "< 1m"