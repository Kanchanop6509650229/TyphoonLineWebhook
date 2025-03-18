import re
import json
from typing import Union, List, Dict
import logging

class TokenCounter:
    def __init__(self, model_name="scb10x/scb10x-llama3-1-typhoon2-60256"):
        self.model_name = model_name
        self.history = {}
        
        # Try to load tiktoken for accurate counting
        try:
            import tiktoken
            # ใช้ encoding ที่ใกล้เคียงกับ Llama-based models
            # cl100k_base เป็น encoding ของ OpenAI ที่ใช้กับ GPT-3.5/4
            # แต่สำหรับโมเดล Llama อาจมีความแตกต่าง
            self.tokenizer = tiktoken.get_encoding("cl100k_base")
            self.use_tiktoken = True
            logging.info("Using tiktoken for token counting (approximation for Llama models)")
        except (ImportError, ModuleNotFoundError):
            self.use_tiktoken = False
            logging.info("Tiktoken not found, using approximate token counting")
    
    def count_tokens(self, text: Union[str, List[str]]) -> Union[int, List[int]]:
        """Count tokens in text or list of texts"""
        if isinstance(text, list):
            return [self._count_single_text(t) for t in text]
        return self._count_single_text(text)
    
    def _count_single_text(self, text: str) -> int:
        """Count tokens in a single text string"""
        if not text:
            return 0
            
        # Use cached value if available
        text_hash = hash(text[:100])
        if text_hash in self.history:
            return self.history[text_hash]
            
        # Use tiktoken if available
        if self.use_tiktoken:
            token_count = len(self.tokenizer.encode(text))
            
            # Apply adjustment factor for Thai language for Llama models
            # Thai in Llama models often uses more tokens than in OpenAI models
            if any(re.findall(r'[\u0E00-\u0E7F]', text)):
                # If text contains Thai characters, apply a small adjustment
                # This is an approximation based on empirical testing
                thai_chars_ratio = len(re.findall(r'[\u0E00-\u0E7F]', text)) / len(text)
                adjustment = 1.0 + (0.2 * thai_chars_ratio)  # Up to 20% adjustment based on Thai content
                token_count = int(token_count * adjustment)
        else:
            # Advanced multi-language token estimator
            # Remove excess whitespace
            text = re.sub(r'\s+', ' ', text.strip())
            
            # Handle Thai, English, and other characters
            thai_chars = len(re.findall(r'[\u0E00-\u0E7F]', text))
            english_words = len(re.findall(r'[a-zA-Z]+', text))
            numbers = len(re.findall(r'[0-9]+', text))
            symbols = len(re.findall(r'[^\w\s\u0E00-\u0E7F]', text))
            
            # Llama and Thai-specific models typically use more tokens for Thai text
            # Thai is approximately 1.2 tokens per character for Llama models
            # English is approximately 0.6 tokens per word for Llama models
            token_count = int((thai_chars * 1.2) + (english_words * 0.6) + numbers + symbols)
            token_count = max(1, token_count)  # Ensure at least 1 token
        
        # Cache result (limited to 1000 items)
        if len(self.history) > 1000:
            self.history.clear()
        self.history[text_hash] = token_count
        
        return token_count
    
    def count_message_tokens(self, messages):
        """Count tokens in a complete message array for API calls"""
        if not messages:
            return 0
            
        # Together.ai / Llama message format overhead
        # Each message has overhead tokens based on role and formatting
        base_tokens = 4  # Base overhead for chat completion
        
        for message in messages:
            # Add content tokens
            content = message.get("content", "")
            content_tokens = self.count_tokens(content)
            base_tokens += content_tokens
            
            # Add role overhead (similar to Llama models)
            # Each role adds approximately 4 tokens
            role = message.get("role", "")
            base_tokens += 4
            
            # Add extra tokens for system message (which may be treated differently)
            if role == "system":
                base_tokens += 2
                
        # Add safety margin for Together.ai specifics
        base_tokens = int(base_tokens * 1.05)  # Add 5% safety margin
        
        return base_tokens