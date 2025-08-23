"""
Real-time alerting and notification system for TyphoonLineWebhook
Provides multi-channel alerting, escalation policies, and notification management
"""
import os
import json
import time
import logging
import asyncio
import smtplib
import requests
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional, Callable, Union
from dataclasses import dataclass, asdict
from enum import Enum
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email import encoders
import threading
from collections import defaultdict, deque

class AlertSeverity(Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"
    EMERGENCY = "emergency"

class AlertStatus(Enum):
    ACTIVE = "active"
    ACKNOWLEDGED = "acknowledged"
    RESOLVED = "resolved"
    SUPPRESSED = "suppressed"

class NotificationChannel(Enum):
    EMAIL = "email"
    SLACK = "slack"
    WEBHOOK = "webhook"
    LINE = "line"
    SMS = "sms"

@dataclass
class Alert:
    id: str
    title: str
    description: str
    severity: AlertSeverity
    status: AlertStatus
    source: str
    timestamp: datetime
    acknowledged_by: Optional[str] = None
    acknowledged_at: Optional[datetime] = None
    resolved_at: Optional[datetime] = None
    metadata: Dict[str, Any] = None
    tags: List[str] = None
    
    def __post_init__(self):
        if self.metadata is None:
            self.metadata = {}
        if self.tags is None:
            self.tags = []

@dataclass
class NotificationRule:
    name: str
    conditions: Dict[str, Any]
    channels: List[NotificationChannel]
    recipients: List[str]
    throttle_minutes: int = 5
    escalation_delay_minutes: int = 30
    max_escalations: int = 3
    enabled: bool = True

class EmailNotifier:
    """Email notification handler"""
    
    def __init__(self):
        self.smtp_server = os.getenv('SMTP_SERVER', 'localhost')
        self.smtp_port = int(os.getenv('SMTP_PORT', 587))
        self.smtp_username = os.getenv('SMTP_USERNAME')
        self.smtp_password = os.getenv('SMTP_PASSWORD')
        self.from_email = os.getenv('ALERT_FROM_EMAIL', 'noreply@typhoon-webhook.com')
        self.use_tls = os.getenv('SMTP_USE_TLS', 'true').lower() == 'true'
    
    async def send_notification(self, alert: Alert, recipients: List[str]) -> bool:
        """Send email notification"""
        try:
            if not self.smtp_username or not self.smtp_password:
                logging.warning("SMTP credentials not configured, skipping email notification")
                return False
            
            # Create message
            msg = MIMEMultipart()
            msg['From'] = self.from_email
            msg['To'] = ', '.join(recipients)
            msg['Subject'] = f"[{alert.severity.value.upper()}] {alert.title}"
            
            # Email body
            body = self._create_email_body(alert)
            msg.attach(MIMEText(body, 'html'))
            
            # Send email
            server = smtplib.SMTP(self.smtp_server, self.smtp_port)
            if self.use_tls:
                server.starttls()
            server.login(self.smtp_username, self.smtp_password)
            
            text = msg.as_string()
            server.sendmail(self.from_email, recipients, text)
            server.quit()
            
            logging.info(f"Email alert sent for {alert.id} to {len(recipients)} recipients")
            return True
            
        except Exception as e:
            logging.error(f"Failed to send email notification: {str(e)}")
            return False
    
    def _create_email_body(self, alert: Alert) -> str:
        """Create HTML email body"""
        severity_colors = {
            AlertSeverity.INFO: '#17a2b8',
            AlertSeverity.WARNING: '#ffc107',
            AlertSeverity.CRITICAL: '#dc3545',
            AlertSeverity.EMERGENCY: '#6f42c1'
        }
        
        color = severity_colors.get(alert.severity, '#6c757d')
        
        return f"""
        <html>
        <body style="font-family: Arial, sans-serif; margin: 0; padding: 20px;">
            <div style="border-left: 4px solid {color}; padding-left: 20px; margin-bottom: 20px;">
                <h2 style="color: {color}; margin: 0 0 10px 0;">{alert.title}</h2>
                <p style="margin: 0; color: #666;"><strong>Severity:</strong> {alert.severity.value.upper()}</p>
                <p style="margin: 0; color: #666;"><strong>Source:</strong> {alert.source}</p>
                <p style="margin: 0; color: #666;"><strong>Time:</strong> {alert.timestamp.strftime('%Y-%m-%d %H:%M:%S UTC')}</p>
            </div>
            
            <div style="background-color: #f8f9fa; padding: 15px; border-radius: 5px; margin-bottom: 20px;">
                <h3 style="margin: 0 0 10px 0; color: #333;">Description</h3>
                <p style="margin: 0; color: #555; line-height: 1.5;">{alert.description}</p>
            </div>
            
            {self._format_metadata_html(alert.metadata) if alert.metadata else ''}
            
            <div style="margin-top: 20px; padding-top: 20px; border-top: 1px solid #dee2e6;">
                <p style="margin: 0; color: #6c757d; font-size: 12px;">
                    Alert ID: {alert.id}<br>
                    Generated by TyphoonLineWebhook Monitoring System
                </p>
            </div>
        </body>
        </html>
        """
    
    def _format_metadata_html(self, metadata: Dict[str, Any]) -> str:
        """Format metadata as HTML"""
        if not metadata:
            return ""
        
        html = '<div style="background-color: #e9ecef; padding: 15px; border-radius: 5px; margin-bottom: 20px;">'
        html += '<h3 style="margin: 0 0 10px 0; color: #333;">Additional Details</h3>'
        html += '<ul style="margin: 0; color: #555;">'
        
        for key, value in metadata.items():
            html += f'<li><strong>{key}:</strong> {value}</li>'
        
        html += '</ul></div>'
        return html

class SlackNotifier:
    """Slack notification handler"""
    
    def __init__(self):
        self.webhook_url = os.getenv('SLACK_WEBHOOK_URL')
        self.channel = os.getenv('SLACK_CHANNEL', '#alerts')
        self.username = os.getenv('SLACK_USERNAME', 'TyphoonWebhook')
    
    async def send_notification(self, alert: Alert, recipients: List[str]) -> bool:
        """Send Slack notification"""
        try:
            if not self.webhook_url:
                logging.warning("Slack webhook URL not configured, skipping Slack notification")
                return False
            
            # Create Slack message
            color = self._get_alert_color(alert.severity)
            
            payload = {
                "channel": self.channel,
                "username": self.username,
                "icon_emoji": ":warning:",
                "attachments": [
                    {
                        "color": color,
                        "title": alert.title,
                        "text": alert.description,
                        "fields": [
                            {
                                "title": "Severity",
                                "value": alert.severity.value.upper(),
                                "short": True
                            },
                            {
                                "title": "Source",
                                "value": alert.source,
                                "short": True
                            },
                            {
                                "title": "Time",
                                "value": alert.timestamp.strftime('%Y-%m-%d %H:%M:%S UTC'),
                                "short": False
                            }
                        ],
                        "footer": f"Alert ID: {alert.id}",
                        "ts": int(alert.timestamp.timestamp())
                    }
                ]
            }
            
            # Add metadata fields if present
            if alert.metadata:
                for key, value in alert.metadata.items():
                    payload["attachments"][0]["fields"].append({
                        "title": key,
                        "value": str(value),
                        "short": True
                    })
            
            # Send to Slack
            response = requests.post(
                self.webhook_url,
                json=payload,
                timeout=10
            )
            
            if response.status_code == 200:
                logging.info(f"Slack alert sent for {alert.id}")
                return True
            else:
                logging.error(f"Slack notification failed: {response.status_code}")
                return False
                
        except Exception as e:
            logging.error(f"Failed to send Slack notification: {str(e)}")
            return False
    
    def _get_alert_color(self, severity: AlertSeverity) -> str:
        """Get color code for alert severity"""
        colors = {
            AlertSeverity.INFO: "#36a64f",      # Green
            AlertSeverity.WARNING: "#ff9f00",   # Orange
            AlertSeverity.CRITICAL: "#ff0000",  # Red
            AlertSeverity.EMERGENCY: "#800080"  # Purple
        }
        return colors.get(severity, "#808080")

class WebhookNotifier:
    """Generic webhook notification handler"""
    
    async def send_notification(self, alert: Alert, webhook_urls: List[str]) -> bool:
        """Send webhook notification"""
        success_count = 0
        
        for url in webhook_urls:
            try:
                payload = {
                    "alert": asdict(alert),
                    "timestamp": alert.timestamp.isoformat(),
                    "severity": alert.severity.value,
                    "status": alert.status.value
                }
                
                response = requests.post(
                    url,
                    json=payload,
                    headers={'Content-Type': 'application/json'},
                    timeout=10
                )
                
                if response.status_code in [200, 201, 202]:
                    success_count += 1
                    logging.info(f"Webhook alert sent to {url}")
                else:
                    logging.error(f"Webhook notification failed to {url}: {response.status_code}")
                    
            except Exception as e:
                logging.error(f"Failed to send webhook notification to {url}: {str(e)}")
        
        return success_count > 0

class AlertManager:
    """Main alert management system"""
    
    def __init__(self):
        self.alerts = {}  # Active alerts by ID
        self.alert_history = deque(maxlen=10000)  # Alert history
        self.notification_rules = []
        self.notification_handlers = {
            NotificationChannel.EMAIL: EmailNotifier(),
            NotificationChannel.SLACK: SlackNotifier(),
            NotificationChannel.WEBHOOK: WebhookNotifier()
        }
        
        # Throttling and escalation tracking
        self.throttle_tracker = defaultdict(list)  # Track sent notifications
        self.escalation_tracker = defaultdict(int)  # Track escalation levels
        
        self.lock = threading.Lock()
        
        # Start background processing
        self.processing_active = True
        self.processor_thread = threading.Thread(target=self._process_alerts, daemon=True)
        self.processor_thread.start()
        
        # Load default notification rules
        self._load_default_rules()
    
    def _load_default_rules(self):
        """Load default notification rules"""
        # Critical system issues
        self.add_notification_rule(NotificationRule(
            name="critical_system_alerts",
            conditions={
                "severity": [AlertSeverity.CRITICAL, AlertSeverity.EMERGENCY],
                "source": ["database", "system_resources", "external_api"]
            },
            channels=[NotificationChannel.EMAIL, NotificationChannel.SLACK],
            recipients=self._get_admin_contacts(),
            throttle_minutes=1,
            escalation_delay_minutes=15
        ))
        
        # Warning level alerts
        self.add_notification_rule(NotificationRule(
            name="warning_alerts",
            conditions={
                "severity": [AlertSeverity.WARNING],
            },
            channels=[NotificationChannel.SLACK],
            recipients=self._get_admin_contacts(),
            throttle_minutes=10,
            escalation_delay_minutes=60
        ))
        
        # Database specific alerts
        self.add_notification_rule(NotificationRule(
            name="database_alerts",
            conditions={
                "source": ["database"],
                "severity": [AlertSeverity.WARNING, AlertSeverity.CRITICAL]
            },
            channels=[NotificationChannel.EMAIL],
            recipients=self._get_database_contacts(),
            throttle_minutes=5
        ))
    
    def _get_admin_contacts(self) -> List[str]:
        """Get admin contact list"""
        contacts = os.getenv('ADMIN_EMAIL_CONTACTS', '').split(',')
        return [contact.strip() for contact in contacts if contact.strip()]
    
    def _get_database_contacts(self) -> List[str]:
        """Get database admin contact list"""
        contacts = os.getenv('DB_ADMIN_EMAIL_CONTACTS', '').split(',')
        return [contact.strip() for contact in contacts if contact.strip()]
    
    def create_alert(
        self,
        title: str,
        description: str,
        severity: AlertSeverity,
        source: str,
        metadata: Optional[Dict[str, Any]] = None,
        tags: Optional[List[str]] = None
    ) -> Alert:
        """Create a new alert"""
        alert_id = f"{source}_{int(time.time())}_{hash(title) % 10000}"
        
        alert = Alert(
            id=alert_id,
            title=title,
            description=description,
            severity=severity,
            status=AlertStatus.ACTIVE,
            source=source,
            timestamp=datetime.now(),
            metadata=metadata or {},
            tags=tags or []
        )
        
        with self.lock:
            self.alerts[alert_id] = alert
            self.alert_history.append(alert)
        
        logging.info(f"Alert created: {alert_id} - {title}")
        
        # Trigger immediate processing for critical/emergency alerts
        if severity in [AlertSeverity.CRITICAL, AlertSeverity.EMERGENCY]:
            asyncio.create_task(self._process_alert(alert))
        
        return alert
    
    def acknowledge_alert(self, alert_id: str, acknowledged_by: str) -> bool:
        """Acknowledge an alert"""
        with self.lock:
            if alert_id in self.alerts:
                alert = self.alerts[alert_id]
                alert.status = AlertStatus.ACKNOWLEDGED
                alert.acknowledged_by = acknowledged_by
                alert.acknowledged_at = datetime.now()
                
                logging.info(f"Alert acknowledged: {alert_id} by {acknowledged_by}")
                return True
        
        return False
    
    def resolve_alert(self, alert_id: str) -> bool:
        """Resolve an alert"""
        with self.lock:
            if alert_id in self.alerts:
                alert = self.alerts[alert_id]
                alert.status = AlertStatus.RESOLVED
                alert.resolved_at = datetime.now()
                
                # Remove from active alerts
                del self.alerts[alert_id]
                
                logging.info(f"Alert resolved: {alert_id}")
                return True
        
        return False
    
    def add_notification_rule(self, rule: NotificationRule):
        """Add a notification rule"""
        with self.lock:
            self.notification_rules.append(rule)
        logging.info(f"Notification rule added: {rule.name}")
    
    def _process_alerts(self):
        """Background thread to process alerts"""
        while self.processing_active:
            try:
                with self.lock:
                    active_alerts = list(self.alerts.values())
                
                for alert in active_alerts:
                    if alert.status == AlertStatus.ACTIVE:
                        asyncio.create_task(self._process_alert(alert))
                
                time.sleep(30)  # Process every 30 seconds
                
            except Exception as e:
                logging.error(f"Alert processing error: {str(e)}")
                time.sleep(60)
    
    async def _process_alert(self, alert: Alert):
        """Process a single alert"""
        try:
            # Find matching notification rules
            matching_rules = self._find_matching_rules(alert)
            
            for rule in matching_rules:
                if not rule.enabled:
                    continue
                
                # Check throttling
                if self._is_throttled(alert, rule):
                    continue
                
                # Send notifications
                await self._send_notifications(alert, rule)
                
                # Update throttle tracker
                self._update_throttle_tracker(alert, rule)
                
        except Exception as e:
            logging.error(f"Failed to process alert {alert.id}: {str(e)}")
    
    def _find_matching_rules(self, alert: Alert) -> List[NotificationRule]:
        """Find notification rules that match the alert"""
        matching_rules = []
        
        for rule in self.notification_rules:
            if self._rule_matches_alert(rule, alert):
                matching_rules.append(rule)
        
        return matching_rules
    
    def _rule_matches_alert(self, rule: NotificationRule, alert: Alert) -> bool:
        """Check if a rule matches an alert"""
        conditions = rule.conditions
        
        # Check severity
        if 'severity' in conditions:
            if alert.severity not in conditions['severity']:
                return False
        
        # Check source
        if 'source' in conditions:
            if alert.source not in conditions['source']:
                return False
        
        # Check tags
        if 'tags' in conditions:
            required_tags = conditions['tags']
            if not any(tag in alert.tags for tag in required_tags):
                return False
        
        return True
    
    def _is_throttled(self, alert: Alert, rule: NotificationRule) -> bool:
        """Check if notifications are throttled for this rule"""
        throttle_key = f"{rule.name}_{alert.source}"
        now = datetime.now()
        cutoff = now - timedelta(minutes=rule.throttle_minutes)
        
        recent_notifications = [
            timestamp for timestamp in self.throttle_tracker[throttle_key]
            if timestamp > cutoff
        ]
        
        # Clean old entries
        self.throttle_tracker[throttle_key] = recent_notifications
        
        return len(recent_notifications) > 0
    
    def _update_throttle_tracker(self, alert: Alert, rule: NotificationRule):
        """Update throttle tracker after sending notification"""
        throttle_key = f"{rule.name}_{alert.source}"
        self.throttle_tracker[throttle_key].append(datetime.now())
    
    async def _send_notifications(self, alert: Alert, rule: NotificationRule):
        """Send notifications for an alert using a rule"""
        for channel in rule.channels:
            if channel in self.notification_handlers:
                handler = self.notification_handlers[channel]
                
                try:
                    if channel == NotificationChannel.WEBHOOK:
                        # For webhooks, recipients are URLs
                        success = await handler.send_notification(alert, rule.recipients)
                    else:
                        success = await handler.send_notification(alert, rule.recipients)
                    
                    if success:
                        logging.info(f"Notification sent via {channel.value} for alert {alert.id}")
                    else:
                        logging.warning(f"Failed to send notification via {channel.value} for alert {alert.id}")
                        
                except Exception as e:
                    logging.error(f"Notification handler error ({channel.value}): {str(e)}")
    
    def get_active_alerts(self) -> List[Alert]:
        """Get all active alerts"""
        with self.lock:
            return list(self.alerts.values())
    
    def get_alert_history(self, hours: int = 24) -> List[Alert]:
        """Get alert history"""
        cutoff_time = datetime.now() - timedelta(hours=hours)
        
        with self.lock:
            return [
                alert for alert in self.alert_history
                if alert.timestamp > cutoff_time
            ]
    
    def get_alert_statistics(self) -> Dict[str, Any]:
        """Get alert statistics"""
        with self.lock:
            active_count = len(self.alerts)
            total_history = len(self.alert_history)
            
            # Count by severity
            severity_counts = defaultdict(int)
            source_counts = defaultdict(int)
            
            for alert in self.alert_history:
                severity_counts[alert.severity.value] += 1
                source_counts[alert.source] += 1
            
            return {
                'active_alerts': active_count,
                'total_historical_alerts': total_history,
                'severity_distribution': dict(severity_counts),
                'source_distribution': dict(source_counts),
                'notification_rules': len(self.notification_rules)
            }
    
    def stop(self):
        """Stop the alert manager"""
        self.processing_active = False
        if self.processor_thread.is_alive():
            self.processor_thread.join(timeout=5)

# Global alert manager instance
_alert_manager = None

def get_alert_manager() -> AlertManager:
    """Get global alert manager instance"""
    global _alert_manager
    if _alert_manager is None:
        _alert_manager = AlertManager()
    return _alert_manager

def create_alert(
    title: str,
    description: str,
    severity: AlertSeverity,
    source: str,
    metadata: Optional[Dict[str, Any]] = None,
    tags: Optional[List[str]] = None
) -> Alert:
    """Convenience function to create an alert"""
    manager = get_alert_manager()
    return manager.create_alert(title, description, severity, source, metadata, tags)

# Integration with health checker and monitoring
async def integrate_with_health_checker():
    """Integration function to create alerts from health check results"""
    try:
        from .comprehensive_health_checker import get_health_checker
        
        health_checker = get_health_checker()
        system_health = await health_checker.run_all_health_checks()
        
        alert_manager = get_alert_manager()
        
        # Create alerts for unhealthy components
        for component_name, health_result in system_health.components.items():
            if health_result.status.value in ['critical', 'warning']:
                severity = AlertSeverity.CRITICAL if health_result.status.value == 'critical' else AlertSeverity.WARNING
                
                alert_manager.create_alert(
                    title=f"Component Health Issue: {component_name}",
                    description=health_result.message,
                    severity=severity,
                    source=component_name,
                    metadata={
                        'response_time': health_result.response_time,
                        'component_details': health_result.details,
                        'metrics': health_result.metrics
                    },
                    tags=['health_check', 'automated']
                )
                
    except Exception as e:
        logging.error(f"Failed to integrate with health checker: {str(e)}")

def init_alerting_system():
    """Initialize the alerting system"""
    alert_manager = get_alert_manager()
    logging.info("Alerting system initialized")
    return alert_manager