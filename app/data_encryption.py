"""
Data encryption at rest system for TyphoonLineWebhook
Implements field-level encryption, key management, and secure data handling
"""
import os
import json
import base64
import hashlib
import secrets
import logging
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, Union, List, Tuple
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.backends import default_backend
from enum import Enum
import threading

class EncryptionLevel(Enum):
    """Encryption levels for different types of data"""
    NONE = "none"
    BASIC = "basic"  # Simple encryption for non-sensitive data
    STANDARD = "standard"  # Standard encryption for sensitive data
    HIGH = "high"  # High-security encryption for highly sensitive data

class KeyRotationStatus(Enum):
    """Key rotation status"""
    ACTIVE = "active"
    ROTATING = "rotating"
    DEPRECATED = "deprecated"
    REVOKED = "revoked"

class EncryptionKey:
    """Represents an encryption key with metadata"""
    
    def __init__(self, key_id: str, key_data: bytes, created_at: datetime, 
                 status: KeyRotationStatus = KeyRotationStatus.ACTIVE):
        self.key_id = key_id
        self.key_data = key_data
        self.created_at = created_at
        self.status = status
        self.last_used = datetime.now()
        self.usage_count = 0
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert key metadata to dictionary (excludes actual key data)"""
        return {
            'key_id': self.key_id,
            'created_at': self.created_at.isoformat(),
            'status': self.status.value,
            'last_used': self.last_used.isoformat(),
            'usage_count': self.usage_count
        }

class KeyManager:
    """
    Secure key management system with rotation support
    """
    
    def __init__(self, master_key: Optional[str] = None):
        """
        Initialize key manager
        
        Args:
            master_key: Master key for encrypting other keys (from environment or config)
        """
        self.master_key = master_key or self._generate_master_key()
        self.keys: Dict[str, EncryptionKey] = {}
        self.lock = threading.RLock()
        
        # Key rotation settings
        self.key_rotation_interval = timedelta(days=90)  # Rotate keys every 90 days
        self.max_key_usage = 1000000  # Maximum encryptions per key
        
        # Initialize with a default key
        self._initialize_default_keys()
    
    def _generate_master_key(self) -> str:
        """Generate a new master key"""
        # In production, this should come from a secure key management service
        master_key = os.getenv('ENCRYPTION_MASTER_KEY')
        if not master_key:
            # Generate a new master key (should be stored securely)
            master_key = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode('utf-8')
            logging.warning("Generated new master key - store this securely!")
            logging.warning(f"ENCRYPTION_MASTER_KEY={master_key}")
        
        return master_key
    
    def _initialize_default_keys(self) -> None:
        """Initialize default encryption keys"""
        with self.lock:
            # Create keys for different encryption levels
            for level in [EncryptionLevel.BASIC, EncryptionLevel.STANDARD, EncryptionLevel.HIGH]:
                key_id = f"{level.value}_default"
                if key_id not in self.keys:
                    self._create_new_key(key_id, level)
    
    def _create_new_key(self, key_id: str, level: EncryptionLevel) -> EncryptionKey:
        """Create a new encryption key"""
        # Generate key based on encryption level
        if level == EncryptionLevel.HIGH:
            key_size = 32  # 256-bit key
        elif level == EncryptionLevel.STANDARD:
            key_size = 32  # 256-bit key
        else:  # BASIC
            key_size = 32  # Still use 256-bit for consistency
        
        key_data = secrets.token_bytes(key_size)
        encryption_key = EncryptionKey(key_id, key_data, datetime.now())
        
        self.keys[key_id] = encryption_key
        logging.info(f"Created new encryption key: {key_id}")
        
        return encryption_key
    
    def get_key(self, key_id: str) -> Optional[EncryptionKey]:
        """Get encryption key by ID"""
        with self.lock:
            key = self.keys.get(key_id)
            if key and key.status == KeyRotationStatus.ACTIVE:
                key.last_used = datetime.now()
                key.usage_count += 1
                return key
            return None
    
    def get_active_key(self, level: EncryptionLevel) -> EncryptionKey:
        """Get the active key for an encryption level"""
        key_id = f"{level.value}_default"
        key = self.get_key(key_id)
        
        if not key or self._should_rotate_key(key):
            # Create new key or rotate existing one
            key = self._rotate_key(key_id, level)
        
        return key
    
    def _should_rotate_key(self, key: EncryptionKey) -> bool:
        """Check if key should be rotated"""
        age = datetime.now() - key.created_at
        return (
            age > self.key_rotation_interval or
            key.usage_count > self.max_key_usage
        )
    
    def _rotate_key(self, key_id: str, level: EncryptionLevel) -> EncryptionKey:
        """Rotate an encryption key"""
        with self.lock:
            old_key = self.keys.get(key_id)
            if old_key:
                old_key.status = KeyRotationStatus.DEPRECATED
            
            # Create new key with timestamped ID
            new_key_id = f"{level.value}_{int(datetime.now().timestamp())}"
            new_key = self._create_new_key(new_key_id, level)
            
            # Update the default key reference
            self.keys[key_id] = new_key
            
            logging.info(f"Rotated encryption key: {key_id} -> {new_key_id}")
            return new_key
    
    def list_keys(self) -> List[Dict[str, Any]]:
        """List all keys with their metadata"""
        with self.lock:
            return [key.to_dict() for key in self.keys.values()]

class DataEncryption:
    """
    Field-level data encryption system
    """
    
    def __init__(self, key_manager: KeyManager):
        """
        Initialize data encryption system
        
        Args:
            key_manager: Key manager instance
        """
        self.key_manager = key_manager
        self.field_encryption_config = self._load_field_config()
    
    def _load_field_config(self) -> Dict[str, EncryptionLevel]:
        """Load field encryption configuration"""
        return {
            # Highly sensitive fields
            'user_message': EncryptionLevel.HIGH,
            'bot_response': EncryptionLevel.HIGH,
            'email': EncryptionLevel.HIGH,
            'phone': EncryptionLevel.HIGH,
            'personal_info': EncryptionLevel.HIGH,
            
            # Standard sensitive fields
            'display_name': EncryptionLevel.STANDARD,
            'user_id': EncryptionLevel.STANDARD,
            'session_data': EncryptionLevel.STANDARD,
            'form_data': EncryptionLevel.STANDARD,
            
            # Basic fields
            'metadata': EncryptionLevel.BASIC,
            'preferences': EncryptionLevel.BASIC,
            'statistics': EncryptionLevel.BASIC,
        }
    
    def encrypt_field(self, field_name: str, value: Any, 
                     encryption_level: Optional[EncryptionLevel] = None) -> Tuple[str, str]:
        """
        Encrypt a field value
        
        Args:
            field_name: Name of the field
            value: Value to encrypt
            encryption_level: Override encryption level
            
        Returns:
            Tuple of (encrypted_value, key_id)
        """
        if value is None:
            return None, None
        
        # Determine encryption level
        level = encryption_level or self.field_encryption_config.get(field_name, EncryptionLevel.BASIC)
        
        if level == EncryptionLevel.NONE:
            return str(value), "none"
        
        # Get encryption key
        key = self.key_manager.get_active_key(level)
        
        # Convert value to string if needed
        if not isinstance(value, str):
            value = json.dumps(value, ensure_ascii=False)
        
        # Encrypt the value
        encrypted_value = self._encrypt_value(value, key, level)
        
        return encrypted_value, key.key_id
    
    def decrypt_field(self, encrypted_value: str, key_id: str, 
                     return_json: bool = False) -> Any:
        """
        Decrypt a field value
        
        Args:
            encrypted_value: Encrypted value
            key_id: Key ID used for encryption
            return_json: Whether to parse result as JSON
            
        Returns:
            Decrypted value
        """
        if not encrypted_value or key_id == "none":
            return encrypted_value
        
        # Get decryption key
        key = self.key_manager.get_key(key_id)
        if not key:
            raise ValueError(f"Encryption key not found: {key_id}")
        
        # Determine encryption level from key_id
        level = self._get_level_from_key_id(key_id)
        
        # Decrypt the value
        decrypted_value = self._decrypt_value(encrypted_value, key, level)
        
        # Parse JSON if requested
        if return_json:
            try:
                return json.loads(decrypted_value)
            except json.JSONDecodeError:
                return decrypted_value
        
        return decrypted_value
    
    def _encrypt_value(self, value: str, key: EncryptionKey, level: EncryptionLevel) -> str:
        """Encrypt a value using the specified key and level"""
        value_bytes = value.encode('utf-8')
        
        if level == EncryptionLevel.HIGH:
            # Use AES-256-GCM for high security
            encrypted_data = self._encrypt_aes_gcm(value_bytes, key.key_data)
        else:
            # Use Fernet for standard/basic encryption
            fernet = Fernet(base64.urlsafe_b64encode(key.key_data))
            encrypted_data = fernet.encrypt(value_bytes)
        
        return base64.urlsafe_b64encode(encrypted_data).decode('utf-8')
    
    def _decrypt_value(self, encrypted_value: str, key: EncryptionKey, level: EncryptionLevel) -> str:
        """Decrypt a value using the specified key and level"""
        encrypted_data = base64.urlsafe_b64decode(encrypted_value.encode('utf-8'))
        
        if level == EncryptionLevel.HIGH:
            # Use AES-256-GCM for high security
            decrypted_data = self._decrypt_aes_gcm(encrypted_data, key.key_data)
        else:
            # Use Fernet for standard/basic encryption
            fernet = Fernet(base64.urlsafe_b64encode(key.key_data))
            decrypted_data = fernet.decrypt(encrypted_data)
        
        return decrypted_data.decode('utf-8')
    
    def _encrypt_aes_gcm(self, data: bytes, key: bytes) -> bytes:
        """Encrypt data using AES-256-GCM"""
        # Generate random IV
        iv = secrets.token_bytes(12)  # 96-bit IV for GCM
        
        # Create cipher
        cipher = Cipher(algorithms.AES(key), modes.GCM(iv), backend=default_backend())
        encryptor = cipher.encryptor()
        
        # Encrypt data
        ciphertext = encryptor.update(data) + encryptor.finalize()
        
        # Return IV + tag + ciphertext
        return iv + encryptor.tag + ciphertext
    
    def _decrypt_aes_gcm(self, encrypted_data: bytes, key: bytes) -> bytes:
        """Decrypt data using AES-256-GCM"""
        # Extract IV, tag, and ciphertext
        iv = encrypted_data[:12]
        tag = encrypted_data[12:28]
        ciphertext = encrypted_data[28:]
        
        # Create cipher
        cipher = Cipher(algorithms.AES(key), modes.GCM(iv, tag), backend=default_backend())
        decryptor = cipher.decryptor()
        
        # Decrypt data
        return decryptor.update(ciphertext) + decryptor.finalize()
    
    def _get_level_from_key_id(self, key_id: str) -> EncryptionLevel:
        """Get encryption level from key ID"""
        if key_id.startswith('high_'):
            return EncryptionLevel.HIGH
        elif key_id.startswith('standard_'):
            return EncryptionLevel.STANDARD
        else:
            return EncryptionLevel.BASIC
    
    def encrypt_record(self, record: Dict[str, Any]) -> Dict[str, Any]:
        """
        Encrypt sensitive fields in a database record
        
        Args:
            record: Database record dictionary
            
        Returns:
            Record with encrypted fields
        """
        encrypted_record = record.copy()
        encryption_metadata = {}
        
        for field_name, value in record.items():
            if field_name in self.field_encryption_config:
                encrypted_value, key_id = self.encrypt_field(field_name, value)
                encrypted_record[field_name] = encrypted_value
                encryption_metadata[field_name] = key_id
        
        # Store encryption metadata
        if encryption_metadata:
            encrypted_record['_encryption_keys'] = json.dumps(encryption_metadata)
        
        return encrypted_record
    
    def decrypt_record(self, record: Dict[str, Any]) -> Dict[str, Any]:
        """
        Decrypt encrypted fields in a database record
        
        Args:
            record: Encrypted database record
            
        Returns:
            Record with decrypted fields
        """
        if '_encryption_keys' not in record:
            return record
        
        decrypted_record = record.copy()
        
        try:
            encryption_metadata = json.loads(record['_encryption_keys'])
            
            for field_name, key_id in encryption_metadata.items():
                if field_name in record:
                    decrypted_value = self.decrypt_field(
                        record[field_name], 
                        key_id, 
                        return_json=field_name in ['form_data', 'metadata', 'preferences']
                    )
                    decrypted_record[field_name] = decrypted_value
            
            # Remove encryption metadata from final record
            del decrypted_record['_encryption_keys']
            
        except (json.JSONDecodeError, KeyError, ValueError) as e:
            logging.error(f"Failed to decrypt record: {str(e)}")
            # Return original record if decryption fails
            return record
        
        return decrypted_record

class EncryptedDatabaseManager:
    """
    Database manager with transparent field-level encryption
    """
    
    def __init__(self, db_manager, encryption_system: DataEncryption):
        """
        Initialize encrypted database manager
        
        Args:
            db_manager: Original database manager
            encryption_system: Data encryption system
        """
        self.db = db_manager
        self.encryption = encryption_system
    
    def insert_encrypted_record(self, table: str, record: Dict[str, Any]) -> int:
        """Insert record with automatic encryption"""
        encrypted_record = self.encryption.encrypt_record(record)
        
        # Build INSERT query
        columns = list(encrypted_record.keys())
        placeholders = ', '.join(['%s'] * len(columns))
        values = list(encrypted_record.values())
        
        query = f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders})"
        
        return self.db.execute_and_get_last_id(query, tuple(values))
    
    def select_and_decrypt_records(self, query: str, params: Optional[tuple] = None) -> List[Dict[str, Any]]:
        """Select records and automatically decrypt them"""
        encrypted_records = self.db.execute_query(query, params, dictionary=True)
        
        decrypted_records = []
        for record in encrypted_records:
            decrypted_record = self.encryption.decrypt_record(record)
            decrypted_records.append(decrypted_record)
        
        return decrypted_records
    
    def update_encrypted_record(self, table: str, record: Dict[str, Any], where_clause: str, where_params: tuple) -> int:
        """Update record with automatic encryption"""
        encrypted_record = self.encryption.encrypt_record(record)
        
        # Build UPDATE query
        set_clauses = [f"{column} = %s" for column in encrypted_record.keys()]
        values = list(encrypted_record.values()) + list(where_params)
        
        query = f"UPDATE {table} SET {', '.join(set_clauses)} WHERE {where_clause}"
        
        return self.db.execute_and_commit(query, tuple(values))

class DataProtectionAudit:
    """
    Audit system for data protection and encryption operations
    """
    
    def __init__(self, encryption_system: DataEncryption):
        """
        Initialize data protection audit system
        
        Args:
            encryption_system: Data encryption system to audit
        """
        self.encryption = encryption_system
        self.audit_log = []
        self.lock = threading.Lock()
    
    def audit_encryption_operation(self, operation: str, field_name: str, 
                                  key_id: str, success: bool, details: Dict[str, Any] = None) -> None:
        """Log encryption/decryption operation"""
        with self.lock:
            audit_entry = {
                'timestamp': datetime.now().isoformat(),
                'operation': operation,
                'field_name': field_name,
                'key_id': key_id,
                'success': success,
                'details': details or {}
            }
            
            self.audit_log.append(audit_entry)
            
            # Log to file
            logging.info(f"Encryption audit: {operation} - {field_name} - {success}")
    
    def get_audit_summary(self, hours: int = 24) -> Dict[str, Any]:
        """Get encryption audit summary"""
        cutoff_time = datetime.now() - timedelta(hours=hours)
        cutoff_iso = cutoff_time.isoformat()
        
        recent_entries = [
            entry for entry in self.audit_log
            if entry['timestamp'] > cutoff_iso
        ]
        
        # Count operations
        operation_counts = {}
        success_counts = {'success': 0, 'failure': 0}
        
        for entry in recent_entries:
            operation = entry['operation']
            operation_counts[operation] = operation_counts.get(operation, 0) + 1
            
            if entry['success']:
                success_counts['success'] += 1
            else:
                success_counts['failure'] += 1
        
        return {
            'period_hours': hours,
            'total_operations': len(recent_entries),
            'operation_breakdown': operation_counts,
            'success_rate': success_counts['success'] / max(1, len(recent_entries)),
            'key_usage': self.encryption.key_manager.list_keys()
        }

def create_encryption_system(master_key: Optional[str] = None) -> Tuple[KeyManager, DataEncryption, EncryptedDatabaseManager]:
    """
    Create and configure the complete encryption system
    
    Args:
        master_key: Master encryption key
        
    Returns:
        Tuple of (KeyManager, DataEncryption, EncryptedDatabaseManager)
    """
    key_manager = KeyManager(master_key)
    data_encryption = DataEncryption(key_manager)
    
    logging.info("Data encryption system initialized")
    
    return key_manager, data_encryption

# Export main classes and functions
__all__ = [
    'EncryptionLevel',
    'KeyRotationStatus',
    'KeyManager',
    'DataEncryption',
    'EncryptedDatabaseManager',
    'DataProtectionAudit',
    'create_encryption_system'
]