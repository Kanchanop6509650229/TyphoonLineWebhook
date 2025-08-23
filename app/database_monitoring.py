"""
Database performance monitoring module for TyphoonLineWebhook chatbot
Tracks query performance, connection health, and provides optimization insights
"""
import logging
import time
import threading
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional, Callable
from collections import defaultdict, deque
from functools import wraps
from .database_manager import DatabaseManager

class QueryPerformanceMonitor:
    """
    Monitor database query performance and provide optimization recommendations
    """
    
    def __init__(self, db_manager: DatabaseManager, max_history_size: int = 1000):
        """
        Initialize query performance monitor
        
        Args:
            db_manager: DatabaseManager instance
            max_history_size: Maximum number of query records to keep in memory
        """
        self.db = db_manager
        self.max_history_size = max_history_size
        self.query_history = deque(maxlen=max_history_size)
        self.slow_query_threshold = 1.0  # seconds
        self.query_stats = defaultdict(list)
        self.connection_metrics = {}
        self.monitoring_active = True
        self.lock = threading.Lock()
        
        # Start background monitoring thread
        self.monitor_thread = threading.Thread(target=self._background_monitor, daemon=True)
        self.monitor_thread.start()
    
    def monitor_query(self, func: Callable) -> Callable:
        """
        Decorator to monitor database query performance
        
        Args:
            func: Database function to monitor
            
        Returns:
            Wrapped function with performance monitoring
        """
        @wraps(func)
        def wrapper(*args, **kwargs):
            if not self.monitoring_active:
                return func(*args, **kwargs)
            
            start_time = time.time()
            query_info = {
                'function': func.__name__,
                'start_time': start_time,
                'timestamp': datetime.now(),
                'args_count': len(args),
                'kwargs_count': len(kwargs)
            }
            
            try:
                result = func(*args, **kwargs)
                execution_time = time.time() - start_time
                
                query_info.update({
                    'execution_time': execution_time,
                    'status': 'success',
                    'result_count': len(result) if isinstance(result, (list, tuple)) else 1
                })
                
                self._record_query_performance(query_info)
                
                # Log slow queries
                if execution_time > self.slow_query_threshold:
                    logging.warning(
                        f"Slow query detected: {func.__name__} took {execution_time:.2f}s"
                    )
                
                return result
                
            except Exception as e:
                execution_time = time.time() - start_time
                query_info.update({
                    'execution_time': execution_time,
                    'status': 'error',
                    'error': str(e)
                })
                
                self._record_query_performance(query_info)
                logging.error(f"Query failed: {func.__name__} - {str(e)}")
                raise
        
        return wrapper
    
    def _record_query_performance(self, query_info: Dict[str, Any]) -> None:
        """
        Record query performance information
        
        Args:
            query_info: Dictionary containing query performance data
        """
        with self.lock:
            self.query_history.append(query_info)
            
            # Update function-specific statistics
            func_name = query_info['function']
            self.query_stats[func_name].append({
                'execution_time': query_info['execution_time'],
                'timestamp': query_info['timestamp'],
                'status': query_info['status']
            })
            
            # Keep only recent stats per function (last 100 calls)
            if len(self.query_stats[func_name]) > 100:
                self.query_stats[func_name] = self.query_stats[func_name][-100:]
    
    def get_performance_summary(self) -> Dict[str, Any]:
        """
        Get comprehensive performance summary
        
        Returns:
            Dictionary containing performance metrics and recommendations
        """
        with self.lock:
            if not self.query_history:
                return {'status': 'no_data'}
            
            # Calculate overall statistics
            total_queries = len(self.query_history)
            recent_queries = [q for q in self.query_history 
                            if q['timestamp'] > datetime.now() - timedelta(hours=1)]
            
            execution_times = [q['execution_time'] for q in self.query_history]
            slow_queries = [q for q in self.query_history 
                          if q['execution_time'] > self.slow_query_threshold]
            
            # Function-specific statistics
            function_stats = {}
            for func_name, stats in self.query_stats.items():
                if stats:
                    times = [s['execution_time'] for s in stats]
                    error_count = sum(1 for s in stats if s['status'] == 'error')
                    
                    function_stats[func_name] = {
                        'call_count': len(stats),
                        'avg_time': sum(times) / len(times),
                        'max_time': max(times),
                        'min_time': min(times),
                        'error_rate': error_count / len(stats),
                        'slow_query_count': sum(1 for t in times if t > self.slow_query_threshold)
                    }
            
            # Generate recommendations
            recommendations = self._generate_performance_recommendations(function_stats)
            
            return {
                'status': 'active',
                'summary': {
                    'total_queries': total_queries,
                    'recent_queries_1h': len(recent_queries),
                    'avg_execution_time': sum(execution_times) / len(execution_times),
                    'slow_query_count': len(slow_queries),
                    'slow_query_rate': len(slow_queries) / total_queries,
                    'monitoring_threshold': self.slow_query_threshold
                },
                'function_stats': function_stats,
                'recommendations': recommendations,
                'connection_metrics': self.connection_metrics
            }
    
    def _generate_performance_recommendations(self, function_stats: Dict[str, Any]) -> List[str]:
        """
        Generate performance optimization recommendations
        
        Args:
            function_stats: Function performance statistics
            
        Returns:
            List of optimization recommendations
        """
        recommendations = []
        
        for func_name, stats in function_stats.items():
            # High average execution time
            if stats['avg_time'] > 0.5:
                recommendations.append(
                    f"Function '{func_name}' has high average execution time ({stats['avg_time']:.2f}s) - consider query optimization"
                )
            
            # High error rate
            if stats['error_rate'] > 0.1:
                recommendations.append(
                    f"Function '{func_name}' has high error rate ({stats['error_rate']:.1%}) - investigate connection issues"
                )
            
            # Many slow queries
            if stats['slow_query_count'] > 10:
                recommendations.append(
                    f"Function '{func_name}' has {stats['slow_query_count']} slow queries - add database indexes or optimize queries"
                )
        
        # Global recommendations
        if not recommendations:
            recommendations.append("Database performance is within acceptable ranges")
        
        return recommendations
    
    def _background_monitor(self) -> None:
        """
        Background thread to collect connection metrics
        """
        while self.monitoring_active:
            try:
                # Collect connection metrics every 60 seconds
                metrics = self.db.get_connection_metrics()
                
                with self.lock:
                    self.connection_metrics = {
                        'timestamp': datetime.now(),
                        'pool_status': metrics.get('connection_pool', {}),
                        'database_status': metrics.get('database_status', {}),
                        'performance_metrics': metrics.get('performance_metrics', {})
                    }
                
                time.sleep(60)  # Wait 60 seconds before next collection
                
            except Exception as e:
                logging.error(f"Background monitoring error: {str(e)}")
                time.sleep(30)  # Shorter wait on error
    
    def get_slow_queries(self, limit: int = 10) -> List[Dict[str, Any]]:
        """
        Get the slowest queries from recent history
        
        Args:
            limit: Maximum number of slow queries to return
            
        Returns:
            List of slow query records
        """
        with self.lock:
            slow_queries = [q for q in self.query_history 
                          if q['execution_time'] > self.slow_query_threshold]
            
            # Sort by execution time (slowest first)
            slow_queries.sort(key=lambda x: x['execution_time'], reverse=True)
            
            return slow_queries[:limit]
    
    def set_slow_query_threshold(self, threshold: float) -> None:
        """
        Set the threshold for slow query detection
        
        Args:
            threshold: Threshold in seconds
        """
        self.slow_query_threshold = threshold
        logging.info(f"Slow query threshold set to {threshold} seconds")
    
    def export_performance_data(self, hours: int = 24) -> Dict[str, Any]:
        """
        Export performance data for external analysis
        
        Args:
            hours: Number of hours of data to export
            
        Returns:
            Dictionary containing exported performance data
        """
        with self.lock:
            cutoff_time = datetime.now() - timedelta(hours=hours)
            recent_data = [q for q in self.query_history if q['timestamp'] > cutoff_time]
            
            return {
                'export_timestamp': datetime.now().isoformat(),
                'data_period_hours': hours,
                'query_count': len(recent_data),
                'queries': recent_data,
                'summary': self.get_performance_summary()
            }
    
    def stop_monitoring(self) -> None:
        """Stop the performance monitoring"""
        self.monitoring_active = False
        logging.info("Database performance monitoring stopped")

class DatabaseHealthChecker:
    """
    Comprehensive database health checking system
    """
    
    def __init__(self, db_manager: DatabaseManager):
        """
        Initialize database health checker
        
        Args:
            db_manager: DatabaseManager instance
        """
        self.db = db_manager
        self.health_checks = {
            'connection': self._check_connection,
            'response_time': self._check_response_time,
            'disk_space': self._check_disk_space,
            'table_integrity': self._check_table_integrity,
            'connection_pool': self._check_connection_pool
        }
    
    def run_health_check(self, check_name: Optional[str] = None) -> Dict[str, Any]:
        """
        Run health checks
        
        Args:
            check_name: Specific check to run, or None for all checks
            
        Returns:
            Dictionary containing health check results
        """
        results = {
            'timestamp': datetime.now().isoformat(),
            'overall_status': 'healthy',
            'checks': {}
        }
        
        checks_to_run = {check_name: self.health_checks[check_name]} if check_name else self.health_checks
        
        for name, check_func in checks_to_run.items():
            try:
                check_result = check_func()
                results['checks'][name] = check_result
                
                if not check_result.get('healthy', False):
                    results['overall_status'] = 'unhealthy'
                    
            except Exception as e:
                results['checks'][name] = {
                    'healthy': False,
                    'error': str(e),
                    'message': f"Health check '{name}' failed with error"
                }
                results['overall_status'] = 'unhealthy'
        
        return results
    
    def _check_connection(self) -> Dict[str, Any]:
        """Check database connection health"""
        try:
            is_connected = self.db.check_connection()
            return {
                'healthy': is_connected,
                'message': 'Database connection is healthy' if is_connected else 'Database connection failed'
            }
        except Exception as e:
            return {
                'healthy': False,
                'error': str(e),
                'message': 'Connection check failed'
            }
    
    def _check_response_time(self) -> Dict[str, Any]:
        """Check database response time"""
        try:
            start_time = time.time()
            self.db.execute_query("SELECT 1")
            response_time = time.time() - start_time
            
            healthy = response_time < 1.0  # 1 second threshold
            
            return {
                'healthy': healthy,
                'response_time': response_time,
                'message': f'Response time: {response_time:.3f}s {"(healthy)" if healthy else "(slow)"}'
            }
        except Exception as e:
            return {
                'healthy': False,
                'error': str(e),
                'message': 'Response time check failed'
            }
    
    def _check_disk_space(self) -> Dict[str, Any]:
        """Check database disk space usage"""
        try:
            # Get database size information
            size_query = """
                SELECT 
                    ROUND(SUM(data_length + index_length) / 1024 / 1024, 2) as size_mb
                FROM information_schema.tables 
                WHERE table_schema = DATABASE()
            """
            result = self.db.execute_query(size_query)
            
            if result and result[0][0]:
                size_mb = float(result[0][0])
                # Basic threshold check (can be made configurable)
                healthy = size_mb < 10000  # 10GB threshold
                
                return {
                    'healthy': healthy,
                    'size_mb': size_mb,
                    'message': f'Database size: {size_mb} MB {"(healthy)" if healthy else "(large)"}'
                }
            else:
                return {
                    'healthy': True,
                    'size_mb': 0,
                    'message': 'Database size: Unknown (possibly empty)'
                }
                
        except Exception as e:
            return {
                'healthy': False,
                'error': str(e),
                'message': 'Disk space check failed'
            }
    
    def _check_table_integrity(self) -> Dict[str, Any]:
        """Check table integrity"""
        try:
            tables = ['conversations', 'follow_ups', 'user_metrics', 'registration_codes']
            table_status = {}
            all_healthy = True
            
            for table in tables:
                if self.db.table_exists(table):
                    # Get row count
                    count_result = self.db.execute_query(f"SELECT COUNT(*) FROM {table}")
                    row_count = count_result[0][0] if count_result else 0
                    
                    table_status[table] = {
                        'exists': True,
                        'row_count': row_count,
                        'healthy': True
                    }
                else:
                    table_status[table] = {
                        'exists': False,
                        'healthy': False
                    }
                    all_healthy = False
            
            return {
                'healthy': all_healthy,
                'tables': table_status,
                'message': 'All tables healthy' if all_healthy else 'Some tables missing or unhealthy'
            }
            
        except Exception as e:
            return {
                'healthy': False,
                'error': str(e),
                'message': 'Table integrity check failed'
            }
    
    def _check_connection_pool(self) -> Dict[str, Any]:
        """Check connection pool health"""
        try:
            pool_status = self.db.get_pool_status()
            healthy = pool_status.get('status') == 'active'
            
            return {
                'healthy': healthy,
                'pool_status': pool_status,
                'message': f'Connection pool status: {pool_status.get("status", "unknown")}'
            }
            
        except Exception as e:
            return {
                'healthy': False,
                'error': str(e),
                'message': 'Connection pool check failed'
            }

def create_performance_monitor(db_manager: DatabaseManager) -> QueryPerformanceMonitor:
    """
    Create and configure a performance monitor instance
    
    Args:
        db_manager: DatabaseManager instance
        
    Returns:
        Configured QueryPerformanceMonitor instance
    """
    monitor = QueryPerformanceMonitor(db_manager)
    logging.info("Database performance monitoring initialized")
    return monitor

def create_health_checker(db_manager: DatabaseManager) -> DatabaseHealthChecker:
    """
    Create and configure a health checker instance
    
    Args:
        db_manager: DatabaseManager instance
        
    Returns:
        Configured DatabaseHealthChecker instance
    """
    health_checker = DatabaseHealthChecker(db_manager)
    logging.info("Database health checker initialized")
    return health_checker