"""
Microservices architecture with event-driven messaging for TyphoonLineWebhook
"""
import os
import json
import logging
import asyncio
import uuid
from datetime import datetime
from typing import Dict, Any, List, Optional, Callable
from dataclasses import dataclass, asdict
from enum import Enum
import threading
from abc import ABC, abstractmethod
import aioredis
import pika
from aiohttp import web
import signal

class EventType(Enum):
    USER_MESSAGE_RECEIVED = "user_message_received"
    RISK_ASSESSMENT_COMPLETED = "risk_assessment_completed"
    CRISIS_DETECTED = "crisis_detected"
    AI_RESPONSE_GENERATED = "ai_response_generated"
    NOTIFICATION_SENT = "notification_sent"
    USER_REGISTERED = "user_registered"
    VOICE_TRANSCRIBED = "voice_transcribed"
    ANALYTICS_UPDATED = "analytics_updated"

@dataclass
class Event:
    event_id: str
    event_type: EventType
    payload: Dict[str, Any]
    timestamp: datetime
    source_service: str
    correlation_id: Optional[str] = None
    user_id: Optional[str] = None

class EventBus:
    """Redis-based event bus for microservices communication"""
    
    def __init__(self):
        self.redis = None
        self.subscribers = {}
        self.lock = threading.Lock()
        
    async def initialize(self):
        """Initialize Redis connection"""
        try:
            self.redis = await aioredis.from_url(
                f"redis://{os.getenv('REDIS_HOST', 'localhost')}:{os.getenv('REDIS_PORT', 6379)}"
            )
            logging.info("Event bus initialized with Redis")
        except Exception as e:
            logging.error(f"Failed to initialize event bus: {str(e)}")
            raise
    
    async def publish(self, event: Event):
        """Publish event to the bus"""
        try:
            event_data = {
                'event_id': event.event_id,
                'event_type': event.event_type.value,
                'payload': event.payload,
                'timestamp': event.timestamp.isoformat(),
                'source_service': event.source_service,
                'correlation_id': event.correlation_id,
                'user_id': event.user_id
            }
            
            # Publish to specific channel
            channel = f"events:{event.event_type.value}"
            await self.redis.publish(channel, json.dumps(event_data))
            
            # Also publish to general events channel
            await self.redis.publish("events:all", json.dumps(event_data))
            
            logging.debug(f"Published event {event.event_id}: {event.event_type.value}")
            
        except Exception as e:
            logging.error(f"Failed to publish event: {str(e)}")
    
    async def subscribe(self, event_type: EventType, handler: Callable):
        """Subscribe to specific event type"""
        channel = f"events:{event_type.value}"
        
        with self.lock:
            if channel not in self.subscribers:
                self.subscribers[channel] = []
            self.subscribers[channel].append(handler)
        
        # Start listening if this is the first subscriber
        if len(self.subscribers[channel]) == 1:
            asyncio.create_task(self._listen_to_channel(channel))
    
    async def _listen_to_channel(self, channel: str):
        """Listen to specific channel"""
        try:
            pubsub = self.redis.pubsub()
            await pubsub.subscribe(channel)
            
            async for message in pubsub.listen():
                if message['type'] == 'message':
                    try:
                        event_data = json.loads(message['data'])
                        event = Event(
                            event_id=event_data['event_id'],
                            event_type=EventType(event_data['event_type']),
                            payload=event_data['payload'],
                            timestamp=datetime.fromisoformat(event_data['timestamp']),
                            source_service=event_data['source_service'],
                            correlation_id=event_data.get('correlation_id'),
                            user_id=event_data.get('user_id')
                        )
                        
                        # Call all handlers for this channel
                        with self.lock:
                            handlers = self.subscribers.get(channel, [])
                        
                        for handler in handlers:
                            try:
                                await handler(event)
                            except Exception as e:
                                logging.error(f"Event handler error: {str(e)}")
                                
                    except Exception as e:
                        logging.error(f"Failed to process event message: {str(e)}")
                        
        except Exception as e:
            logging.error(f"Channel listener error: {str(e)}")

class BaseService(ABC):
    """Base class for microservices"""
    
    def __init__(self, service_name: str, port: int):
        self.service_name = service_name
        self.port = port
        self.app = web.Application()
        self.event_bus = EventBus()
        self.running = False
        
        # Setup routes
        self._setup_base_routes()
        self.setup_routes()
        
        # Setup event handlers
        self.setup_event_handlers()
    
    def _setup_base_routes(self):
        """Setup base routes for all services"""
        self.app.router.add_get('/health', self.health_check)
        self.app.router.add_get('/info', self.service_info)
    
    @abstractmethod
    def setup_routes(self):
        """Setup service-specific routes"""
        pass
    
    @abstractmethod
    def setup_event_handlers(self):
        """Setup service-specific event handlers"""
        pass
    
    async def health_check(self, request):
        """Health check endpoint"""
        return web.json_response({
            'status': 'healthy',
            'service': self.service_name,
            'timestamp': datetime.now().isoformat()
        })
    
    async def service_info(self, request):
        """Service information endpoint"""
        return web.json_response({
            'name': self.service_name,
            'version': '1.0.0',
            'port': self.port,
            'uptime': datetime.now().isoformat()
        })
    
    async def publish_event(self, event_type: EventType, payload: Dict[str, Any], 
                          user_id: str = None, correlation_id: str = None):
        """Publish an event"""
        event = Event(
            event_id=str(uuid.uuid4()),
            event_type=event_type,
            payload=payload,
            timestamp=datetime.now(),
            source_service=self.service_name,
            correlation_id=correlation_id,
            user_id=user_id
        )
        
        await self.event_bus.publish(event)
    
    async def start(self):
        """Start the service"""
        # Initialize event bus
        await self.event_bus.initialize()
        
        # Start web server
        runner = web.AppRunner(self.app)
        await runner.setup()
        
        site = web.TCPSite(runner, '0.0.0.0', self.port)
        await site.start()
        
        self.running = True
        logging.info(f"Service {self.service_name} started on port {self.port}")
        
        return runner

class ChatService(BaseService):
    """Chat processing microservice"""
    
    def __init__(self):
        super().__init__("chat-service", 8001)
    
    def setup_routes(self):
        """Setup chat service routes"""
        self.app.router.add_post('/message', self.handle_message)
        self.app.router.add_get('/history', self.get_history)
    
    def setup_event_handlers(self):
        """Setup chat service event handlers"""
        asyncio.create_task(
            self.event_bus.subscribe(EventType.AI_RESPONSE_GENERATED, self.handle_ai_response)
        )
    
    async def handle_message(self, request):
        """Handle incoming chat message"""
        try:
            data = await request.json()
            user_id = data.get('user_id')
            message = data.get('message')
            
            # Publish user message event
            await self.publish_event(
                EventType.USER_MESSAGE_RECEIVED,
                {'user_id': user_id, 'message': message},
                user_id=user_id
            )
            
            return web.json_response({'status': 'received', 'message_id': str(uuid.uuid4())})
            
        except Exception as e:
            return web.json_response({'error': str(e)}, status=400)
    
    async def get_history(self, request):
        """Get chat history"""
        user_id = request.query.get('user_id')
        return web.json_response({'history': [], 'user_id': user_id})
    
    async def handle_ai_response(self, event: Event):
        """Handle AI response generated event"""
        logging.info(f"Chat service received AI response: {event.payload}")

class RiskAssessmentService(BaseService):
    """Risk assessment microservice"""
    
    def __init__(self):
        super().__init__("risk-service", 8002)
    
    def setup_routes(self):
        """Setup risk assessment routes"""
        self.app.router.add_post('/assess', self.assess_risk)
        self.app.router.add_get('/history', self.get_risk_history)
    
    def setup_event_handlers(self):
        """Setup risk service event handlers"""
        asyncio.create_task(
            self.event_bus.subscribe(EventType.USER_MESSAGE_RECEIVED, self.handle_message_for_risk)
        )
    
    async def assess_risk(self, request):
        """Assess risk level"""
        try:
            data = await request.json()
            message = data.get('message')
            user_id = data.get('user_id')
            
            # Simple risk assessment (would use ML model in production)
            risk_level = 'low'
            crisis_keywords = ['suicide', 'kill myself', 'ฆ่าตัวตาย', 'อยากตาย']
            
            if any(keyword in message.lower() for keyword in crisis_keywords):
                risk_level = 'critical'
                
                await self.publish_event(
                    EventType.CRISIS_DETECTED,
                    {'user_id': user_id, 'risk_level': risk_level, 'message': message},
                    user_id=user_id
                )
            
            # Publish risk assessment completed
            await self.publish_event(
                EventType.RISK_ASSESSMENT_COMPLETED,
                {'user_id': user_id, 'risk_level': risk_level, 'confidence': 0.8},
                user_id=user_id
            )
            
            return web.json_response({
                'risk_level': risk_level,
                'confidence': 0.8,
                'recommendations': []
            })
            
        except Exception as e:
            return web.json_response({'error': str(e)}, status=400)
    
    async def get_risk_history(self, request):
        """Get risk assessment history"""
        user_id = request.query.get('user_id')
        return web.json_response({'history': [], 'user_id': user_id})
    
    async def handle_message_for_risk(self, event: Event):
        """Handle user message for risk assessment"""
        if event.payload.get('user_id'):
            # Trigger risk assessment
            await self.assess_risk_from_event(event.payload)
    
    async def assess_risk_from_event(self, payload: Dict[str, Any]):
        """Assess risk from event payload"""
        logging.info(f"Risk assessment triggered for message: {payload.get('message', '')}")

class NotificationService(BaseService):
    """Notification microservice"""
    
    def __init__(self):
        super().__init__("notification-service", 8004)
    
    def setup_routes(self):
        """Setup notification routes"""
        self.app.router.add_post('/send', self.send_notification)
    
    def setup_event_handlers(self):
        """Setup notification event handlers"""
        asyncio.create_task(
            self.event_bus.subscribe(EventType.CRISIS_DETECTED, self.handle_crisis)
        )
    
    async def send_notification(self, request):
        """Send notification"""
        try:
            data = await request.json()
            
            # Publish notification sent event
            await self.publish_event(
                EventType.NOTIFICATION_SENT,
                data,
                user_id=data.get('user_id')
            )
            
            return web.json_response({'status': 'sent'})
            
        except Exception as e:
            return web.json_response({'error': str(e)}, status=400)
    
    async def handle_crisis(self, event: Event):
        """Handle crisis detected event"""
        user_id = event.user_id
        logging.critical(f"Crisis detected for user {user_id} - sending emergency notifications")
        
        # Send emergency notification
        notification_payload = {
            'user_id': user_id,
            'type': 'crisis_alert',
            'message': 'Emergency support needed',
            'priority': 'critical'
        }
        
        await self.publish_event(
            EventType.NOTIFICATION_SENT,
            notification_payload,
            user_id=user_id
        )

class AnalyticsService(BaseService):
    """Analytics microservice"""
    
    def __init__(self):
        super().__init__("analytics-service", 8003)
    
    def setup_routes(self):
        """Setup analytics routes"""
        self.app.router.add_get('/dashboard', self.get_dashboard_data)
        self.app.router.add_get('/reports', self.get_reports)
    
    def setup_event_handlers(self):
        """Setup analytics event handlers"""
        events_to_track = [
            EventType.USER_MESSAGE_RECEIVED,
            EventType.RISK_ASSESSMENT_COMPLETED,
            EventType.CRISIS_DETECTED,
            EventType.NOTIFICATION_SENT
        ]
        
        for event_type in events_to_track:
            asyncio.create_task(
                self.event_bus.subscribe(event_type, self.track_event)
            )
    
    async def get_dashboard_data(self, request):
        """Get dashboard analytics data"""
        return web.json_response({
            'active_users': 150,
            'messages_today': 1200,
            'risk_assessments': 340,
            'crisis_interventions': 5
        })
    
    async def get_reports(self, request):
        """Get analytics reports"""
        return web.json_response({'reports': []})
    
    async def track_event(self, event: Event):
        """Track event for analytics"""
        logging.info(f"Analytics tracking: {event.event_type.value}")
        
        # Update analytics data
        await self.publish_event(
            EventType.ANALYTICS_UPDATED,
            {'event_tracked': event.event_type.value, 'timestamp': event.timestamp.isoformat()},
            correlation_id=event.correlation_id
        )

class ServiceOrchestrator:
    """Orchestrate all microservices"""
    
    def __init__(self):
        self.services = [
            ChatService(),
            RiskAssessmentService(),
            AnalyticsService(),
            NotificationService()
        ]
        self.runners = []
    
    async def start_all_services(self):
        """Start all microservices"""
        for service in self.services:
            runner = await service.start()
            self.runners.append(runner)
        
        logging.info("All microservices started successfully")
    
    async def stop_all_services(self):
        """Stop all microservices"""
        for runner in self.runners:
            await runner.cleanup()
        
        logging.info("All microservices stopped")
    
    def setup_signal_handlers(self):
        """Setup signal handlers for graceful shutdown"""
        def signal_handler(signum, frame):
            logging.info(f"Received signal {signum}, shutting down services...")
            asyncio.create_task(self.stop_all_services())
        
        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)

# Global orchestrator
_orchestrator = None

def get_service_orchestrator() -> ServiceOrchestrator:
    """Get global service orchestrator"""
    global _orchestrator
    if _orchestrator is None:
        _orchestrator = ServiceOrchestrator()
    return _orchestrator

async def start_microservices():
    """Start all microservices"""
    orchestrator = get_service_orchestrator()
    orchestrator.setup_signal_handlers()
    await orchestrator.start_all_services()
    return orchestrator

def init_microservices():
    """Initialize microservices architecture"""
    orchestrator = get_service_orchestrator()
    logging.info("Microservices architecture initialized")
    return orchestrator