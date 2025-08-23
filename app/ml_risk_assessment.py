"""
ML-powered risk assessment system for TyphoonLineWebhook
"""
import re
import json
import logging
import numpy as np
from datetime import datetime, timedelta
from typing import Dict, Any, List, Tuple, Optional
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.ensemble import RandomForestClassifier
import joblib
import threading
from dataclasses import dataclass
from enum import Enum

class RiskLevel(Enum):
    LOW = "low"
    MEDIUM = "medium"  
    HIGH = "high"
    CRITICAL = "critical"

class CrisisType(Enum):
    SUBSTANCE_ABUSE = "substance_abuse"
    SUICIDAL_IDEATION = "suicidal_ideation"
    SELF_HARM = "self_harm"
    OVERDOSE = "overdose"

@dataclass
class RiskAssessment:
    user_id: str
    message: str
    risk_level: RiskLevel
    confidence: float
    crisis_types: List[CrisisType]
    keywords: List[str]
    timestamp: datetime
    requires_intervention: bool

class MLRiskModel:
    """Machine learning model for risk assessment"""
    
    def __init__(self):
        self.vectorizer = TfidfVectorizer(max_features=500, ngram_range=(1, 2))
        self.classifier = RandomForestClassifier(n_estimators=50, random_state=42)
        self.is_trained = False
        self.lock = threading.Lock()
        
        # Thai risk keywords
        self.risk_keywords = {
            RiskLevel.CRITICAL: ['ฆ่าตัวตาย', 'อยากตาย', 'จบชีวิต', 'เกินขนาด', 'overdose'],
            RiskLevel.HIGH: ['ซึมเศร้ามาก', 'สิ้นหวัง', 'หยุดยา', 'เลิกยา', 'วิกฤต'],
            RiskLevel.MEDIUM: ['เครียด', 'กังวล', 'ใช้ยา', 'เสพ', 'เศร้า']
        }
    
    def predict_risk(self, message: str) -> Tuple[RiskLevel, float, List[str]]:
        """Predict risk level for message"""
        message_lower = message.lower()
        matched_keywords = []
        risk_score = 0
        
        # Check keywords
        for risk_level, keywords in self.risk_keywords.items():
            matches = [kw for kw in keywords if kw in message_lower]
            if matches:
                matched_keywords.extend(matches)
                if risk_level == RiskLevel.CRITICAL:
                    risk_score = max(risk_score, 4)
                elif risk_level == RiskLevel.HIGH:
                    risk_score = max(risk_score, 3)
                elif risk_level == RiskLevel.MEDIUM:
                    risk_score = max(risk_score, 2)
        
        # Determine risk level
        if risk_score >= 4:
            return RiskLevel.CRITICAL, 0.9, matched_keywords
        elif risk_score >= 3:
            return RiskLevel.HIGH, 0.8, matched_keywords
        elif risk_score >= 2:
            return RiskLevel.MEDIUM, 0.7, matched_keywords
        else:
            return RiskLevel.LOW, 0.5, matched_keywords

class CrisisInterventionSystem:
    """Crisis intervention system"""
    
    def __init__(self, ml_model: MLRiskModel):
        self.ml_model = ml_model
        self.intervention_messages = {
            'suicidal': "ฉันเป็นห่วงคุณมาก โปรดติดต่อสายด่วน 1323 ทันที",
            'overdose': "เหตุฉุกเฉิน โทร 1669 หรือไปโรงพยาบาลทันที",
            'high_risk': "คุณต้องการความช่วยเหลือ สายด่วน 1300 พร้อมช่วย"
        }
    
    def assess_and_intervene(self, user_id: str, message: str) -> RiskAssessment:
        """Assess risk and trigger intervention"""
        risk_level, confidence, keywords = self.ml_model.predict_risk(message)
        
        # Identify crisis types
        crisis_types = []
        message_lower = message.lower()
        
        if any(word in message_lower for word in ['ฆ่าตัวตาย', 'อยากตาย']):
            crisis_types.append(CrisisType.SUICIDAL_IDEATION)
        if any(word in message_lower for word in ['เกินขนาด', 'overdose']):
            crisis_types.append(CrisisType.OVERDOSE)
        if any(word in message_lower for word in ['เสพ', 'ยา']):
            crisis_types.append(CrisisType.SUBSTANCE_ABUSE)
        
        requires_intervention = risk_level in [RiskLevel.CRITICAL, RiskLevel.HIGH]
        
        assessment = RiskAssessment(
            user_id=user_id,
            message=message,
            risk_level=risk_level,
            confidence=confidence,
            crisis_types=crisis_types,
            keywords=keywords,
            timestamp=datetime.now(),
            requires_intervention=requires_intervention
        )
        
        if requires_intervention:
            self._trigger_intervention(assessment)
        
        return assessment
    
    def _trigger_intervention(self, assessment: RiskAssessment) -> None:
        """Send crisis intervention message"""
        logging.critical(f"Crisis intervention for {assessment.user_id}: {assessment.risk_level.value}")
        
        # Select message
        if CrisisType.SUICIDAL_IDEATION in assessment.crisis_types:
            message = self.intervention_messages['suicidal']
        elif CrisisType.OVERDOSE in assessment.crisis_types:
            message = self.intervention_messages['overdose']
        else:
            message = self.intervention_messages['high_risk']
        
        # Queue intervention message
        try:
            from .background_tasks import send_follow_up_message
            send_follow_up_message.apply_async(
                args=[assessment.user_id, message, 'crisis_intervention'],
                priority=10
            )
        except Exception as e:
            logging.error(f"Failed to queue intervention: {e}")

class PredictiveAnalytics:
    """User progress analytics"""
    
    def predict_recovery_likelihood(self, user_id: str, user_data: Dict) -> Dict[str, Any]:
        """Predict recovery likelihood"""
        conversations = user_data.get('conversations', [])
        if not conversations:
            return {'likelihood': 0.5, 'confidence': 0.0}
        
        # Simple metrics
        recent = conversations[-7:]  # Last week
        high_risk_count = sum(1 for c in recent if c.get('risk_level') in ['high', 'critical'])
        engagement = len(recent) / 7
        
        # Calculate likelihood
        likelihood = max(0.1, min(0.9, (1 - high_risk_count/7) * engagement))
        
        return {
            'likelihood': likelihood,
            'confidence': 0.7,
            'high_risk_days': high_risk_count,
            'engagement_score': engagement
        }

def create_enhanced_risk_system():
    """Create risk assessment system"""
    ml_model = MLRiskModel()
    crisis_system = CrisisInterventionSystem(ml_model)
    analytics = PredictiveAnalytics()
    
    logging.info("ML risk assessment system initialized")
    return ml_model, crisis_system, analytics