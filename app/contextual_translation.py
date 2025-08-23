"""
Advanced translation system with context-aware responses for TyphoonLineWebhook
Provides intelligent translation, context understanding, and culturally appropriate responses
"""
import os
import json
import logging
import re
from datetime import datetime
from typing import Dict, Any, List, Optional, Tuple, Union
from dataclasses import dataclass
from enum import Enum
import threading
from collections import defaultdict
import requests

class ResponseContext(Enum):
    GREETING = "greeting"
    CRISIS = "crisis"
    SUBSTANCE_ABUSE = "substance_abuse"
    MENTAL_HEALTH = "mental_health"
    GENERAL_SUPPORT = "general_support"
    MEDICAL_ADVICE = "medical_advice"
    ENCOURAGEMENT = "encouragement"
    INFORMATION = "information"
    ERROR = "error"

class SentimentLevel(Enum):
    VERY_NEGATIVE = "very_negative"
    NEGATIVE = "negative"
    NEUTRAL = "neutral"
    POSITIVE = "positive"
    VERY_POSITIVE = "very_positive"

@dataclass
class ContextualTranslation:
    key: str
    context: ResponseContext
    sentiment: SentimentLevel
    thai_formal: str
    thai_casual: str
    english_formal: str
    english_casual: str
    cultural_notes: Optional[str] = None
    usage_examples: Optional[List[str]] = None

class ContextAnalyzer:
    """Analyze message context and sentiment for appropriate responses"""
    
    def __init__(self):
        # Context keywords for different scenarios
        self.context_keywords = {
            ResponseContext.CRISIS: {
                'thai': ['ฆ่าตัวตาย', 'อยากตาย', 'จบชีวิต', 'สิ้นหวัง', 'เจ็บปวด', 'ทุกข์', 'ไม่ไหว'],
                'english': ['suicide', 'kill myself', 'end life', 'hopeless', 'can\'t take it', 'want to die', 'hurt myself']
            },
            ResponseContext.SUBSTANCE_ABUSE: {
                'thai': ['ยาเสพติด', 'เสพ', 'เลิกยา', 'ถอนยา', 'เหล้า', 'บุหรี่', 'เข็ม', 'ยาบ้า', 'กัญชา'],
                'english': ['drugs', 'addiction', 'substance', 'withdraw', 'alcohol', 'cigarette', 'needle', 'cocaine', 'heroin']
            },
            ResponseContext.MENTAL_HEALTH: {
                'thai': ['ซึมเศร้า', 'เครียด', 'วิตกกังวล', 'นอนไม่หลับ', 'เศร้า', 'โกรธ', 'กลัว', 'เหงา'],
                'english': ['depressed', 'depression', 'anxiety', 'stressed', 'sad', 'angry', 'scared', 'lonely', 'anxious']
            },
            ResponseContext.MEDICAL_ADVICE: {
                'thai': ['หมอ', 'โรงพยาบาล', 'ยา', 'รักษา', 'อาการ', 'ปวด', 'เจ็บ', 'ตรวจ'],
                'english': ['doctor', 'hospital', 'medicine', 'treatment', 'symptoms', 'pain', 'hurt', 'checkup']
            },
            ResponseContext.ENCOURAGEMENT: {
                'thai': ['ช่วย', 'กำลังใจ', 'สู้', 'เข้มแข็ง', 'หวัง', 'ดีขึ้น', 'ผ่านพ้น'],
                'english': ['help', 'support', 'strength', 'hope', 'better', 'overcome', 'encourage', 'strong']
            }
        }
        
        # Sentiment indicators
        self.sentiment_indicators = {
            SentimentLevel.VERY_NEGATIVE: {
                'thai': ['แย่มาก', 'เลวร้าย', 'ทนไม่ไหว', 'สิ้นหวัง', 'ไม่มีทาง', 'ขาดใจ'],
                'english': ['terrible', 'awful', 'can\'t stand', 'hopeless', 'impossible', 'devastated']
            },
            SentimentLevel.NEGATIVE: {
                'thai': ['แย่', 'เศร้า', 'เสียใจ', 'ไม่ดี', 'ลำบาก', 'หนัก'],
                'english': ['bad', 'sad', 'sorry', 'difficult', 'hard', 'tough', 'not good']
            },
            SentimentLevel.NEUTRAL: {
                'thai': ['ปกติ', 'เฉยๆ', 'ไม่แน่ใจ', 'อาจจะ', 'บางครั้ง'],
                'english': ['normal', 'okay', 'not sure', 'maybe', 'sometimes', 'average']
            },
            SentimentLevel.POSITIVE: {
                'thai': ['ดี', 'ดีขึ้น', 'ดีใจ', 'สบาย', 'โอเค', 'ไม่เป็นไร'],
                'english': ['good', 'better', 'happy', 'comfortable', 'okay', 'fine', 'alright']
            },
            SentimentLevel.VERY_POSITIVE: {
                'thai': ['ดีมาก', 'ยอดเยี่ยม', 'สุดยอด', 'มีความสุข', 'ตื่นเต้น'],
                'english': ['great', 'excellent', 'amazing', 'wonderful', 'fantastic', 'excited']
            }
        }
        
        # Formality indicators
        self.formal_indicators = {
            'thai': ['ครับ', 'ค่ะ', 'คะ', 'ขอบพระคุณ', 'กราบ', 'เรียน'],
            'english': ['sir', 'madam', 'please', 'thank you very much', 'respectfully', 'sincerely']
        }
    
    def analyze_context(self, message: str, language: str = 'auto') -> ResponseContext:
        """Analyze message to determine response context"""
        message_lower = message.lower()
        
        # Score each context based on keyword matches
        context_scores = defaultdict(int)
        
        for context, keywords in self.context_keywords.items():
            if language == 'auto':
                # Check both languages
                thai_matches = sum(1 for keyword in keywords['thai'] if keyword in message_lower)
                english_matches = sum(1 for keyword in keywords['english'] if keyword in message_lower)
                context_scores[context] = thai_matches + english_matches
            elif language == 'thai':
                context_scores[context] = sum(1 for keyword in keywords['thai'] if keyword in message_lower)
            elif language == 'english':
                context_scores[context] = sum(1 for keyword in keywords['english'] if keyword in message_lower)
        
        # Return context with highest score, default to general support
        if context_scores:
            best_context = max(context_scores, key=context_scores.get)
            if context_scores[best_context] > 0:
                return best_context
        
        return ResponseContext.GENERAL_SUPPORT
    
    def analyze_sentiment(self, message: str, language: str = 'auto') -> SentimentLevel:
        """Analyze message sentiment"""
        message_lower = message.lower()
        
        sentiment_scores = defaultdict(int)
        
        for sentiment, indicators in self.sentiment_indicators.items():
            if language == 'auto':
                thai_matches = sum(1 for indicator in indicators['thai'] if indicator in message_lower)
                english_matches = sum(1 for indicator in indicators['english'] if indicator in message_lower)
                sentiment_scores[sentiment] = thai_matches + english_matches
            elif language == 'thai':
                sentiment_scores[sentiment] = sum(1 for indicator in indicators['thai'] if indicator in message_lower)
            elif language == 'english':
                sentiment_scores[sentiment] = sum(1 for indicator in indicators['english'] if indicator in message_lower)
        
        # Return sentiment with highest score, default to neutral
        if sentiment_scores:
            best_sentiment = max(sentiment_scores, key=sentiment_scores.get)
            if sentiment_scores[best_sentiment] > 0:
                return best_sentiment
        
        return SentimentLevel.NEUTRAL
    
    def detect_formality(self, message: str, language: str = 'auto') -> bool:
        """Detect if message uses formal language"""
        message_lower = message.lower()
        
        if language == 'auto':
            formal_indicators = self.formal_indicators['thai'] + self.formal_indicators['english']
        else:
            formal_indicators = self.formal_indicators.get(language, [])
        
        return any(indicator in message_lower for indicator in formal_indicators)

class ContextualTranslationEngine:
    """Advanced translation engine with context awareness"""
    
    def __init__(self):
        self.context_analyzer = ContextAnalyzer()
        self.translations = {}
        self.lock = threading.Lock()
        
        # Load contextual translations
        self._load_contextual_translations()
    
    def _load_contextual_translations(self):
        """Load contextual translation database"""
        contextual_translations = [
            # Crisis intervention responses
            ContextualTranslation(
                key="crisis_immediate_help",
                context=ResponseContext.CRISIS,
                sentiment=SentimentLevel.VERY_NEGATIVE,
                thai_formal="ขอให้คุณติดต่อสายด่วนสุขภาพจิต 1323 ทันที หรือไปโรงพยาบาลใกล้บ้านค่ะ",
                thai_casual="โทรหาสายด่วน 1323 ได้เลยนะ หรือไปโรงพยาบาลเร็วๆ",
                english_formal="Please contact the mental health hotline 1323 immediately or visit the nearest hospital.",
                english_casual="Call the hotline 1323 right now or go to a hospital quickly.",
                cultural_notes="Thai responses include more formal particles and indirect communication"
            ),
            
            ContextualTranslation(
                key="crisis_support",
                context=ResponseContext.CRISIS,
                sentiment=SentimentLevel.VERY_NEGATIVE,
                thai_formal="ชีวิตของคุณมีค่ามาก คุณไม่ได้อยู่คนเดียวค่ะ",
                thai_casual="คุณมีคุณค่านะ และไม่ได้อยู่คนเดียวจริงๆ",
                english_formal="Your life is very valuable. You are not alone in this.",
                english_casual="You matter, and you're definitely not alone.",
                cultural_notes="Thai version emphasizes collective support values"
            ),
            
            # Substance abuse support
            ContextualTranslation(
                key="substance_quit_support",
                context=ResponseContext.SUBSTANCE_ABUSE,
                sentiment=SentimentLevel.NEGATIVE,
                thai_formal="การเลิกสารเสพติดเป็นกระบวนการที่ต้องใช้เวลา อย่าท้อแท้เลยค่ะ",
                thai_casual="การเลิกยาต้องใช้เวลานะ อย่าเพิ่งยอมแพ้",
                english_formal="Recovery from substance use is a process that takes time. Please don't lose hope.",
                english_casual="Getting clean takes time. Don't give up yet.",
                cultural_notes="Thai emphasizes patience and perseverance"
            ),
            
            ContextualTranslation(
                key="relapse_understanding",
                context=ResponseContext.SUBSTANCE_ABUSE,
                sentiment=SentimentLevel.NEGATIVE,
                thai_formal="การกลับไปเสพใหม่เป็นส่วนหนึ่งของการฟื้นฟู ไม่ได้หมายความว่าคุณล้มเหลวค่ะ",
                thai_casual="กลับไปเสพใหม่ก็เป็นเรื่องปกติในการฟื้นตัว ไม่ใช่ความผิดของคุณนะ",
                english_formal="Relapse can be part of recovery. It doesn't mean you've failed.",
                english_casual="Slipping up is normal in recovery. It's not your fault.",
                cultural_notes="Thai culture emphasizes face-saving and reducing blame"
            ),
            
            # Mental health support
            ContextualTranslation(
                key="depression_hope",
                context=ResponseContext.MENTAL_HEALTH,
                sentiment=SentimentLevel.NEGATIVE,
                thai_formal="ความซึมเศร้าสามารถรักษาได้ค่ะ มีหลายวิธีที่จะช่วยให้คุณรู้สึกดีขึ้น",
                thai_casual="ซึมเศร้ารักษาได้นะ มีหลายทางที่จะทำให้ดีขึ้น",
                english_formal="Depression is treatable. There are many ways to help you feel better.",
                english_casual="Depression can be treated. There are lots of ways to get better.",
                cultural_notes="Thai version includes reassurance about treatment availability"
            ),
            
            ContextualTranslation(
                key="anxiety_technique",
                context=ResponseContext.MENTAL_HEALTH,
                sentiment=SentimentLevel.NEGATIVE,
                thai_formal="เมื่อรู้สึกวิตกกังวล ลองหายใจเข้าลึกๆ แล้วหายใจออกช้าๆ ค่ะ",
                thai_casual="กังวลแล้วลองหายใจลึกๆ แล้วหายใจออกช้าๆ นะ",
                english_formal="When feeling anxious, try taking slow, deep breaths.",
                english_casual="When you're anxious, try some slow, deep breathing.",
                cultural_notes="Thai includes more detailed breathing instructions"
            ),
            
            # General encouragement
            ContextualTranslation(
                key="general_encouragement",
                context=ResponseContext.ENCOURAGEMENT,
                sentiment=SentimentLevel.NEUTRAL,
                thai_formal="คุณกำลังทำได้ดีแล้วค่ะ การขอความช่วยเหลือแสดงถึงความกล้าหาญ",
                thai_casual="คุณทำได้ดีแล้วนะ การมาขอความช่วยเหลือเป็นเรื่องที่กล้าหาญมาก",
                english_formal="You're doing well by seeking help. That takes courage.",
                english_casual="You're doing great by asking for help. That's really brave.",
                cultural_notes="Thai culture values acknowledging effort and courage"
            ),
            
            # Medical advice
            ContextualTranslation(
                key="see_professional",
                context=ResponseContext.MEDICAL_ADVICE,
                sentiment=SentimentLevel.NEUTRAL,
                thai_formal="ขอแนะนำให้ปรึกษาแพทย์หรือผู้เชี่ยวชาญด้านสุขภาพจิตค่ะ",
                thai_casual="ควรไปปรึกษาหมอหรือนักจิตวิทยานะ",
                english_formal="I recommend consulting with a doctor or mental health professional.",
                english_casual="You should talk to a doctor or therapist.",
                cultural_notes="Thai version is more indirect and suggests rather than directs"
            ),
            
            # Error messages
            ContextualTranslation(
                key="system_error",
                context=ResponseContext.ERROR,
                sentiment=SentimentLevel.NEUTRAL,
                thai_formal="ขออภัยค่ะ ระบบมีปัญหาชั่วคราว กรุณาลองใหม่ในอีกสักครู่",
                thai_casual="ขอโทษนะ ระบบมีปัญหา ลองใหม่อีกครั้งได้ไหม",
                english_formal="I apologize for the system error. Please try again in a moment.",
                english_casual="Sorry about that error. Can you try again?",
                cultural_notes="Thai includes more elaborate apology structure"
            )
        ]
        
        # Store translations by context and key
        for translation in contextual_translations:
            with self.lock:
                if translation.context not in self.translations:
                    self.translations[translation.context] = {}
                
                self.translations[translation.context][translation.key] = translation
    
    def get_contextual_response(self, message: str, user_language: str, 
                              user_id: str = None, is_formal: bool = None) -> str:
        """Get contextually appropriate response"""
        # Analyze context and sentiment
        context = self.context_analyzer.analyze_context(message, user_language)
        sentiment = self.context_analyzer.analyze_sentiment(message, user_language)
        
        # Detect formality if not specified
        if is_formal is None:
            is_formal = self.context_analyzer.detect_formality(message, user_language)
        
        # Find best matching translation
        best_translation = self._find_best_translation(context, sentiment)
        
        if best_translation:
            return self._get_appropriate_variant(
                best_translation, user_language, is_formal
            )
        
        # Fallback to general response
        return self._get_fallback_response(user_language, is_formal)
    
    def _find_best_translation(self, context: ResponseContext, 
                             sentiment: SentimentLevel) -> Optional[ContextualTranslation]:
        """Find best matching translation for context and sentiment"""
        with self.lock:
            if context not in self.translations:
                return None
            
            context_translations = self.translations[context]
            
            # First try to find exact sentiment match
            for translation in context_translations.values():
                if translation.sentiment == sentiment:
                    return translation
            
            # Then try to find any translation in this context
            if context_translations:
                return next(iter(context_translations.values()))
            
            return None
    
    def _get_appropriate_variant(self, translation: ContextualTranslation, 
                               language: str, is_formal: bool) -> str:
        """Get appropriate language variant based on formality"""
        if language == 'thai' or language == 'th':
            return translation.thai_formal if is_formal else translation.thai_casual
        else:  # English
            return translation.english_formal if is_formal else translation.english_casual
    
    def _get_fallback_response(self, language: str, is_formal: bool) -> str:
        """Get fallback response when no specific translation found"""
        if language == 'thai' or language == 'th':
            return "ขอบคุณที่แบ่งปันค่ะ ฉันเข้าใจความรู้สึกของคุณ" if is_formal else "ขอบคุณนะที่เล่าให้ฟัง เข้าใจเลย"
        else:
            return "Thank you for sharing. I understand how you feel." if is_formal else "Thanks for telling me. I get it."

class ResponsePersonalizer:
    """Personalize responses based on user history and preferences"""
    
    def __init__(self):
        self.user_profiles = {}
        self.interaction_history = defaultdict(list)
        self.lock = threading.Lock()
    
    def update_user_profile(self, user_id: str, message: str, 
                          context: ResponseContext, sentiment: SentimentLevel):
        """Update user profile with interaction data"""
        with self.lock:
            if user_id not in self.user_profiles:
                self.user_profiles[user_id] = {
                    'preferred_formality': None,
                    'common_contexts': defaultdict(int),
                    'sentiment_history': [],
                    'language_preference': None,
                    'last_interaction': None
                }
            
            profile = self.user_profiles[user_id]
            profile['common_contexts'][context] += 1
            profile['sentiment_history'].append(sentiment)
            profile['last_interaction'] = datetime.now()
            
            # Keep only last 10 sentiment records
            if len(profile['sentiment_history']) > 10:
                profile['sentiment_history'] = profile['sentiment_history'][-10:]
            
            # Store interaction in history
            self.interaction_history[user_id].append({
                'timestamp': datetime.now(),
                'message_length': len(message),
                'context': context,
                'sentiment': sentiment
            })
            
            # Keep only last 50 interactions
            if len(self.interaction_history[user_id]) > 50:
                self.interaction_history[user_id] = self.interaction_history[user_id][-50:]
    
    def get_personalized_formality(self, user_id: str, detected_formality: bool) -> bool:
        """Get personalized formality preference"""
        with self.lock:
            if user_id in self.user_profiles:
                profile = self.user_profiles[user_id]
                if profile['preferred_formality'] is not None:
                    return profile['preferred_formality']
            
            return detected_formality
    
    def get_user_context_patterns(self, user_id: str) -> Dict[ResponseContext, int]:
        """Get user's common interaction contexts"""
        with self.lock:
            if user_id in self.user_profiles:
                return dict(self.user_profiles[user_id]['common_contexts'])
            return {}
    
    def should_check_in(self, user_id: str) -> bool:
        """Determine if we should proactively check in with user"""
        with self.lock:
            if user_id not in self.user_profiles:
                return False
            
            profile = self.user_profiles[user_id]
            last_interaction = profile.get('last_interaction')
            
            if not last_interaction:
                return False
            
            # Check if it's been more than 3 days since last interaction
            days_since = (datetime.now() - last_interaction).days
            
            # Check if user has history of crisis or substance abuse contexts
            crisis_contexts = profile['common_contexts'].get(ResponseContext.CRISIS, 0)
            substance_contexts = profile['common_contexts'].get(ResponseContext.SUBSTANCE_ABUSE, 0)
            
            return days_since >= 3 and (crisis_contexts > 0 or substance_contexts > 0)

# Global translation engine instance
_translation_engine = None
_response_personalizer = None

def get_translation_engine() -> ContextualTranslationEngine:
    """Get global translation engine instance"""
    global _translation_engine
    if _translation_engine is None:
        _translation_engine = ContextualTranslationEngine()
    return _translation_engine

def get_response_personalizer() -> ResponsePersonalizer:
    """Get global response personalizer instance"""
    global _response_personalizer
    if _response_personalizer is None:
        _response_personalizer = ResponsePersonalizer()
    return _response_personalizer

def get_contextual_response(message: str, user_language: str, user_id: str = None) -> str:
    """Convenience function to get contextual response"""
    engine = get_translation_engine()
    personalizer = get_response_personalizer()
    
    # Detect formality and get personalized preference
    detected_formality = engine.context_analyzer.detect_formality(message, user_language)
    is_formal = personalizer.get_personalized_formality(user_id, detected_formality) if user_id else detected_formality
    
    # Get contextual response
    response = engine.get_contextual_response(message, user_language, user_id, is_formal)
    
    # Update user profile if user_id provided
    if user_id:
        context = engine.context_analyzer.analyze_context(message, user_language)
        sentiment = engine.context_analyzer.analyze_sentiment(message, user_language)
        personalizer.update_user_profile(user_id, message, context, sentiment)
    
    return response

def init_contextual_translation():
    """Initialize contextual translation system"""
    engine = get_translation_engine()
    personalizer = get_response_personalizer()
    logging.info("Contextual translation system initialized")
    return engine, personalizer