import re
import json
import functools
from typing import Union, List, Dict, Any, Optional, Tuple
import logging
from collections import OrderedDict

class LRUCache:
    """
    A simple LRU (Least Recently Used) cache implementation
    """
    def __init__(self, capacity: int = 1000):
        self.cache = OrderedDict()
        self.capacity = capacity

    def get(self, key: Any) -> Optional[Any]:
        """Get item from cache and move to end (most recently used)"""
        if key not in self.cache:
            return None
        # Move to end (mark as recently used)
        self.cache.move_to_end(key)
        return self.cache[key]

    def put(self, key: Any, value: Any) -> None:
        """Add item to cache or update existing item"""
        # If key exists, update and move to end
        if key in self.cache:
            self.cache.move_to_end(key)
        # Add new item
        self.cache[key] = value
        # Remove oldest item if capacity exceeded
        if len(self.cache) > self.capacity:
            self.cache.popitem(last=False)

class TokenCounter:
    """
    Efficient token counter for Thai and English text with LLM models
    """
    # Thai character range for regex
    THAI_CHAR_RANGE = r'[\u0E00-\u0E7F]'

    def __init__(self, model_name: str = "scb10x/scb10x-llama3-1-typhoon2-60256", cache_size: int = 2000):
        """
        Initialize the token counter

        Args:
            model_name: Name of the model to count tokens for
            cache_size: Size of the LRU cache for token counts
        """
        self.model_name = model_name
        self.cache = LRUCache(cache_size)
        self.thai_pattern = re.compile(self.THAI_CHAR_RANGE)
        self.english_pattern = re.compile(r'[a-zA-Z]+')
        self.number_pattern = re.compile(r'[0-9]+')
        self.symbol_pattern = re.compile(r'[^\w\s\u0E00-\u0E7F]')

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
        Count tokens in a single text string with efficient caching

        Args:
            text: Text string to count tokens for

        Returns:
            Number of tokens in the text
        """
        if not text:
            return 0

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

        # Add safety margin for Together.ai specifics (5%)
        total_tokens = int(total_tokens * 1.05)

        return total_tokens

    def _calculate_message_tokens(self, messages: List[Dict[str, str]]) -> int:
        """
        Helper method to calculate tokens for message array

        Args:
            messages: List of message dictionaries

        Returns:
            Token count before safety margin
        """
        # Base overhead for chat completion format
        base_tokens = 4

        # Process each message
        for message in messages:
            # Count content tokens
            content = message.get("content", "")
            content_tokens = self.count_tokens(content)
            base_tokens += content_tokens

            # Add role formatting overhead
            role = message.get("role", "")
            base_tokens += 4  # Standard role overhead

            # Extra tokens for system messages
            if role == "system":
                base_tokens += 2

        return base_tokens

    def estimate_completion_tokens(self, prompt_tokens: int, max_output_tokens: int = 500) -> Tuple[int, int]:
        """
        Estimate token usage for a completion request

        Args:
            prompt_tokens: Number of tokens in the prompt
            max_output_tokens: Maximum number of tokens in the output

        Returns:
            Tuple of (prompt_tokens, estimated_completion_tokens)
        """
        # Estimate actual completion tokens (usually less than max)
        estimated_completion = min(max_output_tokens, int(prompt_tokens * 0.7))

        return prompt_tokens, estimated_completion
