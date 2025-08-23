"""
Multi-language support system for TyphoonLineWebhook
Provides automatic language detection, translation management, and localization
"""
import os
import json
import logging
import re
from datetime import datetime
from typing import Dict, Any, List, Optional, Tuple
from dataclasses import dataclass
from enum import Enum
import threading
from collections import defaultdict
import unicodedata

class SupportedLanguage(Enum):
    THAI = "th"
    ENGLISH = "en"
    AUTO = "auto"

@dataclass
class LanguageConfig:
    code: str
    name: str
    native_name: str
    rtl: bool = False
    default_font: str = "Arial"
    date_format: str = "%Y-%m-%d"
    time_format: str = "%H:%M:%S"

@dataclass
class TranslationEntry:
    key: str
    thai: str
    english: str
    context: Optional[str] = None
    category: str = "general"

class LanguageDetector:
    """Automatic language detection for Thai and English"""
    
    def __init__(self):
        # Thai Unicode ranges
        self.thai_ranges = [
            (0x0E00, 0x0E7F),  # Thai block
            (0x0E80, 0x0EFF),  # Lao block (some shared characters)
        ]
        
        # Common Thai words for pattern matching
        self.thai_common_words = {
            'สวัสดี', 'ขอบคุณ', 'ครับ', 'ค่ะ', 'กิน', 'นอน', 'ไป', 'มา', 'ดี', 'แย่',
            'เศร้า', 'ดีใจ', 'เครียด', 'ปวดหัว', 'ช่วย', 'ยา', 'โรงพยาบาล', 'หมอ',
            'เสพ', 'เลิก', 'บุหรี่', 'เหล้า', 'ยาเสพติด', 'ซึมเศร้า', 'ฆ่าตัวตาย'
        }
        
        # English patterns
        self.english_patterns = [
            r'\b(the|and|or|but|in|on|at|to|for|of|with|by)\b',
            r'\b(I|you|he|she|it|we|they)\b',
            r'\b(am|is|are|was|were|have|has|had|do|does|did)\b',
            r'\b(help|feel|sad|happy|stressed|pain|medicine|doctor)\b'
        ]
    
    def detect_language(self, text: str) -> Tuple[SupportedLanguage, float]:
        """
        Detect language of input text
        Returns: (detected_language, confidence_score)
        """
        if not text or not text.strip():
            return SupportedLanguage.ENGLISH, 0.5
        
        text = text.strip().lower()
        
        # Count Thai characters
        thai_char_count = self._count_thai_characters(text)
        total_chars = len([c for c in text if c.isalpha()])
        
        if total_chars == 0:
            return SupportedLanguage.ENGLISH, 0.5
        
        thai_ratio = thai_char_count / total_chars
        
        # Check for Thai common words
        thai_word_matches = sum(1 for word in self.thai_common_words if word in text)
        
        # Check for English patterns
        english_pattern_matches = sum(
            1 for pattern in self.english_patterns 
            if re.search(pattern, text, re.IGNORECASE)
        )
        
        # Calculate confidence scores
        thai_confidence = 0.0
        english_confidence = 0.0
        
        # Thai character ratio scoring
        if thai_ratio > 0.7:
            thai_confidence += 0.8
        elif thai_ratio > 0.3:
            thai_confidence += 0.4
        
        # Thai word matching scoring
        if thai_word_matches > 0:
            thai_confidence += min(thai_word_matches * 0.2, 0.6)
        
        # English pattern scoring
        if english_pattern_matches > 0:
            english_confidence += min(english_pattern_matches * 0.3, 0.8)
        
        # ASCII characters boost English confidence
        ascii_ratio = sum(1 for c in text if ord(c) < 128) / len(text)
        if ascii_ratio > 0.8:
            english_confidence += 0.3
        
        # Determine final language
        if thai_confidence > english_confidence and thai_confidence > 0.4:
            return SupportedLanguage.THAI, min(thai_confidence, 1.0)
        elif english_confidence > 0.3:
            return SupportedLanguage.ENGLISH, min(english_confidence, 1.0)
        else:
            # Default to Thai if uncertain (since it's a Thai-focused system)
            return SupportedLanguage.THAI, 0.5
    
    def _count_thai_characters(self, text: str) -> int:
        """Count Thai Unicode characters in text"""
        count = 0
        for char in text:
            code_point = ord(char)
            for start, end in self.thai_ranges:
                if start <= code_point <= end:
                    count += 1
                    break
        return count
    
    def get_language_stats(self, text: str) -> Dict[str, Any]:
        """Get detailed language statistics for text"""
        thai_chars = self._count_thai_characters(text)
        total_chars = len([c for c in text if c.isalpha()])
        ascii_chars = sum(1 for c in text if ord(c) < 128)
        
        thai_words = sum(1 for word in self.thai_common_words if word in text.lower())
        english_patterns = sum(
            1 for pattern in self.english_patterns 
            if re.search(pattern, text, re.IGNORECASE)
        )
        
        return {
            'total_characters': len(text),
            'alphabetic_characters': total_chars,
            'thai_characters': thai_chars,
            'ascii_characters': ascii_chars,
            'thai_character_ratio': thai_chars / max(total_chars, 1),
            'ascii_ratio': ascii_chars / max(len(text), 1),
            'thai_word_matches': thai_words,
            'english_pattern_matches': english_patterns
        }

class TranslationManager:
    """Manage translations between Thai and English"""
    
    def __init__(self):
        self.translations = {}
        self.lock = threading.Lock()
        self.fallback_language = SupportedLanguage.THAI
        
        # Load default translations
        self._load_default_translations()
    
    def _load_default_translations(self):
        """Load default translation entries"""
        default_translations = [
            # Greetings and basic responses
            TranslationEntry("greeting", "สวัสดีค่ะ", "Hello", "general", "greeting"),
            TranslationEntry("how_are_you", "เป็นอย่างไรบ้างคะ?", "How are you?", "general", "greeting"),
            TranslationEntry("thank_you", "ขอบคุณค่ะ", "Thank you", "general", "courtesy"),
            TranslationEntry("goodbye", "ลาก่อนค่ะ", "Goodbye", "general", "farewell"),
            
            # Support and encouragement
            TranslationEntry("support_message", "ฉันเข้าใจความรู้สึกของคุณ", "I understand how you feel", "support", "empathy"),
            TranslationEntry("encouragement", "คุณไม่ได้อยู่คนเดียว เราพร้อมช่วยเหลือ", "You're not alone, we're here to help", "support", "encouragement"),
            TranslationEntry("seek_help", "การขอความช่วยเหลือเป็นสิ่งที่กล้าหาญ", "Seeking help is a brave thing to do", "support", "encouragement"),
            
            # Crisis intervention
            TranslationEntry("crisis_immediate", "โปรดติดต่อสายด่วน 1323 ทันที", "Please contact emergency hotline 1323 immediately", "crisis", "emergency"),
            TranslationEntry("crisis_support", "คุณมีคุณค่า ชีวิตคุณมีความหมาย", "You are valuable, your life has meaning", "crisis", "support"),
            TranslationEntry("professional_help", "ควรปรึกษาผู้เชี่ยวชาญ", "You should consult with a professional", "advice", "medical"),
            
            # Substance abuse support
            TranslationEntry("quit_support", "การเลิกใช้สารเสพติดต้องใช้เวลา อย่าท้อแท้", "Quitting substances takes time, don't give up", "substance", "encouragement"),
            TranslationEntry("relapse_normal", "การกลับไปเสพใหม่เป็นเรื่องปกติในกระบวนการฟื้นฟู", "Relapse is normal in the recovery process", "substance", "education"),
            TranslationEntry("withdrawal_info", "อาการถอนยาจะค่อยๆ ดีขึ้น", "Withdrawal symptoms will gradually improve", "substance", "information"),
            
            # Mental health
            TranslationEntry("depression_support", "ความซึมเศร้าสามารถรักษาได้", "Depression is treatable", "mental_health", "hope"),
            TranslationEntry("anxiety_help", "ลองหายใจลึกๆ และนับ 1 ถึง 10", "Try taking deep breaths and count from 1 to 10", "mental_health", "technique"),
            TranslationEntry("stress_management", "การจัดการความเครียดมีหลายวิธี", "There are many ways to manage stress", "mental_health", "information"),
            
            # Error messages
            TranslationEntry("error_general", "ขออภัยค่ะ เกิดข้อผิดพลาด กรุณาลองใหม่", "Sorry, an error occurred. Please try again", "error", "general"),
            TranslationEntry("error_connection", "เกิดปัญหาการเชื่อมต่อ กรุณาตรวจสอบอินเทอร์เน็ต", "Connection error, please check your internet", "error", "technical"),
            TranslationEntry("error_system", "ระบบไม่สามารถใช้งานได้ในขณะนี้", "System is currently unavailable", "error", "system"),
            
            # Questions and prompts
            TranslationEntry("how_feel_today", "วันนี้คุณรู้สึกอย่างไร?", "How do you feel today?", "question", "mood"),
            TranslationEntry("need_help", "คุณต้องการความช่วยเหลือใช่หรือไม่?", "Do you need help?", "question", "support"),
            TranslationEntry("tell_more", "เล่าให้ฟังเพิ่มเติมได้ไหม?", "Can you tell me more?", "question", "conversation"),
            
            # Information and resources
            TranslationEntry("hotline_info", "สายด่วนสุขภาพจิต: 1323", "Mental health hotline: 1323", "resource", "contact"),
            TranslationEntry("emergency_info", "กรณีฉุกเฉิน: 1669", "Emergency: 1669", "resource", "emergency"),
            TranslationEntry("available_247", "บริการนี้ใช้งานได้ 24 ชั่วโมง", "This service is available 24 hours", "info", "availability"),
        ]
        
        # Store translations
        for entry in default_translations:
            self.add_translation(entry)
    
    def add_translation(self, entry: TranslationEntry):
        """Add translation entry"""
        with self.lock:
            if entry.category not in self.translations:
                self.translations[entry.category] = {}
            
            self.translations[entry.category][entry.key] = {
                'thai': entry.thai,
                'english': entry.english,
                'context': entry.context
            }
        
        logging.debug(f"Added translation: {entry.key}")
    
    def get_translation(self, key: str, language: SupportedLanguage, category: str = None) -> str:
        """Get translation for key in specified language"""
        with self.lock:
            # Search in specific category first
            if category and category in self.translations:
                if key in self.translations[category]:
                    translation = self.translations[category][key]
                    if language == SupportedLanguage.THAI:
                        return translation['thai']
                    elif language == SupportedLanguage.ENGLISH:
                        return translation['english']
            
            # Search across all categories
            for cat_translations in self.translations.values():
                if key in cat_translations:
                    translation = cat_translations[key]
                    if language == SupportedLanguage.THAI:
                        return translation['thai']
                    elif language == SupportedLanguage.ENGLISH:
                        return translation['english']
            
            # Return key if translation not found
            logging.warning(f"Translation not found for key: {key}")
            return key
    
    def get_translations_by_category(self, category: str) -> Dict[str, Dict[str, str]]:
        """Get all translations in a category"""
        with self.lock:
            return self.translations.get(category, {}).copy()
    
    def search_translations(self, search_term: str, language: SupportedLanguage = None) -> List[Dict[str, Any]]:
        """Search translations by content"""
        results = []
        search_term = search_term.lower()
        
        with self.lock:
            for category, translations in self.translations.items():
                for key, translation in translations.items():
                    match = False
                    
                    if language == SupportedLanguage.THAI or language is None:
                        if search_term in translation['thai'].lower():
                            match = True
                    
                    if language == SupportedLanguage.ENGLISH or language is None:
                        if search_term in translation['english'].lower():
                            match = True
                    
                    if key.lower().find(search_term) != -1:
                        match = True
                    
                    if match:
                        results.append({
                            'key': key,
                            'category': category,
                            'thai': translation['thai'],
                            'english': translation['english'],
                            'context': translation.get('context')
                        })
        
        return results

class MultiLanguageManager:
    """Main multi-language support manager"""
    
    def __init__(self):
        self.detector = LanguageDetector()
        self.translator = TranslationManager()
        
        # Language configurations
        self.language_configs = {
            SupportedLanguage.THAI: LanguageConfig(
                code="th",
                name="Thai",
                native_name="ไทย",
                default_font="Noto Sans Thai",
                date_format="%d/%m/%Y",
                time_format="%H:%M น."
            ),
            SupportedLanguage.ENGLISH: LanguageConfig(
                code="en",
                name="English",
                native_name="English",
                default_font="Arial",
                date_format="%Y-%m-%d",
                time_format="%H:%M"
            )
        }
        
        # User language preferences
        self.user_languages = {}
        self.usage_stats = defaultdict(int)
    
    def detect_user_language(self, user_id: str, message: str) -> SupportedLanguage:
        """Detect and store user's preferred language"""
        detected_lang, confidence = self.detector.detect_language(message)
        
        # Store user preference if confidence is high
        if confidence > 0.7:
            self.user_languages[user_id] = detected_lang
            self.usage_stats[detected_lang.value] += 1
        
        return detected_lang
    
    def get_user_language(self, user_id: str, default_message: str = None) -> SupportedLanguage:
        """Get user's preferred language"""
        # Check stored preference first
        if user_id in self.user_languages:
            return self.user_languages[user_id]
        
        # Try to detect from default message
        if default_message:
            return self.detect_user_language(user_id, default_message)
        
        # Default to Thai
        return SupportedLanguage.THAI
    
    def set_user_language(self, user_id: str, language: SupportedLanguage):
        """Manually set user's language preference"""
        self.user_languages[user_id] = language
        self.usage_stats[language.value] += 1
        logging.info(f"Set language for user {user_id}: {language.value}")
    
    def translate_message(self, key: str, user_id: str, category: str = None, 
                         variables: Dict[str, str] = None) -> str:
        """Translate message for user"""
        user_lang = self.get_user_language(user_id)
        message = self.translator.get_translation(key, user_lang, category)
        
        # Replace variables if provided
        if variables:
            for var_key, var_value in variables.items():
                message = message.replace(f"{{{var_key}}}", str(var_value))
        
        return message
    
    def format_datetime(self, dt: datetime, user_id: str, 
                       include_time: bool = True) -> str:
        """Format datetime according to user's language preferences"""
        user_lang = self.get_user_language(user_id)
        config = self.language_configs[user_lang]
        
        if include_time:
            return dt.strftime(f"{config.date_format} {config.time_format}")
        else:
            return dt.strftime(config.date_format)
    
    def get_language_config(self, language: SupportedLanguage) -> LanguageConfig:
        """Get language configuration"""
        return self.language_configs.get(language, self.language_configs[SupportedLanguage.THAI])
    
    def get_supported_languages(self) -> List[Dict[str, str]]:
        """Get list of supported languages"""
        return [
            {
                'code': config.code,
                'name': config.name,
                'native_name': config.native_name
            }
            for config in self.language_configs.values()
        ]
    
    def get_usage_statistics(self) -> Dict[str, Any]:
        """Get language usage statistics"""
        total_users = len(self.user_languages)
        
        return {
            'total_users_with_preference': total_users,
            'language_distribution': dict(self.usage_stats),
            'language_percentages': {
                lang: (count / max(sum(self.usage_stats.values()), 1)) * 100
                for lang, count in self.usage_stats.items()
            }
        }
    
    def create_multilingual_response(self, base_key: str, user_id: str, 
                                   context: Dict[str, Any] = None) -> Dict[str, str]:
        """Create response in multiple languages for comparison"""
        thai_msg = self.translator.get_translation(base_key, SupportedLanguage.THAI)
        english_msg = self.translator.get_translation(base_key, SupportedLanguage.ENGLISH)
        
        # Replace context variables if provided
        if context:
            for key, value in context.items():
                placeholder = f"{{{key}}}"
                thai_msg = thai_msg.replace(placeholder, str(value))
                english_msg = english_msg.replace(placeholder, str(value))
        
        return {
            'thai': thai_msg,
            'english': english_msg,
            'detected_user_language': self.get_user_language(user_id).value
        }

# Global manager instance
_language_manager = None

def get_language_manager() -> MultiLanguageManager:
    """Get global language manager instance"""
    global _language_manager
    if _language_manager is None:
        _language_manager = MultiLanguageManager()
    return _language_manager

def detect_language(text: str) -> Tuple[SupportedLanguage, float]:
    """Convenience function to detect language"""
    manager = get_language_manager()
    return manager.detector.detect_language(text)

def translate_for_user(key: str, user_id: str, category: str = None, 
                      variables: Dict[str, str] = None) -> str:
    """Convenience function to translate for user"""
    manager = get_language_manager()
    return manager.translate_message(key, user_id, category, variables)

def init_language_support():
    """Initialize language support system"""
    manager = get_language_manager()
    logging.info("Multi-language support system initialized")
    return manager