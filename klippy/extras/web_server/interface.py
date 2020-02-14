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
        self.printer.register_event_handler(
            "pause_resume:paused", lambda s=self:
            s._handle_paused_state("paused"))
        self.printer.register_event_handler(
            "pause_resume:resumed", lambda s=self:
            s._handle_paused_state("resumed"))
        self.printer.register_event_handler(
            "pause_resume:cleared", lambda s=self:
            s._handle_paused_state("cleared"))

        self.request_callbacks = {
            'get_klippy_info': self._get_klippy_info,
            'run_gcode': self._run_gcode,
            'get_status': self._get_object_status,
            'get_object_info': self._get_object_info,
            'add_subscription': self._add_subscription,
            'get_subscribed': self._get_subscribed_objs,
            'start_print': self._start_print,
            'cancel_print': self._cancel_print,
            'pause_print': self._pause_print,
            'resume_print': self._resume_print,
            'restart': self._host_restart,
            'firmware_restart': self._firmware_restart
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

        # Get timeouts
        server_config['base_timeout'] = config.getfloat('request_timeout', 5.)
        server_config['gcode_timeout'] = config.getfloat('gcode_timeout', 60.)
        long_gcs = config.get('long_running_gcodes', None)
        if long_gcs is not None:
            try:
                long_gcs = long_gcs.split('/n')
                long_gcs = [cmd.split(',', 1) for cmd in long_gcs
                            if cmd.strip()]
                long_gcs = {k.strip().upper(): float(v.strip()) for (k, v)
                            in long_gcs if k.strip()}
            except Exception:
                raise config.error("Error parsing long_running_gcodes")
            server_config['long_running_gcodes'] = long_gcs

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
        self.send_server_cmd('notification', 'printer_state_changed', state)

    def _handle_klippy_state(self, state):
        self.send_server_cmd('notification', 'klippy_state_changed', state)

    def _handle_paused_state(self, state):
        self.send_server_cmd('notification', 'paused_state_changed', state)

    def _handle_gcode_response(self, gc_response):
        self.send_server_cmd('notification', 'gcode_response', gc_response)

    def _get_klippy_info(self):
        version = self.printer.get_start_args().get('software_version')
        hostname = socket.gethostname()
        return {'hostname': hostname, 'version': version,
                "is_ready": self.ready}

    def register_url(self, endpoint, callback):
        #  At the moment modules may only register "GET" requests
        request_name = "get_" + endpoint
        if request_name in self.request_callbacks:
            # TODO:  This should really raise a config error
            raise Exception
        self.request_callbacks[request_name] = callback
        self.send_server_cmd('register_ep', request_name, endpoint)

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
            return WSGIManager.error(cmd, e.message, 400)
        return "ok"

    def _get_object_info(self):
        return dict(self.status_objs)

    def _get_subscribed_objs(self):
        return self.sub_hdlr.get_sub_info()

    def _get_object_status(self, printer_objects):
        if type(printer_objects) != dict:
            return WSGIManager.error(
                "get_status", "Parameter must be a dict type")
        return self.handle_status_request(printer_objects)

    def _add_subscription(self, printer_objects):
        if type(printer_objects) != dict:
            return WSGIManager.error(
                "add_subscription", "Parameter must be a dict type")
        self.sub_hdlr.add_subscripton(printer_objects)
        return "ok"

    def _start_print(self, file_name):
        # prepend a '/', this assures that the gcode parser
        # will correctly accept the M23 command.
        if file_name[0] != '/':
            file_name = '/' + file_name
        start_cmd = "M23 " + file_name + "\nM24"
        return self._run_gcode(start_cmd, 'start_print')

    def _cancel_print(self):
        return self._run_gcode(self.cancel_gcode.render(), 'cancel_print')

    def _pause_print(self):
        if self.pause_resume.get_status(0)['is_paused']:
            return WSGIManager.error(
                'pause_print', "Print Already Paused")
        return self._run_gcode(self.pause_gcode.render(), 'pause_print')

    def _resume_print(self):
        if not self.pause_resume.get_status(0)['is_paused']:
            return WSGIManager.error(
                'resume_print', "Print Not Paused")
        return self._run_gcode(self.resume_gcode.render(), 'resume_print')

    def _host_restart(self):
        self.printer.request_exit('restart')
        return "ok"

    def _firmware_restart(self):
        self.printer.request_exit('firmware_restart')
        return "ok"

    def run_async_commmand(self, cmd, uid, *args):
        self.reactor.register_async_callback(
            lambda e, s=self: s.exec_server_command(cmd, uid, *args))

    def exec_server_command(self, cmd, uid, *args):
        func = self.request_callbacks.get(cmd, None)
        if func is not None:
            try:
                result = func(*args)
            except Exception as e:
                result = WSGIManager.error(cmd, str(e))
        else:
            result = WSGIManager.error(cmd, "No handler for command")
            logging.info(
                "[WEBSERVER]: KlippyServerInterface: No callback for "
                "request %s" % (cmd))
        self.send_server_cmd('response', uid, cmd, result)

    cmd_GET_API_KEY_help = "Print webserver API key to terminal"
    def cmd_GET_API_KEY(self, params):
        api_key = self.wsgi_manager.get_api_key()
        self.gcode.respond_info(
            "Curent Webserver API Key: %s" % (api_key))
