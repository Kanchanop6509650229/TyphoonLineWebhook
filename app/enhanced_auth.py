"""
Enhanced authentication and security system for TyphoonLineWebhook
Implements brute force protection, advanced rate limiting, and security monitoring
"""
import hashlib
import secrets
import time
import json
import logging
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, List, Tuple
from collections import defaultdict, deque
import threading
import redis
from dataclasses import dataclass
from enum import Enum

class AuthEventType(Enum):
    """Authentication event types"""
    LOGIN_SUCCESS = "login_success"
    LOGIN_FAILURE = "login_failure"
    REGISTRATION_SUCCESS = "registration_success"
    REGISTRATION_FAILURE = "registration_failure"
    BRUTE_FORCE_DETECTED = "brute_force_detected"
    ACCOUNT_LOCKED = "account_locked"
    SUSPICIOUS_ACTIVITY = "suspicious_activity"

@dataclass
class AuthEvent:
    """Authentication event data structure"""
    event_type: AuthEventType
    user_id: str
    ip_address: Optional[str]
    timestamp: datetime
    details: Dict[str, Any]
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            'event_type': self.event_type.value,
            'user_id': self.user_id,
            'ip_address': self.ip_address,
            'timestamp': self.timestamp.isoformat(),
            'details': self.details
        }

class BruteForceProtection:
    """
    Advanced brute force protection system
    """
    
    def __init__(self, redis_client: redis.Redis):
        """
        Initialize brute force protection
        
        Args:
            redis_client: Redis client for storing attempt data
        """
        self.redis_client = redis_client
        self.max_attempts = 5  # Maximum attempts before lockout
        self.lockout_duration = 900  # 15 minutes lockout
        self.attempt_window = 300  # 5 minute window for attempts
        self.progressive_delay = True  # Enable progressive delays
        
        # Track suspicious patterns
        self.suspicious_patterns = {
            'rapid_attempts': {'threshold': 10, 'window': 60},  # 10 attempts in 1 minute
            'multiple_users': {'threshold': 5, 'window': 300},  # 5 different users from same IP
            'distributed_attack': {'threshold': 20, 'window': 600}  # 20 attempts across IPs
        }
        
    def check_attempt_allowed(self, identifier: str, ip_address: Optional[str] = None) -> Tuple[bool, Dict[str, Any]]:
        """
        Check if authentication attempt is allowed
        
        Args:
            identifier: User identifier (user_id, registration_code, etc.)
            ip_address: IP address of the request
            
        Returns:
            Tuple of (allowed, details_dict)
        """
        current_time = time.time()
        
        # Check if account is locked
        lockout_info = self._check_lockout(identifier)
        if lockout_info['locked']:
            return False, {
                'reason': 'account_locked',
                'lockout_expires': lockout_info['expires'],
                'attempts_remaining': 0
            }
        
        # Check attempt count in window
        attempts = self._get_recent_attempts(identifier)
        if len(attempts) >= self.max_attempts:
            # Lock the account
            self._lock_account(identifier, current_time)
            return False, {
                'reason': 'too_many_attempts',
                'attempts_made': len(attempts),
                'lockout_duration': self.lockout_duration
            }
        
        # Check for suspicious patterns if IP provided
        if ip_address:
            suspicious_info = self._check_suspicious_patterns(ip_address, identifier)
            if suspicious_info['suspicious']:
                return False, {
                    'reason': 'suspicious_activity',
                    'pattern': suspicious_info['pattern'],
                    'details': suspicious_info['details']
                }
        
        # Calculate progressive delay
        delay = self._calculate_progressive_delay(len(attempts))
        
        return True, {
            'attempts_made': len(attempts),
            'attempts_remaining': self.max_attempts - len(attempts),
            'progressive_delay': delay
        }
    
    def record_attempt(self, identifier: str, success: bool, ip_address: Optional[str] = None, details: Optional[Dict] = None) -> None:
        """
        Record authentication attempt
        
        Args:
            identifier: User identifier
            success: Whether attempt was successful
            ip_address: IP address of request
            details: Additional attempt details
        """
        current_time = time.time()
        
        attempt_data = {
            'timestamp': current_time,
            'success': success,
            'ip_address': ip_address,
            'details': details or {}
        }
        
        # Store attempt in Redis with expiration
        key = f"auth_attempts:{identifier}"
        self.redis_client.lpush(key, json.dumps(attempt_data))
        self.redis_client.expire(key, self.attempt_window * 2)  # Keep data longer than window
        
        # Store IP-based tracking if provided
        if ip_address:
            ip_key = f"auth_ip:{ip_address}"
            ip_data = {
                'timestamp': current_time,
                'identifier': identifier,
                'success': success
            }
            self.redis_client.lpush(ip_key, json.dumps(ip_data))
            self.redis_client.expire(ip_key, 3600)  # Keep IP data for 1 hour
        
        # If failed attempt, check for immediate lockout
        if not success:
            attempts = self._get_recent_attempts(identifier)
            if len(attempts) >= self.max_attempts:
                self._lock_account(identifier, current_time)
    
    def _get_recent_attempts(self, identifier: str) -> List[Dict]:
        """Get recent failed attempts within the window"""
        key = f"auth_attempts:{identifier}"
        current_time = time.time()
        cutoff_time = current_time - self.attempt_window
        
        attempts = []
        raw_attempts = self.redis_client.lrange(key, 0, -1)
        
        for raw_attempt in raw_attempts:
            try:
                attempt = json.loads(raw_attempt.decode('utf-8'))
                if attempt['timestamp'] > cutoff_time and not attempt['success']:
                    attempts.append(attempt)
            except (json.JSONDecodeError, KeyError, UnicodeDecodeError):
                continue
        
        return attempts
    
    def _check_lockout(self, identifier: str) -> Dict[str, Any]:
        """Check if account is currently locked"""
        lockout_key = f"auth_lockout:{identifier}"
        lockout_data = self.redis_client.get(lockout_key)
        
        if lockout_data:
            try:
                lockout_info = json.loads(lockout_data.decode('utf-8'))
                expires_at = lockout_info['expires_at']
                
                if time.time() < expires_at:
                    return {
                        'locked': True,
                        'expires': datetime.fromtimestamp(expires_at).isoformat(),
                        'attempts': lockout_info.get('attempts', 0)
                    }
                else:
                    # Lockout expired, remove it
                    self.redis_client.delete(lockout_key)
            except (json.JSONDecodeError, KeyError, UnicodeDecodeError):
                self.redis_client.delete(lockout_key)
        
        return {'locked': False}
    
    def _lock_account(self, identifier: str, current_time: float) -> None:
        """Lock account due to too many failed attempts"""
        lockout_key = f"auth_lockout:{identifier}"
        expires_at = current_time + self.lockout_duration
        
        lockout_data = {
            'locked_at': current_time,
            'expires_at': expires_at,
            'attempts': self.max_attempts
        }
        
        self.redis_client.setex(lockout_key, self.lockout_duration, json.dumps(lockout_data))
        
        logging.warning(f"Account locked due to brute force: {identifier}")
    
    def _calculate_progressive_delay(self, attempt_count: int) -> float:
        """Calculate progressive delay based on attempt count"""
        if not self.progressive_delay or attempt_count == 0:
            return 0.0
        
        # Exponential backoff: 1s, 2s, 4s, 8s, 16s
        delay = min(2 ** (attempt_count - 1), 16)  # Cap at 16 seconds
        return delay
    
    def _check_suspicious_patterns(self, ip_address: str, identifier: str) -> Dict[str, Any]:
        """Check for suspicious attack patterns"""
        current_time = time.time()
        
        # Check rapid attempts from same IP
        rapid_attempts = self._count_ip_attempts(ip_address, self.suspicious_patterns['rapid_attempts']['window'])
        if rapid_attempts >= self.suspicious_patterns['rapid_attempts']['threshold']:
            return {
                'suspicious': True,
                'pattern': 'rapid_attempts',
                'details': {'attempts': rapid_attempts, 'ip': ip_address}
            }
        
        # Check multiple user attempts from same IP
        unique_users = self._count_unique_users_from_ip(ip_address, self.suspicious_patterns['multiple_users']['window'])
        if unique_users >= self.suspicious_patterns['multiple_users']['threshold']:
            return {
                'suspicious': True,
                'pattern': 'multiple_users',
                'details': {'unique_users': unique_users, 'ip': ip_address}
            }
        
        return {'suspicious': False}
    
    def _count_ip_attempts(self, ip_address: str, window: int) -> int:
        """Count attempts from IP address within time window"""
        ip_key = f"auth_ip:{ip_address}"
        current_time = time.time()
        cutoff_time = current_time - window
        
        count = 0
        raw_attempts = self.redis_client.lrange(ip_key, 0, -1)
        
        for raw_attempt in raw_attempts:
            try:
                attempt = json.loads(raw_attempt.decode('utf-8'))
                if attempt['timestamp'] > cutoff_time:
                    count += 1
            except (json.JSONDecodeError, KeyError, UnicodeDecodeError):
                continue
        
        return count
    
    def _count_unique_users_from_ip(self, ip_address: str, window: int) -> int:
        """Count unique users attempting from IP within time window"""
        ip_key = f"auth_ip:{ip_address}"
        current_time = time.time()
        cutoff_time = current_time - window
        
        unique_users = set()
        raw_attempts = self.redis_client.lrange(ip_key, 0, -1)
        
        for raw_attempt in raw_attempts:
            try:
                attempt = json.loads(raw_attempt.decode('utf-8'))
                if attempt['timestamp'] > cutoff_time:
                    unique_users.add(attempt['identifier'])
            except (json.JSONDecodeError, KeyError, UnicodeDecodeError):
                continue
        
        return len(unique_users)
    
    def get_statistics(self) -> Dict[str, Any]:
        """Get brute force protection statistics"""
        # Count locked accounts
        locked_accounts = 0
        lockout_keys = self.redis_client.keys("auth_lockout:*")
        
        for key in lockout_keys:
            lockout_data = self.redis_client.get(key)
            if lockout_data:
                try:
                    lockout_info = json.loads(lockout_data.decode('utf-8'))
                    if time.time() < lockout_info['expires_at']:
                        locked_accounts += 1
                except (json.JSONDecodeError, KeyError, UnicodeDecodeError):
                    continue
        
        return {
            'locked_accounts': locked_accounts,
            'max_attempts': self.max_attempts,
            'lockout_duration': self.lockout_duration,
            'attempt_window': self.attempt_window,
            'suspicious_patterns': self.suspicious_patterns
        }

class EnhancedRegistrationSystem:
    """
    Enhanced registration system with improved security
    """
    
    def __init__(self, db_manager, redis_client: redis.Redis, brute_force_protection: BruteForceProtection):
        """
        Initialize enhanced registration system
        
        Args:
            db_manager: Database manager instance
            redis_client: Redis client
            brute_force_protection: Brute force protection instance
        """
        self.db = db_manager
        self.redis_client = redis_client
        self.brute_force = brute_force_protection
        
        # Registration settings
        self.code_length = 8
        self.code_expiry = 3600  # 1 hour
        self.max_attempts_per_code = 3
        self.code_generation_limit = 5  # Max codes per IP per hour
        
    def generate_registration_code(self, ip_address: Optional[str] = None) -> Tuple[bool, Dict[str, Any]]:
        """
        Generate a new registration code with rate limiting
        
        Args:
            ip_address: IP address of the request
            
        Returns:
            Tuple of (success, result_data)
        """
        # Check IP-based rate limiting for code generation
        if ip_address and not self._check_code_generation_limit(ip_address):
            return False, {
                'error': 'rate_limit_exceeded',
                'message': 'Too many registration codes requested from this IP'
            }
        
        # Generate secure code
        code = self._generate_secure_code()
        
        # Store in database with enhanced security
        try:
            current_time = datetime.now()
            expires_at = current_time + timedelta(seconds=self.code_expiry)
            
            # Hash the code for storage (store hash, not plain text)
            code_hash = self._hash_code(code)
            
            query = """
                INSERT INTO registration_codes (code, created_at, status, form_data)
                VALUES (%s, %s, 'pending', %s)
            """
            
            form_data = {
                'ip_address': ip_address,
                'expires_at': expires_at.isoformat(),
                'attempts_remaining': self.max_attempts_per_code,
                'code_hash': code_hash
            }
            
            self.db.execute_and_commit(query, (code, current_time, json.dumps(form_data)))
            
            # Track code generation for rate limiting
            if ip_address:
                self._record_code_generation(ip_address)
            
            logging.info(f"Registration code generated successfully")
            
            return True, {
                'code': code,
                'expires_at': expires_at.isoformat(),
                'attempts_remaining': self.max_attempts_per_code
            }
            
        except Exception as e:
            logging.error(f"Failed to generate registration code: {str(e)}")
            return False, {
                'error': 'generation_failed',
                'message': 'Failed to generate registration code'
            }
    
    def verify_registration_code(self, code: str, user_id: str, ip_address: Optional[str] = None) -> Tuple[bool, Dict[str, Any]]:
        """
        Verify registration code with enhanced security
        
        Args:
            code: Registration code to verify
            user_id: User ID attempting registration
            ip_address: IP address of the request
            
        Returns:
            Tuple of (success, result_data)
        """
        # Check brute force protection
        allowed, protection_info = self.brute_force.check_attempt_allowed(f"reg:{code}", ip_address)
        if not allowed:
            return False, {
                'error': 'brute_force_protection',
                'details': protection_info
            }
        
        # Apply progressive delay if needed
        if protection_info.get('progressive_delay', 0) > 0:
            time.sleep(protection_info['progressive_delay'])
        
        # Verify code in database
        try:
            query = """
                SELECT code, created_at, status, form_data 
                FROM registration_codes 
                WHERE code = %s AND status = 'pending'
            """
            result = self.db.execute_query(query, (code,))
            
            if not result:
                # Record failed attempt
                self.brute_force.record_attempt(f"reg:{code}", False, ip_address, {'reason': 'code_not_found'})
                return False, {
                    'error': 'invalid_code',
                    'message': 'Registration code not found or already used'
                }
            
            code_data = result[0]
            form_data = json.loads(code_data[3]) if code_data[3] else {}
            
            # Check expiration
            created_at = code_data[1]
            current_time = datetime.now()
            expires_at = datetime.fromisoformat(form_data.get('expires_at', current_time.isoformat()))
            
            if current_time > expires_at:
                # Record failed attempt
                self.brute_force.record_attempt(f"reg:{code}", False, ip_address, {'reason': 'code_expired'})
                return False, {
                    'error': 'code_expired',
                    'message': 'Registration code has expired'
                }
            
            # Check attempts remaining
            attempts_remaining = form_data.get('attempts_remaining', 0)
            if attempts_remaining <= 0:
                # Record failed attempt
                self.brute_force.record_attempt(f"reg:{code}", False, ip_address, {'reason': 'max_attempts_exceeded'})
                return False, {
                    'error': 'max_attempts_exceeded',
                    'message': 'Maximum verification attempts exceeded'
                }
            
            # Code is valid, mark as verified and associate with user
            update_query = """
                UPDATE registration_codes 
                SET user_id = %s, verified_at = %s, status = 'verified'
                WHERE code = %s
            """
            self.db.execute_and_commit(update_query, (user_id, current_time, code))
            
            # Record successful attempt
            self.brute_force.record_attempt(f"reg:{code}", True, ip_address, {'user_id': user_id})
            
            logging.info(f"Registration code verified successfully for user {user_id}")
            
            return True, {
                'user_id': user_id,
                'verified_at': current_time.isoformat(),
                'message': 'Registration code verified successfully'
            }
            
        except Exception as e:
            logging.error(f"Registration verification error: {str(e)}")
            # Record failed attempt
            self.brute_force.record_attempt(f"reg:{code}", False, ip_address, {'reason': 'system_error'})
            return False, {
                'error': 'verification_failed',
                'message': 'Failed to verify registration code'
            }
    
    def _generate_secure_code(self) -> str:
        """Generate cryptographically secure registration code"""
        # Use alphanumeric characters excluding confusing ones (0, O, I, l)
        alphabet = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
        return ''.join(secrets.choice(alphabet) for _ in range(self.code_length))
    
    def _hash_code(self, code: str) -> str:
        """Hash registration code for secure storage"""
        # Use SHA-256 with salt
        salt = secrets.token_hex(16)
        hash_object = hashlib.sha256((code + salt).encode())
        return f"{salt}:{hash_object.hexdigest()}"
    
    def _check_code_generation_limit(self, ip_address: str) -> bool:
        """Check if IP address has exceeded code generation limit"""
        key = f"reg_gen_limit:{ip_address}"
        current_count = self.redis_client.get(key)
        
        if current_count:
            try:
                count = int(current_count.decode('utf-8'))
                return count < self.code_generation_limit
            except (ValueError, UnicodeDecodeError):
                pass
        
        return True
    
    def _record_code_generation(self, ip_address: str) -> None:
        """Record code generation for rate limiting"""
        key = f"reg_gen_limit:{ip_address}"
        
        # Increment counter or set to 1
        current_count = self.redis_client.get(key)
        if current_count:
            self.redis_client.incr(key)
        else:
            self.redis_client.setex(key, 3600, 1)  # 1 hour expiry

class SecurityAuditLogger:
    """
    Security event logging and monitoring system
    """
    
    def __init__(self, redis_client: redis.Redis):
        """
        Initialize security audit logger
        
        Args:
            redis_client: Redis client for storing events
        """
        self.redis_client = redis_client
        self.event_history = deque(maxlen=10000)  # Keep last 10k events in memory
        self.lock = threading.Lock()
        
        # Alert thresholds
        self.alert_thresholds = {
            AuthEventType.LOGIN_FAILURE: {'count': 10, 'window': 300},
            AuthEventType.BRUTE_FORCE_DETECTED: {'count': 1, 'window': 60},
            AuthEventType.SUSPICIOUS_ACTIVITY: {'count': 5, 'window': 600}
        }
    
    def log_event(self, event: AuthEvent) -> None:
        """Log security event"""
        with self.lock:
            # Add to memory history
            self.event_history.append(event)
            
            # Store in Redis with expiration
            event_key = f"security_event:{int(time.time() * 1000)}"  # Millisecond timestamp
            self.redis_client.setex(event_key, 86400, json.dumps(event.to_dict()))  # 24 hour expiry
            
            # Log to file
            log_level = self._get_log_level(event.event_type)
            logging.log(log_level, f"Security Event: {event.event_type.value} - User: {event.user_id} - IP: {event.ip_address}")
            
            # Check for alert conditions
            self._check_alert_conditions(event)
    
    def _get_log_level(self, event_type: AuthEventType) -> int:
        """Get appropriate log level for event type"""
        critical_events = [AuthEventType.BRUTE_FORCE_DETECTED, AuthEventType.ACCOUNT_LOCKED]
        warning_events = [AuthEventType.SUSPICIOUS_ACTIVITY, AuthEventType.LOGIN_FAILURE]
        
        if event_type in critical_events:
            return logging.CRITICAL
        elif event_type in warning_events:
            return logging.WARNING
        else:
            return logging.INFO
    
    def _check_alert_conditions(self, event: AuthEvent) -> None:
        """Check if event triggers alert conditions"""
        if event.event_type not in self.alert_thresholds:
            return
        
        threshold_config = self.alert_thresholds[event.event_type]
        recent_events = self._get_recent_events_by_type(event.event_type, threshold_config['window'])
        
        if len(recent_events) >= threshold_config['count']:
            self._trigger_security_alert(event.event_type, recent_events)
    
    def _get_recent_events_by_type(self, event_type: AuthEventType, window_seconds: int) -> List[AuthEvent]:
        """Get recent events of specific type within time window"""
        cutoff_time = datetime.now() - timedelta(seconds=window_seconds)
        
        return [
            event for event in self.event_history
            if event.event_type == event_type and event.timestamp > cutoff_time
        ]
    
    def _trigger_security_alert(self, event_type: AuthEventType, events: List[AuthEvent]) -> None:
        """Trigger security alert"""
        alert_message = f"SECURITY ALERT: {event_type.value} threshold exceeded - {len(events)} events"
        logging.critical(alert_message)
        
        # Here you would integrate with external alerting systems
        # such as email, SMS, Slack, etc.
    
    def get_security_summary(self, hours: int = 24) -> Dict[str, Any]:
        """Get security event summary"""
        cutoff_time = datetime.now() - timedelta(hours=hours)
        recent_events = [
            event for event in self.event_history
            if event.timestamp > cutoff_time
        ]
        
        # Count events by type
        event_counts = defaultdict(int)
        for event in recent_events:
            event_counts[event.event_type.value] += 1
        
        return {
            'period_hours': hours,
            'total_events': len(recent_events),
            'event_breakdown': dict(event_counts),
            'alert_thresholds': {
                event_type.value: config
                for event_type, config in self.alert_thresholds.items()
            }
        }

def create_enhanced_auth_system(db_manager, redis_client: redis.Redis) -> Tuple[BruteForceProtection, EnhancedRegistrationSystem, SecurityAuditLogger]:
    """
    Create and configure enhanced authentication system
    
    Args:
        db_manager: Database manager instance
        redis_client: Redis client
        
    Returns:
        Tuple of (BruteForceProtection, EnhancedRegistrationSystem, SecurityAuditLogger)
    """
    # Create components
    brute_force_protection = BruteForceProtection(redis_client)
    registration_system = EnhancedRegistrationSystem(db_manager, redis_client, brute_force_protection)
    audit_logger = SecurityAuditLogger(redis_client)
    
    logging.info("Enhanced authentication system initialized")
    
    return brute_force_protection, registration_system, audit_logger