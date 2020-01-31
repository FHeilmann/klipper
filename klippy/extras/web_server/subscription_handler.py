# Klippy Status Subscription Handler
#
# Copyright (C) 2019 Eric Callahan <arksine.code@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license
import re
import logging

class SubscriptionHandler:
    def __init__(self, config, ksi):
        self.ksi = ksi
        self.reactor = ksi.reactor
        self.tick_time = config.getfloat('tick_time', .25, above=0.)
        self.available_objects = []
        self.subscriptions = []
        self.status_timer = self.reactor.register_timer(
            self._send_status_handler, self.reactor.NEVER)
        self.poll_ticks = {
            'toolhead': 1,
            'gcode': 1,
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

    def _send_status_handler(self, eventtime):
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
            status = self.ksi.handle_status_request(current_subs)
            self.ksi.send_server_cmd('event', 'status_update_event', status)

        return eventtime + self.tick_time

    def set_available_objs(self, objs):
        self.available_objects = objs

    def stop(self):
        self.reactor.update_timer(self.status_timer, self.reactor.NEVER)

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
                    objects[key] = list(self.ksi.status_objs[key])
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
        self.reactor.update_timer(self.status_timer, waketime)
