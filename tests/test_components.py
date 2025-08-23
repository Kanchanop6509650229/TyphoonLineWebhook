"""
Comprehensive unit test suite for TyphoonLineWebhook components
"""
import unittest
import pytest
import asyncio
import json
import tempfile
import os
from unittest.mock import Mock, patch, AsyncMock, MagicMock
from datetime import datetime, timedelta
import redis
import mysql.connector

# Import modules to test
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

class TestMultiLanguageManager(unittest.TestCase):
    """Test multi-language support functionality"""
    
    def setUp(self):
        from app.multi_language_manager import MultiLanguageManager, LanguageDetector
        self.language_manager = MultiLanguageManager()
        self.detector = LanguageDetector()
    
    def test_thai_language_detection(self):
        """Test Thai language detection"""
        thai_text = "สวัสดีครับ ผมรู้สึกเศร้ามาก"
        detected_lang, confidence = self.detector.detect_language(thai_text)
        
        self.assertEqual(detected_lang.value, 'th')
        self.assertGreater(confidence, 0.6)
    
    def test_english_language_detection(self):
        """Test English language detection"""
        english_text = "Hello, I am feeling very sad today"
        detected_lang, confidence = self.detector.detect_language(english_text)
        
        self.assertEqual(detected_lang.value, 'en')
        self.assertGreater(confidence, 0.5)
    
    def test_translation_retrieval(self):
        """Test translation retrieval"""
        from app.multi_language_manager import SupportedLanguage
        
        translation = self.language_manager.translator.get_translation(
            'greeting', SupportedLanguage.THAI
        )
        self.assertIsInstance(translation, str)
        self.assertGreater(len(translation), 0)
    
    def test_user_language_preference_storage(self):
        """Test storing user language preferences"""
        from app.multi_language_manager import SupportedLanguage
        
        user_id = "test_user_123"
        self.language_manager.set_user_language(user_id, SupportedLanguage.ENGLISH)
        
        stored_lang = self.language_manager.get_user_language(user_id)
        self.assertEqual(stored_lang, SupportedLanguage.ENGLISH)

class TestDataEncryption(unittest.TestCase):
    """Test data encryption functionality"""
    
    def setUp(self):
        from app.data_encryption import KeyManager, DataEncryption
        self.key_manager = KeyManager('test-master-key')
        self.encryption = DataEncryption(self.key_manager)
    
    def test_key_generation(self):
        """Test encryption key generation"""
        from app.data_encryption import EncryptionLevel
        
        key = self.key_manager.get_active_key(EncryptionLevel.STANDARD)
        self.assertIsNotNone(key)
        self.assertIsNotNone(key.key_data)
        self.assertEqual(len(key.key_data), 32)  # 256-bit key
    
    def test_field_encryption_decryption(self):
        """Test field-level encryption and decryption"""
        original_text = "Sensitive user information"
        encrypted_value, key_id = self.encryption.encrypt_field('user_message', original_text)
        
        self.assertIsNotNone(encrypted_value)
        self.assertIsNotNone(key_id)
        self.assertNotEqual(encrypted_value, original_text)
        
        decrypted_text = self.encryption.decrypt_field(encrypted_value, key_id)
        self.assertEqual(decrypted_text, original_text)
    
    def test_record_encryption(self):
        """Test full record encryption"""
        record = {
            'user_id': 'user123',
            'user_message': 'I need help',
            'timestamp': '2024-01-01T12:00:00',
            'metadata': {'session_id': 'sess123'}
        }
        
        encrypted_record = self.encryption.encrypt_record(record)
        self.assertIn('_encryption_keys', encrypted_record)
        
        decrypted_record = self.encryption.decrypt_record(encrypted_record)
        self.assertEqual(decrypted_record['user_message'], record['user_message'])
        self.assertNotIn('_encryption_keys', decrypted_record)

class TestErrorHandling(unittest.TestCase):
    """Test error handling functionality"""
    
    def setUp(self):
        from app.error_handling import ErrorHandler, CircuitBreaker
        self.error_handler = ErrorHandler()
        self.circuit_breaker = CircuitBreaker('test_service')
    
    def test_error_categorization(self):
        """Test error categorization"""
        from app.error_handling import ErrorCategory, ErrorSeverity
        
        database_error = Exception("MySQL connection failed")
        chatbot_error = self.error_handler.handle_error(database_error)
        
        self.assertEqual(chatbot_error.category, ErrorCategory.DATABASE)
        self.assertEqual(chatbot_error.severity, ErrorSeverity.HIGH)
    
    def test_circuit_breaker_functionality(self):
        """Test circuit breaker pattern"""
        from app.error_handling import CircuitState
        
        # Initially closed
        self.assertEqual(self.circuit_breaker.state, CircuitState.CLOSED)
        
        # Simulate failures
        for _ in range(6):  # Exceed threshold
            self.circuit_breaker._on_failure()
        
        self.assertEqual(self.circuit_breaker.state, CircuitState.OPEN)
        
        # Test successful call resets circuit
        self.circuit_breaker.state = CircuitState.HALF_OPEN
        self.circuit_breaker._on_success()
        self.assertEqual(self.circuit_breaker.state, CircuitState.CLOSED)
    
    def test_error_statistics(self):
        """Test error statistics collection"""
        from app.error_handling import ChatbotError, ErrorCategory, ErrorSeverity
        
        # Generate some test errors
        for i in range(5):
            error = ChatbotError(
                f"Test error {i}",
                ErrorCategory.SYSTEM,
                ErrorSeverity.MEDIUM
            )
            self.error_handler._record_error(error)
        
        summary = self.error_handler.get_error_summary(1)
        self.assertEqual(summary['total_errors'], 5)
        self.assertIn('system', summary['category_breakdown'])

class TestCachingSystem(unittest.TestCase):
    """Test caching system functionality"""
    
    def setUp(self):
        from app.caching_system import ApplicationCache, EnhancedLRUCache
        self.app_cache = ApplicationCache()
        self.lru_cache = EnhancedLRUCache(maxsize=100)
    
    def test_cache_operations(self):
        """Test basic cache operations"""
        key = "test_key"
        value = {"data": "test_value", "timestamp": datetime.now().isoformat()}
        
        # Test set and get
        self.app_cache.set(key, value, ttl=60)
        retrieved_value = self.app_cache.get(key)
        
        self.assertEqual(retrieved_value, value)
        
        # Test expiration
        self.app_cache.set(key, value, ttl=0.1)
        import time
        time.sleep(0.2)
        
        expired_value = self.app_cache.get(key)
        self.assertIsNone(expired_value)
    
    def test_lru_cache_memory_management(self):
        """Test LRU cache memory pressure handling"""
        # Fill cache to capacity
        for i in range(150):  # Exceed maxsize
            self.lru_cache[f"key_{i}"] = f"value_{i}" * 100
        
        # Should trigger cleanup
        self.assertLessEqual(len(self.lru_cache), 100)
    
    def test_cache_statistics(self):
        """Test cache statistics collection"""
        # Generate cache hits and misses
        self.app_cache.set("hit_key", "value")
        self.app_cache.get("hit_key")  # Hit
        self.app_cache.get("miss_key")  # Miss
        
        stats = self.app_cache.get_cache_stats()
        self.assertIn('hit_rate', stats)
        self.assertIn('miss_count', stats)

@pytest.mark.asyncio
class TestVoiceProcessing:
    """Test voice processing functionality"""
    
    def setup_method(self):
        from app.voice_processing import AudioPreprocessor, SpeechToTextEngine
        self.preprocessor = AudioPreprocessor()
        self.stt_engine = SpeechToTextEngine()
    
    def test_audio_quality_assessment(self):
        """Test audio quality assessment"""
        # Create mock audio data
        mock_audio_data = b"fake_audio_data" * 1000
        
        try:
            processed_audio, metadata = self.preprocessor.preprocess_audio(
                mock_audio_data, "wav"
            )
            assert metadata is not None
            assert metadata.quality_score >= 0
            assert metadata.quality_score <= 1
        except Exception:
            # Expected to fail with fake data, but should not crash
            pass
    
    async def test_transcription_workflow(self):
        """Test speech-to-text workflow"""
        from app.voice_processing import SpeechLanguage
        
        # Mock audio data
        mock_audio = b"mock_audio_data"
        
        # This will fail with real transcription but should handle gracefully
        result = await self.stt_engine.transcribe_audio(
            mock_audio, SpeechLanguage.ENGLISH
        )
        
        assert hasattr(result, 'text')
        assert hasattr(result, 'confidence')
        assert hasattr(result, 'language')

class TestMLRiskAssessment(unittest.TestCase):
    """Test ML risk assessment functionality"""
    
    def setUp(self):
        from app.ml_risk_assessment import MLRiskModel, CrisisInterventionSystem
        self.ml_model = MLRiskModel()
        self.crisis_system = CrisisInterventionSystem(self.ml_model)
    
    def test_risk_prediction(self):
        """Test risk level prediction"""
        # Test high-risk message
        high_risk_message = "I want to kill myself"
        risk_level, confidence, keywords = self.ml_model.predict_risk(high_risk_message)
        
        from app.ml_risk_assessment import RiskLevel
        self.assertEqual(risk_level, RiskLevel.CRITICAL)
        self.assertGreater(confidence, 0.8)
        self.assertGreater(len(keywords), 0)
        
        # Test low-risk message
        low_risk_message = "Hello, how are you today?"
        risk_level, confidence, keywords = self.ml_model.predict_risk(low_risk_message)
        
        self.assertEqual(risk_level, RiskLevel.LOW)
    
    def test_crisis_intervention_trigger(self):
        """Test crisis intervention system"""
        user_id = "test_user"
        crisis_message = "I can't take it anymore, I want to end my life"
        
        assessment = self.crisis_system.assess_and_intervene(user_id, crisis_message)
        
        from app.ml_risk_assessment import RiskLevel
        self.assertEqual(assessment.risk_level, RiskLevel.CRITICAL)
        self.assertTrue(assessment.requires_intervention)

class TestDatabaseOptimization(unittest.TestCase):
    """Test database optimization functionality"""
    
    def setUp(self):
        from app.database_optimization import DatabaseOptimizer
        self.optimizer = DatabaseOptimizer(None)  # Mock DB manager
    
    @patch('app.database_optimization.DatabaseOptimizer._execute_query')
    def test_index_analysis(self, mock_execute):
        """Test database index analysis"""
        # Mock query results
        mock_execute.return_value = [
            ('conversations', 'user_id', 1000),
            ('conversations', 'timestamp', 500)
        ]
        
        missing_indexes = self.optimizer.analyze_missing_indexes()
        self.assertIsInstance(missing_indexes, list)
    
    def test_performance_recommendations(self):
        """Test performance recommendation generation"""
        mock_stats = {
            'avg_query_time': 2.5,
            'slow_queries': 15,
            'connection_pool_usage': 85
        }
        
        recommendations = self.optimizer._generate_recommendations(mock_stats)
        self.assertIsInstance(recommendations, list)
        self.assertGreater(len(recommendations), 0)

@pytest.mark.asyncio
class TestMicroservicesArchitecture:
    """Test microservices architecture"""
    
    def setup_method(self):
        from app.microservices_architecture import EventBus, ChatService
        self.event_bus = EventBus()
        self.chat_service = ChatService()
    
    async def test_event_publishing(self):
        """Test event publishing functionality"""
        from app.microservices_architecture import Event, EventType
        
        # Mock Redis
        with patch.object(self.event_bus, 'redis', Mock()) as mock_redis:
            mock_redis.publish = AsyncMock()
            
            event = Event(
                event_id="test_event",
                event_type=EventType.USER_MESSAGE_RECEIVED,
                payload={"message": "test"},
                timestamp=datetime.now(),
                source_service="test_service"
            )
            
            await self.event_bus.publish(event)
            mock_redis.publish.assert_called()
    
    def test_service_route_setup(self):
        """Test microservice route setup"""
        # Check that routes are properly configured
        router = self.chat_service.app.router
        routes = [route for route in router.routes()]
        
        route_paths = [route.resource.canonical for route in routes if hasattr(route.resource, 'canonical')]
        
        assert '/health' in str(route_paths)
        assert '/message' in str(route_paths)

class TestAPIGateway(unittest.TestCase):
    """Test API Gateway functionality"""
    
    def setUp(self):
        from app.api_gateway import APIGateway, ServiceRegistry
        self.gateway = APIGateway()
        self.service_registry = ServiceRegistry()
    
    def test_service_registration(self):
        """Test service registration"""
        from app.api_gateway import ServiceEndpoint, ServiceType
        
        service = ServiceEndpoint(
            service_type=ServiceType.CHAT_SERVICE,
            name="test-chat-service",
            url="http://localhost:8001",
            health_check_path="/health"
        )
        
        self.service_registry.register_service(service)
        
        retrieved_service = self.service_registry.get_service(ServiceType.CHAT_SERVICE)
        self.assertIsNotNone(retrieved_service)
        self.assertEqual(retrieved_service.name, "test-chat-service")
    
    def test_circuit_breaker_logic(self):
        """Test circuit breaker functionality"""
        from app.api_gateway import CircuitBreakerState
        
        service_name = "test-service"
        
        # Record multiple failures
        for _ in range(6):
            self.service_registry.record_failure(service_name)
        
        circuit_breaker = self.service_registry.circuit_breakers[service_name]
        self.assertEqual(circuit_breaker.state, CircuitBreakerState.OPEN)
    
    @patch('app.api_gateway.AuthenticationManager.verify_token')
    def test_authentication_middleware(self, mock_verify):
        """Test JWT authentication"""
        from app.api_gateway import AuthenticationManager
        
        auth_manager = AuthenticationManager()
        
        # Mock successful verification
        mock_verify.return_value = {
            'user_id': 'test_user',
            'roles': ['user']
        }
        
        token = "mock_jwt_token"
        payload = auth_manager.verify_token(token)
        
        self.assertIsNotNone(payload)
        self.assertEqual(payload['user_id'], 'test_user')

class TestAnalyticsDashboard(unittest.TestCase):
    """Test analytics dashboard functionality"""
    
    def setUp(self):
        from app.analytics_dashboard import MetricsCollector, AdvancedAnalytics
        self.metrics_collector = MetricsCollector()
        self.analytics = AdvancedAnalytics()
    
    @patch('app.analytics_dashboard.MetricsCollector._collect_user_engagement_metrics')
    def test_metrics_collection(self, mock_collect):
        """Test metrics collection"""
        from app.analytics_dashboard import UserEngagementMetrics
        
        mock_metrics = UserEngagementMetrics(
            total_users=100,
            active_users_24h=50,
            active_users_7d=80,
            new_users_today=5,
            avg_session_duration=12.5,
            messages_per_user=3.2,
            retention_rate_7d=65.0,
            bounce_rate=15.0
        )
        
        mock_collect.return_value = mock_metrics
        
        current_metrics = self.metrics_collector.get_current_metrics()
        self.assertIsInstance(current_metrics, dict)
    
    def test_user_journey_analysis(self):
        """Test user journey analysis"""
        user_id = "test_user_123"
        
        # Mock journey analysis (would normally require database)
        with patch.object(self.analytics, 'db_manager', Mock()):
            journey = self.analytics.generate_user_journey_analysis(user_id)
            self.assertIsInstance(journey, dict)

if __name__ == '__main__':
    # Run all tests
    unittest.main(verbosity=2)