"""
Slack Webhook Handler for Infrastructure Monitoring Agent.

This module provides a Flask-based webhook endpoint to handle Slack interactive
button responses for approval workflow integration.
"""

import logging
import json
import hmac
import hashlib
import time
from typing import Optional
from flask import Flask, request, jsonify
from urllib.parse import parse_qs

from .approval_workflow import ApprovalWorkflow


class SlackWebhookHandler:
    """
    Slack webhook handler for interactive approval responses.
    
    Provides secure webhook endpoint for handling Slack button interactions
    and integrating with the approval workflow system.
    """
    
    def __init__(
        self, 
        approval_workflow: ApprovalWorkflow,
        slack_signing_secret: Optional[str] = None,
        app_name: str = "slack_approval_handler"
    ):
        """
        Initialize Slack webhook handler.
        
        Args:
            approval_workflow: Approval workflow instance
            slack_signing_secret: Slack app signing secret for verification
            app_name: Flask app name
        """
        self.approval_workflow = approval_workflow
        self.slack_signing_secret = slack_signing_secret
        self.logger = logging.getLogger(__name__)
        
        # Create Flask app
        self.app = Flask(app_name)
        self.app.config['SECRET_KEY'] = 'infrastructure-monitoring-slack-handler'
        
        # Register routes
        self._register_routes()
        
        self.logger.info("Slack webhook handler initialized")
    
    def _register_routes(self):
        """Register Flask routes for Slack interactions."""
        
        @self.app.route('/slack/interactive', methods=['POST'])
        def handle_interactive():
            """Handle Slack interactive component responses."""
            try:
                # Verify Slack request if signing secret is provided
                if self.slack_signing_secret:
                    if not self._verify_slack_request():
                        self.logger.warning("Invalid Slack request signature")
                        return jsonify({"error": "Invalid request signature"}), 401
                
                # Parse payload
                payload_str = request.form.get('payload')
                if not payload_str:
                    return jsonify({"error": "No payload provided"}), 400
                
                payload = json.loads(payload_str)
                
                # Log interaction
                user = payload.get('user', {})
                action = payload.get('actions', [{}])[0]
                self.logger.info(
                    f"Slack interaction: {action.get('name')} by {user.get('name')} "
                    f"for request {action.get('value')}"
                )
                
                # Handle interaction
                response = self.approval_workflow.handle_slack_interaction(payload)
                
                return jsonify(response)
                
            except json.JSONDecodeError as e:
                self.logger.error(f"Invalid JSON payload: {e}")
                return jsonify({"error": "Invalid JSON payload"}), 400
            except Exception as e:
                self.logger.error(f"Error handling Slack interaction: {e}")
                return jsonify({"error": "Internal server error"}), 500
        
        @self.app.route('/slack/health', methods=['GET'])
        def health_check():
            """Health check endpoint."""
            return jsonify({
                "status": "healthy",
                "service": "slack_webhook_handler",
                "timestamp": time.time()
            })
        
        @self.app.route('/slack/stats', methods=['GET'])
        def approval_stats():
            """Get approval workflow statistics."""
            try:
                stats = self.approval_workflow.get_approval_statistics()
                return jsonify(stats)
            except Exception as e:
                self.logger.error(f"Error getting approval stats: {e}")
                return jsonify({"error": "Failed to get statistics"}), 500
    
    def _verify_slack_request(self) -> bool:
        """
        Verify Slack request signature for security.
        
        Returns:
            True if signature is valid, False otherwise
        """
        try:
            # Get Slack signature and timestamp
            slack_signature = request.headers.get('X-Slack-Signature', '')
            slack_timestamp = request.headers.get('X-Slack-Request-Timestamp', '')
            
            if not slack_signature or not slack_timestamp:
                return False
            
            # Check timestamp (prevent replay attacks)
            current_time = int(time.time())
            if abs(current_time - int(slack_timestamp)) > 300:  # 5 minutes
                self.logger.warning("Slack request timestamp too old")
                return False
            
            # Verify signature
            request_body = request.get_data()
            sig_basestring = f"v0:{slack_timestamp}:{request_body.decode('utf-8')}"
            
            expected_signature = 'v0=' + hmac.new(
                self.slack_signing_secret.encode(),
                sig_basestring.encode(),
                hashlib.sha256
            ).hexdigest()
            
            return hmac.compare_digest(expected_signature, slack_signature)
            
        except Exception as e:
            self.logger.error(f"Error verifying Slack signature: {e}")
            return False
    
    def run(self, host: str = '0.0.0.0', port: int = 5000, debug: bool = False):
        """
        Run the Flask webhook server.
        
        Args:
            host: Server host
            port: Server port
            debug: Debug mode
        """
        self.logger.info(f"Starting Slack webhook handler on {host}:{port}")
        self.app.run(host=host, port=port, debug=debug)
    
    def get_app(self) -> Flask:
        """
        Get Flask app instance for integration with other servers.
        
        Returns:
            Flask app instance
        """
        return self.app


def create_slack_webhook_handler(
    approval_workflow: ApprovalWorkflow,
    slack_signing_secret: Optional[str] = None
) -> SlackWebhookHandler:
    """
    Factory function to create Slack webhook handler.
    
    Args:
        approval_workflow: Approval workflow instance
        slack_signing_secret: Slack app signing secret
        
    Returns:
        Configured Slack webhook handler
    """
    return SlackWebhookHandler(
        approval_workflow=approval_workflow,
        slack_signing_secret=slack_signing_secret
    )