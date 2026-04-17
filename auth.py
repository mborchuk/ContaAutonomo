#!/usr/bin/env python3
"""
Authentication and Security Module

Provides:
- AuthProvider: abstract interface for pluggable auth backends
- PasswordAuthProvider: built-in password-based authentication
- AuthService: registry that manages multiple auth providers
- AuthManager: legacy encryption/password utilities (backward compat)
"""

import os
import json
import hashlib
import logging
from abc import ABC, abstractmethod
from werkzeug.security import generate_password_hash, check_password_hash
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.backends import default_backend
import base64

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Auth Provider Interface
# ---------------------------------------------------------------------------

class AuthResult:
    """Result of an authentication attempt."""

    def __init__(self, success, identity=None, error=None, redirect_url=None):
        self.success = success
        self.identity = identity or {}   # {'name': ..., 'email': ..., 'provider': ...}
        self.error = error               # human-readable error message
        self.redirect_url = redirect_url  # for OAuth — URL to redirect to

    def __bool__(self):
        return self.success


class AuthProvider(ABC):
    """Abstract interface for authentication providers.

    Modules can implement this to add Google, Azure AD, Cognito, SAML, etc.
    Each provider has a unique id, a display name, and handles its own
    authentication flow.

    Lifecycle:
      1. Module returns provider instances from get_auth_providers()
      2. AuthService registers them at startup
      3. Login page shows buttons/forms for each enabled provider
      4. On login attempt, AuthService delegates to the matching provider
      5. Provider returns AuthResult with user identity
    """

    @property
    @abstractmethod
    def provider_id(self) -> str:
        """Unique identifier, e.g. 'password', 'google', 'azure_ad'."""

    @property
    @abstractmethod
    def display_name(self) -> str:
        """Human-readable name shown on login page, e.g. 'Google Account'."""

    @property
    def icon(self) -> str:
        """Emoji or icon class for the login button."""
        return '🔑'

    @property
    def login_button_style(self) -> str:
        """Optional inline CSS for the login button."""
        return ''

    @property
    def is_external(self) -> bool:
        """True for OAuth/SAML providers that redirect to external IdP."""
        return False

    def is_configured(self) -> bool:
        """Return True if this provider is properly configured and ready."""
        return True

    def authenticate(self, request) -> AuthResult:
        """Authenticate a user from a Flask request (form POST or callback).

        For password-based: read form fields and verify.
        For OAuth: initiate redirect or handle callback.

        Args:
            request: Flask request object

        Returns:
            AuthResult with success/failure and user identity
        """
        return AuthResult(False, error='Not implemented')

    def get_login_form_html(self) -> str:
        """Return HTML snippet for this provider's login form/button.

        Rendered on the login page. For password providers, this is the
        password input field. For OAuth, this is a "Sign in with X" button.
        """
        return ''

    def get_callback_routes(self):
        """Return list of (rule, endpoint, view_func) for OAuth callbacks.

        Example:
            return [('/auth/google/callback', 'google_callback', self._handle_callback)]
        """
        return []

    def on_logout(self, session):
        """Called when user logs out. Clean up provider-specific session data."""
        pass


# ---------------------------------------------------------------------------
# Built-in Password Provider
# ---------------------------------------------------------------------------

class PasswordAuthProvider(AuthProvider):
    """Built-in password-based authentication (the default)."""

    def __init__(self, auth_manager):
        self._mgr = auth_manager

    @property
    def provider_id(self):
        return 'password'

    @property
    def display_name(self):
        return 'Password'

    @property
    def icon(self):
        return '🔒'

    def is_configured(self):
        return not self._mgr.is_first_run()

    def authenticate(self, request):
        password = request.form.get('password', '')
        if not password:
            return AuthResult(False, error='Password is required.')
        if self._mgr.verify_password(password):
            return AuthResult(True, identity={
                'name': 'Admin',
                'provider': 'password',
            })
        return AuthResult(False, error='Invalid password.')

    def get_login_form_html(self):
        return '''
        <div class="form-group">
            <label for="password">Password</label>
            <input type="password" id="password" name="password" required autofocus
                   style="width:100%;padding:12px;border:2px solid #ddd;border-radius:5px;font-size:16px;">
        </div>
        <button type="submit" name="auth_provider" value="password"
                class="btn" style="width:100%;padding:12px;background:linear-gradient(135deg,#667eea 0%,#764ba2 100%);color:#fff;border:none;border-radius:5px;font-size:16px;font-weight:bold;cursor:pointer;">
            Login
        </button>
        '''


# ---------------------------------------------------------------------------
# Auth Service — provider registry
# ---------------------------------------------------------------------------

class AuthService:
    """Manages registered auth providers.

    Used by auth_routes to:
    - List available providers for the login page
    - Delegate authentication to the correct provider
    - Handle provider-specific callbacks
    """

    def __init__(self):
        self._providers = {}  # provider_id -> AuthProvider

    def register(self, provider):
        """Register an auth provider."""
        pid = provider.provider_id
        if pid in self._providers:
            logger.warning('Auth provider %r already registered — replacing', pid)
        self._providers[pid] = provider
        logger.info('Auth provider registered: %s (%s)', pid, provider.display_name)

    def unregister(self, provider_id):
        """Remove an auth provider."""
        self._providers.pop(provider_id, None)

    def get(self, provider_id):
        """Get a provider by id."""
        return self._providers.get(provider_id)

    @property
    def providers(self):
        """All registered providers (dict)."""
        return dict(self._providers)

    def available_providers(self):
        """List of configured and ready providers."""
        return [p for p in self._providers.values() if p.is_configured()]

    def authenticate(self, provider_id, request):
        """Delegate authentication to a specific provider.

        Args:
            provider_id: which provider to use
            request: Flask request object

        Returns:
            AuthResult
        """
        provider = self._providers.get(provider_id)
        if not provider:
            return AuthResult(False, error=f'Unknown auth provider: {provider_id}')
        if not provider.is_configured():
            return AuthResult(False, error=f'{provider.display_name} is not configured.')
        return provider.authenticate(request)

    def get_callback_routes(self):
        """Collect callback routes from all providers (for OAuth flows)."""
        routes = []
        for provider in self._providers.values():
            routes.extend(provider.get_callback_routes())
        return routes

    def on_logout(self, session):
        """Notify all providers of logout."""
        for provider in self._providers.values():
            try:
                provider.on_logout(session)
            except Exception as e:
                logger.debug('Provider %s on_logout error: %s',
                             provider.provider_id, e)


# ---------------------------------------------------------------------------
# Legacy AuthManager (kept for backward compat — encryption, password mgmt)
# ---------------------------------------------------------------------------

class AuthManager:
    """Manages password storage and encryption keys.

    This class handles the low-level password hashing, config file management,
    and encryption key derivation. It does NOT handle the auth flow — that's
    delegated to AuthProvider implementations.
    """

    def __init__(self, config_file='instance/auth_config.json'):
        self.config_file = config_file
        self.config_dir = os.path.dirname(config_file)
        self.ensure_config_dir()

    def ensure_config_dir(self):
        if not os.path.exists(self.config_dir):
            os.makedirs(self.config_dir, exist_ok=True)

    def is_first_run(self):
        return not os.path.exists(self.config_file)

    def setup_password(self, password):
        if not self.is_first_run():
            raise Exception("Password already set. Use change_password instead.")
        salt = os.urandom(32)
        password_hash = generate_password_hash(password)
        config = {
            'password_hash': password_hash,
            'salt': base64.b64encode(salt).decode('utf-8'),
            'version': '1.0'
        }
        with open(self.config_file, 'w') as f:
            json.dump(config, f)
        return True

    def verify_password(self, password):
        if self.is_first_run():
            return False
        with open(self.config_file, 'r') as f:
            config = json.load(f)
        return check_password_hash(config['password_hash'], password)

    def change_password(self, old_password, new_password):
        if not self.verify_password(old_password):
            raise Exception("Current password is incorrect")
        salt = os.urandom(32)
        password_hash = generate_password_hash(new_password)
        config = {
            'password_hash': password_hash,
            'salt': base64.b64encode(salt).decode('utf-8'),
            'version': '1.0'
        }
        with open(self.config_file, 'w') as f:
            json.dump(config, f)
        return True

    def get_encryption_key(self, password):
        with open(self.config_file, 'r') as f:
            config = json.load(f)
        salt = base64.b64decode(config['salt'])
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(), length=32, salt=salt,
            iterations=100000, backend=default_backend()
        )
        return base64.urlsafe_b64encode(kdf.derive(password.encode()))

    def encrypt_file(self, file_path, password):
        key = self.get_encryption_key(password)
        fernet = Fernet(key)
        with open(file_path, 'rb') as f:
            data = f.read()
        encrypted_data = fernet.encrypt(data)
        with open(file_path + '.encrypted', 'wb') as f:
            f.write(encrypted_data)
        return file_path + '.encrypted'

    def decrypt_file(self, encrypted_file_path, password, output_path):
        key = self.get_encryption_key(password)
        fernet = Fernet(key)
        with open(encrypted_file_path, 'rb') as f:
            encrypted_data = f.read()
        try:
            decrypted_data = fernet.decrypt(encrypted_data)
        except Exception:
            raise Exception("Failed to decrypt file. Wrong password?")
        with open(output_path, 'wb') as f:
            f.write(decrypted_data)
        return output_path

    def get_db_uri(self, password):
        return 'sqlite:///invoices.db'

    def get_db_encryption_key(self, password):
        key = self.get_encryption_key(password)
        return hashlib.sha256(key).hexdigest()


# Global instances
auth_manager = AuthManager()
auth_service = AuthService()

# Register built-in password provider
auth_service.register(PasswordAuthProvider(auth_manager))
