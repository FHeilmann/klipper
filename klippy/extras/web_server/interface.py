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
        gcode_macro = self.printer.try_load_module(config, 'gcode_macro')
        self.cancel_gcode = gcode_macro.load_template(
            config, 'cancel_gcode', "M25\nM26 S0\nCLEAR_PAUSE")
        self.pause_gcode = gcode_macro.load_template(
            config, 'pause_gcode', "PAUSE")
        self.resume_gcode = gcode_macro.load_template(
            config, 'resume_gcode', "RESUME")
        self.pause_resume = self.printer.try_load_module(
            config, 'pause_resume')
        self.server_manager = self._init_server(config)
        self.status_hdlr = StatusHandler(
            config, self.server_manager.send_notification)

        self.gcode.register_command(
            "GET_API_KEY", self.cmd_GET_API_KEY,
            desc=self.cmd_GET_API_KEY_help)

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
            "idle_timeout:ready", lambda e, s=self:
            s._handle_printer_state("ready"))
        self.printer.register_event_handler(
            "idle_timeout:idle", lambda e, s=self:
            s._handle_printer_state("idle"))
        self.printer.register_event_handler(
            "idle_timeout:printing", lambda e, s=self:
            s._handle_printer_state("printing"))
        self.printer.register_event_handler(
            "pause_resume:paused", lambda s=self:
            s._handle_paused_state("paused"))
        self.printer.register_event_handler(
            "pause_resume:resumed", lambda s=self:
            s._handle_paused_state("resumed"))
        self.printer.register_event_handler(
            "pause_resume:cleared", lambda s=self:
            s._handle_paused_state("cleared"))

        # Register Webhooks
        self.webhooks = self.printer.lookup_object('webhooks')
        self.webhooks.register_endpoint(
            "/printer/print/start", self._handle_web_request,
            methods=["POST"])
        self.webhooks.register_endpoint(
            "/printer/print/cancel", self._handle_web_request,
            methods=["POST"])
        self.webhooks.register_endpoint(
            "/printer/print/pause", self._handle_web_request,
            methods=["POST"])
        self.webhooks.register_endpoint(
            "/printer/print/resume", self._handle_web_request,
            methods=["POST"])

    def _init_server(self, config):
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
                    tup_opt = tup_opt.split('/n')
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
        return ServerManager(self.send_async_request, server_config)

    def _handle_ready(self):
        self.status_hdlr.handle_ready()
        self._handle_klippy_state("ready")

    def _handle_disconnect(self):
        self.status_hdlr.stop()
        self._handle_klippy_state("disconnect")
        self.server_manager.shutdown()

    def _handle_printer_state(self, state):
        self.server_manager.send_notification('printer_state_changed', state)

    def _handle_klippy_state(self, state):
        self.server_manager.send_notification('klippy_state_changed', state)

    def _handle_paused_state(self, state):
        self.server_manager.send_notification('paused_state_changed', state)

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

    def _handle_web_request(self, web_request):
        path = web_request.get_path()
        if path == "/printer/print/start":
            filename = web_request.get('filename')
            if filename[0] != '/':
                filename = '/' + filename
            script = "M23 " + filename + "\nM24"
        elif path == "/printer/print/cancel":
            script = self.cancel_gcode.render()
        elif path == "/printer/print/pause":
            if self.pause_resume.get_status(0)['is_paused']:
                raise web_request.error("Print Already Paused")
            script = self.pause_gcode.render()
        elif path == "/printer/print/resume":
            if not self.pause_resume.get_status(0)['is_paused']:
                raise web_request.error("Print Not Paused")
            script = self.resume_gcode.render()
        else:
            raise web_request.error("Invalid Path")
        web_request.put('script', script)
        self.gcode.run_script_from_remote(web_request)

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
