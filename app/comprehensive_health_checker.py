"""
Comprehensive health check system for TyphoonLineWebhook
Monitors all system components and provides detailed health reports
"""
import os
import time
import asyncio
import logging
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional, Callable
from dataclasses import dataclass, asdict
from enum import Enum
import threading
import json
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed

class ComponentStatus(Enum):
    HEALTHY = "healthy"
    WARNING = "warning"
    CRITICAL = "critical"
    UNKNOWN = "unknown"

@dataclass
class HealthCheckResult:
    component: str
    status: ComponentStatus
    message: str
    response_time: float
    timestamp: datetime
    details: Dict[str, Any]
    metrics: Dict[str, float]

@dataclass
class SystemHealth:
    overall_status: ComponentStatus
    components: Dict[str, HealthCheckResult]
    timestamp: datetime
    summary: Dict[str, Any]

class DatabaseHealthChecker:
    """Health checker for database components"""
    
    def __init__(self, db_manager=None):
        self.db_manager = db_manager
    
    async def check_database_connection(self) -> HealthCheckResult:
        """Check database connection health"""
        start_time = time.time()
        
        try:
            if not self.db_manager:
                from .database_manager import DatabaseManager
                config = {
                    'MYSQL_HOST': os.getenv('MYSQL_HOST'),
                    'MYSQL_PORT': int(os.getenv('MYSQL_PORT', 3306)),
                    'MYSQL_USER': os.getenv('MYSQL_USER'),
                    'MYSQL_PASSWORD': os.getenv('MYSQL_PASSWORD'),
                    'MYSQL_DB': os.getenv('MYSQL_DB')
                }
                self.db_manager = DatabaseManager(config)
            
            # Test connection
            is_connected = self.db_manager.check_connection()
            response_time = time.time() - start_time
            
            if is_connected:
                # Get additional metrics
                pool_status = self.db_manager.get_pool_status()
                metrics = self.db_manager.get_connection_metrics()
                
                return HealthCheckResult(
                    component="database_connection",
                    status=ComponentStatus.HEALTHY,
                    message="Database connection is healthy",
                    response_time=response_time,
                    timestamp=datetime.now(),
                    details={
                        "host": os.getenv('MYSQL_HOST'),
                        "database": os.getenv('MYSQL_DB'),
                        "pool_status": pool_status
                    },
                    metrics={
                        "response_time": response_time,
                        "active_connections": metrics.get('active_connections', 0),
                        "pool_size": metrics.get('pool_size', 0)
                    }
                )
            else:
                return HealthCheckResult(
                    component="database_connection",
                    status=ComponentStatus.CRITICAL,
                    message="Database connection failed",
                    response_time=response_time,
                    timestamp=datetime.now(),
                    details={},
                    metrics={"response_time": response_time}
                )
                
        except Exception as e:
            response_time = time.time() - start_time
            return HealthCheckResult(
                component="database_connection",
                status=ComponentStatus.CRITICAL,
                message=f"Database health check failed: {str(e)}",
                response_time=response_time,
                timestamp=datetime.now(),
                details={"error": str(e)},
                metrics={"response_time": response_time}
            )
    
    async def check_database_performance(self) -> HealthCheckResult:
        """Check database performance metrics"""
        start_time = time.time()
        
        try:
            # Test query performance
            test_query = "SELECT 1 as test_value"
            query_start = time.time()
            result = self.db_manager.execute_query(test_query)
            query_time = time.time() - query_start
            
            response_time = time.time() - start_time
            
            # Determine status based on query performance
            if query_time < 0.1:  # Less than 100ms
                status = ComponentStatus.HEALTHY
                message = f"Database performance is excellent ({query_time:.3f}s)"
            elif query_time < 0.5:  # Less than 500ms
                status = ComponentStatus.HEALTHY
                message = f"Database performance is good ({query_time:.3f}s)"
            elif query_time < 1.0:  # Less than 1s
                status = ComponentStatus.WARNING
                message = f"Database performance is slow ({query_time:.3f}s)"
            else:
                status = ComponentStatus.CRITICAL
                message = f"Database performance is critical ({query_time:.3f}s)"
            
            return HealthCheckResult(
                component="database_performance",
                status=status,
                message=message,
                response_time=response_time,
                timestamp=datetime.now(),
                details={"test_query_time": query_time},
                metrics={
                    "response_time": response_time,
                    "query_time": query_time
                }
            )
            
        except Exception as e:
            response_time = time.time() - start_time
            return HealthCheckResult(
                component="database_performance",
                status=ComponentStatus.CRITICAL,
                message=f"Database performance check failed: {str(e)}",
                response_time=response_time,
                timestamp=datetime.now(),
                details={"error": str(e)},
                metrics={"response_time": response_time}
            )

class CacheHealthChecker:
    """Health checker for caching systems"""
    
    def __init__(self):
        self.redis_client = None
    
    async def check_redis_connection(self) -> HealthCheckResult:
        """Check Redis connection health"""
        start_time = time.time()
        
        try:
            import redis
            
            if not self.redis_client:
                self.redis_client = redis.Redis(
                    host=os.getenv('REDIS_HOST', 'localhost'),
                    port=int(os.getenv('REDIS_PORT', 6379)),
                    db=int(os.getenv('REDIS_DB', 0)),
                    decode_responses=True
                )
            
            # Test Redis connection
            ping_result = self.redis_client.ping()
            response_time = time.time() - start_time
            
            if ping_result:
                # Get Redis info
                info = self.redis_client.info()
                memory_usage = info.get('used_memory', 0)
                connected_clients = info.get('connected_clients', 0)
                
                return HealthCheckResult(
                    component="redis_cache",
                    status=ComponentStatus.HEALTHY,
                    message="Redis cache is healthy",
                    response_time=response_time,
                    timestamp=datetime.now(),
                    details={
                        "redis_version": info.get('redis_version'),
                        "uptime_seconds": info.get('uptime_in_seconds'),
                        "connected_clients": connected_clients
                    },
                    metrics={
                        "response_time": response_time,
                        "memory_usage": memory_usage,
                        "connected_clients": connected_clients
                    }
                )
            else:
                return HealthCheckResult(
                    component="redis_cache",
                    status=ComponentStatus.CRITICAL,
                    message="Redis ping failed",
                    response_time=response_time,
                    timestamp=datetime.now(),
                    details={},
                    metrics={"response_time": response_time}
                )
                
        except Exception as e:
            response_time = time.time() - start_time
            return HealthCheckResult(
                component="redis_cache",
                status=ComponentStatus.CRITICAL,
                message=f"Redis health check failed: {str(e)}",
                response_time=response_time,
                timestamp=datetime.now(),
                details={"error": str(e)},
                metrics={"response_time": response_time}
            )
    
    async def check_application_cache(self) -> HealthCheckResult:
        """Check application-level cache health"""
        start_time = time.time()
        
        try:
            from .caching_system import ApplicationCache
            
            # Test cache operations
            cache = ApplicationCache()
            test_key = "health_check_test"
            test_value = {"timestamp": datetime.now().isoformat()}
            
            # Test set operation
            cache.set(test_key, test_value, ttl=60)
            
            # Test get operation
            retrieved_value = cache.get(test_key)
            
            response_time = time.time() - start_time
            
            if retrieved_value == test_value:
                stats = cache.get_cache_stats()
                return HealthCheckResult(
                    component="application_cache",
                    status=ComponentStatus.HEALTHY,
                    message="Application cache is healthy",
                    response_time=response_time,
                    timestamp=datetime.now(),
                    details={"cache_stats": stats},
                    metrics={
                        "response_time": response_time,
                        "hit_rate": stats.get('hit_rate', 0),
                        "cache_size": stats.get('current_size', 0)
                    }
                )
            else:
                return HealthCheckResult(
                    component="application_cache",
                    status=ComponentStatus.WARNING,
                    message="Application cache test failed",
                    response_time=response_time,
                    timestamp=datetime.now(),
                    details={"expected": test_value, "actual": retrieved_value},
                    metrics={"response_time": response_time}
                )
                
        except Exception as e:
            response_time = time.time() - start_time
            return HealthCheckResult(
                component="application_cache",
                status=ComponentStatus.CRITICAL,
                message=f"Application cache health check failed: {str(e)}",
                response_time=response_time,
                timestamp=datetime.now(),
                details={"error": str(e)},
                metrics={"response_time": response_time}
            )

class ExternalServiceHealthChecker:
    """Health checker for external services"""
    
    async def check_xai_api(self) -> HealthCheckResult:
        """Check xAI Grok API health via centralized client"""
        start_time = time.time()
        
        try:
            api_key = os.getenv('XAI_API_KEY')
            if not api_key:
                return HealthCheckResult(
                    component="xai_api",
                    status=ComponentStatus.WARNING,
                    message="xAI API key not configured",
                    response_time=0,
                    timestamp=datetime.now(),
                    details={},
                    metrics={}
                )
            # Use centralized client to perform a minimal ping
            from .llm import grok_client
            _ = await grok_client.astream_chat(
                messages=[{"role": "user", "content": "ping"}],
                model=os.getenv('XAI_MODEL', 'grok-4'),
                max_tokens=5,
            )
            response_time = time.time() - start_time
            return HealthCheckResult(
                component="xai_api",
                status=ComponentStatus.HEALTHY,
                message="xAI Grok API is healthy",
                response_time=response_time,
                timestamp=datetime.now(),
                details={},
                metrics={"response_time": response_time}
            )
                
        except Exception as e:
            response_time = time.time() - start_time
            return HealthCheckResult(
                component="xai_api",
                status=ComponentStatus.CRITICAL,
                message=f"xAI Grok API health check failed: {str(e)}",
                response_time=response_time,
                timestamp=datetime.now(),
                details={"error": str(e)},
                metrics={"response_time": response_time}
            )
    
    async def check_line_api(self) -> HealthCheckResult:
        """Check LINE API health"""
        start_time = time.time()
        
        try:
            access_token = os.getenv('LINE_CHANNEL_ACCESS_TOKEN')
            if not access_token:
                return HealthCheckResult(
                    component="line_api",
                    status=ComponentStatus.WARNING,
                    message="LINE API access token not configured",
                    response_time=0,
                    timestamp=datetime.now(),
                    details={},
                    metrics={}
                )
            
            # Check LINE API health via bot info endpoint
            headers = {
                'Authorization': f'Bearer {access_token}',
                'Content-Type': 'application/json'
            }
            
            response = requests.get(
                'https://api.line.me/v2/bot/info',
                headers=headers,
                timeout=10
            )
            
            response_time = time.time() - start_time
            
            if response.status_code == 200:
                bot_info = response.json()
                return HealthCheckResult(
                    component="line_api",
                    status=ComponentStatus.HEALTHY,
                    message="LINE API is healthy",
                    response_time=response_time,
                    timestamp=datetime.now(),
                    details={
                        "bot_id": bot_info.get('userId'),
                        "display_name": bot_info.get('displayName')
                    },
                    metrics={"response_time": response_time}
                )
            else:
                return HealthCheckResult(
                    component="line_api",
                    status=ComponentStatus.WARNING,
                    message=f"LINE API returned status {response.status_code}",
                    response_time=response_time,
                    timestamp=datetime.now(),
                    details={"status_code": response.status_code},
                    metrics={"response_time": response_time}
                )
                
        except Exception as e:
            response_time = time.time() - start_time
            return HealthCheckResult(
                component="line_api",
                status=ComponentStatus.CRITICAL,
                message=f"LINE API health check failed: {str(e)}",
                response_time=response_time,
                timestamp=datetime.now(),
                details={"error": str(e)},
                metrics={"response_time": response_time}
            )

class SystemResourceHealthChecker:
    """Health checker for system resources"""
    
    async def check_system_resources(self) -> HealthCheckResult:
        """Check system resource usage"""
        start_time = time.time()
        
        try:
            import psutil
            
            # Get system metrics
            cpu_percent = psutil.cpu_percent(interval=1)
            memory = psutil.virtual_memory()
            disk = psutil.disk_usage('/')
            
            response_time = time.time() - start_time
            
            # Determine overall status
            critical_threshold = 90
            warning_threshold = 75
            
            status = ComponentStatus.HEALTHY
            messages = []
            
            if cpu_percent > critical_threshold:
                status = ComponentStatus.CRITICAL
                messages.append(f"CPU usage critical: {cpu_percent:.1f}%")
            elif cpu_percent > warning_threshold:
                status = ComponentStatus.WARNING
                messages.append(f"CPU usage high: {cpu_percent:.1f}%")
            
            if memory.percent > critical_threshold:
                status = ComponentStatus.CRITICAL
                messages.append(f"Memory usage critical: {memory.percent:.1f}%")
            elif memory.percent > warning_threshold:
                status = ComponentStatus.WARNING
                messages.append(f"Memory usage high: {memory.percent:.1f}%")
            
            if disk.percent > critical_threshold:
                status = ComponentStatus.CRITICAL
                messages.append(f"Disk usage critical: {disk.percent:.1f}%")
            elif disk.percent > warning_threshold:
                status = ComponentStatus.WARNING
                messages.append(f"Disk usage high: {disk.percent:.1f}%")
            
            if not messages:
                messages.append("System resources are healthy")
            
            return HealthCheckResult(
                component="system_resources",
                status=status,
                message="; ".join(messages),
                response_time=response_time,
                timestamp=datetime.now(),
                details={
                    "cpu_count": psutil.cpu_count(),
                    "memory_total": memory.total,
                    "disk_total": disk.total
                },
                metrics={
                    "response_time": response_time,
                    "cpu_percent": cpu_percent,
                    "memory_percent": memory.percent,
                    "disk_percent": disk.percent
                }
            )
            
        except Exception as e:
            response_time = time.time() - start_time
            return HealthCheckResult(
                component="system_resources",
                status=ComponentStatus.CRITICAL,
                message=f"System resource check failed: {str(e)}",
                response_time=response_time,
                timestamp=datetime.now(),
                details={"error": str(e)},
                metrics={"response_time": response_time}
            )

class ComprehensiveHealthChecker:
    """Main comprehensive health checker coordinator"""
    
    def __init__(self):
        self.db_checker = DatabaseHealthChecker()
        self.cache_checker = CacheHealthChecker()
        self.service_checker = ExternalServiceHealthChecker()
        self.resource_checker = SystemResourceHealthChecker()
        
        # Health check registry
        self.health_checks = {
            'database_connection': self.db_checker.check_database_connection,
            'database_performance': self.db_checker.check_database_performance,
            'redis_cache': self.cache_checker.check_redis_connection,
            'application_cache': self.cache_checker.check_application_cache,
            'xai_api': self.service_checker.check_xai_api,
            'line_api': self.service_checker.check_line_api,
            'system_resources': self.resource_checker.check_system_resources
        }
        
        self.health_history = []
        self.max_history = 100
        self.lock = threading.Lock()
    
    async def run_all_health_checks(self) -> SystemHealth:
        """Run all health checks concurrently"""
        results = {}
        
        # Run all checks concurrently
        tasks = []
        for name, check_func in self.health_checks.items():
            tasks.append(asyncio.create_task(check_func()))
        
        # Wait for all checks to complete
        completed_checks = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Process results
        for i, (name, check_func) in enumerate(self.health_checks.items()):
            result = completed_checks[i]
            if isinstance(result, Exception):
                # Handle exception during health check
                results[name] = HealthCheckResult(
                    component=name,
                    status=ComponentStatus.CRITICAL,
                    message=f"Health check failed with exception: {str(result)}",
                    response_time=0,
                    timestamp=datetime.now(),
                    details={"error": str(result)},
                    metrics={}
                )
            else:
                results[name] = result
        
        # Determine overall system health
        overall_status = self._determine_overall_status(results)
        
        # Create summary
        summary = self._create_summary(results)
        
        # Create system health object
        system_health = SystemHealth(
            overall_status=overall_status,
            components=results,
            timestamp=datetime.now(),
            summary=summary
        )
        
        # Store in history
        with self.lock:
            self.health_history.append(system_health)
            if len(self.health_history) > self.max_history:
                self.health_history.pop(0)
        
        return system_health
    
    def _determine_overall_status(self, results: Dict[str, HealthCheckResult]) -> ComponentStatus:
        """Determine overall system health status"""
        statuses = [result.status for result in results.values()]
        
        if ComponentStatus.CRITICAL in statuses:
            return ComponentStatus.CRITICAL
        elif ComponentStatus.WARNING in statuses:
            return ComponentStatus.WARNING
        elif ComponentStatus.UNKNOWN in statuses:
            return ComponentStatus.UNKNOWN
        else:
            return ComponentStatus.HEALTHY
    
    def _create_summary(self, results: Dict[str, HealthCheckResult]) -> Dict[str, Any]:
        """Create health check summary"""
        status_counts = {status.value: 0 for status in ComponentStatus}
        total_response_time = 0
        component_count = len(results)
        
        for result in results.values():
            status_counts[result.status.value] += 1
            total_response_time += result.response_time
        
        avg_response_time = total_response_time / component_count if component_count > 0 else 0
        
        return {
            'total_components': component_count,
            'status_distribution': status_counts,
            'average_response_time': avg_response_time,
            'healthy_percentage': (status_counts['healthy'] / component_count * 100) if component_count > 0 else 0
        }
    
    async def run_specific_check(self, component_name: str) -> HealthCheckResult:
        """Run a specific health check"""
        if component_name not in self.health_checks:
            return HealthCheckResult(
                component=component_name,
                status=ComponentStatus.UNKNOWN,
                message=f"Unknown component: {component_name}",
                response_time=0,
                timestamp=datetime.now(),
                details={},
                metrics={}
            )
        
        try:
            return await self.health_checks[component_name]()
        except Exception as e:
            return HealthCheckResult(
                component=component_name,
                status=ComponentStatus.CRITICAL,
                message=f"Health check failed: {str(e)}",
                response_time=0,
                timestamp=datetime.now(),
                details={"error": str(e)},
                metrics={}
            )
    
    def get_health_history(self, hours: int = 24) -> List[SystemHealth]:
        """Get health check history"""
        with self.lock:
            cutoff_time = datetime.now() - timedelta(hours=hours)
            return [
                health for health in self.health_history
                if health.timestamp > cutoff_time
            ]
    
    def export_health_report(self) -> Dict[str, Any]:
        """Export comprehensive health report"""
        with self.lock:
            latest_health = self.health_history[-1] if self.health_history else None
            
            report = {
                'generated_at': datetime.now().isoformat(),
                'current_health': asdict(latest_health) if latest_health else None,
                'available_checks': list(self.health_checks.keys()),
                'history_count': len(self.health_history)
            }
            
            if len(self.health_history) > 1:
                # Add trend analysis
                recent_checks = self.health_history[-10:]  # Last 10 checks
                trend_data = {}
                
                for check_name in self.health_checks.keys():
                    statuses = [
                        getattr(health.components.get(check_name), 'status', ComponentStatus.UNKNOWN)
                        for health in recent_checks
                    ]
                    
                    trend_data[check_name] = {
                        'recent_statuses': [status.value for status in statuses],
                        'stability': len(set(statuses)) == 1  # True if all same status
                    }
                
                report['trends'] = trend_data
            
            return report

# Global health checker instance
_health_checker = None

def get_health_checker() -> ComprehensiveHealthChecker:
    """Get global health checker instance"""
    global _health_checker
    if _health_checker is None:
        _health_checker = ComprehensiveHealthChecker()
    return _health_checker

async def run_system_health_check() -> SystemHealth:
    """Convenience function to run full system health check"""
    checker = get_health_checker()
    return await checker.run_all_health_checks()

async def check_component_health(component_name: str) -> HealthCheckResult:
    """Convenience function to check specific component"""
    checker = get_health_checker()
    return await checker.run_specific_check(component_name)
