# API Key Based Authorization
#
# Copyright (C) 2020 Eric Callahan <arksine.code@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license
import base64
import os
import time
import logging
import tornado
from tornado.ioloop import IOLoop, PeriodicCallback

TOKEN_TIMEOUT = 5
CONNECTION_TIMEOUT = 3600
PRUNE_CHECK_TIME = 300 * 1000

class AuthManager:
    def __init__(self, config):
        self.api_key = config.get('api_key')
        self.auth_enabled = config.get("require_auth", True)
        self.trusted_ips = config.get("trusted_ips", [])
        self.trusted_ranges = config.get("trusted_ranges", [])
        self.trusted_connections = {}
        self.access_tokens = {}
        self.prune_handler = PeriodicCallback(
            self._prune_conn_handler, PRUNE_CHECK_TIME)
        self.prune_handler.start()
        logging.info(
            "[WEBSERVER]: Authorization Plugin Initialized\n"
            "Auth Enabled: %s\n"
            "Trusted IPs:\n%s\n"
            "Trusted IP Ranges:\n%s" %
            (self.auth_enabled,
             ('\n').join(self.trusted_ips),
             ('\n').join(self.trusted_ranges)))

    def _prune_conn_handler(self):
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
        self.access_tokens.pop(token)

    def is_enabled(self):
        return self.auth_enabled

    def get_access_token(self):
        token = base64.b32encode(os.urandom(20))
        loop = IOLoop.current()
        self.access_tokens[token] = loop.call_later(
            TOKEN_TIMEOUT, self._token_expire_handler, token)
        return token

    def _check_trusted_connection(self, ip):
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

    def _check_access_token(self, token):
        if token in self.access_tokens:
            token_handler = self.access_tokens.pop(token)
            IOLoop.current().remove_timeout(token_handler)
            return True
        else:
            return False

    def check_authorized(self, request):
        # Authorization is disabled, request may pass
        if not self.auth_enabled:
            return True

        # Check if IP is trusted
        ip = request.remote_ip
        if self._check_trusted_connection(ip):
            return True

        # Check API Key Header
        key = request.headers.get("X-Api-Key")
        if key and key == self.api_key:
            return True

        # Check one-shot access token
        token = request.arguments.get('token', [""])[0]
        if self._check_access_token(token):
            return True
        return False

    def close(self):
        self.prune_handler.stop()

class AuthorizedRequestHandler(tornado.web.RequestHandler):
    def initialize(self, server_manager):
        self.manager = server_manager
        self.auth_manager = server_manager.auth_manager

    def prepare(self):
        if not self.auth_manager.check_authorized(self.request):
            raise tornado.web.HTTPError(401, "Unauthorized")

    def set_default_headers(self):
        if self.settings['enable_cors']:
            self.set_header("Access-Control-Allow-Origin", "*")
            self.set_header(
                "Access-Control-Allow-Methods",
                "GET, POST, PUT, DELETE, OPTIONS")
            self.set_header(
                "Access-Control-Allow-Headers",
                "Origin, Accept, Content-Type, X-Requested-With, "
                "X-CRSF-Token")

    def options(self, *args, **kwargs):
        # Enable CORS if configured
        if self.settings['enable_cors']:
            self.set_status(204)
            self.finish()
        else:
            super(AuthorizedRequestHandler, self).options()

# Due to the way Python treats multiple inheritance its best
# to create a separate authorized handler for serving files
class AuthorizedFileHandler(tornado.web.StaticFileHandler):
    def initialize(self, server_manager, path, default_filename=None):
        super(AuthorizedFileHandler, self).initialize(path, default_filename)
        self.manager = server_manager
        self.auth_manager = server_manager.auth_manager

    def prepare(self):
        if not self.auth_manager.check_authorized(self.request):
            raise tornado.web.HTTPError(401, "Unauthorized")

    def set_default_headers(self):
        if self.settings['enable_cors']:
            self.set_header("Access-Control-Allow-Origin", "*")
            self.set_header(
                "Access-Control-Allow-Methods",
                "GET, POST, PUT, DELETE, OPTIONS")
            self.set_header(
                "Access-Control-Allow-Headers",
                "Origin, Accept, Content-Type, X-Requested-With, "
                "X-CRSF-Token")

    def options(self, *args, **kwargs):
        # Enable CORS if configured
        if self.settings['enable_cors']:
            self.set_status(204)
            self.finish()
        else:
            super(AuthorizedFileHandler, self).options()
