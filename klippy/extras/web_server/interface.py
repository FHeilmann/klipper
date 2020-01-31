# Klippy - Web Server Interface
#
# Copyright (C) 2019 Eric Callahan <arksine.code@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license
import re
import os
import socket
import logging
from server import WSGIManager
from subscription_handler import SubscriptionHandler

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
        self.wsgi_manager = self._init_server(config)
        self.send_server_cmd = self.wsgi_manager.run_async_server_cmd
        self.status_objs = []
        self.sub_hdlr = SubscriptionHandler(config, self)
        self.ready = False

        self.gcode.register_command(
            "GET_API_KEY", self.cmd_GET_API_KEY,
            desc=self.cmd_GET_API_KEY_help)

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

        self.request_callbacks = {
            'get_klippy_info': self._get_klippy_info,
            'run_gcode': self._run_gcode,
            'get_status': self._get_object_status,
            'get_object_info': self._get_object_info,
            'add_subscription': self._add_subscription,
            'get_subscribed': self._get_subscribed_objs,
            'query_endstops': self._query_endstops,
            'start_print': self._start_print,
            'cancel_print': self._cancel_print,
            'pause_print': self._pause_print,
            'resume_print': self._resume_print
        }

    def _init_server(self, config):
        server_config = {}

        # Base Config
        server_config['host'] = config.get('host', '0.0.0.0')
        server_config['port'] = config.getint('port', 7125)

        # Get www path
        default_path = os.path.join(os.path.dirname(__file__), "www/")
        server_config['web_path'] = os.path.normpath(os.path.expanduser(
            config.get('web_path', default_path)))

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
        return WSGIManager(self.run_async_commmand, server_config)

    def _handle_ready(self):
        self.status_objs = {}
        objs = self.printer.lookup_objects()
        status_objs = [(n, o) for n, o in objs if hasattr(o, "get_status")]
        eventtime = self.reactor.monotonic()
        for name, obj in status_objs:
            attrs = obj.get_status(eventtime)
            self.status_objs[name] = attrs.keys()
        self.sub_hdlr.set_available_objs(list(self.status_objs.keys()))
        self.ready = True
        self._handle_klippy_state("ready")

    def _handle_disconnect(self):
        self.sub_hdlr.stop()
        self._handle_klippy_state("disconnect")
        self.wsgi_manager.shutdown()

    def _handle_printer_state(self, state):
        self.send_server_cmd('event', 'printer_state_event', state)

    def _handle_klippy_state(self, state):
        self.send_server_cmd('event', 'klippy_state_event', state)

    def _handle_gcode_response(self, data):
        self.send_server_cmd('event', 'gcode_response', data)

    def _get_klippy_info(self, data=''):
        version = self.printer.get_start_args().get('software_version')
        hostname = socket.gethostname()
        return {'hostname': hostname, 'version': version,
                "is_ready": self.ready}

    def handle_status_request(self, objects):
        if self.ready:
            for name in objects:
                obj = self.printer.lookup_object(name, None)
                if obj is not None and name in self.status_objs:
                    status = obj.get_status(self.reactor.monotonic())
                    if not objects[name]:
                        objects[name] = status
                    else:
                        attrs = list(objects[name])
                        objects[name] = {}
                        for a in attrs:
                            objects[name][a] = status.get(a, "<invalid>")
                else:
                    objects[name] = "<invalid>"
        else:
            objects = {"status": "Klippy Not Ready"}
        return objects

    def _run_gcode(self, gc, cmd='run_gcode'):
        if "M112" in gc.upper():
            self.gcode.cmd_M112({})
            return "ok"
        try:
            self.gcode.run_script(gc)
        except Exception as e:
            logging.exception("[WEBSERVER]: Script running error: %s" % gc)
            return WSGIManager.error('cmd', e.message, 400)
        return "ok"

    def _get_object_info(self, data=""):
        return dict(self.status_objs)

    def _get_subscribed_objs(self, data=""):
        return self.sub_hdlr.get_sub_info()

    def _query_endstops(self, data=""):
        query_endstops = self.printer.lookup_object('query_endstops')
        return query_endstops.get_endstop_state()

    def _get_object_status(self, data):
        if type(data) != dict:
            return WSGIManager.error(
                "get_status", "Parameter must be a dict type")
        return self.handle_status_request(data)

    def _add_subscription(self, data):
        if type(data) != dict:
            return WSGIManager.error(
                "add_subscription", "Parameter must be a dict type")
        self.sub_hdlr.add_subscripton(data)
        return "ok"

    def _start_print(self, data):
        # prepend a '/', this assures that the gcode parser
        # will correctly accept the M23 command.
        if data[0] != '/':
            data = '/' + data
        start_cmd = "M23 " + data + "\nM24"
        return self._run_gcode(start_cmd, 'start_print')

    def _cancel_print(self, data):
        return self._run_gcode(self.cancel_gcode.render(), 'cancel_print')

    def _pause_print(self, data):
        if self.pause_resume.get_status(0)['is_paused']:
            return WSGIManager.error(
                'pause_print', "Print Already Paused")
        return self._run_gcode(self.pause_gcode.render(), 'pause_print')

    def _resume_print(self, data):
        if not self.pause_resume.get_status(0)['is_paused']:
            return WSGIManager.error(
                'resume_print', "Print Not Paused")
        return self._run_gcode(self.resume_gcode.render(), 'resume_print')

    def run_async_commmand(self, cmd, *args):
        self.reactor.register_async_callback(
            lambda e, s=self: s.exec_server_command(cmd, *args))

    def exec_server_command(self, cmd, uid, data):
        func = self.request_callbacks.get(cmd, None)
        if func is not None:
            resp = func(data)
        else:
            resp = WSGIManager.error(cmd, "No handler for command")
            logging.info(
                "[WEBSERVER]: KlippyServerInterface: No callback for "
                "request %s" % (cmd))
        self.send_server_cmd('response', uid, cmd, resp)

    cmd_GET_API_KEY_help = "Print webserver API key to terminal"
    def cmd_GET_API_KEY(self, params):
        api_key = self.wsgi_manager.get_api_key()
        self.gcode.respond_info(
            "Curent Webserver API Key: %s" % (api_key))
