"""
Audio message processing pipeline with quality optimization for TyphoonLineWebhook
"""
import os
import io
import json
import logging
import asyncio
import tempfile
from datetime import datetime
from typing import Dict, Any, List, Optional, Tuple
from dataclasses import dataclass
from enum import Enum
import threading
import uuid
from pydub import AudioSegment
from pydub.effects import normalize, compress_dynamic_range
import numpy as np

class ProcessingStatus(Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"

@dataclass
class AudioJob:
    job_id: str
    user_id: str
    input_audio: bytes
    status: ProcessingStatus
    created_at: datetime
    completed_at: Optional[datetime] = None
    transcription: Optional[str] = None
    enhanced_audio: Optional[bytes] = None
    error_message: Optional[str] = None

class AudioQualityAnalyzer:
    """Analyze and optimize audio quality"""
    
    def __init__(self):
        self.optimal_sample_rate = 16000
        self.optimal_bit_depth = 16
        self.noise_threshold = -40  # dB
        self.silence_threshold = -50  # dB
    
    def analyze_quality(self, audio: AudioSegment) -> Dict[str, Any]:
        """Analyze audio quality metrics"""
        try:
            # Basic metrics
            duration = len(audio) / 1000.0
            sample_rate = audio.frame_rate
            channels = audio.channels
            
            # Volume analysis
            dBFS = audio.dBFS
            max_dBFS = audio.max_dBFS
            
            # Silence detection
            silence_ratio = self._calculate_silence_ratio(audio)
            
            # Noise estimation
            noise_level = self._estimate_noise_level(audio)
            
            # SNR estimation
            snr = max_dBFS - noise_level if noise_level > -100 else 60
            
            # Overall quality score (0-100)
            quality_score = self._calculate_quality_score(
                duration, sample_rate, dBFS, silence_ratio, snr
            )
            
            return {
                'duration': duration,
                'sample_rate': sample_rate,
                'channels': channels,
                'dBFS': dBFS,
                'max_dBFS': max_dBFS,
                'silence_ratio': silence_ratio,
                'noise_level': noise_level,
                'snr': snr,
                'quality_score': quality_score,
                'recommendations': self._generate_recommendations(audio)
            }
            
        except Exception as e:
            logging.error(f"Quality analysis failed: {str(e)}")
            return {'error': str(e)}
    
    def _calculate_silence_ratio(self, audio: AudioSegment) -> float:
        """Calculate ratio of silence in audio"""
        try:
            # Detect silence segments
            silence_ranges = []
            chunk_size = 100  # ms
            
            for i in range(0, len(audio), chunk_size):
                chunk = audio[i:i+chunk_size]
                if chunk.dBFS < self.silence_threshold:
                    silence_ranges.append((i, i+chunk_size))
            
            total_silence = sum(end - start for start, end in silence_ranges)
            return total_silence / len(audio)
            
        except:
            return 0.0
    
    def _estimate_noise_level(self, audio: AudioSegment) -> float:
        """Estimate background noise level"""
        try:
            # Find quiet segments for noise estimation
            chunk_size = 500  # ms
            noise_samples = []
            
            for i in range(0, len(audio), chunk_size):
                chunk = audio[i:i+chunk_size]
                if chunk.dBFS < self.noise_threshold:
                    noise_samples.append(chunk.dBFS)
            
            return np.mean(noise_samples) if noise_samples else -60
            
        except:
            return -60
    
    def _calculate_quality_score(self, duration: float, sample_rate: int, 
                               dBFS: float, silence_ratio: float, snr: float) -> float:
        """Calculate overall quality score"""
        score = 50  # Base score
        
        # Duration scoring
        if 1 <= duration <= 60:
            score += 20
        elif duration > 0.5:
            score += 10
        
        # Sample rate scoring
        if sample_rate >= 16000:
            score += 15
        elif sample_rate >= 8000:
            score += 10
        
        # Volume scoring
        if -30 <= dBFS <= -10:
            score += 10
        elif dBFS > -40:
            score += 5
        
        # Silence ratio scoring
        if silence_ratio < 0.3:
            score += 10
        elif silence_ratio < 0.5:
            score += 5
        
        # SNR scoring
        if snr > 20:
            score += 15
        elif snr > 10:
            score += 10
        elif snr > 5:
            score += 5
        
        return min(100, max(0, score))
    
    def _generate_recommendations(self, audio: AudioSegment) -> List[str]:
        """Generate improvement recommendations"""
        recommendations = []
        
        if audio.frame_rate < 16000:
            recommendations.append("Increase sample rate to 16kHz for better recognition")
        
        if audio.dBFS < -40:
            recommendations.append("Audio level too low - increase recording volume")
        
        if audio.dBFS > -6:
            recommendations.append("Audio may be clipping - reduce recording volume")
        
        if len(audio) < 500:
            recommendations.append("Audio too short - record longer messages")
        
        if len(audio) > 300000:  # 5 minutes
            recommendations.append("Audio too long - consider shorter messages")
        
        return recommendations

class AudioEnhancer:
    """Enhance audio quality for better processing"""
    
    def __init__(self):
        self.target_sample_rate = 16000
        self.target_loudness = -23.0  # LUFS
    
    def enhance_audio(self, audio: AudioSegment, quality_analysis: Dict[str, Any]) -> AudioSegment:
        """Enhance audio based on quality analysis"""
        try:
            enhanced = audio
            
            # Normalize sample rate
            if enhanced.frame_rate != self.target_sample_rate:
                enhanced = enhanced.set_frame_rate(self.target_sample_rate)
            
            # Convert to mono for speech processing
            if enhanced.channels > 1:
                enhanced = enhanced.set_channels(1)
            
            # Remove leading/trailing silence
            enhanced = enhanced.strip_silence(silence_len=500, silence_thresh=-40)
            
            # Normalize volume
            enhanced = normalize(enhanced, headroom=3.0)
            
            # Apply noise reduction (simple high-pass filter)
            enhanced = enhanced.high_pass_filter(80)
            
            # Apply dynamic range compression for speech
            enhanced = compress_dynamic_range(
                enhanced, threshold=-20.0, ratio=4.0, attack=5.0, release=50.0
            )
            
            # Final volume adjustment
            target_dBFS = -16.0
            current_dBFS = enhanced.dBFS
            if current_dBFS < -30:
                gain = target_dBFS - current_dBFS
                enhanced = enhanced + gain
            
            return enhanced
            
        except Exception as e:
            logging.error(f"Audio enhancement failed: {str(e)}")
            return audio

class AudioPipeline:
    """Main audio processing pipeline"""
    
    def __init__(self, stt_engine=None, tts_engine=None):
        self.stt_engine = stt_engine
        self.tts_engine = tts_engine
        self.quality_analyzer = AudioQualityAnalyzer()
        self.enhancer = AudioEnhancer()
        
        # Job tracking
        self.active_jobs = {}
        self.job_history = []
        self.lock = threading.Lock()
        
        # Processing queue
        self.processing_queue = asyncio.Queue(maxsize=100)
        self.worker_tasks = []
        self._start_workers()
    
    def _start_workers(self):
        """Start background workers"""
        for i in range(3):  # 3 worker tasks
            task = asyncio.create_task(self._worker())
            self.worker_tasks.append(task)
    
    async def _worker(self):
        """Background worker for processing audio"""
        while True:
            try:
                job = await self.processing_queue.get()
                await self._process_job(job)
                self.processing_queue.task_done()
            except Exception as e:
                logging.error(f"Worker error: {str(e)}")
                await asyncio.sleep(1)
    
    async def process_audio_message(self, audio_data: bytes, user_id: str,
                                   language: str = 'auto') -> str:
        """Process incoming audio message"""
        try:
            # Create processing job
            job = AudioJob(
                job_id=str(uuid.uuid4()),
                user_id=user_id,
                input_audio=audio_data,
                status=ProcessingStatus.PENDING,
                created_at=datetime.now()
            )
            
            # Add to tracking
            with self.lock:
                self.active_jobs[job.job_id] = job
            
            # Add to processing queue
            await self.processing_queue.put(job)
            
            # Wait for completion (with timeout)
            timeout = 30  # 30 seconds
            start_time = datetime.now()
            
            while (datetime.now() - start_time).seconds < timeout:
                with self.lock:
                    current_job = self.active_jobs.get(job.job_id)
                    if current_job and current_job.status == ProcessingStatus.COMPLETED:
                        return current_job.transcription or ""
                    elif current_job and current_job.status == ProcessingStatus.FAILED:
                        raise Exception(current_job.error_message or "Processing failed")
                
                await asyncio.sleep(0.5)
            
            raise Exception("Processing timeout")
            
        except Exception as e:
            logging.error(f"Audio processing failed: {str(e)}")
            return ""
    
    async def _process_job(self, job: AudioJob):
        """Process individual audio job"""
        try:
            # Update status
            job.status = ProcessingStatus.PROCESSING
            
            # Load audio
            audio = AudioSegment.from_file(io.BytesIO(job.input_audio))
            
            # Analyze quality
            quality_analysis = self.quality_analyzer.analyze_quality(audio)
            
            # Enhance audio
            enhanced_audio = self.enhancer.enhance_audio(audio, quality_analysis)
            
            # Convert to bytes for STT
            enhanced_buffer = io.BytesIO()
            enhanced_audio.export(enhanced_buffer, format="wav")
            enhanced_bytes = enhanced_buffer.getvalue()
            
            # Store enhanced audio
            job.enhanced_audio = enhanced_bytes
            
            # Transcribe if STT engine available
            if self.stt_engine:
                from .voice_processing import SpeechLanguage
                
                lang_map = {'th': SpeechLanguage.THAI, 'en': SpeechLanguage.ENGLISH}
                speech_lang = lang_map.get('auto', SpeechLanguage.AUTO)
                
                result = await self.stt_engine.transcribe_audio(enhanced_bytes, speech_lang)
                job.transcription = result.text
            
            # Complete job
            job.status = ProcessingStatus.COMPLETED
            job.completed_at = datetime.now()
            
            # Move to history
            with self.lock:
                self.job_history.append(job)
                if len(self.job_history) > 1000:
                    self.job_history = self.job_history[-1000:]
            
        except Exception as e:
            job.status = ProcessingStatus.FAILED
            job.error_message = str(e)
            job.completed_at = datetime.now()
            logging.error(f"Job processing failed: {str(e)}")
    
    def get_job_status(self, job_id: str) -> Optional[AudioJob]:
        """Get job status"""
        with self.lock:
            return self.active_jobs.get(job_id)
    
    def get_processing_stats(self) -> Dict[str, Any]:
        """Get processing statistics"""
        with self.lock:
            active_count = len(self.active_jobs)
            completed_jobs = [j for j in self.job_history if j.status == ProcessingStatus.COMPLETED]
            failed_jobs = [j for j in self.job_history if j.status == ProcessingStatus.FAILED]
            
            return {
                'active_jobs': active_count,
                'completed_jobs': len(completed_jobs),
                'failed_jobs': len(failed_jobs),
                'success_rate': len(completed_jobs) / max(len(self.job_history), 1) * 100,
                'queue_size': self.processing_queue.qsize()
            }

# Global pipeline instance
_audio_pipeline = None

def get_audio_pipeline(stt_engine=None, tts_engine=None) -> AudioPipeline:
    """Get global audio pipeline instance"""
    global _audio_pipeline
    if _audio_pipeline is None:
        _audio_pipeline = AudioPipeline(stt_engine, tts_engine)
    return _audio_pipeline

async def process_voice_message(audio_data: bytes, user_id: str) -> str:
    """Convenience function to process voice message"""
    from .voice_processing import get_stt_engine, get_tts_engine
    
    stt_engine = get_stt_engine()
    tts_engine = get_tts_engine()
    pipeline = get_audio_pipeline(stt_engine, tts_engine)
    
    return await pipeline.process_audio_message(audio_data, user_id)

def init_audio_pipeline():
    """Initialize audio processing pipeline"""
    from .voice_processing import get_stt_engine, get_tts_engine
    
    stt_engine = get_stt_engine()
    tts_engine = get_tts_engine()
    pipeline = get_audio_pipeline(stt_engine, tts_engine)
    
    logging.info("Audio processing pipeline initialized")
    return pipeline