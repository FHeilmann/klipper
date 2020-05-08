# Moonraker - Moonraker API server configuation and event relay
#
# Copyright (C) 2020 Eric Callahan <arksine.code@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license
import logging
import uuid
import re
import os

API_KEY_FILE = '.klippy_api_key'
MAX_TICKS = 64

class StatusHandler:
    def __init__(self, config, notification_cb):
        self.printer = config.get_printer()
        self.send_notification = notification_cb
        self.printer_ready = False
        self.reactor = self.printer.get_reactor()
        self.tick_time = config.getfloat('tick_time', .25, above=0.)
        self.current_tick = 0
        self.available_objects = {}
        self.subscriptions = []
        self.subscription_timer = self.reactor.register_timer(
            self._batch_subscription_handler, self.reactor.NEVER)
        self.poll_ticks = {
            'toolhead': 1,
            'gcode': 1,
            'idle_timeout': 1,
            'pause_resume': 1,
            'fan': 2,
            'virtual_sdcard': 4,
            'extruder.*': 4,
            'heater.*': 4,
            'temperature_fan': 4,
            'gcode_macro.*': 0,
            'default': 16.
        }
        # Fetch user defined update intervals
        for i in range(1, 7):
            modules = config.get('status_tier_%d' % (i), None)
            if modules is None:
                continue
            ticks = 2 ** (i - 1)
            modules = modules.strip().split('\n')
            modules = [m.strip() for m in modules if m.strip()]
            for name in modules:
                if name.startswith("gcode_macro"):
                    # gcode_macros are blacklisted
                    continue
                self.poll_ticks[name] = ticks

        # Register webhooks
        webhooks = self.printer.lookup_object('webhooks')
        webhooks.register_endpoint(
            '/printer/objects', self._handle_object_request)
        webhooks.register_endpoint(
            '/printer/status', self._handle_status_request,
            params={'arg_parser': "status_parser"})
        webhooks.register_endpoint(
            '/printer/subscriptions', self._handle_subscription_request,
            ['GET', 'POST'], {'arg_parser': "status_parser"})

    def initialize(self):
        self.available_objects = {}
        avail_sensors = []
        eventtime = self.reactor.monotonic()
        objs = self.printer.lookup_objects()
        status_objs = {n: o for n, o in objs if hasattr(o, "get_status")}
        for name, obj in status_objs.iteritems():
            attrs = obj.get_status(eventtime)
            self.available_objects[name] = attrs.keys()
            if name == "heaters":
                avail_sensors = attrs['available_sensors']

        # Create a subscription for available temperature sensors
        # that have a "get_status" method
        t_sensor_subs = {s: [] for s in avail_sensors
                         if s in self.available_objects}

        self.add_subscripton(t_sensor_subs, init=True)
        self.printer_ready = True
        return t_sensor_subs.keys()

    def _batch_subscription_handler(self, eventtime):
        # self.subscriptions is a 2D array, with the inner array
        # arranged in the form of:
        # [<subscripton>, <poll_ticks>]

        # Accumulate ready status subscriptions
        current_subs = {}
        for sub in self.subscriptions:
            # if no remainder then we process this subscription
            if not self.current_tick % sub[1]:
                # no ticks remaining, process
                current_subs.update(sub[0])

        if current_subs:
            status = self._process_status_request(current_subs)
            self.send_notification('status_update', status)

        self.current_tick = (self.current_tick + 1) % MAX_TICKS
        return eventtime + self.tick_time

    def _process_status_request(self, objects):
        if self.printer_ready:
            for name in objects:
                obj = self.printer.lookup_object(name, None)
                if obj is not None and name in self.available_objects:
                    status = obj.get_status(self.reactor.monotonic())
                    # Determine requested attributes.  If empty, return
                    # all requested attributes
                    if not objects[name]:
                        requested_attrs = status.keys()
                    else:
                        requested_attrs = list(objects[name])
                    objects[name] = {}
                    for attr in requested_attrs:
                        val = status.get(attr, "<invalid>")
                        # Don't return callable values
                        if callable(val):
                            continue
                        objects[name][attr] = val
                else:
                    objects[name] = "<invalid>"
        else:
            objects = {"status": "Klippy Not Ready"}
        return objects

    def _handle_object_request(self, web_request):
        web_request.send(dict(self.available_objects))

    def _handle_status_request(self, web_request):
        args = web_request.get_args()
        result = self._process_status_request(args)
        web_request.send(result)

    def _handle_subscription_request(self, web_request):
        method = web_request.get_method()
        if method.upper() == "POST":
            # add a subscription
            args = web_request.get_args()
            if args:
                self.add_subscripton(args)
            else:
                raise web_request.error("Invalid argument")
        else:
            # get subscription info
            result = self.get_sub_info()
            web_request.send(result)

    def stop(self):
        self.printer_ready = False
        self.reactor.update_timer(self.subscription_timer, self.reactor.NEVER)

    def get_poll_ticks(self, obj):
        if obj in self.poll_ticks:
            return self.poll_ticks[obj]
        else:
            for key, poll_ticks in self.poll_ticks.iteritems():
                if re.match(key, obj):
                    return poll_ticks
        return self.poll_ticks['default']

    def get_sub_info(self):
        objects = {}
        poll_times = {}
        for sub in self.subscriptions:
            objects.update(sub[0])
            for key, attrs in sub[0].iteritems():
                poll_times[key] = sub[1] * self.tick_time
                if attrs == []:
                    objects[key] = list(self.available_objects[key])
        return {'objects': objects, 'poll_times': poll_times}

    def get_sub_by_poll_ticks(self, poll_ticks):
        for sub in self.subscriptions:
            if sub[1] == poll_ticks:
                return sub
        return None

    def add_subscripton(self, new_sub, init=False):
        if not new_sub:
            return
        for obj in new_sub:
            if obj not in self.available_objects:
                logging.info(
                    "api_server: Object {%s} not available for subscription"
                    % (obj))
                continue
            poll_ticks = self.get_poll_ticks(obj)
            if poll_ticks == 0:
                # Blacklisted object, cannot subscribe
                continue
            existing_sub = self.get_sub_by_poll_ticks(poll_ticks)
            if existing_sub is not None:
                existing_sub[0][obj] = new_sub[obj]
            else:
                req = {obj: new_sub[obj]}
                self.subscriptions.append([req, poll_ticks])

        waketime = self.reactor.monotonic() + self.tick_time
        if init:
            # Add one second to the initial to give all sensors a chance to
            # update
            waketime += 1.
        self.reactor.update_timer(self.subscription_timer, waketime)

class MoonrakerConfig:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.reactor = self.printer.get_reactor()
        self.webhooks = self.printer.lookup_object('webhooks')
        self.server_conn = self.webhooks.get_connection()
        self.server_send = self.server_conn.send
        self.status_hdlr = StatusHandler(
            config, self.send_notification)

        # Get API Key
        key_path = os.path.normpath(
            os.path.expanduser(config.get('api_key_path', '~')))
        self.api_key_loc = os.path.join(key_path, API_KEY_FILE)
        self.api_key = self._read_api_key()

        # Register GCode
        gcode = self.printer.lookup_object('gcode')
        gcode.register_command(
            "GET_API_KEY", self.cmd_GET_API_KEY,
            desc=self.cmd_GET_API_KEY_help)

        # Register webhooks
        self.webhooks.register_endpoint(
            '/access/api_key', self._handle_apikey_request,
            methods=['GET', 'POST'])
        self.webhooks.register_endpoint(
            '/access/oneshot_token', None,
            params={'handler': 'TokenRequestHandler'})

        # Load Server Config
        self._load_server_config(config)

        # Start Server process and handle Klippy events only
        # if not in batch mode
        if self.server_conn.is_connected():
            logging.info("Moonraker: server connection detected")
            self.printer.register_event_handler(
                "klippy:ready", self._handle_ready)
            self.printer.register_event_handler(
                "klippy:shutdown", self._handle_shutdown)
            self.printer.register_event_handler(
                "gcode:request_restart", self._handle_restart)
            self.printer.register_event_handler(
                "gcode:respond", self._handle_gcode_response)
        else:
            logging.info("Moonraker: server not connected,"
                         " events will not be processed")

        # Attempt to load the pause_resume modules
        self.printer.try_load_module(config, "pause_resume")

    def _load_server_config(self, config):
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

        server_config = {}
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
                "RemoteAPI: The [virtual_sdcard] section "
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
                    "api_server: Unknown value in trusted_clients option, %s"
                    % (ip))
        server_config['trusted_ips'] = trusted_ips
        server_config['trusted_ranges'] = trusted_ranges
        params = {'config': server_config}
        self.server_send({'method': "load_config", 'params': params})

    def _handle_ready(self):
        sensors = self.status_hdlr.initialize()
        params = {'sensors': sensors}
        self.server_send({'method': "set_klippy_ready", 'params': params})

    def _handle_restart(self, eventtime):
        self.status_hdlr.stop()

    def _handle_shutdown(self):
        self.server_send({'method': "set_klippy_shutdown", 'params': {}})

    def _handle_gcode_response(self, gc_response):
        self.send_notification('gcode_response', gc_response)

    def _handle_apikey_request(self, web_request):
        method = web_request.get_method()
        if method == "POST":
            # POST requests generate and return a new API Key
            self.api_key = self._create_api_key()
        web_request.send(self.api_key)

    def send_notification(self, notify_name, state):
        params = {'name': notify_name, 'state': state}
        self.server_send({'method': 'notification', 'params': params})

    def _read_api_key(self):
        if os.path.exists(self.api_key_loc):
            with open(self.api_key_loc, 'r') as f:
                api_key = f.read()
            return api_key
        # API Key file doesn't exist.  Generate
        # a new api key and create the file.
        logging.info(
            "api_server: No API Key file found, creating new one at:\n%s"
            % (self.api_key_loc))
        return self._create_api_key()

    def _create_api_key(self):
        api_key = uuid.uuid4().hex
        with open(self.api_key_loc, 'w') as f:
            f.write(api_key)
        return api_key

    cmd_GET_API_KEY_help = "Print webserver API key to terminal"
    def cmd_GET_API_KEY(self, gcmd):
        gcmd.respond_info(
            "Curent Webserver API Key: %s" % (self.api_key), log=False)

def load_config(config):
    return MoonrakerConfig(config)
