"""
Background task processing system for TyphoonLineWebhook using Celery
Handles AI response generation, database operations, and scheduled tasks
"""
import os
import json
import logging
import time
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, List, Union, Callable
from celery import Celery, Task
from celery.signals import task_prerun, task_postrun, task_failure, task_success
from celery.exceptions import Retry, WorkerLostError
from kombu import Queue
import redis
from dataclasses import dataclass
from enum import Enum

class TaskPriority(Enum):
    """Task priority levels"""
    LOW = 1
    NORMAL = 5
    HIGH = 8
    CRITICAL = 10

class TaskStatus(Enum):
    """Task execution status"""
    PENDING = "pending"
    STARTED = "started"
    SUCCESS = "success"
    FAILURE = "failure"
    RETRY = "retry"
    REVOKED = "revoked"

@dataclass
class TaskResult:
    """Task execution result"""
    task_id: str
    status: TaskStatus
    result: Any
    error: Optional[str]
    execution_time: float
    retry_count: int
    timestamp: datetime

class CeleryConfig:
    """Celery configuration"""
    
    # Broker settings (Redis)
    broker_url = os.getenv('CELERY_BROKER_URL', 'redis://localhost:6379/1')
    result_backend = os.getenv('CELERY_RESULT_BACKEND', 'redis://localhost:6379/2')
    
    # Task settings
    task_serializer = 'json'
    accept_content = ['json']
    result_serializer = 'json'
    timezone = 'Asia/Bangkok'
    enable_utc = True
    
    # Task routing
    task_routes = {
        'chatbot.ai_response.*': {'queue': 'ai_processing'},
        'chatbot.database.*': {'queue': 'database_ops'},
        'chatbot.notifications.*': {'queue': 'notifications'},
        'chatbot.analytics.*': {'queue': 'analytics'},
        'chatbot.maintenance.*': {'queue': 'maintenance'}
    }
    
    # Queue definitions
    task_queues = (
        Queue('ai_processing', routing_key='ai_processing', queue_arguments={'x-max-priority': 10}),
        Queue('database_ops', routing_key='database_ops', queue_arguments={'x-max-priority': 8}),
        Queue('notifications', routing_key='notifications', queue_arguments={'x-max-priority': 9}),
        Queue('analytics', routing_key='analytics', queue_arguments={'x-max-priority': 3}),
        Queue('maintenance', routing_key='maintenance', queue_arguments={'x-max-priority': 1}),
    )
    
    # Worker settings
    worker_prefetch_multiplier = 1
    worker_max_tasks_per_child = 1000
    worker_disable_rate_limits = False
    
    # Task execution settings
    task_acks_late = True
    task_reject_on_worker_lost = True
    task_soft_time_limit = 300  # 5 minutes
    task_time_limit = 600      # 10 minutes
    
    # Retry settings
    task_retry_jitter = True
    task_retry_delay = 60
    task_max_retries = 3
    
    # Result settings
    result_expires = 3600  # 1 hour
    result_persistent = True
    
    # Beat scheduler settings (for periodic tasks)
    beat_schedule = {
        'cleanup-expired-sessions': {
            'task': 'chatbot.maintenance.cleanup_expired_sessions',
            'schedule': 300.0,  # Every 5 minutes
            'options': {'queue': 'maintenance', 'priority': TaskPriority.LOW.value}
        },
        'database-maintenance': {
            'task': 'chatbot.maintenance.database_maintenance',
            'schedule': 3600.0,  # Every hour
            'options': {'queue': 'maintenance', 'priority': TaskPriority.LOW.value}
        },
        'generate-analytics-reports': {
            'task': 'chatbot.analytics.generate_daily_reports',
            'schedule': 86400.0,  # Daily
            'options': {'queue': 'analytics', 'priority': TaskPriority.NORMAL.value}
        },
        'health-check-external-services': {
            'task': 'chatbot.maintenance.health_check_services',
            'schedule': 180.0,  # Every 3 minutes
            'options': {'queue': 'maintenance', 'priority': TaskPriority.NORMAL.value}
        }
    }

# Initialize Celery app
celery_app = Celery('typhoon_chatbot')
celery_app.config_from_object(CeleryConfig)

class BaseTaskWithLogging(Task):
    """Base task class with enhanced logging and error handling"""
    
    abstract = True
    
    def on_retry(self, exc, task_id, args, kwargs, einfo):
        """Called when task is retried"""
        logging.warning(f"Task {task_id} retry {self.request.retries + 1}: {exc}")
    
    def on_failure(self, exc, task_id, args, kwargs, einfo):
        """Called when task fails"""
        logging.error(f"Task {task_id} failed: {exc}")
        
        # Store failure information for monitoring
        try:
            redis_client = redis.Redis.from_url(CeleryConfig.result_backend)
            failure_data = {
                'task_id': task_id,
                'task_name': self.name,
                'error': str(exc),
                'args': args,
                'kwargs': kwargs,
                'timestamp': datetime.now().isoformat()
            }
            redis_client.lpush('task_failures', json.dumps(failure_data))
            redis_client.ltrim('task_failures', 0, 999)  # Keep last 1000 failures
        except Exception as e:
            logging.error(f"Failed to log task failure: {e}")
    
    def on_success(self, retval, task_id, args, kwargs):
        """Called when task succeeds"""
        logging.info(f"Task {task_id} completed successfully")

# AI Response Generation Tasks

@celery_app.task(bind=True, base=BaseTaskWithLogging, queue='ai_processing')
def generate_ai_response(self, user_id: str, user_message: str, conversation_history: List[Dict], 
                        system_config: Dict[str, Any]) -> Dict[str, Any]:
    """
    Generate AI response in background
    
    Args:
        user_id: LINE user ID
        user_message: User's message
        conversation_history: Recent conversation history
        system_config: System configuration for AI generation
        
    Returns:
        Dict containing AI response and metadata
    """
    start_time = time.time()
    
    try:
        # Import here to avoid circular imports
        from .async_api import AsyncDeepseekClient
        from .error_handling import get_error_handler, CircuitState
        import asyncio
        
        # Get circuit breaker for xAI Grok API
        error_handler = get_error_handler()
        circuit_breaker = error_handler.get_circuit_breaker('xai_api')
        
        if circuit_breaker and circuit_breaker.state == CircuitState.OPEN:
            raise Exception("xAI API circuit breaker is open")
        
        # Create async client and generate response
        async def generate_response():
            client = AsyncDeepseekClient(
                api_key=system_config.get('xai_api_key'),
                model=system_config.get('xai_model', 'grok-4')
            )
            
            await client.setup()
            
            try:
                # Prepare messages for AI
                messages = conversation_history + [
                    {"role": "user", "content": user_message}
                ]
                
                response = await client.generate_completion(
                    messages=messages,
                    config=system_config.get('generation_config', {})
                )
                
                return response.choices[0].message.content
                
            finally:
                await client.close()
        
        # Run async function
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        try:
            ai_response = loop.run_until_complete(generate_response())
        finally:
            loop.close()
        
        execution_time = time.time() - start_time
        
        # Record successful generation
        if circuit_breaker:
            circuit_breaker._on_success()
        
        return {
            'success': True,
            'response': ai_response,
            'execution_time': execution_time,
            'model_used': system_config.get('xai_model', 'grok-4'),
            'token_count': len(ai_response.split()) * 1.3  # Rough estimate
        }
        
    except Exception as e:
        execution_time = time.time() - start_time
        
        # Record failure in circuit breaker
        if circuit_breaker:
            circuit_breaker._on_failure()
        
        # Log error details
        logging.error(f"AI response generation failed for user {user_id}: {str(e)}")
        
        # Retry logic
        if self.request.retries < self.max_retries:
            # Progressive delay: 60s, 120s, 240s
            delay = 60 * (2 ** self.request.retries)
            raise self.retry(countdown=delay, exc=e)
        
        return {
            'success': False,
            'error': str(e),
            'execution_time': execution_time,
            'fallback_response': "ขออภัยค่ะ ระบบกำลังมีปัญหา กรุณาลองใหม่ในอีกสักครู่"
        }

@celery_app.task(bind=True, base=BaseTaskWithLogging, queue='ai_processing')
def process_conversation_summary(self, user_id: str, conversation_history: List[Tuple], 
                               max_tokens: int = 1000) -> Dict[str, Any]:
    """
    Generate conversation summary in background
    
    Args:
        user_id: User ID
        conversation_history: List of conversation tuples
        max_tokens: Maximum tokens for summary
        
    Returns:
        Dict containing summary and metadata
    """
    try:
        from .async_api import AsyncDeepseekClient
        import asyncio
        
        if not conversation_history:
            return {'success': True, 'summary': '', 'token_count': 0}
        
        async def generate_summary():
            client = AsyncDeepseekClient(
                api_key=os.getenv('XAI_API_KEY'),
                model=os.getenv('XAI_MODEL', 'grok-4')
            )
            
            await client.setup()
            
            try:
                summary = await client.summarize_conversation(
                    history=conversation_history,
                    system_message={"role": "system", "content": "You are a helpful assistant that summarizes conversations."},
                    max_tokens=max_tokens
                )
                
                return summary
                
            finally:
                await client.close()
        
        # Run async function
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        try:
            summary = loop.run_until_complete(generate_summary())
        finally:
            loop.close()
        
        return {
            'success': True,
            'summary': summary,
            'token_count': len(summary.split()) * 1.3,
            'conversations_processed': len(conversation_history)
        }
        
    except Exception as e:
        logging.error(f"Conversation summary failed for user {user_id}: {str(e)}")
        
        if self.request.retries < self.max_retries:
            delay = 120 * (2 ** self.request.retries)
            raise self.retry(countdown=delay, exc=e)
        
        return {
            'success': False,
            'error': str(e),
            'fallback_summary': 'สรุปการสนทนาไม่สามารถสร้างได้ในขณะนี้'
        }

# Database Operations Tasks

@celery_app.task(bind=True, base=BaseTaskWithLogging, queue='database_ops')
def save_conversation_async(self, user_id: str, user_message: str, bot_response: str, 
                          metadata: Dict[str, Any]) -> Dict[str, Any]:
    """
    Save conversation to database asynchronously
    
    Args:
        user_id: User ID
        user_message: User's message
        bot_response: Bot's response
        metadata: Additional metadata
        
    Returns:
        Dict containing save result
    """
    try:
        from .chat_history_db import ChatHistoryDB
        from .database_manager import DatabaseManager
        
        # Get database connection
        db_config = {
            'MYSQL_HOST': os.getenv('MYSQL_HOST'),
            'MYSQL_PORT': int(os.getenv('MYSQL_PORT', 3306)),
            'MYSQL_USER': os.getenv('MYSQL_USER'),
            'MYSQL_PASSWORD': os.getenv('MYSQL_PASSWORD'),
            'MYSQL_DB': os.getenv('MYSQL_DB')
        }
        
        db_manager = DatabaseManager(db_config)
        chat_db = ChatHistoryDB(db_manager)
        
        # Save conversation with metadata
        conversation_id = chat_db.save_conversation(
            user_id=user_id,
            user_message=user_message,
            bot_response=bot_response,
            token_count=metadata.get('token_count', 0),
            important=metadata.get('important', False)
        )
        
        return {
            'success': True,
            'conversation_id': conversation_id,
            'saved_at': datetime.now().isoformat()
        }
        
    except Exception as e:
        logging.error(f"Failed to save conversation for user {user_id}: {str(e)}")
        
        # Retry with exponential backoff
        if self.request.retries < self.max_retries:
            delay = 30 * (2 ** self.request.retries)
            raise self.retry(countdown=delay, exc=e)
        
        return {
            'success': False,
            'error': str(e)
        }

@celery_app.task(bind=True, base=BaseTaskWithLogging, queue='database_ops')
def update_user_metrics_async(self, user_id: str, metrics: Dict[str, Any]) -> Dict[str, Any]:
    """
    Update user metrics asynchronously
    
    Args:
        user_id: User ID
        metrics: Metrics to update
        
    Returns:
        Dict containing update result
    """
    try:
        from .database_manager import DatabaseManager
        
        db_config = {
            'MYSQL_HOST': os.getenv('MYSQL_HOST'),
            'MYSQL_PORT': int(os.getenv('MYSQL_PORT', 3306)),
            'MYSQL_USER': os.getenv('MYSQL_USER'),
            'MYSQL_PASSWORD': os.getenv('MYSQL_PASSWORD'),
            'MYSQL_DB': os.getenv('MYSQL_DB')
        }
        
        db_manager = DatabaseManager(db_config)
        
        # Update metrics
        for metric_name, metric_value in metrics.items():
            query = """
                INSERT INTO user_metrics (user_id, metric_name, metric_value, timestamp)
                VALUES (%s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                metric_value = VALUES(metric_value),
                timestamp = VALUES(timestamp)
            """
            
            db_manager.execute_and_commit(
                query, 
                (user_id, metric_name, metric_value, datetime.now())
            )
        
        return {
            'success': True,
            'metrics_updated': len(metrics),
            'updated_at': datetime.now().isoformat()
        }
        
    except Exception as e:
        logging.error(f"Failed to update metrics for user {user_id}: {str(e)}")
        
        if self.request.retries < self.max_retries:
            delay = 20 * (2 ** self.request.retries)
            raise self.retry(countdown=delay, exc=e)
        
        return {
            'success': False,
            'error': str(e)
        }

# Notification Tasks

@celery_app.task(bind=True, base=BaseTaskWithLogging, queue='notifications')
def send_follow_up_message(self, user_id: str, message: str, follow_up_type: str) -> Dict[str, Any]:
    """
    Send follow-up message to user
    
    Args:
        user_id: LINE user ID
        message: Message to send
        follow_up_type: Type of follow-up
        
    Returns:
        Dict containing send result
    """
    try:
        from linebot import LineBotApi
        from linebot.models import TextSendMessage
        
        line_bot_api = LineBotApi(os.getenv('LINE_CHANNEL_ACCESS_TOKEN'))
        
        # Send message
        line_bot_api.push_message(
            user_id,
            TextSendMessage(text=message)
        )
        
        # Log follow-up
        logging.info(f"Follow-up message sent to {user_id}: {follow_up_type}")
        
        return {
            'success': True,
            'message_sent': True,
            'follow_up_type': follow_up_type,
            'sent_at': datetime.now().isoformat()
        }
        
    except Exception as e:
        logging.error(f"Failed to send follow-up to {user_id}: {str(e)}")
        
        if self.request.retries < self.max_retries:
            delay = 60 * (2 ** self.request.retries)
            raise self.retry(countdown=delay, exc=e)
        
        return {
            'success': False,
            'error': str(e)
        }

# Analytics Tasks

@celery_app.task(bind=True, base=BaseTaskWithLogging, queue='analytics')
def generate_daily_reports(self) -> Dict[str, Any]:
    """Generate daily analytics reports"""
    try:
        from .database_manager import DatabaseManager
        
        db_config = {
            'MYSQL_HOST': os.getenv('MYSQL_HOST'),
            'MYSQL_PORT': int(os.getenv('MYSQL_PORT', 3306)),
            'MYSQL_USER': os.getenv('MYSQL_USER'),
            'MYSQL_PASSWORD': os.getenv('MYSQL_PASSWORD'),
            'MYSQL_DB': os.getenv('MYSQL_DB')
        }
        
        db_manager = DatabaseManager(db_config)
        
        # Generate various reports
        today = datetime.now().date()
        yesterday = today - timedelta(days=1)
        
        # User activity report
        activity_query = """
            SELECT COUNT(DISTINCT user_id) as active_users,
                   COUNT(*) as total_conversations,
                   AVG(token_count) as avg_tokens
            FROM conversations
            WHERE DATE(timestamp) = %s
        """
        
        activity_result = db_manager.execute_query(activity_query, (yesterday,))
        
        report_data = {
            'date': yesterday.isoformat(),
            'active_users': activity_result[0][0] if activity_result else 0,
            'total_conversations': activity_result[0][1] if activity_result else 0,
            'avg_tokens': float(activity_result[0][2]) if activity_result and activity_result[0][2] else 0,
            'generated_at': datetime.now().isoformat()
        }
        
        return {
            'success': True,
            'report': report_data
        }
        
    except Exception as e:
        logging.error(f"Failed to generate daily reports: {str(e)}")
        return {
            'success': False,
            'error': str(e)
        }

# Maintenance Tasks

@celery_app.task(bind=True, base=BaseTaskWithLogging, queue='maintenance')
def cleanup_expired_sessions(self) -> Dict[str, Any]:
    """Clean up expired Redis sessions"""
    try:
        from .caching_system import RedisMonitor
        import redis
        
        redis_client = redis.Redis(
            host=os.getenv('REDIS_HOST', 'localhost'),
            port=int(os.getenv('REDIS_PORT', 6379)),
            db=int(os.getenv('REDIS_DB', 0)),
            decode_responses=True
        )
        
        monitor = RedisMonitor(redis_client)
        monitor._perform_cleanup()
        
        return {
            'success': True,
            'cleanup_completed': True,
            'cleaned_at': datetime.now().isoformat()
        }
        
    except Exception as e:
        logging.error(f"Session cleanup failed: {str(e)}")
        return {
            'success': False,
            'error': str(e)
        }

@celery_app.task(bind=True, base=BaseTaskWithLogging, queue='maintenance')
def database_maintenance(self) -> Dict[str, Any]:
    """Perform database maintenance tasks"""
    try:
        from .database_optimization import optimize_database
        
        db_config = {
            'MYSQL_HOST': os.getenv('MYSQL_HOST'),
            'MYSQL_PORT': int(os.getenv('MYSQL_PORT', 3306)),
            'MYSQL_USER': os.getenv('MYSQL_USER'),
            'MYSQL_PASSWORD': os.getenv('MYSQL_PASSWORD'),
            'MYSQL_DB': os.getenv('MYSQL_DB')
        }
        
        result = optimize_database(db_config)
        
        return {
            'success': result,
            'maintenance_completed': True,
            'completed_at': datetime.now().isoformat()
        }
        
    except Exception as e:
        logging.error(f"Database maintenance failed: {str(e)}")
        return {
            'success': False,
            'error': str(e)
        }

# Task Monitoring and Management

class TaskMonitor:
    """Monitor and manage Celery tasks"""
    
    def __init__(self):
        self.redis_client = redis.Redis.from_url(CeleryConfig.result_backend)
    
    def get_task_statistics(self) -> Dict[str, Any]:
        """Get task execution statistics"""
        try:
            # Get active tasks
            inspect = celery_app.control.inspect()
            active_tasks = inspect.active()
            scheduled_tasks = inspect.scheduled()
            
            # Get failure information
            failures = self.redis_client.lrange('task_failures', 0, -1)
            failure_count = len(failures)
            
            # Count tasks by queue
            queue_stats = {}
            if active_tasks:
                for worker, tasks in active_tasks.items():
                    for task in tasks:
                        queue = task.get('delivery_info', {}).get('routing_key', 'unknown')
                        queue_stats[queue] = queue_stats.get(queue, 0) + 1
            
            return {
                'active_workers': len(active_tasks) if active_tasks else 0,
                'active_tasks': sum(len(tasks) for tasks in active_tasks.values()) if active_tasks else 0,
                'scheduled_tasks': sum(len(tasks) for tasks in scheduled_tasks.values()) if scheduled_tasks else 0,
                'recent_failures': failure_count,
                'queue_distribution': queue_stats,
                'timestamp': datetime.now().isoformat()
            }
            
        except Exception as e:
            logging.error(f"Failed to get task statistics: {str(e)}")
            return {'error': str(e)}
    
    def get_recent_failures(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Get recent task failures"""
        try:
            failures = self.redis_client.lrange('task_failures', 0, limit - 1)
            return [json.loads(failure.decode('utf-8')) for failure in failures]
        except Exception as e:
            logging.error(f"Failed to get recent failures: {str(e)}")
            return []

# Task submission helpers

def submit_ai_response_task(user_id: str, user_message: str, conversation_history: List[Dict], 
                          system_config: Dict[str, Any], priority: TaskPriority = TaskPriority.HIGH) -> str:
    """Submit AI response generation task"""
    task = generate_ai_response.apply_async(
        args=[user_id, user_message, conversation_history, system_config],
        priority=priority.value
    )
    return task.id

def submit_database_save_task(user_id: str, user_message: str, bot_response: str, 
                            metadata: Dict[str, Any], priority: TaskPriority = TaskPriority.NORMAL) -> str:
    """Submit database save task"""
    task = save_conversation_async.apply_async(
        args=[user_id, user_message, bot_response, metadata],
        priority=priority.value
    )
    return task.id

def get_task_result(task_id: str, timeout: Optional[float] = None) -> TaskResult:
    """Get task result with timeout"""
    try:
        result = celery_app.AsyncResult(task_id)
        
        if timeout:
            result_data = result.get(timeout=timeout)
        else:
            result_data = result.result
        
        return TaskResult(
            task_id=task_id,
            status=TaskStatus(result.status.lower()),
            result=result_data,
            error=None,
            execution_time=0.0,  # Would need to be tracked separately
            retry_count=0,  # Would need to be tracked separately
            timestamp=datetime.now()
        )
        
    except Exception as e:
        return TaskResult(
            task_id=task_id,
            status=TaskStatus.FAILURE,
            result=None,
            error=str(e),
            execution_time=0.0,
            retry_count=0,
            timestamp=datetime.now()
        )

# Export main components
__all__ = [
    'celery_app',
    'TaskPriority',
    'TaskStatus',
    'TaskResult',
    'TaskMonitor',
    'submit_ai_response_task',
    'submit_database_save_task',
    'get_task_result'
]
