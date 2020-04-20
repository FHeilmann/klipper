# Klippy WebHooks registration
#
# Copyright (C) 2020 Eric Callahan <arksine.code@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license
import logging

AVAILABLE_METHODS = ['GET', 'POST', 'DELETE']

class WebHooksError(Exception):
    pass

class WebHooks:
    def __init__(self):
        self._endpoints = {}
        self._hooks = []

    def register_endpoint(self, path, callback, methods=['GET'], params={}):
        if path in self._endpoints:
            raise WebHooksError("Path already registered to an endpoint")

        methods = [m.upper() for m in methods]
        for method in methods:
            if method not in AVAILABLE_METHODS:
                raise WebHooksError(
                    "Requested Method [%s] for endpoint '%s' is not valid"
                    % (method, path))

        self._endpoints[path] = callback
        self._hooks.append((path, methods, params))

    def get_hooks(self):
        return (list(self._hooks))

    def get_callback(self, path):
        cb = self._endpoints.get(path, None)
        if cb is None:
            msg = "No registered callback for path '%s" % path
            logging.info(logging.info(msg))
            raise WebHooksError(msg)
        return cb
