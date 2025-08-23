"""
Multi-level caching system for TyphoonLineWebhook chatbot
Implements application-level caching, Redis monitoring, and intelligent data management
"""
import json
import time
import logging
import threading
import psutil
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, List, Union, Callable
from collections import defaultdict, OrderedDict
from functools import wraps
import redis
import pickle

class ApplicationCache:
    """
    Application-level cache for frequently accessed data
    """
    
    def __init__(self, max_size: int = 10000, ttl: int = 3600):
        """
        Initialize application cache
        
        Args:
            max_size: Maximum number of items to cache
            ttl: Time to live in seconds (default 1 hour)
        """
        self.max_size = max_size
        self.ttl = ttl
        self.cache = OrderedDict()
        self.timestamps = {}
        self.access_count = defaultdict(int)
        self.lock = threading.RLock()
        
        # Performance metrics
        self.hits = 0
        self.misses = 0
        self.evictions = 0
        
    def get(self, key: str) -> Optional[Any]:
        """Get item from cache"""
        with self.lock:
            current_time = time.time()
            
            # Check if key exists and is not expired
            if key in self.cache:
                if current_time - self.timestamps[key] < self.ttl:
                    # Move to end (LRU)
                    self.cache.move_to_end(key)
                    self.access_count[key] += 1
                    self.hits += 1
                    return self.cache[key]
                else:
                    # Expired, remove
                    del self.cache[key]
                    del self.timestamps[key]
                    del self.access_count[key]
            
            self.misses += 1
            return None
    
    def set(self, key: str, value: Any, ttl: Optional[int] = None) -> None:
        """Set item in cache"""
        with self.lock:
            current_time = time.time()
            item_ttl = ttl or self.ttl
            
            # Remove expired items if we're at capacity
            if len(self.cache) >= self.max_size:
                self._evict_expired()
                
                # If still at capacity, evict LRU items
                while len(self.cache) >= self.max_size:
                    self._evict_lru()
            
            self.cache[key] = value
            self.timestamps[key] = current_time
            self.access_count[key] = 1
            
            # Move to end
            self.cache.move_to_end(key)
    
    def delete(self, key: str) -> bool:
        """Delete item from cache"""
        with self.lock:
            if key in self.cache:
                del self.cache[key]
                del self.timestamps[key]
                del self.access_count[key]
                return True
            return False
    
    def _evict_expired(self) -> None:
        """Remove expired items"""
        current_time = time.time()
        expired_keys = [
            key for key, timestamp in self.timestamps.items()
            if current_time - timestamp >= self.ttl
        ]
        
        for key in expired_keys:
            del self.cache[key]
            del self.timestamps[key]
            del self.access_count[key]
            self.evictions += 1
    
    def _evict_lru(self) -> None:
        """Remove least recently used item"""
        if self.cache:
            key = next(iter(self.cache))
            del self.cache[key]
            del self.timestamps[key]
            del self.access_count[key]
            self.evictions += 1
    
    def clear(self) -> None:
        """Clear all cache entries"""
        with self.lock:
            self.cache.clear()
            self.timestamps.clear()
            self.access_count.clear()
    
    def get_stats(self) -> Dict[str, Any]:
        """Get cache statistics"""
        with self.lock:
            total_requests = self.hits + self.misses
            hit_rate = (self.hits / total_requests) if total_requests > 0 else 0
            
            return {
                'size': len(self.cache),
                'max_size': self.max_size,
                'hits': self.hits,
                'misses': self.misses,
                'hit_rate': hit_rate,
                'evictions': self.evictions,
                'ttl': self.ttl
            }

class RedisMonitor:
    """
    Redis session monitoring and cleanup system
    """
    
    def __init__(self, redis_client: redis.Redis):
        """
        Initialize Redis monitor
        
        Args:
            redis_client: Redis client instance
        """
        self.redis_client = redis_client
        self.monitoring_active = True
        self.cleanup_interval = 600  # 10 minutes
        self.memory_threshold = 0.8  # 80% memory usage threshold
        self.lock = threading.Lock()
        
        # Statistics
        self.sessions_cleaned = 0
        self.memory_cleanups = 0
        self.last_cleanup = time.time()
        
        # Start monitoring thread
        self.monitor_thread = threading.Thread(target=self._monitoring_loop, daemon=True)
        self.monitor_thread.start()
        
    def _monitoring_loop(self) -> None:
        """Main monitoring loop"""
        while self.monitoring_active:
            try:
                current_time = time.time()
                
                # Perform cleanup if interval passed
                if current_time - self.last_cleanup > self.cleanup_interval:
                    self._perform_cleanup()
                    self.last_cleanup = current_time
                
                # Check memory usage
                if self._is_memory_pressure():
                    self._emergency_cleanup()
                
                time.sleep(60)  # Check every minute
                
            except Exception as e:
                logging.error(f"Redis monitoring error: {str(e)}")
                time.sleep(30)
    
    def _perform_cleanup(self) -> None:
        """Perform regular cleanup of expired sessions"""
        try:
            # Get all session keys
            session_keys = self.redis_client.keys("chat_session:*")
            token_keys = self.redis_client.keys("session_tokens:*")
            activity_keys = self.redis_client.keys("last_activity:*")
            
            current_time = time.time()
            cleaned_count = 0
            
            # Check session activity and clean old ones
            for key in activity_keys:
                try:
                    last_activity = self.redis_client.get(key)
                    if last_activity:
                        if isinstance(last_activity, bytes):
                            last_activity = last_activity.decode('utf-8')
                        
                        last_activity_time = float(last_activity)
                        # Clean sessions older than 7 days
                        if current_time - last_activity_time > 604800:  # 7 days
                            user_id = key.decode('utf-8').split(':')[1]
                            self._clean_user_session(user_id)
                            cleaned_count += 1
                            
                except Exception as e:
                    logging.warning(f"Error cleaning session {key}: {str(e)}")
                    continue
            
            self.sessions_cleaned += cleaned_count
            if cleaned_count > 0:
                logging.info(f"Redis cleanup: removed {cleaned_count} expired sessions")
                
        except Exception as e:
            logging.error(f"Redis cleanup error: {str(e)}")
    
    def _emergency_cleanup(self) -> None:
        """Perform emergency cleanup when memory is high"""
        logging.warning("Redis emergency cleanup triggered due to memory pressure")
        
        try:
            # Get memory info
            memory_info = self.redis_client.info('memory')
            used_memory = memory_info.get('used_memory', 0)
            
            # Clean oldest sessions first
            activity_keys = self.redis_client.keys("last_activity:*")
            activity_data = []
            
            for key in activity_keys:
                try:
                    last_activity = self.redis_client.get(key)
                    if last_activity:
                        if isinstance(last_activity, bytes):
                            last_activity = last_activity.decode('utf-8')
                        
                        last_activity_time = float(last_activity)
                        user_id = key.decode('utf-8').split(':')[1]
                        activity_data.append((user_id, last_activity_time))
                        
                except Exception:
                    continue
            
            # Sort by activity time (oldest first)
            activity_data.sort(key=lambda x: x[1])
            
            # Remove oldest 25% of sessions
            cleanup_count = max(1, len(activity_data) // 4)
            for user_id, _ in activity_data[:cleanup_count]:
                self._clean_user_session(user_id)
            
            self.memory_cleanups += 1
            logging.info(f"Emergency cleanup removed {cleanup_count} sessions")
            
        except Exception as e:
            logging.error(f"Emergency cleanup error: {str(e)}")
    
    def _clean_user_session(self, user_id: str) -> None:
        """Clean all session data for a user"""
        try:
            keys_to_delete = [
                f"chat_session:{user_id}",
                f"session_tokens:{user_id}",
                f"last_activity:{user_id}",
                f"timeout_warning:{user_id}",
                f"progress:{user_id}"
            ]
            
            for key in keys_to_delete:
                try:
                    self.redis_client.delete(key)
                except Exception:
                    pass
                    
        except Exception as e:
            logging.warning(f"Error cleaning user session {user_id}: {str(e)}")
    
    def _is_memory_pressure(self) -> bool:
        """Check if Redis is under memory pressure"""
        try:
            memory_info = self.redis_client.info('memory')
            used_memory = memory_info.get('used_memory', 0)
            max_memory = memory_info.get('maxmemory', 0)
            
            if max_memory > 0:
                usage_ratio = used_memory / max_memory
                return usage_ratio > self.memory_threshold
            else:
                # If no max memory set, check system memory
                system_memory = psutil.virtual_memory()
                return system_memory.percent / 100.0 > self.memory_threshold
                
        except Exception:
            return False
    
    def get_redis_stats(self) -> Dict[str, Any]:
        """Get Redis statistics and health metrics"""
        try:
            info = self.redis_client.info()
            memory_info = self.redis_client.info('memory')
            stats_info = self.redis_client.info('stats')
            
            # Count session-related keys
            session_count = len(self.redis_client.keys("chat_session:*"))
            token_count = len(self.redis_client.keys("session_tokens:*"))
            activity_count = len(self.redis_client.keys("last_activity:*"))
            
            return {
                'connected_clients': info.get('connected_clients', 0),
                'used_memory_human': memory_info.get('used_memory_human', '0B'),
                'used_memory': memory_info.get('used_memory', 0),
                'max_memory': memory_info.get('maxmemory', 0),
                'keyspace_hits': stats_info.get('keyspace_hits', 0),
                'keyspace_misses': stats_info.get('keyspace_misses', 0),
                'total_commands_processed': stats_info.get('total_commands_processed', 0),
                'session_keys': {
                    'chat_sessions': session_count,
                    'session_tokens': token_count,
                    'last_activities': activity_count
                },
                'cleanup_stats': {
                    'sessions_cleaned': self.sessions_cleaned,
                    'memory_cleanups': self.memory_cleanups,
                    'last_cleanup': datetime.fromtimestamp(self.last_cleanup).isoformat()
                }
            }
            
        except Exception as e:
            return {'error': str(e)}
    
    def stop_monitoring(self) -> None:
        """Stop the monitoring thread"""
        self.monitoring_active = False
        logging.info("Redis monitoring stopped")

class CacheManager:
    """
    Unified cache management system
    """
    
    def __init__(self, redis_client: redis.Redis):
        """
        Initialize cache manager
        
        Args:
            redis_client: Redis client instance
        """
        self.redis_client = redis_client
        self.app_cache = ApplicationCache()
        self.redis_monitor = RedisMonitor(redis_client)
        
        # Cache warming strategies
        self.warming_strategies = {}
        
    def register_warming_strategy(self, cache_key: str, strategy: Callable) -> None:
        """
        Register a cache warming strategy
        
        Args:
            cache_key: Cache key pattern to warm
            strategy: Function to generate cache data
        """
        self.warming_strategies[cache_key] = strategy
        logging.info(f"Registered cache warming strategy for {cache_key}")
    
    def warm_cache(self, key_pattern: Optional[str] = None) -> Dict[str, Any]:
        """
        Warm cache using registered strategies
        
        Args:
            key_pattern: Specific pattern to warm, or None for all
            
        Returns:
            Dictionary with warming results
        """
        results = {}
        strategies_to_run = (
            {key_pattern: self.warming_strategies[key_pattern]} 
            if key_pattern and key_pattern in self.warming_strategies 
            else self.warming_strategies
        )
        
        for pattern, strategy in strategies_to_run.items():
            try:
                start_time = time.time()
                data = strategy()
                
                if isinstance(data, dict):
                    for key, value in data.items():
                        self.app_cache.set(key, value)
                
                execution_time = time.time() - start_time
                results[pattern] = {
                    'success': True,
                    'execution_time': execution_time,
                    'items_warmed': len(data) if isinstance(data, dict) else 1
                }
                
            except Exception as e:
                results[pattern] = {
                    'success': False,
                    'error': str(e)
                }
                logging.error(f"Cache warming failed for {pattern}: {str(e)}")
        
        return results
    
    def get_comprehensive_stats(self) -> Dict[str, Any]:
        """Get comprehensive caching statistics"""
        return {
            'application_cache': self.app_cache.get_stats(),
            'redis_stats': self.redis_monitor.get_redis_stats(),
            'warming_strategies': list(self.warming_strategies.keys())
        }
    
    def optimize_all_caches(self) -> Dict[str, Any]:
        """Optimize all cache layers"""
        results = {}
        
        # Optimize application cache
        try:
            self.app_cache._evict_expired()
            results['application_cache'] = 'optimized'
        except Exception as e:
            results['application_cache'] = f'error: {str(e)}'
        
        # Force Redis cleanup
        try:
            self.redis_monitor._perform_cleanup()
            results['redis_cache'] = 'optimized'
        except Exception as e:
            results['redis_cache'] = f'error: {str(e)}'
        
        return results

# Decorator for automatic caching
def cached(cache_manager: CacheManager, ttl: int = 3600, key_prefix: str = ""):
    """
    Decorator for automatic function result caching
    
    Args:
        cache_manager: CacheManager instance
        ttl: Time to live in seconds
        key_prefix: Prefix for cache keys
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs):
            # Generate cache key
            key_parts = [key_prefix or func.__name__]
            key_parts.extend(str(arg) for arg in args)
            key_parts.extend(f"{k}={v}" for k, v in sorted(kwargs.items()))
            cache_key = ":".join(key_parts)
            
            # Try to get from cache
            cached_result = cache_manager.app_cache.get(cache_key)
            if cached_result is not None:
                return cached_result
            
            # Execute function and cache result
            result = func(*args, **kwargs)
            cache_manager.app_cache.set(cache_key, result, ttl)
            
            return result
        return wrapper
    return decorator

# Global cache manager instance
_cache_manager = None

def get_cache_manager(redis_client: redis.Redis) -> CacheManager:
    """Get or create global cache manager instance"""
    global _cache_manager
    if _cache_manager is None:
        _cache_manager = CacheManager(redis_client)
    return _cache_manager

def setup_default_warming_strategies(cache_manager: CacheManager, db_manager) -> None:
    """
    Setup default cache warming strategies
    
    Args:
        cache_manager: CacheManager instance
        db_manager: DatabaseManager instance
    """
    def warm_user_metrics():
        """Warm frequently accessed user metrics"""
        try:
            # Get recent active users
            query = """
                SELECT DISTINCT user_id 
                FROM conversations 
                WHERE timestamp > DATE_SUB(NOW(), INTERVAL 24 HOUR)
                LIMIT 100
            """
            result = db_manager.execute_query(query)
            
            warming_data = {}
            for row in result:
                user_id = row[0]
                # Pre-calculate some metrics
                warming_data[f"user_active:{user_id}"] = True
                
            return warming_data
            
        except Exception as e:
            logging.error(f"Error warming user metrics: {str(e)}")
            return {}
    
    def warm_system_config():
        """Warm system configuration data"""
        return {
            'system_config:version': '1.0',
            'system_config:features': ['chat', 'risk_assessment', 'follow_up'],
            'system_config:limits': {'max_message_length': 2000, 'max_tokens': 4000}
        }
    
    # Register strategies
    cache_manager.register_warming_strategy('user_metrics', warm_user_metrics)
    cache_manager.register_warming_strategy('system_config', warm_system_config)
    
    logging.info("Default cache warming strategies registered")