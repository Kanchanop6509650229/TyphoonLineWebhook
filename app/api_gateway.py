"""
API Gateway for TyphoonLineWebhook microservices architecture
Provides service routing, authentication, rate limiting, and request/response handling
"""
import os
import json
import logging
import asyncio
import time
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional, Callable
from dataclasses import dataclass, asdict
from enum import Enum
import threading
from collections import defaultdict, deque
import hashlib
import jwt
import aiohttp
from aiohttp import web, ClientSession
from aiohttp.web_middlewares import middleware
import redis
from functools import wraps

class ServiceType(Enum):
    CHAT_SERVICE = "chat"
    RISK_ASSESSMENT = "risk"
    ANALYTICS = "analytics"
    NOTIFICATIONS = "notifications"
    VOICE_PROCESSING = "voice"
    USER_MANAGEMENT = "users"

@dataclass
class ServiceEndpoint:
    service_type: ServiceType
    name: str
    url: str
    health_check_path: str
    timeout: int = 30
    retry_count: int = 3
    circuit_breaker_enabled: bool = True

@dataclass
class RouteConfig:
    path: str
    service_type: ServiceType
    target_path: str
    methods: List[str]
    auth_required: bool = True
    rate_limit: Optional[int] = None
    timeout: int = 30

class CircuitBreakerState(Enum):
    CLOSED = "closed"
    OPEN = "open" 
    HALF_OPEN = "half_open"

@dataclass
class CircuitBreaker:
    service_name: str
    failure_threshold: int = 5
    timeout: int = 60
    state: CircuitBreakerState = CircuitBreakerState.CLOSED
    failure_count: int = 0
    last_failure_time: Optional[datetime] = None
    success_count: int = 0

class ServiceRegistry:
    """Service discovery and health monitoring"""
    
    def __init__(self):
        self.services = {}
        self.circuit_breakers = {}
        self.health_status = {}
        self.lock = threading.Lock()
        
        # Initialize default services
        self._register_default_services()
        
        # Start health check background task
        self.health_check_active = True
        self.health_check_task = None
    
    def _register_default_services(self):
        """Register default microservice endpoints"""
        default_services = [
            ServiceEndpoint(
                service_type=ServiceType.CHAT_SERVICE,
                name="chat-service",
                url="http://localhost:8001",
                health_check_path="/health"
            ),
            ServiceEndpoint(
                service_type=ServiceType.RISK_ASSESSMENT,
                name="risk-service", 
                url="http://localhost:8002",
                health_check_path="/health"
            ),
            ServiceEndpoint(
                service_type=ServiceType.ANALYTICS,
                name="analytics-service",
                url="http://localhost:8003", 
                health_check_path="/health"
            ),
            ServiceEndpoint(
                service_type=ServiceType.NOTIFICATIONS,
                name="notification-service",
                url="http://localhost:8004",
                health_check_path="/health"
            ),
            ServiceEndpoint(
                service_type=ServiceType.VOICE_PROCESSING,
                name="voice-service",
                url="http://localhost:8005",
                health_check_path="/health"
            )
        ]
        
        for service in default_services:
            self.register_service(service)
    
    def register_service(self, service: ServiceEndpoint):
        """Register a microservice"""
        with self.lock:
            self.services[service.name] = service
            
            # Initialize circuit breaker
            self.circuit_breakers[service.name] = CircuitBreaker(
                service_name=service.name
            )
            
            # Initialize health status
            self.health_status[service.name] = {
                'healthy': False,
                'last_check': None,
                'response_time': 0,
                'error_count': 0
            }
        
        logging.info(f"Registered service: {service.name} at {service.url}")
    
    def get_service(self, service_type: ServiceType) -> Optional[ServiceEndpoint]:
        """Get service endpoint by type"""
        with self.lock:
            for service in self.services.values():
                if service.service_type == service_type:
                    return service
            return None
    
    def get_healthy_service(self, service_type: ServiceType) -> Optional[ServiceEndpoint]:
        """Get healthy service endpoint"""
        service = self.get_service(service_type)
        if not service:
            return None
        
        # Check circuit breaker
        circuit_breaker = self.circuit_breakers.get(service.name)
        if circuit_breaker and circuit_breaker.state == CircuitBreakerState.OPEN:
            # Check if we should try half-open
            if (circuit_breaker.last_failure_time and 
                datetime.now() - circuit_breaker.last_failure_time > timedelta(seconds=circuit_breaker.timeout)):
                circuit_breaker.state = CircuitBreakerState.HALF_OPEN
                logging.info(f"Circuit breaker for {service.name} moved to HALF_OPEN")
            else:
                return None
        
        # Check health status
        health = self.health_status.get(service.name, {})
        if health.get('healthy', False):
            return service
        
        return None
    
    def record_success(self, service_name: str):
        """Record successful service call"""
        with self.lock:
            circuit_breaker = self.circuit_breakers.get(service_name)
            if circuit_breaker:
                circuit_breaker.failure_count = 0
                circuit_breaker.success_count += 1
                
                if circuit_breaker.state == CircuitBreakerState.HALF_OPEN:
                    circuit_breaker.state = CircuitBreakerState.CLOSED
                    logging.info(f"Circuit breaker for {service_name} reset to CLOSED")
    
    def record_failure(self, service_name: str):
        """Record failed service call"""
        with self.lock:
            circuit_breaker = self.circuit_breakers.get(service_name)
            if circuit_breaker:
                circuit_breaker.failure_count += 1
                circuit_breaker.last_failure_time = datetime.now()
                
                if circuit_breaker.failure_count >= circuit_breaker.failure_threshold:
                    circuit_breaker.state = CircuitBreakerState.OPEN
                    logging.warning(f"Circuit breaker for {service_name} OPENED")
    
    async def start_health_checks(self):
        """Start background health checking"""
        self.health_check_task = asyncio.create_task(self._health_check_loop())
    
    async def _health_check_loop(self):
        """Background health checking loop"""
        while self.health_check_active:
            try:
                await self._check_all_services()
                await asyncio.sleep(30)  # Check every 30 seconds
            except Exception as e:
                logging.error(f"Health check error: {str(e)}")
                await asyncio.sleep(60)
    
    async def _check_all_services(self):
        """Check health of all registered services"""
        async with ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as session:
            tasks = []
            for service_name, service in self.services.items():
                task = asyncio.create_task(self._check_service_health(session, service))
                tasks.append(task)
            
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
    
    async def _check_service_health(self, session: ClientSession, service: ServiceEndpoint):
        """Check individual service health"""
        try:
            start_time = time.time()
            health_url = f"{service.url}{service.health_check_path}"
            
            async with session.get(health_url) as response:
                response_time = time.time() - start_time
                
                if response.status == 200:
                    with self.lock:
                        self.health_status[service.name] = {
                            'healthy': True,
                            'last_check': datetime.now(),
                            'response_time': response_time,
                            'error_count': 0
                        }
                else:
                    self._mark_service_unhealthy(service.name, f"HTTP {response.status}")
                    
        except Exception as e:
            self._mark_service_unhealthy(service.name, str(e))
    
    def _mark_service_unhealthy(self, service_name: str, error: str):
        """Mark service as unhealthy"""
        with self.lock:
            health = self.health_status.get(service_name, {})
            health.update({
                'healthy': False,
                'last_check': datetime.now(),
                'error_count': health.get('error_count', 0) + 1,
                'last_error': error
            })
            self.health_status[service_name] = health
        
        logging.warning(f"Service {service_name} marked unhealthy: {error}")

class AuthenticationManager:
    """JWT-based authentication and authorization"""
    
    def __init__(self):
        self.secret_key = os.getenv('JWT_SECRET_KEY', 'dev-secret-key')
        self.algorithm = 'HS256'
        self.token_expiry = 3600  # 1 hour
        
        # Redis for token blacklist
        self.redis_client = None
        self._init_redis()
    
    def _init_redis(self):
        """Initialize Redis connection"""
        try:
            self.redis_client = redis.Redis(
                host=os.getenv('REDIS_HOST', 'localhost'),
                port=int(os.getenv('REDIS_PORT', 6379)),
                db=int(os.getenv('REDIS_DB', 0)),
                decode_responses=True
            )
            self.redis_client.ping()
        except Exception as e:
            logging.warning(f"Redis not available for token blacklist: {str(e)}")
            self.redis_client = None
    
    def generate_token(self, user_id: str, roles: List[str] = None) -> str:
        """Generate JWT token"""
        payload = {
            'user_id': user_id,
            'roles': roles or ['user'],
            'iat': datetime.utcnow(),
            'exp': datetime.utcnow() + timedelta(seconds=self.token_expiry),
            'iss': 'typhoon-gateway'
        }
        
        return jwt.encode(payload, self.secret_key, algorithm=self.algorithm)
    
    def verify_token(self, token: str) -> Optional[Dict[str, Any]]:
        """Verify JWT token"""
        try:
            # Check if token is blacklisted
            if self.redis_client and self.redis_client.get(f"blacklist:{token}"):
                return None
            
            payload = jwt.decode(token, self.secret_key, algorithms=[self.algorithm])
            return payload
            
        except jwt.ExpiredSignatureError:
            logging.warning("Token expired")
            return None
        except jwt.InvalidTokenError:
            logging.warning("Invalid token")
            return None
    
    def blacklist_token(self, token: str):
        """Add token to blacklist"""
        if self.redis_client:
            try:
                # Store with TTL equal to remaining token lifetime
                payload = jwt.decode(token, self.secret_key, algorithms=[self.algorithm], options={"verify_exp": False})
                exp = payload.get('exp')
                if exp:
                    ttl = max(0, exp - int(datetime.utcnow().timestamp()))
                    self.redis_client.setex(f"blacklist:{token}", ttl, "1")
            except Exception as e:
                logging.error(f"Failed to blacklist token: {str(e)}")

class RateLimiter:
    """Rate limiting implementation"""
    
    def __init__(self):
        self.redis_client = None
        self._init_redis()
        
        # Fallback in-memory rate limiting
        self.memory_limits = defaultdict(lambda: deque())
        self.lock = threading.Lock()
    
    def _init_redis(self):
        """Initialize Redis for distributed rate limiting"""
        try:
            self.redis_client = redis.Redis(
                host=os.getenv('REDIS_HOST', 'localhost'),
                port=int(os.getenv('REDIS_PORT', 6379)),
                db=int(os.getenv('REDIS_DB', 0)),
                decode_responses=True
            )
            self.redis_client.ping()
        except Exception as e:
            logging.warning(f"Redis not available for rate limiting: {str(e)}")
            self.redis_client = None
    
    async def check_rate_limit(self, key: str, limit: int, window: int = 60) -> bool:
        """Check if request is within rate limit"""
        if self.redis_client:
            return await self._redis_rate_limit(key, limit, window)
        else:
            return self._memory_rate_limit(key, limit, window)
    
    async def _redis_rate_limit(self, key: str, limit: int, window: int) -> bool:
        """Redis-based rate limiting"""
        try:
            current_time = int(time.time())
            pipeline = self.redis_client.pipeline()
            
            # Remove expired entries
            pipeline.zremrangebyscore(key, 0, current_time - window)
            
            # Count current requests
            pipeline.zcard(key)
            
            # Add current request
            pipeline.zadd(key, {str(current_time): current_time})
            
            # Set expiry
            pipeline.expire(key, window)
            
            results = pipeline.execute()
            current_count = results[1]
            
            return current_count < limit
            
        except Exception as e:
            logging.error(f"Redis rate limiting error: {str(e)}")
            return True  # Allow request on error
    
    def _memory_rate_limit(self, key: str, limit: int, window: int) -> bool:
        """In-memory rate limiting fallback"""
        with self.lock:
            current_time = time.time()
            requests = self.memory_limits[key]
            
            # Remove expired requests
            while requests and requests[0] < current_time - window:
                requests.popleft()
            
            # Check limit
            if len(requests) >= limit:
                return False
            
            # Add current request
            requests.append(current_time)
            return True

class APIGateway:
    """Main API Gateway implementation"""
    
    def __init__(self, port: int = 8000):
        self.port = port
        self.app = web.Application(middlewares=[
            self.auth_middleware,
            self.rate_limit_middleware,
            self.logging_middleware
        ])
        
        # Initialize components
        self.service_registry = ServiceRegistry()
        self.auth_manager = AuthenticationManager()
        self.rate_limiter = RateLimiter()
        
        # Route configuration
        self.routes = self._configure_routes()
        self._setup_routes()
        
        # Metrics
        self.request_count = defaultdict(int)
        self.response_times = defaultdict(list)
    
    def _configure_routes(self) -> List[RouteConfig]:
        """Configure API routes"""
        return [
            # Authentication routes
            RouteConfig("/auth/login", ServiceType.USER_MANAGEMENT, "/login", ["POST"], False),
            RouteConfig("/auth/logout", ServiceType.USER_MANAGEMENT, "/logout", ["POST"], True),
            
            # Chat service routes
            RouteConfig("/api/chat/message", ServiceType.CHAT_SERVICE, "/message", ["POST"], True, 100),
            RouteConfig("/api/chat/history", ServiceType.CHAT_SERVICE, "/history", ["GET"], True, 50),
            
            # Risk assessment routes
            RouteConfig("/api/risk/assess", ServiceType.RISK_ASSESSMENT, "/assess", ["POST"], True, 30),
            RouteConfig("/api/risk/history", ServiceType.RISK_ASSESSMENT, "/history", ["GET"], True, 20),
            
            # Voice processing routes
            RouteConfig("/api/voice/transcribe", ServiceType.VOICE_PROCESSING, "/transcribe", ["POST"], True, 10),
            RouteConfig("/api/voice/synthesize", ServiceType.VOICE_PROCESSING, "/synthesize", ["POST"], True, 20),
            
            # Analytics routes
            RouteConfig("/api/analytics/dashboard", ServiceType.ANALYTICS, "/dashboard", ["GET"], True, 50),
            RouteConfig("/api/analytics/reports", ServiceType.ANALYTICS, "/reports", ["GET"], True, 10),
            
            # Notification routes
            RouteConfig("/api/notifications/send", ServiceType.NOTIFICATIONS, "/send", ["POST"], True, 100),
        ]
    
    def _setup_routes(self):
        """Setup HTTP routes"""
        # Health check endpoint
        self.app.router.add_get('/health', self.health_check)
        
        # Gateway info endpoint
        self.app.router.add_get('/gateway/info', self.gateway_info)
        
        # Dynamic routes from configuration
        for route in self.routes:
            for method in route.methods:
                handler = self._create_route_handler(route)
                self.app.router.add_route(method, route.path, handler)
    
    def _create_route_handler(self, route: RouteConfig):
        """Create route handler for service proxy"""
        async def handler(request):
            return await self._proxy_request(request, route)
        return handler
    
    @middleware
    async def auth_middleware(self, request, handler):
        """Authentication middleware"""
        # Skip auth for certain paths
        if request.path in ['/health', '/gateway/info'] or not getattr(request, 'route_config', {}).get('auth_required', True):
            return await handler(request)
        
        # Extract token
        auth_header = request.headers.get('Authorization')
        if not auth_header or not auth_header.startswith('Bearer '):
            return web.Response(status=401, text='Missing or invalid authorization header')
        
        token = auth_header[7:]  # Remove 'Bearer '
        
        # Verify token
        payload = self.auth_manager.verify_token(token)
        if not payload:
            return web.Response(status=401, text='Invalid or expired token')
        
        # Add user info to request
        request['user'] = payload
        
        return await handler(request)
    
    @middleware
    async def rate_limit_middleware(self, request, handler):
        """Rate limiting middleware"""
        route_config = getattr(request, 'route_config', None)
        if not route_config or not route_config.rate_limit:
            return await handler(request)
        
        # Create rate limit key
        user_id = request.get('user', {}).get('user_id', 'anonymous')
        key = f"rate_limit:{user_id}:{request.path}"
        
        # Check rate limit
        allowed = await self.rate_limiter.check_rate_limit(
            key, route_config.rate_limit, 60
        )
        
        if not allowed:
            return web.Response(status=429, text='Rate limit exceeded')
        
        return await handler(request)
    
    @middleware
    async def logging_middleware(self, request, handler):
        """Request logging middleware"""
        start_time = time.time()
        
        try:
            response = await handler(request)
            
            # Log successful request
            duration = time.time() - start_time
            logging.info(f"{request.method} {request.path} -> {response.status} ({duration:.3f}s)")
            
            # Update metrics
            self.request_count[request.path] += 1
            self.response_times[request.path].append(duration)
            
            return response
            
        except Exception as e:
            # Log error
            duration = time.time() - start_time
            logging.error(f"{request.method} {request.path} -> ERROR ({duration:.3f}s): {str(e)}")
            raise
    
    async def _proxy_request(self, request, route: RouteConfig):
        """Proxy request to target service"""
        # Get healthy service
        service = self.service_registry.get_healthy_service(route.service_type)
        if not service:
            return web.Response(status=503, text='Service unavailable')
        
        # Build target URL
        target_url = f"{service.url}{route.target_path}"
        
        try:
            # Prepare request data
            headers = dict(request.headers)
            headers.pop('Host', None)  # Remove host header
            
            # Add user context header
            if 'user' in request:
                headers['X-User-ID'] = request['user'].get('user_id', '')
                headers['X-User-Roles'] = ','.join(request['user'].get('roles', []))
            
            # Forward request
            async with ClientSession(timeout=aiohttp.ClientTimeout(total=route.timeout)) as session:
                if request.method in ['POST', 'PUT', 'PATCH']:
                    body = await request.read()
                    async with session.request(
                        request.method, target_url, 
                        headers=headers, data=body
                    ) as response:
                        content = await response.read()
                        
                        # Record success
                        self.service_registry.record_success(service.name)
                        
                        return web.Response(
                            body=content,
                            status=response.status,
                            headers=response.headers
                        )
                else:
                    async with session.request(
                        request.method, target_url, 
                        headers=headers, params=request.query
                    ) as response:
                        content = await response.read()
                        
                        # Record success
                        self.service_registry.record_success(service.name)
                        
                        return web.Response(
                            body=content,
                            status=response.status,
                            headers=response.headers
                        )
                        
        except Exception as e:
            # Record failure
            self.service_registry.record_failure(service.name)
            logging.error(f"Proxy request failed: {str(e)}")
            return web.Response(status=502, text='Bad gateway')
    
    async def health_check(self, request):
        """Gateway health check endpoint"""
        return web.json_response({
            'status': 'healthy',
            'timestamp': datetime.now().isoformat(),
            'services': {
                name: health for name, health in self.service_registry.health_status.items()
            }
        })
    
    async def gateway_info(self, request):
        """Gateway information endpoint"""
        return web.json_response({
            'version': '1.0.0',
            'services': len(self.service_registry.services),
            'routes': len(self.routes),
            'uptime': time.time()
        })
    
    async def start(self):
        """Start the API Gateway"""
        # Start service health checks
        await self.service_registry.start_health_checks()
        
        # Start web server
        runner = web.AppRunner(self.app)
        await runner.setup()
        
        site = web.TCPSite(runner, '0.0.0.0', self.port)
        await site.start()
        
        logging.info(f"API Gateway started on port {self.port}")
        
        return runner

# Global gateway instance
_gateway = None

def get_api_gateway(port: int = 8000) -> APIGateway:
    """Get global API Gateway instance"""
    global _gateway
    if _gateway is None:
        _gateway = APIGateway(port)
    return _gateway

async def start_api_gateway(port: int = 8000):
    """Start API Gateway"""
    gateway = get_api_gateway(port)
    return await gateway.start()

def init_api_gateway():
    """Initialize API Gateway"""
    gateway = get_api_gateway()
    logging.info("API Gateway initialized")
    return gateway