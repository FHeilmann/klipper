import base64
import uuid
import logging
import eventlet
from eventlet.green import os, time
from datetime import datetime, timedelta

bottle = eventlet.import_patched('bottle')
TOKEN_TIMEOUT = 5
CONNECTION_TIMEOUT = 3600
PRUNE_CHECK_TIME = 300
API_KEY_FILE = '.klippy_api_key'

def _read_api_key(path):
    api_file = os.path.join(path, API_KEY_FILE)
    if os.path.exists(api_file):
        with open(api_file, 'r') as f:
            api_key = f.read()
        return api_key
    # API Key file doesn't exist.  Generate
    # a new api key and create the file.
    logging.info(
        "[WEBSERVER]: No API Key file found, creating new one at:\n%s"
        % (api_file))
    return _create_api_key(path)

def _create_api_key(path):
    api_file = os.path.join(path, API_KEY_FILE)
    api_key = uuid.uuid4().hex
    with open(api_file, 'w') as f:
        f.write(api_key)
    return api_key

class ApiAuth:
    name = "api_auth"
    api = 2

    _current_instance = None

    @staticmethod
    def get_manager():
        return ApiAuth._current_instance

    def __init__(self):
        pass

    def setup(self, app):
        config = app.config
        self.api_key_path = config.get(
            "api_auth.api_key_path", os.path.expanduser("~"))
        self.auth_enabled = config.get("api_auth.enabled", True)
        self.trusted_ips = config.get("api_auth.trusted_ips", [])
        self.trusted_ranges = config.get("api_auth.trusted_ranges", [])
        self.trusted_connections = {}
        self.enable_cors = config.get("api_auth.enable_cors", False)
        self.api_key = _read_api_key(self.api_key_path)
        self.access_tokens = {}
        ApiAuth._current_instance = self
        self.prune_gthread = eventlet.spawn(self._prune_conn_handler)
        logging.info(
            "[WEBSERVER]: Authorization Plugin Initialized\n"
            "API Key Path: %s\n"
            "API Key: %s\n"
            "Auth Enabled: %s\n"
            "Cors Enabled: %s\n"
            "Trusted IPs:\n%s\n"
            "Trusted IP Ranges:\n%s" %
            (self.api_key_path, self.api_key, self.auth_enabled,
             self.enable_cors, ('\n').join(self.trusted_ips),
             ('\n').join(self.trusted_ranges)))

    def _prune_conn_handler(self):
        while True:
            eventlet.sleep(PRUNE_CHECK_TIME)
            cur_time = time.time()
            expired_conns = []
            for ip, access_time in self.trusted_connections.iteritems():
                if cur_time - access_time > CONNECTION_TIMEOUT:
                    expired_conns.append(ip)
            for ip in expired_conns:
                self.trusted_connections.pop(ip)
                logging.info(
                    "[WEBSERVER]: Trusted Connection Expired, IP: %s" % (ip))

    def _token_expire_handler(self, token):
        eventlet.sleep(TOKEN_TIMEOUT)
        self.access_tokens.pop(token)

    def is_enabled(self):
        return self.auth_enabled

    def get_api_key(self):
        return self.api_key

    def generate_api_key(self):
        self.api_key = _create_api_key(self.api_key_path)

    def get_access_token(self):
        token = base64.b32encode(os.urandom(20))
        self.access_tokens[token] = eventlet.spawn(
            self._token_expire_handler, token)
        return token

    def _check_access_token(self):
        token = bottle.request.params.get('token')
        if token in self.access_tokens:
            expire_thread = self.access_tokens.pop(token)
            expire_thread.kill()
            return True
        else:
            return False

    def _check_trusted_ips(self):
        ip = bottle.request.environ.get('REMOTE_ADDR')
        if ip is not None:
            if ip in self.trusted_connections:
                self.trusted_connections[ip] = time.time()
                return True
            elif ip in self.trusted_ips or \
                    ip[:ip.rfind('.')] in self.trusted_ranges:
                logging.info(
                    "[WEBSERVER]: Trusted Connection Detected, IP: %s"
                    % (ip))
                self.trusted_connections[ip] = time.time()
                return True
        return False

    def _check_authorized(self):
        # Check if IP is trusted
        if self._check_trusted_ips():
            return True

        # Check API Key Header
        key = bottle.request.headers.get("X-Api-Key")
        if key and key == self.api_key:
            return True

        # Check one-shot access token
        if self._check_access_token():
            return True
        return False

    def apply(self, callback, context):
        allow_unauthorized = context.config.get('api_auth_allow', False)

        def auth_wrapper(*args, **kwargs):
            # Check if this is a forwarded proxy request
            real_ip = bottle.request.headers.get("X-Real-IP")
            if real_ip:
                bottle.request.environ['REMOTE_ADDR'] = real_ip

            if self.auth_enabled and not allow_unauthorized:
                # Auth required, check before continuing
                if not self._check_authorized():
                    bottle.abort(401, "Unauthorized")

            if self.enable_cors:
                # set CORS headers
                bottle.response.headers['Access-Control-Allow-Origin'] = '*'
                bottle.response.headers['Access-Control-Allow-Methods'] = \
                    'GET, POST, PUT, OPTIONS'
                bottle.response.headers['Access-Control-Allow-Headers'] = \
                    'Origin, Accept, Content-Type, X-Requested-With, ' \
                    'X-CSRF-Token'

                if bottle.request.method != 'OPTIONS':
                    # OPTIONS does not require a response
                    return callback(*args, **kwargs)
            else:
                return callback(*args, **kwargs)

        return auth_wrapper

    def close(self):
        self.prune_gthread.kill()
