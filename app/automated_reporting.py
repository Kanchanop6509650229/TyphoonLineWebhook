"""
Automated report generation and custom alert rules for TyphoonLineWebhook administrators
"""
import os
import json
import logging
import asyncio
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional
from dataclasses import dataclass, asdict
from enum import Enum
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email import encoders
import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns
from jinja2 import Template
import threading
import schedule
import time

class ReportType(Enum):
    DAILY_SUMMARY = "daily_summary"
    WEEKLY_ANALYTICS = "weekly_analytics"
    MONTHLY_OVERVIEW = "monthly_overview"
    CUSTOM_PERIOD = "custom_period"

class AlertRuleType(Enum):
    THRESHOLD = "threshold"
    TREND = "trend"
    ANOMALY = "anomaly"
    PATTERN = "pattern"

@dataclass
class ReportConfig:
    name: str
    report_type: ReportType
    recipients: List[str]
    schedule_cron: str
    enabled: bool = True
    include_charts: bool = True
    format: str = "html"  # html, pdf

@dataclass
class CustomAlertRule:
    name: str
    rule_type: AlertRuleType
    metric_name: str
    condition: str
    threshold_value: float
    description: str
    enabled: bool = True
    cooldown_minutes: int = 60

class ReportGenerator:
    """Automated report generator"""
    
    def __init__(self, db_manager=None, metrics_collector=None):
        self.db_manager = db_manager
        self.metrics_collector = metrics_collector
        
    def generate_daily_summary(self, date: datetime = None) -> Dict[str, Any]:
        """Generate daily summary report"""
        if not date:
            date = datetime.now() - timedelta(days=1)
        
        start_date = date.replace(hour=0, minute=0, second=0, microsecond=0)
        end_date = start_date + timedelta(days=1)
        
        try:
            # User metrics
            user_stats = self._get_user_stats(start_date, end_date)
            
            # System metrics
            system_stats = self._get_system_stats(start_date, end_date)
            
            # Risk metrics
            risk_stats = self._get_risk_stats(start_date, end_date)
            
            return {
                'report_date': date.strftime('%Y-%m-%d'),
                'user_metrics': user_stats,
                'system_metrics': system_stats,
                'risk_metrics': risk_stats,
                'generated_at': datetime.now().isoformat()
            }
            
        except Exception as e:
            logging.error(f"Failed to generate daily summary: {str(e)}")
            return {'error': str(e)}
    
    def _get_user_stats(self, start_date: datetime, end_date: datetime) -> Dict[str, Any]:
        """Get user statistics for period"""
        if not self.db_manager:
            return {}
        
        try:
            # Active users
            active_users_query = """
                SELECT COUNT(DISTINCT user_id) FROM conversations 
                WHERE timestamp >= %s AND timestamp < %s
            """
            active_users = self.db_manager.execute_query(active_users_query, (start_date, end_date))[0][0] or 0
            
            # Total conversations
            conversations_query = """
                SELECT COUNT(*) FROM conversations 
                WHERE timestamp >= %s AND timestamp < %s
            """
            total_conversations = self.db_manager.execute_query(conversations_query, (start_date, end_date))[0][0] or 0
            
            # New users
            new_users_query = """
                SELECT COUNT(DISTINCT user_id) FROM conversations 
                WHERE timestamp >= %s AND timestamp < %s
                AND user_id NOT IN (
                    SELECT DISTINCT user_id FROM conversations 
                    WHERE timestamp < %s
                )
            """
            new_users = self.db_manager.execute_query(new_users_query, (start_date, end_date, start_date))[0][0] or 0
            
            return {
                'active_users': active_users,
                'total_conversations': total_conversations,
                'new_users': new_users,
                'avg_conversations_per_user': total_conversations / max(active_users, 1)
            }
            
        except Exception as e:
            logging.error(f"Failed to get user stats: {str(e)}")
            return {}
    
    def _get_system_stats(self, start_date: datetime, end_date: datetime) -> Dict[str, Any]:
        """Get system statistics for period"""
        # Mock implementation - would integrate with monitoring system
        return {
            'avg_cpu_usage': 45.2,
            'avg_memory_usage': 62.8,
            'total_requests': 1250,
            'error_count': 12,
            'avg_response_time': 0.245
        }
    
    def _get_risk_stats(self, start_date: datetime, end_date: datetime) -> Dict[str, Any]:
        """Get risk assessment statistics"""
        if not self.db_manager:
            return {}
        
        try:
            risk_distribution_query = """
                SELECT risk_level, COUNT(*) as count
                FROM conversations 
                WHERE timestamp >= %s AND timestamp < %s 
                AND risk_level IS NOT NULL
                GROUP BY risk_level
            """
            results = self.db_manager.execute_query(risk_distribution_query, (start_date, end_date))
            risk_distribution = {row[0]: row[1] for row in results}
            
            # Crisis interventions
            crisis_query = """
                SELECT COUNT(*) FROM conversations 
                WHERE timestamp >= %s AND timestamp < %s 
                AND risk_level IN ('high', 'critical')
            """
            crisis_interventions = self.db_manager.execute_query(crisis_query, (start_date, end_date))[0][0] or 0
            
            return {
                'risk_distribution': risk_distribution,
                'crisis_interventions': crisis_interventions
            }
            
        except Exception as e:
            logging.error(f"Failed to get risk stats: {str(e)}")
            return {}

class EmailReportSender:
    """Send reports via email"""
    
    def __init__(self):
        self.smtp_server = os.getenv('SMTP_SERVER', 'localhost')
        self.smtp_port = int(os.getenv('SMTP_PORT', 587))
        self.smtp_username = os.getenv('SMTP_USERNAME')
        self.smtp_password = os.getenv('SMTP_PASSWORD')
        self.from_email = os.getenv('REPORT_FROM_EMAIL', 'reports@typhoon-webhook.com')
    
    def send_report(self, report_data: Dict[str, Any], config: ReportConfig) -> bool:
        """Send report via email"""
        try:
            # Generate HTML report content
            html_content = self._generate_html_report(report_data, config)
            
            # Create email message
            msg = MIMEMultipart()
            msg['From'] = self.from_email
            msg['To'] = ', '.join(config.recipients)
            msg['Subject'] = f"TyphoonLineWebhook {config.report_type.value.title()} Report"
            
            # Attach HTML content
            msg.attach(MIMEText(html_content, 'html'))
            
            # Send email
            if self.smtp_username and self.smtp_password:
                server = smtplib.SMTP(self.smtp_server, self.smtp_port)
                server.starttls()
                server.login(self.smtp_username, self.smtp_password)
                server.sendmail(self.from_email, config.recipients, msg.as_string())
                server.quit()
                
                logging.info(f"Report sent to {len(config.recipients)} recipients")
                return True
            else:
                logging.warning("SMTP credentials not configured")
                return False
                
        except Exception as e:
            logging.error(f"Failed to send email report: {str(e)}")
            return False
    
    def _generate_html_report(self, data: Dict[str, Any], config: ReportConfig) -> str:
        """Generate HTML report content"""
        template_str = """
        <!DOCTYPE html>
        <html>
        <head>
            <title>{{ report_title }}</title>
            <style>
                body { font-family: Arial, sans-serif; margin: 20px; }
                .header { background: #667eea; color: white; padding: 20px; border-radius: 5px; }
                .metric-card { background: #f8f9fa; padding: 15px; margin: 10px 0; border-radius: 5px; }
                .metric-value { font-size: 24px; font-weight: bold; color: #333; }
                .metric-label { color: #666; font-size: 14px; }
                table { width: 100%; border-collapse: collapse; margin: 20px 0; }
                th, td { border: 1px solid #ddd; padding: 8px; text-align: left; }
                th { background-color: #f2f2f2; }
                .footer { margin-top: 30px; font-size: 12px; color: #666; }
            </style>
        </head>
        <body>
            <div class="header">
                <h1>{{ report_title }}</h1>
                <p>Generated: {{ generated_at }}</p>
            </div>
            
            {% if user_metrics %}
            <h2>User Engagement</h2>
            <div class="metric-card">
                <div class="metric-value">{{ user_metrics.active_users }}</div>
                <div class="metric-label">Active Users</div>
            </div>
            <div class="metric-card">
                <div class="metric-value">{{ user_metrics.total_conversations }}</div>
                <div class="metric-label">Total Conversations</div>
            </div>
            <div class="metric-card">
                <div class="metric-value">{{ user_metrics.new_users }}</div>
                <div class="metric-label">New Users</div>
            </div>
            {% endif %}
            
            {% if system_metrics %}
            <h2>System Performance</h2>
            <table>
                <tr><th>Metric</th><th>Value</th></tr>
                <tr><td>Average CPU Usage</td><td>{{ "%.1f"|format(system_metrics.avg_cpu_usage) }}%</td></tr>
                <tr><td>Average Memory Usage</td><td>{{ "%.1f"|format(system_metrics.avg_memory_usage) }}%</td></tr>
                <tr><td>Total Requests</td><td>{{ system_metrics.total_requests }}</td></tr>
                <tr><td>Error Count</td><td>{{ system_metrics.error_count }}</td></tr>
            </table>
            {% endif %}
            
            {% if risk_metrics %}
            <h2>Risk Assessment</h2>
            {% if risk_metrics.risk_distribution %}
            <h3>Risk Distribution</h3>
            <table>
                <tr><th>Risk Level</th><th>Count</th></tr>
                {% for level, count in risk_metrics.risk_distribution.items() %}
                <tr><td>{{ level.title() }}</td><td>{{ count }}</td></tr>
                {% endfor %}
            </table>
            {% endif %}
            
            <div class="metric-card">
                <div class="metric-value">{{ risk_metrics.crisis_interventions }}</div>
                <div class="metric-label">Crisis Interventions</div>
            </div>
            {% endif %}
            
            <div class="footer">
                <p>This report was automatically generated by TyphoonLineWebhook Analytics System.</p>
            </div>
        </body>
        </html>
        """
        
        template = Template(template_str)
        return template.render(
            report_title=f"{config.report_type.value.title()} Report",
            generated_at=datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            **data
        )

class CustomAlertRuleEngine:
    """Custom alert rule processing engine"""
    
    def __init__(self, alert_manager=None):
        self.alert_manager = alert_manager
        self.rules = []
        self.rule_states = {}  # Track rule execution state
        
    def add_rule(self, rule: CustomAlertRule):
        """Add custom alert rule"""
        self.rules.append(rule)
        self.rule_states[rule.name] = {
            'last_triggered': None,
            'consecutive_triggers': 0
        }
        logging.info(f"Added custom alert rule: {rule.name}")
    
    def process_rules(self, metrics: Dict[str, Any]):
        """Process all custom alert rules"""
        current_time = datetime.now()
        
        for rule in self.rules:
            if not rule.enabled:
                continue
                
            try:
                # Check cooldown
                rule_state = self.rule_states[rule.name]
                if rule_state['last_triggered']:
                    time_since_trigger = (current_time - rule_state['last_triggered']).total_seconds() / 60
                    if time_since_trigger < rule.cooldown_minutes:
                        continue
                
                # Evaluate rule condition
                if self._evaluate_rule(rule, metrics):
                    self._trigger_custom_alert(rule, metrics, current_time)
                    
            except Exception as e:
                logging.error(f"Failed to process custom alert rule {rule.name}: {str(e)}")
    
    def _evaluate_rule(self, rule: CustomAlertRule, metrics: Dict[str, Any]) -> bool:
        """Evaluate if rule condition is met"""
        # Get metric value
        metric_value = self._get_metric_value(rule.metric_name, metrics)
        if metric_value is None:
            return False
        
        # Evaluate condition
        if rule.rule_type == AlertRuleType.THRESHOLD:
            return self._evaluate_threshold(rule.condition, metric_value, rule.threshold_value)
        elif rule.rule_type == AlertRuleType.TREND:
            return self._evaluate_trend(rule, metric_value)
        
        return False
    
    def _get_metric_value(self, metric_name: str, metrics: Dict[str, Any]) -> Optional[float]:
        """Extract metric value from metrics data"""
        # Navigate nested dictionary
        parts = metric_name.split('.')
        value = metrics
        
        for part in parts:
            if isinstance(value, dict) and part in value:
                value = value[part]
            else:
                return None
        
        return float(value) if isinstance(value, (int, float)) else None
    
    def _evaluate_threshold(self, condition: str, value: float, threshold: float) -> bool:
        """Evaluate threshold condition"""
        if condition == '>':
            return value > threshold
        elif condition == '<':
            return value < threshold
        elif condition == '>=':
            return value >= threshold
        elif condition == '<=':
            return value <= threshold
        elif condition == '==':
            return abs(value - threshold) < 0.001
        
        return False
    
    def _evaluate_trend(self, rule: CustomAlertRule, current_value: float) -> bool:
        """Evaluate trend-based condition (simplified)"""
        # This would require historical data - simplified implementation
        return False
    
    def _trigger_custom_alert(self, rule: CustomAlertRule, metrics: Dict[str, Any], timestamp: datetime):
        """Trigger custom alert"""
        if self.alert_manager:
            from .alerting_system import AlertSeverity, create_alert
            
            create_alert(
                title=f"Custom Alert: {rule.name}",
                description=rule.description,
                severity=AlertSeverity.WARNING,
                source="custom_rule",
                metadata={
                    'rule_name': rule.name,
                    'metric_name': rule.metric_name,
                    'current_value': self._get_metric_value(rule.metric_name, metrics),
                    'threshold': rule.threshold_value
                },
                tags=['custom_alert', 'automated']
            )
        
        # Update rule state
        self.rule_states[rule.name]['last_triggered'] = timestamp
        self.rule_states[rule.name]['consecutive_triggers'] += 1
        
        logging.warning(f"Custom alert triggered: {rule.name}")

class ReportScheduler:
    """Scheduled report generator and sender"""
    
    def __init__(self, db_manager=None, metrics_collector=None):
        self.report_generator = ReportGenerator(db_manager, metrics_collector)
        self.email_sender = EmailReportSender()
        self.report_configs = []
        self.scheduler_active = True
        
        # Start scheduler thread
        self.scheduler_thread = threading.Thread(target=self._run_scheduler, daemon=True)
        self.scheduler_thread.start()
    
    def add_report_config(self, config: ReportConfig):
        """Add report configuration"""
        self.report_configs.append(config)
        
        # Schedule the report
        if config.report_type == ReportType.DAILY_SUMMARY:
            schedule.every().day.at("08:00").do(self._generate_and_send_report, config)
        elif config.report_type == ReportType.WEEKLY_ANALYTICS:
            schedule.every().monday.at("09:00").do(self._generate_and_send_report, config)
        elif config.report_type == ReportType.MONTHLY_OVERVIEW:
            schedule.every().month.do(self._generate_and_send_report, config)
        
        logging.info(f"Scheduled report: {config.name}")
    
    def _run_scheduler(self):
        """Run the report scheduler"""
        while self.scheduler_active:
            try:
                schedule.run_pending()
                time.sleep(60)  # Check every minute
            except Exception as e:
                logging.error(f"Scheduler error: {str(e)}")
                time.sleep(300)  # Wait 5 minutes on error
    
    def _generate_and_send_report(self, config: ReportConfig):
        """Generate and send report"""
        if not config.enabled:
            return
        
        try:
            # Generate report based on type
            if config.report_type == ReportType.DAILY_SUMMARY:
                report_data = self.report_generator.generate_daily_summary()
            else:
                report_data = {'message': 'Report type not implemented yet'}
            
            # Send report
            if not report_data.get('error'):
                success = self.email_sender.send_report(report_data, config)
                if success:
                    logging.info(f"Report sent successfully: {config.name}")
                else:
                    logging.error(f"Failed to send report: {config.name}")
            else:
                logging.error(f"Failed to generate report: {config.name} - {report_data['error']}")
                
        except Exception as e:
            logging.error(f"Report generation/sending failed: {config.name} - {str(e)}")
    
    def stop(self):
        """Stop the scheduler"""
        self.scheduler_active = False

# Global instances
_report_scheduler = None
_alert_rule_engine = None

def init_reporting_system(db_manager=None, metrics_collector=None, alert_manager=None):
    """Initialize automated reporting system"""
    global _report_scheduler, _alert_rule_engine
    
    # Initialize report scheduler
    _report_scheduler = ReportScheduler(db_manager, metrics_collector)
    
    # Add default daily report
    daily_config = ReportConfig(
        name="Daily System Summary",
        report_type=ReportType.DAILY_SUMMARY,
        recipients=os.getenv('ADMIN_EMAIL_CONTACTS', '').split(','),
        schedule_cron="0 8 * * *",  # 8 AM daily
        enabled=True
    )
    _report_scheduler.add_report_config(daily_config)
    
    # Initialize custom alert rule engine
    _alert_rule_engine = CustomAlertRuleEngine(alert_manager)
    
    # Add default custom rules
    _add_default_alert_rules()
    
    logging.info("Automated reporting and alerting system initialized")
    return _report_scheduler, _alert_rule_engine

def _add_default_alert_rules():
    """Add default custom alert rules"""
    global _alert_rule_engine
    
    # High CPU usage rule
    cpu_rule = CustomAlertRule(
        name="High CPU Usage",
        rule_type=AlertRuleType.THRESHOLD,
        metric_name="system_performance.cpu_usage",
        condition=">",
        threshold_value=85.0,
        description="CPU usage is above 85%",
        cooldown_minutes=15
    )
    _alert_rule_engine.add_rule(cpu_rule)
    
    # High memory usage rule
    memory_rule = CustomAlertRule(
        name="High Memory Usage",
        rule_type=AlertRuleType.THRESHOLD,
        metric_name="system_performance.memory_usage",
        condition=">",
        threshold_value=90.0,
        description="Memory usage is above 90%",
        cooldown_minutes=15
    )
    _alert_rule_engine.add_rule(memory_rule)
    
    # Crisis intervention spike
    crisis_rule = CustomAlertRule(
        name="Crisis Intervention Spike",
        rule_type=AlertRuleType.THRESHOLD,
        metric_name="risk_metrics.crisis_interventions",
        condition=">",
        threshold_value=10.0,
        description="Crisis interventions per day exceeded 10",
        cooldown_minutes=60
    )
    _alert_rule_engine.add_rule(crisis_rule)