# Klippy - Web Server Communications Interface
#
# Copyright (C) 2020 Eric Callahan <arksine.code@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license
import re
import os
from server import load_server_process
from server_util import ServerError, read_api_key, create_api_key
from status_handler import StatusHandler

SERVER_TIMEOUT = 5.

class KlippyServerInterface:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.reactor = self.printer.get_reactor()
        self.gcode = self.printer.lookup_object('gcode')
        self.webhooks = self.printer.lookup_object('webhooks')
        is_fileoutput = (self.printer.get_start_args().get('debugoutput')
                         is not None)
        self.printer.try_load_module(config, "pause_resume")
        self.server_pipe = self.server_proc = None
        self.status_hdlr = StatusHandler(
            config, self.send_notification)

        # Get API Key
        self.api_key_path = os.path.normpath(
            os.path.expanduser(config.get('api_key_path', '~')))
        self.api_key = read_api_key(self.api_key_path)

        # Load Server Config
        server_config = self._load_server_config(config)

        self.gcode.register_command(
            "GET_API_KEY", self.cmd_GET_API_KEY,
            desc=self.cmd_GET_API_KEY_help)

        # Register webhooks
        self.webhooks.register_endpoint(
            '/access/api_key', self._handle_apikey_request,
            methods=['GET', 'POST'])
        self.webhooks.register_endpoint(
            '/access/oneshot_token', None,
            params={'handler': 'TokenRequestHandler'})

        # Start Server process and handle Klippy events only
        # if not in batch mode
        if not is_fileoutput:
            pipe, proc = load_server_process(server_config)
            self.server_pipe = pipe
            self.server_proc = proc
            self.reactor.register_fd(
                pipe.fileno(), self._process_server_request)
            self.printer.register_event_handler(
                "klippy:post_config", self._register_hooks)
            self.printer.register_event_handler(
                "klippy:ready", self._handle_ready)
            self.printer.register_event_handler(
                "klippy:disconnect", self._handle_disconnect)
            self.printer.register_event_handler(
                "klippy:shutdown", lambda s=self:
                s._handle_klippy_state("shutdown"))
            self.printer.register_event_handler(
                "gcode:respond", self._handle_gcode_response)

    def _load_server_config(self, config):
        server_config = {}

        # Base Config
        server_config['host'] = config.get('host', '0.0.0.0')
        server_config['port'] = config.getint('port', 7125, minval=1025)
        server_config['parent_pid'] = os.getpid()

        # Get www path
        default_path = os.path.join(os.path.dirname(__file__), "www/")
        server_config['web_path'] = os.path.normpath(os.path.expanduser(
            config.get('web_path', default_path)))

        # Helper to parse (string, float) tuples from the config
        def parse_tuple(option_name):
            tup_opt = config.get(option_name, None)
            if tup_opt is not None:
                try:
                    tup_opt = tup_opt.split('\n')
                    tup_opt = [cmd.split(',', 1) for cmd in tup_opt
                               if cmd.strip()]
                    tup_opt = {k.strip().upper(): float(v.strip()) for (k, v)
                               in tup_opt if k.strip()}
                except Exception:
                    raise config.error("Error parsing %s" % option_name)
                return tup_opt
            return {}

        # Get Timeouts
        server_config['request_timeout'] = config.getfloat(
            'request_timeout', 5.)
        long_reqs = parse_tuple('long_running_requests')
        server_config['long_running_requests'] = {
            '/printer/gcode': 60.,
            '/printer/print/pause': 60.,
            '/printer/print/resume': 60.,
            '/printer/print/cancel': 60.
        }
        server_config['long_running_requests'].update(long_reqs)
        server_config['long_running_gcodes'] = parse_tuple(
            'long_running_gcodes')

        # Check Virtual SDCard is loaded
        if not config.has_section('virtual_sdcard'):
            raise config.error(
                "KlippyServerInterface: The [virtual_sdcard] section "
                "must be present and configured in printer.cfg")

        # Authorization Config
        server_config['api_key'] = self.api_key
        server_config['require_auth'] = config.getboolean('require_auth', True)
        server_config['enable_cors'] = config.getboolean('enable_cors', False)
        trusted_clients = config.get("trusted_clients", "")
        trusted_clients = [c for c in trusted_clients.split('\n') if c.strip()]
        trusted_ips = []
        trusted_ranges = []
        ip_regex = re.compile(
            r'^(([0-9]|[1-9][0-9]|1[0-9]{2}|2[0-4][0-9]|25[0-5])\.){3}'
            r'([0-9]|[1-9][0-9]|1[0-9]{2}|2[0-4][0-9]|25[0-5])$')
        range_regex = re.compile(
            r'^(([0-9]|[1-9][0-9]|1[0-9]{2}|2[0-4][0-9]|25[0-5])\.){3}'
            r'0/24$')
        for ip in trusted_clients:
            if ip_regex.match(ip) is not None:
                trusted_ips.append(ip)
            elif range_regex.match(ip) is not None:
                trusted_ranges.append(ip[:ip.rfind('.')])
            else:
                raise config.error(
                    "[WEBSERVER]: Unknown value in trusted_clients option, %s"
                    % (ip))
        server_config['trusted_ips'] = trusted_ips
        server_config['trusted_ranges'] = trusted_ranges
        return server_config

    def _handle_ready(self):
        self.status_hdlr.handle_ready()
        self._handle_klippy_state("ready")

    def _handle_disconnect(self):
        self.status_hdlr.stop()
        self._handle_klippy_state("disconnect")
        self.server_send('shutdown')
        self.server_proc.join()

    def _handle_klippy_state(self, state):
        self.send_notification('klippy_state_changed', state)

    def _handle_gcode_response(self, gc_response):
        self.send_notification('gcode_response', gc_response)

    def _register_hooks(self):
        # XXX - Make sure the server is up
        hooks = self.webhooks.get_hooks()
        self.server_send('register_hooks', hooks)

    def _handle_apikey_request(self, web_request):
        method = web_request.get_method()
        if method == "POST":
            # POST requests generate and return a new API Key
            self.api_key = create_api_key(self.api_key_path)
        web_request.send(self.api_key)

    def _process_server_request(self, eventtime):
        try:
            base_request = self.server_pipe.recv()
        except Exception:
            return
        web_request = WebRequest(base_request)
        try:
            func = self.webhooks.get_callback(
                web_request.get_path())
            func(web_request)
        except ServerError as e:
            web_request.set_error(e)
        except Exception as e:
            web_request.set_error(ServerError(e.message))
        ident, resp = web_request.finish()
        self.server_send('response', ident, resp)

    def send_notification(self, notify_name, state):
        self.server_send('notification', notify_name, state)

    def server_send(self, cmd, *args):
        self.server_pipe.send((cmd, args))

    cmd_GET_API_KEY_help = "Print webserver API key to terminal"
    def cmd_GET_API_KEY(self, params):
        self.gcode.respond_info(
            "Curent Webserver API Key: %s" % (self.api_key), log=False)

class Sentinel:
    pass

class WebRequest:
    error = ServerError
    def __init__(self, base_request):
        self.id = base_request.id
        self.path = base_request.path
        self.method = base_request.method
        self.args = base_request.args
        self.response = None

    def get(self, item, default=Sentinel):
        if item not in self.args:
            if default == Sentinel:
                raise ServerError("Invalid Argument [%s]" % item)
            return default
        return self.args[item]

    def put(self, name, value):
        self.args[name] = value

    def get_int(self, item):
        return int(self.get(item))

    def get_float(self, item):
        return float(self.get(item))

    def get_args(self):
        return self.args

    def get_path(self):
        return self.path

    def get_method(self):
        return self.method

    def set_error(self, error):
        self.response = error

    def send(self, data):
        if self.response is not None:
            raise ServerError("Multiple calls to send not allowed")
        self.response = data

    def finish(self):
        if self.response is None:
            # No error was set and the user never executed
            # send, default response is "ok"
            self.response = "ok"
        return self.id, self.response
