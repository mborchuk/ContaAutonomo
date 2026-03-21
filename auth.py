#!/usr/bin/env python3
"""
Authentication and Security Module
Handles user authentication, password management, and database encryption
"""

import os
import json
import hashlib
from werkzeug.security import generate_password_hash, check_password_hash
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.backends import default_backend
import base64


class AuthManager:
    """Manages authentication and encryption"""

    def __init__(self, config_file='instance/auth_config.json'):
        self.config_file = config_file
        self.config_dir = os.path.dirname(config_file)
        self.ensure_config_dir()

    def ensure_config_dir(self):
        """Ensure config directory exists"""
        if not os.path.exists(self.config_dir):
            os.makedirs(self.config_dir, exist_ok=True)

    def is_first_run(self):
        """Check if this is the first run (no password set)"""
        return not os.path.exists(self.config_file)

    def setup_password(self, password):
        """Setup password for first time"""
        if not self.is_first_run():
            raise Exception("Password already set. Use change_password instead.")

        # Generate salt
        salt = os.urandom(32)

        # Hash password for storage
        password_hash = generate_password_hash(password)

        # Save config
        config = {
            'password_hash': password_hash,
            'salt': base64.b64encode(salt).decode('utf-8'),
            'version': '1.0'
        }

        with open(self.config_file, 'w') as f:
            json.dump(config, f)

        return True

    def verify_password(self, password):
        """Verify password"""
        if self.is_first_run():
            return False

        with open(self.config_file, 'r') as f:
            config = json.load(f)

        return check_password_hash(config['password_hash'], password)

    def change_password(self, old_password, new_password):
        """Change password"""
        if not self.verify_password(old_password):
            raise Exception("Current password is incorrect")

        # Generate new salt
        salt = os.urandom(32)

        # Hash new password
        password_hash = generate_password_hash(new_password)

        # Update config
        config = {
            'password_hash': password_hash,
            'salt': base64.b64encode(salt).decode('utf-8'),
            'version': '1.0'
        }

        with open(self.config_file, 'w') as f:
            json.dump(config, f)

        return True

    def get_encryption_key(self, password):
        """Derive encryption key from password"""
        with open(self.config_file, 'r') as f:
            config = json.load(f)

        salt = base64.b64decode(config['salt'])

        # Derive key using PBKDF2HMAC
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=100000,
            backend=default_backend()
        )

        key = base64.urlsafe_b64encode(kdf.derive(password.encode()))
        return key

    def encrypt_file(self, file_path, password):
        """Encrypt a file with password"""
        key = self.get_encryption_key(password)
        fernet = Fernet(key)

        # Read file
        with open(file_path, 'rb') as f:
            data = f.read()

        # Encrypt
        encrypted_data = fernet.encrypt(data)

        # Write encrypted file
        with open(file_path + '.encrypted', 'wb') as f:
            f.write(encrypted_data)

        return file_path + '.encrypted'

    def decrypt_file(self, encrypted_file_path, password, output_path):
        """Decrypt a file with password"""
        key = self.get_encryption_key(password)
        fernet = Fernet(key)

        # Read encrypted file
        with open(encrypted_file_path, 'rb') as f:
            encrypted_data = f.read()

        # Decrypt
        try:
            decrypted_data = fernet.decrypt(encrypted_data)
        except Exception as e:
            raise Exception("Failed to decrypt file. Wrong password?")

        # Write decrypted file
        with open(output_path, 'wb') as f:
            f.write(decrypted_data)

        return output_path

    def get_db_uri(self, password):
        """Get database URI with encryption key"""
        # For SQLite with SQLCipher, we'll use a different approach
        # SQLCipher requires the key to be set via PRAGMA
        # We'll return the standard URI and handle encryption separately
        return 'sqlite:///invoices.db'

    def get_db_encryption_key(self, password):
        """Get database encryption key for SQLCipher"""
        # Generate a hex key from password
        key = self.get_encryption_key(password)
        # SQLCipher expects a hex key
        return hashlib.sha256(key).hexdigest()


# Global auth manager instance
auth_manager = AuthManager()
