# Klippy - Web Server Communications Interface
#
# Copyright (C) 2020 Eric Callahan <arksine.code@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license
import re
import os
from server import ServerManager
from server_util import ServerError
from status_handler import StatusHandler

SERVER_TIMEOUT = 5.

class KlippyServerInterface:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.reactor = self.printer.get_reactor()
        self.gcode = self.printer.lookup_object('gcode')

        is_fileoutput = (self.printer.get_start_args().get('debugoutput')
                         is not None)
        self.printer.try_load_module(config, "pause_resume")
        self.server_manager = self._init_server(config, is_fileoutput)
        self.status_hdlr = StatusHandler(
            config, self.server_manager.send_notification)

        self.gcode.register_command(
            "GET_API_KEY", self.cmd_GET_API_KEY,
            desc=self.cmd_GET_API_KEY_help)

        # do not register and process events in batch mode, as the
        # server will not be started
        if not is_fileoutput:
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
            self.printer.register_event_handler(
                "idle_timeout:printing", self._process_printing_transition)
            self.printer.register_event_handler(
                "idle_timeout:ready", self._process_ready_idle_transition)
            self.printer.register_event_handler(
                "idle_timeout:idle", self._process_ready_idle_transition)

        self.webhooks = self.printer.lookup_object('webhooks')

    def _init_server(self, config, is_fileoutput):
        server_config = {}

        # Base Config
        server_config['host'] = config.get('host', '0.0.0.0')
        server_config['port'] = config.getint('port', 7125, minval=1025)

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

        # Get Virtual Sdcard Path
        if not config.has_section('virtual_sdcard'):
            raise config.error(
                "KlippyServerInterface: The [virtual_sdcard] section "
                "must be present and configured in printer.cfg")
        sd_path = config.getsection('virtual_sdcard').get('path')
        server_config['sd_path'] = os.path.normpath(
            os.path.expanduser(sd_path))

        # Authorization Config
        server_config['api_key_path'] = os.path.normpath(
            os.path.expanduser(config.get('api_key_path', '~')))
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

        server_config['allow_file_ops_when_printing'] = config.getboolean(
            'allow_file_ops_when_printing', False)
        server_config['is_fileoutput'] = is_fileoutput
        return ServerManager(self.send_async_request, server_config)

    def _handle_ready(self):
        self.status_hdlr.handle_ready()
        self._handle_klippy_state("ready")

    def _handle_disconnect(self):
        self.status_hdlr.stop()
        self._handle_klippy_state("disconnect")
        self.server_manager.shutdown()

    def _process_printing_transition(self, print_time):
        v_sd = self.printer.lookup_object('virtual_sdcard')
        eventtime = self.reactor.monotonic()
        current_file = v_sd.get_status(eventtime).get("current_file", "")
        self.server_manager.update_printing_state(True, current_file)

    def _process_ready_idle_transition(self, print_time):
        self.server_manager.update_printing_state(False, "")

    def _handle_klippy_state(self, state):
        self.server_manager.send_notification('klippy_state_changed', state)

    def _handle_gcode_response(self, gc_response):
        self.server_manager.send_notification('gcode_response', gc_response)

    def _register_hooks(self):
        curtime = self.reactor.monotonic()
        endtime = curtime + SERVER_TIMEOUT
        server_ready = self.server_manager.is_running()
        while curtime < endtime and not server_ready:
            curtime = self.reactor.pause(curtime + .1)
            server_ready = self.server_manager.is_running()

        if not server_ready:
            raise ServerError("Server Not Ready")
        hooks = self.webhooks.get_hooks()
        self.server_manager.send_webhooks(hooks)

    def send_async_request(self, web_request):
        self.reactor.register_async_callback(
            lambda e, s=self: s.process_web_request(web_request))

    def process_web_request(self, web_request):
        try:
            func = self.webhooks.get_callback(
                web_request.get_path())
            func(web_request)
        except ServerError as e:
            web_request.set_error(e)
        except Exception as e:
            web_request.set_error(ServerError(e.message))
        web_request.finish()

    cmd_GET_API_KEY_help = "Print webserver API key to terminal"
    def cmd_GET_API_KEY(self, params):
        api_key = self.server_manager.get_api_key()
        self.gcode.respond_info(
            "Curent Webserver API Key: %s" % (api_key))
