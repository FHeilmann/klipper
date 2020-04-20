# Web Server Utilities
#
# Copyright (C) 2020 Eric Callahan <arksine.code@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license

import json
import os
import uuid
import logging

DEBUG = True

class ServerError(Exception):
    def __init__(self, message, status_code=400):
        Exception.__init__(self, message)
        self.status_code = status_code

# Some of the status keys are mapped to functions, which fail
# json encoding without a default set.  Here we set them as
# "invalid", as there is no need to serialize those functions
def json_encode_default(data):
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


API_KEY_FILE = '.klippy_api_key'

def read_api_key(path):
    api_file = os.path.join(path, API_KEY_FILE)
    if os.path.exists(api_file):
        with open(api_file, 'r') as f:
            api_key = f.read()
        return api_key
    # API Key file doesn't exist.  Generate
    # a new api key and create the file.
    logging.info(
        "[WEBSERVER]: No API Key file found, creating new one at:\n%s"
        % (api_file))
    return create_api_key(path)

def create_api_key(path):
    api_file = os.path.join(path, API_KEY_FILE)
    api_key = uuid.uuid4().hex
    with open(api_file, 'w') as f:
        f.write(api_key)
    return api_key
