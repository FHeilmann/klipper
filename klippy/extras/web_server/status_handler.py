# Klippy Status and Subscription Handler
#
# Copyright (C) 2020 Eric Callahan <arksine.code@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license
import re
import logging

class StatusHandler:
    def __init__(self, config, notification_cb):
        self.printer = config.get_printer()
        self.send_notification = notification_cb
        self.printer_ready = False
        self.reactor = self.printer.get_reactor()
        self.tick_time = config.getfloat('tick_time', .25, above=0.)
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
            arg_parser="object")
        webhooks.register_endpoint(
            '/printer/subscriptions', self._handle_subscription_request,
            methods=['GET', 'POST'], arg_parser="object")

    def handle_ready(self):
        self.printer_ready = True
        self.available_objects = {}
        objs = self.printer.lookup_objects()
        available_objs = [(n, o) for n, o in objs if hasattr(o, "get_status")]
        eventtime = self.reactor.monotonic()
        for name, obj in available_objs:
            attrs = obj.get_status(eventtime)
            self.available_objects[name] = attrs.keys()

    def _batch_subscription_handler(self, eventtime):
        # self.subscriptions is a 2D array, with the inner array
        # arranged in the form of:
        # [<subscripton>, <poll_ticks>, <ticks_remaining>]

        # Accumulate ready status subscriptions
        current_subs = {}
        for sub in self.subscriptions:
            # subtract a tick
            sub[2] -= 1
            if sub[2] <= 0:
                # no ticks remaining, process
                current_subs.update(sub[0])
                sub[2] = sub[1]

        if current_subs:
            status = self._process_status_request(current_subs)
            self.send_notification('status_update', status)

        return eventtime + self.tick_time

    def _process_status_request(self, objects):
        if self.printer_ready:
            for name in objects:
                obj = self.printer.lookup_object(name, None)
                if obj is not None and name in self.available_objects:
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

    def add_subscripton(self, new_sub):
        if not new_sub:
            return
        for obj in new_sub:
            if obj not in self.available_objects:
                logging.info(
                    "[WEBSERVER] subscription_handler: Subscription Request"
                    " {%s} not available, ignoring" % (obj))
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
                self.subscriptions.append([req, poll_ticks, poll_ticks])

        waketime = self.reactor.monotonic() + self.tick_time
        self.reactor.update_timer(self.subscription_timer, waketime)
