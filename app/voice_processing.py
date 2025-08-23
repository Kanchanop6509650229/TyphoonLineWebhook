"""
Voice processing system for TyphoonLineWebhook
Provides speech-to-text, text-to-speech, and audio message handling capabilities
"""
import os
import io
import json
import logging
import asyncio
import tempfile
from datetime import datetime
from typing import Dict, Any, List, Optional, Tuple, Union
from dataclasses import dataclass
from enum import Enum
import threading
from collections import defaultdict
import requests
import base64
import wave
import audioop
from pydub import AudioSegment
from pydub.effects import normalize, compress_dynamic_range
import speech_recognition as sr
import pyttsx3
import whisper

class AudioFormat(Enum):
    WAV = "wav"
    MP3 = "mp3"
    AAC = "aac"
    M4A = "m4a"
    OGG = "ogg"

class VoiceGender(Enum):
    MALE = "male"
    FEMALE = "female"

class SpeechLanguage(Enum):
    THAI = "th"
    ENGLISH = "en"
    AUTO = "auto"

@dataclass
class AudioMetadata:
    duration: float
    sample_rate: int
    channels: int
    format: AudioFormat
    size_bytes: int
    quality_score: float

@dataclass
class TranscriptionResult:
    text: str
    confidence: float
    language: SpeechLanguage
    processing_time: float
    segments: Optional[List[Dict[str, Any]]] = None
    metadata: Optional[AudioMetadata] = None

@dataclass
class SynthesisResult:
    audio_data: bytes
    format: AudioFormat
    duration: float
    sample_rate: int
    size_bytes: int

class AudioPreprocessor:
    """Preprocess audio for better speech recognition"""
    
    def __init__(self):
        self.target_sample_rate = 16000  # 16kHz for speech recognition
        self.target_channels = 1  # Mono
        self.min_duration = 0.5  # Minimum 0.5 seconds
        self.max_duration = 300  # Maximum 5 minutes
    
    def preprocess_audio(self, audio_data: bytes, input_format: str = None) -> Tuple[bytes, AudioMetadata]:
        """Preprocess audio for optimal recognition"""
        try:
            # Load audio with pydub
            if input_format:
                audio = AudioSegment.from_file(io.BytesIO(audio_data), format=input_format)
            else:
                audio = AudioSegment.from_file(io.BytesIO(audio_data))
            
            # Get original metadata
            original_metadata = AudioMetadata(
                duration=len(audio) / 1000.0,
                sample_rate=audio.frame_rate,
                channels=audio.channels,
                format=AudioFormat(input_format) if input_format else AudioFormat.WAV,
                size_bytes=len(audio_data),
                quality_score=self._assess_audio_quality(audio)
            )
            
            # Validate duration
            if original_metadata.duration < self.min_duration:
                raise ValueError(f"Audio too short: {original_metadata.duration}s (minimum: {self.min_duration}s)")
            
            if original_metadata.duration > self.max_duration:
                logging.warning(f"Audio truncated from {original_metadata.duration}s to {self.max_duration}s")
                audio = audio[:self.max_duration * 1000]
            
            # Normalize and enhance audio
            audio = self._enhance_audio(audio)
            
            # Convert to target format (16kHz, mono, WAV)
            audio = audio.set_frame_rate(self.target_sample_rate)
            audio = audio.set_channels(self.target_channels)
            
            # Export as WAV
            wav_buffer = io.BytesIO()
            audio.export(wav_buffer, format="wav")
            processed_audio = wav_buffer.getvalue()
            
            # Create processed metadata
            processed_metadata = AudioMetadata(
                duration=len(audio) / 1000.0,
                sample_rate=self.target_sample_rate,
                channels=self.target_channels,
                format=AudioFormat.WAV,
                size_bytes=len(processed_audio),
                quality_score=self._assess_audio_quality(audio)
            )
            
            return processed_audio, processed_metadata
            
        except Exception as e:
            logging.error(f"Audio preprocessing failed: {str(e)}")
            raise
    
    def _enhance_audio(self, audio: AudioSegment) -> AudioSegment:
        """Enhance audio quality for better recognition"""
        try:
            # Normalize volume
            audio = normalize(audio)
            
            # Apply dynamic range compression
            audio = compress_dynamic_range(audio, threshold=-20.0, ratio=4.0, attack=5.0, release=50.0)
            
            # Remove silence from beginning and end
            audio = audio.strip_silence(silence_thresh=-40, silence_len=500)
            
            # Apply high-pass filter to remove low-frequency noise
            audio = audio.high_pass_filter(80)
            
            # Apply low-pass filter to remove high-frequency noise
            audio = audio.low_pass_filter(8000)
            
            return audio
            
        except Exception as e:
            logging.warning(f"Audio enhancement failed: {str(e)}")
            return audio
    
    def _assess_audio_quality(self, audio: AudioSegment) -> float:
        """Assess audio quality (0.0 - 1.0)"""
        try:
            # Simple quality assessment based on various factors
            quality_score = 0.5  # Base score
            
            # Sample rate factor
            if audio.frame_rate >= 16000:
                quality_score += 0.2
            elif audio.frame_rate >= 8000:
                quality_score += 0.1
            
            # Duration factor
            duration = len(audio) / 1000.0
            if 1.0 <= duration <= 60.0:  # Optimal duration range
                quality_score += 0.2
            elif duration > 0.5:
                quality_score += 0.1
            
            # Volume analysis
            dBFS = audio.dBFS
            if -30 <= dBFS <= -10:  # Good volume range
                quality_score += 0.1
            elif dBFS > -40:
                quality_score += 0.05
            
            return min(quality_score, 1.0)
            
        except Exception:
            return 0.5

class SpeechToTextEngine:
    """Speech-to-text conversion engine"""
    
    def __init__(self):
        self.preprocessor = AudioPreprocessor()
        
        # Initialize Whisper model for offline recognition
        self.whisper_model = None
        self._load_whisper_model()
        
        # Initialize speech recognition
        self.recognizer = sr.Recognizer()
        
        # Performance settings
        self.recognizer.energy_threshold = 300
        self.recognizer.dynamic_energy_threshold = True
        self.recognizer.pause_threshold = 0.8
        self.recognizer.phrase_threshold = 0.3
        
        # Language-specific settings
        self.language_codes = {
            SpeechLanguage.THAI: "th-TH",
            SpeechLanguage.ENGLISH: "en-US"
        }
    
    def _load_whisper_model(self):
        """Load Whisper model for offline recognition"""
        try:
            # Use base model for balance between speed and accuracy
            self.whisper_model = whisper.load_model("base")
            logging.info("Whisper model loaded successfully")
        except Exception as e:
            logging.error(f"Failed to load Whisper model: {str(e)}")
            self.whisper_model = None
    
    async def transcribe_audio(self, audio_data: bytes, language: SpeechLanguage = SpeechLanguage.AUTO,
                              input_format: str = None) -> TranscriptionResult:
        """Transcribe audio to text"""
        start_time = datetime.now()
        
        try:
            # Preprocess audio
            processed_audio, metadata = self.preprocessor.preprocess_audio(audio_data, input_format)
            
            # Try multiple recognition methods
            results = []
            
            # Method 1: Whisper (offline, most accurate)
            if self.whisper_model:
                whisper_result = await self._transcribe_with_whisper(processed_audio, language)
                if whisper_result:
                    results.append(whisper_result)
            
            # Method 2: Google Speech Recognition (online)
            google_result = await self._transcribe_with_google(processed_audio, language)
            if google_result:
                results.append(google_result)
            
            # Method 3: SpeechRecognition library fallback
            fallback_result = await self._transcribe_with_fallback(processed_audio, language)
            if fallback_result:
                results.append(fallback_result)
            
            # Select best result
            if results:
                best_result = max(results, key=lambda x: x.confidence)
                
                # Calculate processing time
                processing_time = (datetime.now() - start_time).total_seconds()
                best_result.processing_time = processing_time
                best_result.metadata = metadata
                
                return best_result
            else:
                # No successful transcription
                return TranscriptionResult(
                    text="",
                    confidence=0.0,
                    language=language,
                    processing_time=(datetime.now() - start_time).total_seconds(),
                    metadata=metadata
                )
                
        except Exception as e:
            logging.error(f"Transcription failed: {str(e)}")
            return TranscriptionResult(
                text="",
                confidence=0.0,
                language=language,
                processing_time=(datetime.now() - start_time).total_seconds(),
                metadata=None
            )
    
    async def _transcribe_with_whisper(self, audio_data: bytes, language: SpeechLanguage) -> Optional[TranscriptionResult]:
        """Transcribe using Whisper model"""
        try:
            if not self.whisper_model:
                return None
            
            # Save audio to temporary file
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as temp_file:
                temp_file.write(audio_data)
                temp_path = temp_file.name
            
            try:
                # Transcribe with Whisper
                if language == SpeechLanguage.AUTO:
                    result = self.whisper_model.transcribe(temp_path)
                else:
                    lang_code = "th" if language == SpeechLanguage.THAI else "en"
                    result = self.whisper_model.transcribe(temp_path, language=lang_code)
                
                # Extract text and confidence
                text = result["text"].strip()
                
                # Estimate confidence based on Whisper's internal metrics
                confidence = self._estimate_whisper_confidence(result)
                
                # Detect language if auto
                detected_language = self._detect_language_from_whisper(result, language)
                
                return TranscriptionResult(
                    text=text,
                    confidence=confidence,
                    language=detected_language,
                    processing_time=0.0,  # Will be set later
                    segments=result.get("segments")
                )
                
            finally:
                # Clean up temporary file
                os.unlink(temp_path)
                
        except Exception as e:
            logging.error(f"Whisper transcription failed: {str(e)}")
            return None
    
    async def _transcribe_with_google(self, audio_data: bytes, language: SpeechLanguage) -> Optional[TranscriptionResult]:
        """Transcribe using Google Speech Recognition"""
        try:
            # Create audio source from bytes
            with sr.AudioFile(io.BytesIO(audio_data)) as source:
                audio = self.recognizer.record(source)
            
            # Determine language code
            if language == SpeechLanguage.AUTO:
                # Try both languages and pick the best
                results = []
                for lang in [SpeechLanguage.THAI, SpeechLanguage.ENGLISH]:
                    try:
                        lang_code = self.language_codes[lang]
                        text = self.recognizer.recognize_google(audio, language=lang_code)
                        confidence = 0.8  # Google doesn't provide confidence scores
                        results.append((text, confidence, lang))
                    except:
                        continue
                
                if results:
                    # Return result with highest confidence (or first successful one)
                    text, confidence, detected_lang = results[0]
                    return TranscriptionResult(
                        text=text.strip(),
                        confidence=confidence,
                        language=detected_lang,
                        processing_time=0.0
                    )
            else:
                lang_code = self.language_codes[language]
                text = self.recognizer.recognize_google(audio, language=lang_code)
                
                return TranscriptionResult(
                    text=text.strip(),
                    confidence=0.8,
                    language=language,
                    processing_time=0.0
                )
                
        except sr.UnknownValueError:
            logging.warning("Google could not understand the audio")
            return None
        except sr.RequestError as e:
            logging.error(f"Google Speech Recognition service error: {str(e)}")
            return None
        except Exception as e:
            logging.error(f"Google transcription failed: {str(e)}")
            return None
    
    async def _transcribe_with_fallback(self, audio_data: bytes, language: SpeechLanguage) -> Optional[TranscriptionResult]:
        """Fallback transcription method"""
        try:
            # Use SpeechRecognition with different engines
            with sr.AudioFile(io.BytesIO(audio_data)) as source:
                audio = self.recognizer.record(source)
            
            # Try Sphinx (offline, lower quality but reliable)
            try:
                text = self.recognizer.recognize_sphinx(audio)
                return TranscriptionResult(
                    text=text.strip(),
                    confidence=0.6,
                    language=SpeechLanguage.ENGLISH,  # Sphinx primarily supports English
                    processing_time=0.0
                )
            except:
                pass
            
            return None
            
        except Exception as e:
            logging.error(f"Fallback transcription failed: {str(e)}")
            return None
    
    def _estimate_whisper_confidence(self, result: Dict[str, Any]) -> float:
        """Estimate confidence from Whisper result"""
        try:
            # Use average log probability as confidence indicator
            segments = result.get("segments", [])
            if segments:
                avg_logprob = sum(seg.get("avg_logprob", -1.0) for seg in segments) / len(segments)
                # Convert log probability to confidence (0-1)
                confidence = max(0.0, min(1.0, (avg_logprob + 1.0)))
                return confidence
            
            # Default confidence if no segments
            return 0.7
            
        except:
            return 0.7
    
    def _detect_language_from_whisper(self, result: Dict[str, Any], requested_language: SpeechLanguage) -> SpeechLanguage:
        """Detect language from Whisper result"""
        if requested_language != SpeechLanguage.AUTO:
            return requested_language
        
        detected_lang = result.get("language", "en")
        if detected_lang == "th":
            return SpeechLanguage.THAI
        else:
            return SpeechLanguage.ENGLISH

class TextToSpeechEngine:
    """Text-to-speech synthesis engine"""
    
    def __init__(self):
        self.engine = None
        self._initialize_tts_engine()
        
        # Voice settings
        self.voice_settings = {
            SpeechLanguage.THAI: {
                VoiceGender.FEMALE: {"rate": 150, "volume": 0.9},
                VoiceGender.MALE: {"rate": 140, "volume": 0.8}
            },
            SpeechLanguage.ENGLISH: {
                VoiceGender.FEMALE: {"rate": 160, "volume": 0.9},
                VoiceGender.MALE: {"rate": 150, "volume": 0.8}
            }
        }
    
    def _initialize_tts_engine(self):
        """Initialize TTS engine"""
        try:
            self.engine = pyttsx3.init()
            
            # Get available voices
            voices = self.engine.getProperty('voices')
            
            # Set default voice
            if voices:
                self.engine.setProperty('voice', voices[0].id)
            
            logging.info("TTS engine initialized successfully")
            
        except Exception as e:
            logging.error(f"Failed to initialize TTS engine: {str(e)}")
            self.engine = None
    
    async def synthesize_speech(self, text: str, language: SpeechLanguage = SpeechLanguage.THAI,
                               gender: VoiceGender = VoiceGender.FEMALE,
                               output_format: AudioFormat = AudioFormat.MP3) -> SynthesisResult:
        """Synthesize speech from text"""
        try:
            if not self.engine or not text.strip():
                raise ValueError("TTS engine not available or empty text")
            
            # Get voice settings
            settings = self.voice_settings.get(language, {}).get(gender, {"rate": 150, "volume": 0.9})
            
            # Configure engine
            self.engine.setProperty('rate', settings['rate'])
            self.engine.setProperty('volume', settings['volume'])
            
            # Find appropriate voice
            voice_id = self._find_voice(language, gender)
            if voice_id:
                self.engine.setProperty('voice', voice_id)
            
            # Generate speech to temporary file
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as temp_file:
                temp_path = temp_file.name
            
            try:
                # Save to file
                self.engine.save_to_file(text, temp_path)
                self.engine.runAndWait()
                
                # Read generated audio
                with open(temp_path, 'rb') as f:
                    wav_data = f.read()
                
                # Convert to requested format if needed
                if output_format != AudioFormat.WAV:
                    audio_data = self._convert_audio_format(wav_data, output_format)
                else:
                    audio_data = wav_data
                
                # Get audio metadata
                duration = self._get_audio_duration(wav_data)
                sample_rate = 22050  # Default TTS sample rate
                
                return SynthesisResult(
                    audio_data=audio_data,
                    format=output_format,
                    duration=duration,
                    sample_rate=sample_rate,
                    size_bytes=len(audio_data)
                )
                
            finally:
                # Clean up temporary file
                if os.path.exists(temp_path):
                    os.unlink(temp_path)
                
        except Exception as e:
            logging.error(f"Speech synthesis failed: {str(e)}")
            raise
    
    def _find_voice(self, language: SpeechLanguage, gender: VoiceGender) -> Optional[str]:
        """Find appropriate voice for language and gender"""
        try:
            if not self.engine:
                return None
            
            voices = self.engine.getProperty('voices')
            
            # Language preferences
            lang_keywords = {
                SpeechLanguage.THAI: ['thai', 'th'],
                SpeechLanguage.ENGLISH: ['english', 'en', 'us', 'gb']
            }
            
            # Gender preferences
            gender_keywords = {
                VoiceGender.FEMALE: ['female', 'woman', 'girl'],
                VoiceGender.MALE: ['male', 'man', 'boy']
            }
            
            best_voice = None
            best_score = 0
            
            for voice in voices:
                score = 0
                voice_info = voice.name.lower()
                
                # Language matching
                for keyword in lang_keywords.get(language, []):
                    if keyword in voice_info:
                        score += 10
                
                # Gender matching
                for keyword in gender_keywords.get(gender, []):
                    if keyword in voice_info:
                        score += 5
                
                if score > best_score:
                    best_score = score
                    best_voice = voice.id
            
            return best_voice
            
        except Exception as e:
            logging.error(f"Voice selection failed: {str(e)}")
            return None
    
    def _convert_audio_format(self, wav_data: bytes, target_format: AudioFormat) -> bytes:
        """Convert audio to target format"""
        try:
            audio = AudioSegment.from_wav(io.BytesIO(wav_data))
            
            output_buffer = io.BytesIO()
            audio.export(output_buffer, format=target_format.value)
            
            return output_buffer.getvalue()
            
        except Exception as e:
            logging.error(f"Audio format conversion failed: {str(e)}")
            return wav_data
    
    def _get_audio_duration(self, audio_data: bytes) -> float:
        """Get audio duration in seconds"""
        try:
            audio = AudioSegment.from_wav(io.BytesIO(audio_data))
            return len(audio) / 1000.0
        except:
            return 0.0
    
    def get_available_voices(self) -> List[Dict[str, Any]]:
        """Get list of available voices"""
        try:
            if not self.engine:
                return []
            
            voices = self.engine.getProperty('voices')
            
            voice_list = []
            for voice in voices:
                voice_info = {
                    'id': voice.id,
                    'name': voice.name,
                    'language': getattr(voice, 'languages', ['unknown'])[0] if hasattr(voice, 'languages') else 'unknown',
                    'gender': 'unknown'
                }
                
                # Try to determine gender from name
                name_lower = voice.name.lower()
                if any(keyword in name_lower for keyword in ['female', 'woman', 'girl']):
                    voice_info['gender'] = 'female'
                elif any(keyword in name_lower for keyword in ['male', 'man', 'boy']):
                    voice_info['gender'] = 'male'
                
                voice_list.append(voice_info)
            
            return voice_list
            
        except Exception as e:
            logging.error(f"Failed to get available voices: {str(e)}")
            return []

# Global engine instances
_stt_engine = None
_tts_engine = None

def get_stt_engine() -> SpeechToTextEngine:
    """Get global speech-to-text engine instance"""
    global _stt_engine
    if _stt_engine is None:
        _stt_engine = SpeechToTextEngine()
    return _stt_engine

def get_tts_engine() -> TextToSpeechEngine:
    """Get global text-to-speech engine instance"""
    global _tts_engine
    if _tts_engine is None:
        _tts_engine = TextToSpeechEngine()
    return _tts_engine

async def transcribe_voice_message(audio_data: bytes, language: SpeechLanguage = SpeechLanguage.AUTO) -> TranscriptionResult:
    """Convenience function to transcribe voice message"""
    engine = get_stt_engine()
    return await engine.transcribe_audio(audio_data, language)

async def generate_voice_response(text: str, language: SpeechLanguage = SpeechLanguage.THAI) -> SynthesisResult:
    """Convenience function to generate voice response"""
    engine = get_tts_engine()
    return await engine.synthesize_speech(text, language)

def init_voice_processing():
    """Initialize voice processing system"""
    stt_engine = get_stt_engine()
    tts_engine = get_tts_engine()
    logging.info("Voice processing system initialized")
    return stt_engine, tts_engine