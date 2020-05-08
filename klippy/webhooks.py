# Klippy WebHooks registration and server connection
#
# Copyright (C) 2020 Eric Callahan <arksine.code@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license
import logging
import socket
import json

AVAILABLE_METHODS = ['GET', 'POST', 'DELETE']
SOCKET_LOCATION = "/tmp/moonraker"

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

class WebRequestError(Exception):
    def __init__(self, message, status_code=400):
        Exception.__init__(self, message)
        self.status_code = status_code

    def to_dict(self):
        return {
            'error': 'WebRequestError',
            'message': self.message,
            'status_code': self.status_code}

class Sentinel:
    pass

class WebRequest:
    error = WebRequestError
    def __init__(self, base_request):
        self.id = base_request['id']
        self.path = base_request['path']
        self.method = base_request['method']
        self.args = base_request['args']
        self.response = None

    def get(self, item, default=Sentinel):
        if item not in self.args:
            if default == Sentinel:
                raise WebRequestError("Invalid Argument [%s]" % item)
            return default
        return self.args[item]

    def put(self, name, value):
        self.args[name] = value

    def get_int(self, item):
        return int(self.get(item))

    def get_float(self, item):
        return float(self.get(item))

    def get_args(self):
        return self.args

    def get_path(self):
        return self.path

    def get_method(self):
        return self.method

    def set_error(self, error):
        self.response = error.to_dict()

    def send(self, data):
        if self.response is not None:
            raise WebRequestError("Multiple calls to send not allowed")
        self.response = data

    def finish(self):
        if self.response is None:
            # No error was set and the user never executed
            # send, default response is "ok"
            self.response = "ok"
        return {"request_id": self.id, "response": self.response}

class ServerConnection:
    def __init__(self, webhooks, printer):
        self.webhooks = webhooks
        self.reactor = printer.get_reactor()

        # Klippy Connection
        self.fd_handler = self.mutex = None
        self.is_server_connected = False
        self.partial_data = ""
        is_fileoutput = (printer.get_start_args().get('debugoutput')
                         is not None)
        if is_fileoutput:
            # Do not try to connect in klippy batch mode
            return
        self.socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.socket.setblocking(0)
        try:
            self.socket.connect(SOCKET_LOCATION)
        except socket.error:
            logging.debug(
                "ServerConnection: Moonraker server not detected")
            return
        logging.debug("ServerConnection: Moonraker connection established")
        self.is_server_connected = True
        self.fd_handler = self.reactor.register_fd(
            self.socket.fileno(), self.process_received)
        self.mutex = self.reactor.mutex()
        printer.register_event_handler('klippy:disconnect', self.close_socket)

    def close_socket(self):
        if self.is_server_connected:
            logging.info("ServerConnection: lost connection to Moonraker")
            self.is_server_connected = False
            self.reactor.unregister_fd(self.fd_handler)
            try:
                self.socket.close()
            except socket.error:
                pass

    def is_connected(self):
        return self.is_server_connected

    def process_received(self, eventtime):
        try:
            data = self.socket.recv(4096)
        except socket.error as e:
            # If bad file descriptor allow connection to be
            # closed by the data check
            if e.errno == 9:
                data = ''
            else:
                return
        if data == '':
            # Socket Closed
            self.close_socket()
            return
        requests = data.split('\x00')
        requests[0] = self.partial_data + requests[0]
        self.partial_data = requests.pop()
        for req in requests:
            logging.info(
                "ServerConnection: Request received from Moonraker %s" % (req))
            try:
                decoded_req = json_loads_byteified(req)
                self._process_request(decoded_req)
            except Exception:
                logging.exception(
                    "ServerConnection: Error processing Server Request %s"
                    % (req))

    def _process_request(self, req):
        web_request = WebRequest(req)
        try:
            func = self.webhooks.get_callback(
                web_request.get_path())
            func(web_request)
        except WebRequestError as e:
            web_request.set_error(e)
        except Exception as e:
            web_request.set_error(WebRequestError(e.message))
        result = web_request.finish()
        logging.info(
            "ServerConnection: Sending response - %s" % (str(result)))
        self.send({'method': "response", 'params': result})

    def send(self, data):
        if not self.is_server_connected:
            return
        with self.mutex:
            retries = 10
            data = json.dumps(data) + "\x00"
            while data:
                try:
                    sent = self.socket.send(data)
                except socket.error as e:
                    if e.errno == 9 or e.errno == 32 or not retries:
                        sent = 0
                    else:
                        retries -= 1
                        waketime = self.reactor.monotonic() + .001
                        self.reactor.pause(waketime)
                        continue
                if sent > 0:
                    data = data[sent:]
                else:
                    logging.info(
                        "ServerConnection: Error sending server data,"
                        " closing socket")
                    self.close_socket()
                    break

class WebHooks:
    def __init__(self, printer):
        self._endpoints = {}
        self._hooks = []
        self.sconn = ServerConnection(self, printer)

    def register_endpoint(self, path, callback, methods=['GET'], params={}):
        if path in self._endpoints:
            raise WebRequestError("Path already registered to an endpoint")

        methods = [m.upper() for m in methods]
        for method in methods:
            if method not in AVAILABLE_METHODS:
                raise WebRequestError(
                    "Requested Method [%s] for endpoint '%s' is not valid"
                    % (method, path))

        self._endpoints[path] = callback
        hook = (path, methods, params)
        self._hooks.append(hook)
        # Send Hook to server if connected
        if self.sconn.is_connected():
            self.sconn.send({'method': "add_hook", 'params': {'hook': hook}})

    def get_hooks(self):
        return (list(self._hooks))

    def get_connection(self):
        return self.sconn

    def get_callback(self, path):
        cb = self._endpoints.get(path, None)
        if cb is None:
            msg = "webhooks: No registered callback for path '%s'" % (path)
            logging.info(msg)
            raise WebRequestError(msg, 404)
        return cb
