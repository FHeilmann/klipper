# Web Server Utilities
#
# Copyright (C) 2019 Eric Callahan <arksine.code@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license

import json

DEBUG = True

class ServerError(Exception):
    def __init__(self, cmd, msg, status_code=400):
        Exception.__init__(self, msg)
        self.cmd = cmd
        self.status_code = status_code
    def to_dict(self):
        return {"message": self.message, "command": self.cmd}

# Some of the status keys are mapped to functions, which fail
# json encoding without a default set.  Here we set them as
# "invalid", as there is no need to serialize those functions
def json_encode_default(data):
    if isinstance(data, ServerError):
        return data.to_dict()
    else:
        return "<invalid>"

def json_encode(obj):
    return json.dumps(obj, default=json_encode_default)

# Json decodes strings as unicode types in Python 2.x.  This doesn't
# play well with some parts of Klipper (particuarly displays), so we
# need to create an object hook. This solution borrowed from:
#
# https://stackoverflow.com/questions/956867/
#
def byteify(data, ignore_dicts=False):
    if isinstance(data, unicode):
        return data.encode('utf-8')
    if isinstance(data, list):
        return [byteify(i, True) for i in data]
    if isinstance(data, dict) and not ignore_dicts:
        return {byteify(k, True): byteify(v, True)
                for k, v in data.iteritems()}
    return data

def json_loads_byteified(data):
    return byteify(
        json.loads(data, object_hook=byteify), True)

# Decorators for identifying routes
def _route(route, **kwargs):
    def decorator(func):
        if hasattr(func, 'route_dict'):
            func.route_dict[route] = kwargs
        else:
            func.route_dict = {route: kwargs}
        return func
    return decorator

def _get(route, **kwargs):
    def decorator(func):
        kwargs['method'] = 'GET'
        if hasattr(func, 'route_dict'):
            func.route_dict[route] = kwargs
        else:
            func.route_dict = {route: kwargs}
        return func
    return decorator

def _post(route, **kwargs):
    def decorator(func):
        kwargs['method'] = 'POST'
        if hasattr(func, 'route_dict'):
            func.route_dict[route] = kwargs
        else:
            func.route_dict = {route: kwargs}
        return func
    return decorator

def _delete(route, **kwargs):
    def decorator(func):
        kwargs['method'] = 'DELETE'
        if hasattr(func, 'route_dict'):
            func.route_dict[route] = kwargs
        else:
            func.route_dict = {route: kwargs}
        return func
    return decorator

def endpoint():
    pass


endpoint.route = _route
endpoint.get = _get
endpoint.post = _post
endpoint.delete = _delete
