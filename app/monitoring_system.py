"""
Comprehensive monitoring and observability system for TyphoonLineWebhook
Prometheus metrics, health checks, and alerting
"""
import time
import logging
import threading
import psutil
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional, Callable
from prometheus_client import Counter, Histogram, Gauge, start_http_server, CollectorRegistry
from dataclasses import dataclass
from enum import Enum
import json

class HealthStatus(Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"
    CRITICAL = "critical"

@dataclass
class HealthCheck:
    name: str
    status: HealthStatus
    message: str
    response_time: float
    timestamp: datetime
    details: Dict[str, Any]

class PrometheusMetrics:
    """Prometheus metrics collector"""
    
    def __init__(self):
        self.registry = CollectorRegistry()
        
        # Request metrics
        self.request_count = Counter(
            'chatbot_requests_total',
            'Total requests processed',
            ['endpoint', 'method', 'status'],
            registry=self.registry
        )
        
        self.request_duration = Histogram(
            'chatbot_request_duration_seconds',
            'Request duration in seconds',
            ['endpoint'],
            registry=self.registry
        )
        
        # AI/ML metrics
        self.ai_response_count = Counter(
            'chatbot_ai_responses_total',
            'Total AI responses generated',
            ['model', 'status'],
            registry=self.registry
        )
        
        self.ai_response_duration = Histogram(
            'chatbot_ai_response_duration_seconds',
            'AI response generation time',
            ['model'],
            registry=self.registry
        )
        
        self.risk_assessments = Counter(
            'chatbot_risk_assessments_total',
            'Risk assessments performed',
            ['risk_level'],
            registry=self.registry
        )
        
        # Database metrics
        self.db_operations = Counter(
            'chatbot_db_operations_total',
            'Database operations',
            ['operation', 'table', 'status'],
            registry=self.registry
        )
        
        self.db_connection_pool = Gauge(
            'chatbot_db_connections_active',
            'Active database connections',
            registry=self.registry
        )
        
        # Cache metrics
        self.cache_operations = Counter(
            'chatbot_cache_operations_total',
            'Cache operations',
            ['cache_type', 'operation', 'result'],
            registry=self.registry
        )
        
        # System metrics
        self.system_cpu_usage = Gauge(
            'chatbot_system_cpu_percent',
            'System CPU usage percentage',
            registry=self.registry
        )
        
        self.system_memory_usage = Gauge(
            'chatbot_system_memory_percent',
            'System memory usage percentage',
            registry=self.registry
        )
        
        # Business metrics
        self.active_users = Gauge(
            'chatbot_active_users',
            'Currently active users',
            registry=self.registry
        )
        
        self.crisis_interventions = Counter(
            'chatbot_crisis_interventions_total',
            'Crisis interventions triggered',
            ['crisis_type'],
            registry=self.registry
        )
    
    def record_request(self, endpoint: str, method: str, status: str, duration: float):
        """Record HTTP request metrics"""
        self.request_count.labels(endpoint=endpoint, method=method, status=status).inc()
        self.request_duration.labels(endpoint=endpoint).observe(duration)
    
    def record_ai_response(self, model: str, status: str, duration: float):
        """Record AI response metrics"""
        self.ai_response_count.labels(model=model, status=status).inc()
        self.ai_response_duration.labels(model=model).observe(duration)
    
    def record_risk_assessment(self, risk_level: str):
        """Record risk assessment"""
        self.risk_assessments.labels(risk_level=risk_level).inc()
    
    def record_db_operation(self, operation: str, table: str, status: str):
        """Record database operation"""
        self.db_operations.labels(operation=operation, table=table, status=status).inc()
    
    def update_system_metrics(self):
        """Update system resource metrics"""
        self.system_cpu_usage.set(psutil.cpu_percent())
        self.system_memory_usage.set(psutil.virtual_memory().percent)

class HealthChecker:
    """Comprehensive health check system"""
    
    def __init__(self):
        self.checks = {}
        self.check_history = []
        self.max_history = 100
        self.lock = threading.Lock()
    
    def register_check(self, name: str, check_func: Callable, interval: int = 60):
        """Register a health check"""
        self.checks[name] = {
            'func': check_func,
            'interval': interval,
            'last_run': 0,
            'last_result': None
        }
    
    def run_check(self, name: str) -> HealthCheck:
        """Run a specific health check"""
        if name not in self.checks:
            return HealthCheck(
                name=name,
                status=HealthStatus.UNHEALTHY,
                message="Check not found",
                response_time=0.0,
                timestamp=datetime.now(),
                details={}
            )
        
        check = self.checks[name]
        start_time = time.time()
        
        try:
            result = check['func']()
            response_time = time.time() - start_time
            
            health_check = HealthCheck(
                name=name,
                status=result.get('status', HealthStatus.HEALTHY),
                message=result.get('message', 'OK'),
                response_time=response_time,
                timestamp=datetime.now(),
                details=result.get('details', {})
            )
            
            check['last_result'] = health_check
            check['last_run'] = time.time()
            
            with self.lock:
                self.check_history.append(health_check)
                if len(self.check_history) > self.max_history:
                    self.check_history.pop(0)
            
            return health_check
            
        except Exception as e:
            response_time = time.time() - start_time
            health_check = HealthCheck(
                name=name,
                status=HealthStatus.UNHEALTHY,
                message=f"Check failed: {str(e)}",
                response_time=response_time,
                timestamp=datetime.now(),
                details={'error': str(e)}
            )
            
            check['last_result'] = health_check
            return health_check
    
    def run_all_checks(self) -> Dict[str, HealthCheck]:
        """Run all registered health checks"""
        results = {}
        for name in self.checks:
            results[name] = self.run_check(name)
        return results
    
    def get_overall_health(self) -> HealthStatus:
        """Get overall system health"""
        results = self.run_all_checks()
        
        if not results:
            return HealthStatus.UNHEALTHY
        
        statuses = [check.status for check in results.values()]
        
        if any(status == HealthStatus.CRITICAL for status in statuses):
            return HealthStatus.CRITICAL
        elif any(status == HealthStatus.UNHEALTHY for status in statuses):
            return HealthStatus.UNHEALTHY
        elif any(status == HealthStatus.DEGRADED for status in statuses):
            return HealthStatus.DEGRADED
        else:
            return HealthStatus.HEALTHY

class AlertManager:
    """Real-time alerting system"""
    
    def __init__(self):
        self.alert_rules = {}
        self.alert_history = []
        self.notification_handlers = []
        self.max_history = 1000
        self.lock = threading.Lock()
    
    def add_alert_rule(self, name: str, condition: Callable, severity: str, message: str):
        """Add an alert rule"""
        self.alert_rules[name] = {
            'condition': condition,
            'severity': severity,
            'message': message,
            'last_triggered': 0,
            'cooldown': 300  # 5 minutes
        }
    
    def add_notification_handler(self, handler: Callable):
        """Add notification handler"""
        self.notification_handlers.append(handler)
    
    def check_alerts(self, metrics: Dict[str, Any]):
        """Check all alert conditions"""
        current_time = time.time()
        
        for name, rule in self.alert_rules.items():
            try:
                if rule['condition'](metrics):
                    # Check cooldown
                    if current_time - rule['last_triggered'] > rule['cooldown']:
                        self._trigger_alert(name, rule, metrics)
                        rule['last_triggered'] = current_time
            except Exception as e:
                logging.error(f"Alert rule {name} failed: {e}")
    
    def _trigger_alert(self, name: str, rule: Dict, metrics: Dict):
        """Trigger an alert"""
        alert = {
            'name': name,
            'severity': rule['severity'],
            'message': rule['message'],
            'timestamp': datetime.now().isoformat(),
            'metrics': metrics
        }
        
        with self.lock:
            self.alert_history.append(alert)
            if len(self.alert_history) > self.max_history:
                self.alert_history.pop(0)
        
        logging.warning(f"ALERT: {name} - {rule['message']}")
        
        # Send notifications
        for handler in self.notification_handlers:
            try:
                handler(alert)
            except Exception as e:
                logging.error(f"Notification handler failed: {e}")

class SystemMonitor:
    """Main monitoring coordinator"""
    
    def __init__(self, port: int = 8000):
        self.metrics = PrometheusMetrics()
        self.health_checker = HealthChecker()
        self.alert_manager = AlertManager()
        self.monitoring_thread = None
        self.running = False
        self.port = port
        
        # Setup default health checks
        self._setup_default_health_checks()
        self._setup_default_alerts()
    
    def _setup_default_health_checks(self):
        """Setup default health checks"""
        
        def check_database():
            try:
                from .database_manager import DatabaseManager
                import os
                
                db_config = {
                    'MYSQL_HOST': os.getenv('MYSQL_HOST'),
                    'MYSQL_PORT': int(os.getenv('MYSQL_PORT', 3306)),
                    'MYSQL_USER': os.getenv('MYSQL_USER'),
                    'MYSQL_PASSWORD': os.getenv('MYSQL_PASSWORD'),
                    'MYSQL_DB': os.getenv('MYSQL_DB')
                }
                
                db = DatabaseManager(db_config)
                if db.check_connection():
                    return {'status': HealthStatus.HEALTHY, 'message': 'Database connection OK'}
                else:
                    return {'status': HealthStatus.UNHEALTHY, 'message': 'Database connection failed'}
            except Exception as e:
                return {'status': HealthStatus.UNHEALTHY, 'message': f'Database check error: {e}'}
        
        def check_redis():
            try:
                import redis
                import os
                
                redis_client = redis.Redis(
                    host=os.getenv('REDIS_HOST', 'localhost'),
                    port=int(os.getenv('REDIS_PORT', 6379)),
                    db=int(os.getenv('REDIS_DB', 0))
                )
                
                redis_client.ping()
                return {'status': HealthStatus.HEALTHY, 'message': 'Redis connection OK'}
            except Exception as e:
                return {'status': HealthStatus.UNHEALTHY, 'message': f'Redis check error: {e}'}
        
        def check_system_resources():
            cpu_percent = psutil.cpu_percent()
            memory_percent = psutil.virtual_memory().percent
            
            if cpu_percent > 90 or memory_percent > 90:
                return {'status': HealthStatus.CRITICAL, 'message': 'System resources critical'}
            elif cpu_percent > 70 or memory_percent > 70:
                return {'status': HealthStatus.DEGRADED, 'message': 'System resources high'}
            else:
                return {'status': HealthStatus.HEALTHY, 'message': 'System resources OK'}
        
        self.health_checker.register_check('database', check_database, 30)
        self.health_checker.register_check('redis', check_redis, 30)
        self.health_checker.register_check('system', check_system_resources, 10)
    
    def _setup_default_alerts(self):
        """Setup default alert rules"""
        
        def high_cpu_alert(metrics):
            return metrics.get('cpu_percent', 0) > 80
        
        def high_memory_alert(metrics):
            return metrics.get('memory_percent', 0) > 85
        
        def high_error_rate(metrics):
            total_requests = metrics.get('total_requests', 0)
            error_requests = metrics.get('error_requests', 0)
            if total_requests > 10:
                error_rate = error_requests / total_requests
                return error_rate > 0.05  # 5% error rate
            return False
        
        self.alert_manager.add_alert_rule(
            'high_cpu', high_cpu_alert, 'warning',
            'High CPU usage detected'
        )
        
        self.alert_manager.add_alert_rule(
            'high_memory', high_memory_alert, 'warning',
            'High memory usage detected'
        )
        
        self.alert_manager.add_alert_rule(
            'high_error_rate', high_error_rate, 'critical',
            'High error rate detected'
        )
    
    def start_monitoring(self):
        """Start monitoring services"""
        # Start Prometheus metrics server
        start_http_server(self.port, registry=self.metrics.registry)
        logging.info(f"Prometheus metrics server started on port {self.port}")
        
        # Start monitoring thread
        self.running = True
        self.monitoring_thread = threading.Thread(target=self._monitoring_loop, daemon=True)
        self.monitoring_thread.start()
        logging.info("System monitoring started")
    
    def stop_monitoring(self):
        """Stop monitoring services"""
        self.running = False
        if self.monitoring_thread:
            self.monitoring_thread.join()
        logging.info("System monitoring stopped")
    
    def _monitoring_loop(self):
        """Main monitoring loop"""
        while self.running:
            try:
                # Update system metrics
                self.metrics.update_system_metrics()
                
                # Run health checks
                health_results = self.health_checker.run_all_checks()
                
                # Collect metrics for alerting
                metrics = {
                    'cpu_percent': psutil.cpu_percent(),
                    'memory_percent': psutil.virtual_memory().percent,
                    'timestamp': time.time()
                }
                
                # Check alerts
                self.alert_manager.check_alerts(metrics)
                
                time.sleep(30)  # Check every 30 seconds
                
            except Exception as e:
                logging.error(f"Monitoring loop error: {e}")
                time.sleep(60)
    
    def get_status_summary(self) -> Dict[str, Any]:
        """Get comprehensive status summary"""
        overall_health = self.health_checker.get_overall_health()
        health_checks = self.health_checker.run_all_checks()
        
        return {
            'overall_health': overall_health.value,
            'health_checks': {
                name: {
                    'status': check.status.value,
                    'message': check.message,
                    'response_time': check.response_time
                }
                for name, check in health_checks.items()
            },
            'system_metrics': {
                'cpu_percent': psutil.cpu_percent(),
                'memory_percent': psutil.virtual_memory().percent(),
                'disk_percent': psutil.disk_usage('/').percent
            },
            'timestamp': datetime.now().isoformat()
        }

# Global monitor instance
_system_monitor = None

def get_system_monitor(port: int = 8000) -> SystemMonitor:
    """Get global system monitor instance"""
    global _system_monitor
    if _system_monitor is None:
        _system_monitor = SystemMonitor(port)
    return _system_monitor

def init_monitoring_system(port: int = 8000):
    """Initialize and start monitoring system"""
    monitor = get_system_monitor(port)
    monitor.start_monitoring()
    return monitor