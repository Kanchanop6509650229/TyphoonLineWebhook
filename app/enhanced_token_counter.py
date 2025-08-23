import re
import json
import functools
import psutil
import threading
import time
from typing import Union, List, Dict, Any, Optional, Tuple
import logging
from collections import OrderedDict
from datetime import datetime, timedelta

class EnhancedLRUCache:
    """
    Enhanced LRU cache with memory pressure monitoring and intelligent cleanup
    """
    def __init__(self, capacity: int = 1000, memory_threshold: float = 0.8):
        self.cache = OrderedDict()
        self.capacity = capacity
        self.memory_threshold = memory_threshold  # Memory usage threshold (80%)
        self.access_count = {}
        self.access_timestamps = {}
        self.lock = threading.RLock()
        self.cleanup_interval = 300  # 5 minutes
        self.last_cleanup = time.time()
        
        # Performance metrics
        self.hit_count = 0
        self.miss_count = 0
        self.eviction_count = 0
        self.memory_cleanup_count = 0
        
    def get(self, key: Any) -> Optional[Any]:
        """Get item from cache with memory pressure awareness"""
        with self.lock:
            # Check if cleanup is needed
            self._cleanup_if_needed()
            
            if key not in self.cache:
                self.miss_count += 1
                return None
            
            # Move to end (mark as recently used)
            self.cache.move_to_end(key)
            
            # Update access tracking
            self.access_count[key] = self.access_count.get(key, 0) + 1
            self.access_timestamps[key] = time.time()
            
            self.hit_count += 1
            return self.cache[key]

    def put(self, key: Any, value: Any) -> None:
        """Add item to cache with intelligent eviction"""
        with self.lock:
            # Check memory pressure first
            if self._is_memory_pressure():
                self._emergency_cleanup()
            
            # If key exists, update and move to end
            if key in self.cache:
                self.cache.move_to_end(key)
            else:
                # Add new item
                # Remove oldest items if capacity exceeded
                while len(self.cache) >= self.capacity:
                    self._evict_least_valuable()
            
            self.cache[key] = value
            self.access_count[key] = self.access_count.get(key, 0) + 1
            self.access_timestamps[key] = time.time()
    
    def _cleanup_if_needed(self) -> None:
        """Perform cleanup if enough time has passed"""
        current_time = time.time()
        if current_time - self.last_cleanup > self.cleanup_interval:
            self._scheduled_cleanup()
            self.last_cleanup = current_time
    
    def _is_memory_pressure(self) -> bool:
        """Check if system is under memory pressure"""
        try:
            memory = psutil.virtual_memory()
            return memory.percent / 100.0 > self.memory_threshold
        except Exception:
            # If we can't check memory, assume no pressure
            return False
    
    def _emergency_cleanup(self) -> None:
        """Perform emergency cleanup when memory pressure is high"""
        logging.warning("TokenCounter cache performing emergency cleanup due to memory pressure")
        
        # Remove 25% of least valuable items
        items_to_remove = max(1, len(self.cache) // 4)
        
        # Sort by value score (access frequency / time since last access)
        current_time = time.time()
        items_with_scores = []
        
        for key in self.cache:
            access_freq = self.access_count.get(key, 1)
            time_since_access = current_time - self.access_timestamps.get(key, current_time)
            # Lower score = less valuable
            score = access_freq / max(1, time_since_access / 3600)  # normalize by hours
            items_with_scores.append((key, score))
        
        # Sort by score (ascending - least valuable first)
        items_with_scores.sort(key=lambda x: x[1])
        
        # Remove least valuable items
        for key, _ in items_with_scores[:items_to_remove]:
            del self.cache[key]
            self.access_count.pop(key, None)
            self.access_timestamps.pop(key, None)
            self.eviction_count += 1
        
        self.memory_cleanup_count += 1
        logging.info(f"Emergency cleanup removed {items_to_remove} items from TokenCounter cache")
    
    def _scheduled_cleanup(self) -> None:
        """Perform scheduled cleanup of stale entries"""
        current_time = time.time()
        stale_threshold = 3600  # 1 hour
        
        stale_keys = [
            key for key, timestamp in self.access_timestamps.items()
            if current_time - timestamp > stale_threshold
        ]
        
        for key in stale_keys:
            if key in self.cache:
                del self.cache[key]
                self.access_count.pop(key, None)
                self.access_timestamps.pop(key, None)
                self.eviction_count += 1
        
        if stale_keys:
            logging.info(f"Scheduled cleanup removed {len(stale_keys)} stale items from TokenCounter cache")
    
    def _evict_least_valuable(self) -> None:
        """Evict the least valuable item using LRU + access frequency"""
        if not self.cache:
            return
        
        # For capacity-based eviction, use LRU (first item)
        oldest_key = next(iter(self.cache))
        del self.cache[oldest_key]
        self.access_count.pop(oldest_key, None)
        self.access_timestamps.pop(oldest_key, None)
        self.eviction_count += 1
    
    def get_stats(self) -> Dict[str, Any]:
        """Get cache performance statistics"""
        with self.lock:
            total_requests = self.hit_count + self.miss_count
            hit_rate = (self.hit_count / total_requests) if total_requests > 0 else 0
            
            return {
                'capacity': self.capacity,
                'current_size': len(self.cache),
                'hit_count': self.hit_count,
                'miss_count': self.miss_count,
                'hit_rate': hit_rate,
                'eviction_count': self.eviction_count,
                'memory_cleanup_count': self.memory_cleanup_count,
                'memory_threshold': self.memory_threshold,
                'last_cleanup': datetime.fromtimestamp(self.last_cleanup).isoformat()
            }
    
    def clear(self) -> None:
        """Clear all cache entries"""
        with self.lock:
            self.cache.clear()
            self.access_count.clear()
            self.access_timestamps.clear()
    
    def __len__(self) -> int:
        return len(self.cache)

class EnhancedTokenCounter:
    """
    Enhanced token counter for Thai and English text with LLM models
    Includes memory management and performance monitoring
    """
    # Thai character range for regex
    THAI_CHAR_RANGE = r'[\u0E00-\u0E7F]'

    def __init__(self, model_name: str = "scb10x/scb10x-llama3-1-typhoon2-60256", cache_size: int = 2000, memory_threshold: float = 0.75):
        """
        Initialize the enhanced token counter

        Args:
            model_name: Name of the model to count tokens for
            cache_size: Size of the LRU cache for token counts (increased default)
            memory_threshold: Memory usage threshold for cleanup (75%)
        """
        self.model_name = model_name
        self.cache = EnhancedLRUCache(cache_size, memory_threshold)
        self.thai_pattern = re.compile(self.THAI_CHAR_RANGE)
        self.english_pattern = re.compile(r'[a-zA-Z]+')
        self.number_pattern = re.compile(r'[0-9]+')
        self.symbol_pattern = re.compile(r'[^\w\s\u0E00-\u0E7F]')
        
        # Performance tracking
        self.total_tokens_counted = 0
        self.total_requests = 0
        self.average_processing_time = 0.0
        self.lock = threading.RLock()

        # Precompile regex patterns for better performance
        self.whitespace_pattern = re.compile(r'\s+')

        # Try to load tiktoken for accurate counting
        try:
            import tiktoken
            # Use encoding closest to Llama-based models
            # cl100k_base is OpenAI's encoding used with GPT-3.5/4
            self.tokenizer = tiktoken.get_encoding("cl100k_base")
            self.use_tiktoken = True
            logging.info("Using tiktoken for token counting (approximation for Llama models)")
        except (ImportError, ModuleNotFoundError):
            self.use_tiktoken = False
            logging.info("Tiktoken not found, using approximate token counting")

    def count_tokens(self, text: Union[str, List[str]]) -> Union[int, List[int]]:
        """
        Count tokens in text or list of texts

        Args:
            text: Single string or list of strings to count tokens for

        Returns:
            Token count(s) for the input text(s)
        """
        if isinstance(text, list):
            return [self._count_single_text(t) for t in text]
        return self._count_single_text(text)

    def _count_single_text(self, text: str) -> int:
        """
        Count tokens in a single text string with enhanced caching and performance tracking

        Args:
            text: Text string to count tokens for

        Returns:
            Number of tokens in the text
        """
        if not text:
            return 0

        start_time = time.time()
        
        with self.lock:
            self.total_requests += 1
        
        # Use a more robust hash for cache key
        # Using first 100 chars + length as a reasonable compromise
        text_key = hash((text[:100], len(text)))

        # Check cache first
        cached_count = self.cache.get(text_key)
        if cached_count is not None:
            return cached_count

        # Calculate token count based on available method
        if self.use_tiktoken:
            token_count = self._count_with_tiktoken(text)
        else:
            token_count = self._count_with_heuristics(text)

        # Cache the result
        self.cache.put(text_key, token_count)
        
        # Update performance metrics
        processing_time = time.time() - start_time
        with self.lock:
            self.total_tokens_counted += token_count
            # Update rolling average
            self.average_processing_time = (
                (self.average_processing_time * (self.total_requests - 1) + processing_time) / 
                self.total_requests
            )

        return token_count

    def _count_with_tiktoken(self, text: str) -> int:
        """
        Count tokens using tiktoken with Thai language adjustment

        Args:
            text: Text to count tokens for

        Returns:
            Adjusted token count
        """
        # Get base token count from tiktoken
        token_count = len(self.tokenizer.encode(text))

        # Apply consistent adjustment for Thai text
        has_thai = bool(self.thai_pattern.search(text))
        if has_thai:
            # Calculate Thai character ratio more efficiently
            thai_chars = len(self.thai_pattern.findall(text))
            thai_ratio = thai_chars / len(text)

            # Apply adjustment based on Thai content ratio
            # More Thai content = higher adjustment
            adjustment = 1.0 + (0.2 * thai_ratio)
            token_count = int(token_count * adjustment)

        return token_count

    def _count_with_heuristics(self, text: str) -> int:
        """
        Count tokens using heuristics when tiktoken is not available

        Args:
            text: Text to count tokens for

        Returns:
            Estimated token count
        """
        # Normalize whitespace first
        text = self.whitespace_pattern.sub(' ', text.strip())

        # Count different character types
        thai_chars = len(self.thai_pattern.findall(text))
        english_words = len(self.english_pattern.findall(text))
        numbers = len(self.number_pattern.findall(text))
        symbols = len(self.symbol_pattern.findall(text))

        # Apply token estimation formula
        # Thai: ~1.2 tokens per character for Llama models
        # English: ~0.6 tokens per word for Llama models
        token_count = int((thai_chars * 1.2) + (english_words * 0.6) + numbers + symbols)

        # Ensure minimum token count
        return max(1, token_count)

    def count_message_tokens(self, messages: List[Dict[str, str]]) -> int:
        """
        Count tokens in a complete message array for API calls

        Args:
            messages: List of message dictionaries with 'role' and 'content'

        Returns:
            Total token count including formatting overhead
        """
        if not messages:
            return 0

        # Calculate base overhead and message content tokens
        total_tokens = self._calculate_message_tokens(messages)

        # Add safety margin for DeepSeek specifics (5%)
        total_tokens = int(total_tokens * 1.05)

        return total_tokens

    def _calculate_message_tokens(self, messages: List[Dict[str, str]]) -> int:
        """
        Helper method to calculate tokens for message array

        Args:
            messages: List of message dictionaries

        Returns:
            Total token count for the message array
        """
        total_tokens = 0
        
        # Base overhead for message structure
        total_tokens += 10  # JSON formatting overhead
        
        for message in messages:
            # Add role and content tokens
            role = message.get('role', '')
            content = message.get('content', '')
            
            # Role tokens (system, user, assistant)
            total_tokens += max(1, len(role.split()))
            
            # Content tokens
            total_tokens += self.count_tokens(content)
            
            # Message delimiter overhead
            total_tokens += 4
        
        return total_tokens

    def get_performance_stats(self) -> Dict[str, Any]:
        """
        Get comprehensive performance statistics
        
        Returns:
            Dictionary containing performance metrics
        """
        with self.lock:
            cache_stats = self.cache.get_stats()
            
            return {
                'model_name': self.model_name,
                'total_requests': self.total_requests,
                'total_tokens_counted': self.total_tokens_counted,
                'average_processing_time': self.average_processing_time,
                'use_tiktoken': self.use_tiktoken,
                'cache_stats': cache_stats,
                'average_tokens_per_request': (
                    self.total_tokens_counted / self.total_requests 
                    if self.total_requests > 0 else 0
                )
            }
    
    def clear_cache(self) -> None:
        """Clear the token counting cache"""
        self.cache.clear()
        logging.info("TokenCounter cache cleared")
    
    def optimize_cache(self) -> Dict[str, Any]:
        """
        Manually trigger cache optimization
        
        Returns:
            Dictionary with optimization results
        """
        initial_size = len(self.cache)
        initial_stats = self.cache.get_stats()
        
        # Force cleanup
        self.cache._scheduled_cleanup()
        
        final_size = len(self.cache)
        final_stats = self.cache.get_stats()
        
        optimization_result = {
            'initial_cache_size': initial_size,
            'final_cache_size': final_size,
            'items_removed': initial_size - final_size,
            'initial_hit_rate': initial_stats['hit_rate'],
            'final_hit_rate': final_stats['hit_rate']
        }
        
        logging.info(f"Cache optimization completed: {optimization_result}")
        return optimization_result

# For backward compatibility, create alias
TokenCounter = EnhancedTokenCounter