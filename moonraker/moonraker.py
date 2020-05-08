# Moonraker - HTTP/Websocket API Server for Klipper
#
# Copyright (C) 2020 Eric Callahan <arksine.code@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license
import argparse
import os
import time
import socket
import logging
import json
import shlex
import tornado
import tornado.netutil
from tornado import gen
from tornado.process import Subprocess
from tornado.ioloop import IOLoop, PeriodicCallback
from tornado.util import TimeoutError
from tornado.locks import Event
from collections import deque
from app import MoonrakerApp
from utils import ServerError, DEBUG, json_loads_byteified

TEMPERATURE_UPDATE_MS = 1000
TEMPERATURE_STORE_SIZE = 20 * 60

class ServerManager:
    def __init__(self, args):
        self.host = args.address
        self.port = args.port

        # Options configurable by Klippy
        self.request_timeout = 5.
        self.long_running_gcodes = {}
        self.long_running_requests = {}

        # Klippy Connection Handling
        socketfile = os.path.normpath(os.path.expanduser(args.socketfile))
        self.klippy_server_sock = tornado.netutil.bind_unix_socket(
            socketfile, backlog=1)
        self.remove_server_sock = tornado.netutil.add_accept_handler(
            self.klippy_server_sock, self._handle_klippy_connection)
        self.klippy_sock = None
        self.is_klippy_connected = False
        self.is_klippy_ready = False
        self.partial_data = ""

        # Server/IOLoop
        self.server_running = False
        self.moonraker_app = MoonrakerApp(self)
        self.io_loop = IOLoop.current()

        # Register server side hooks
        self.local_endpoints = {
            "/server/temperature_store": self._handle_temp_store_request,
            "/machine/reboot": self._handle_machine_request,
            "/machine/shutdown": self._handle_machine_request}
        hooks = [
            ("/machine/reboot", ['POST'], {}),
            ("/machine/shutdown", ['POST'], {}),
            ("/server/temperature_store", ['GET'], {}),
            ("/server/moonraker.log()", ['GET'],
             {'handler': 'FileRequestHandler', 'path': args.logfile})]
        for hook in hooks:
            self._add_hook(hook, is_local=True)

        # Temperature Store Tracking
        self.last_temps = {}
        self.temperature_store = {}
        self.temp_update_cb = PeriodicCallback(
            self._update_temperature_store, TEMPERATURE_UPDATE_MS)

        # Setup host/server callbacks
        self.pending_requests = {}
        self.server_callbacks = {
            'add_hook': self._add_hook,
            'load_config': self._load_config,
            'set_klippy_ready': self._set_klippy_ready,
            'set_klippy_shutdown': self._set_klippy_shutdown,
            'response': self._handle_klippy_response,
            'notification': self._handle_notification
        }

    def start(self):
        logging.info(
            "Starting Moonraker on (%s, %d)" %
            (self.host, self.port))
        self.moonraker_app.listen(self.host, self.port)
        self.server_running = True

    def _handle_klippy_connection(self, conn, addr):
        if self.is_klippy_connected:
            logging.info("New Connection received while Klippy Connected")
            self.close_client_sock()
        logging.info("Klippy Connection Established")
        self.is_klippy_connected = True
        conn.setblocking(0)
        self.klippy_sock = conn
        self.io_loop.add_handler(
            self.klippy_sock.fileno(), self._handle_klippy_data,
            IOLoop.READ | IOLoop.ERROR)

    def _handle_klippy_data(self, fd, events):
        if events & IOLoop.ERROR:
            self.close_client_sock()
            return
        try:
            data = self.klippy_sock.recv(4096)
        except socket.error as e:
            # If bad file descriptor allow connection to be
            # closed by the data check
            if e.errno == 9:
                data = ''
            else:
                return
        if data == '':
            # Socket Closed
            self.close_client_sock()
            return
        commands = data.split('\x00')
        commands[0] = self.partial_data + commands[0]
        self.partial_data = commands.pop()
        for cmd in commands:
            try:
                decoded_cmd = json_loads_byteified(cmd)
                method = decoded_cmd.get('method')
                params = decoded_cmd.get('params', {})
                cb = self.server_callbacks.get(method)
                if cb is not None:
                    cb(**params)
                else:
                    logging.info("Unknown command received %s" % cmd)
            except Exception:
                logging.exception(
                    "Error processing Klippy Host Response: %s"
                    % (cmd))

    def klippy_send(self, data):
        if not self.is_klippy_connected:
            return False
        retries = 10
        data = json.dumps(data) + "\x00"
        while data:
            try:
                sent = self.klippy_sock.send(data)
            except socket.error as e:
                if e.errno == 9 or e.errno == 32 or not retries:
                    sent = 0
                else:
                    # XXX - Should pause for 1ms here
                    retries -= 1
                    continue
            if sent > 0:
                data = data[sent:]
            else:
                logging.info("Error sending client data, closing socket")
                self.close_client_sock()
                return False
        return True

    def _load_config(self, config):
        self.request_timeout = config.get(
            'request_timeout', self.request_timeout)
        self.long_running_gcodes = config.get(
            'long_running_gcodes', self.long_running_gcodes)
        self.long_running_requests = config.get(
            'long_running_requests', self.long_running_requests)
        self.moonraker_app.load_config(config)

    def _set_klippy_ready(self, sensors):
        logging.info("Klippy ready, setting available sensors: %s"
                     % (str(sensors)))
        new_store = {}
        for sensor in sensors:
            if sensor in self.temperature_store:
                new_store[sensor] = self.temperature_store[sensor]
            else:
                new_store[sensor] = {
                    'temperatures': deque(maxlen=TEMPERATURE_STORE_SIZE),
                    'targets': deque(maxlen=TEMPERATURE_STORE_SIZE)}
        self.temperature_store = new_store
        self.temp_update_cb.start()
        self.is_klippy_ready = True
        self._handle_notification("klippy_state_changed", "ready")

    def _set_klippy_shutdown(self):
        logging.info("Klippy has shutdown")
        self.is_klippy_ready = False
        self._handle_notification("klippy_state_changed", "shutdown")

    def _add_hook(self, hook, is_local=False):
        self.io_loop.add_callback(
            self.moonraker_app.register_hook, *hook)

    def _handle_klippy_response(self, request_id, response):
        req = self.pending_requests.pop(request_id)
        if req is not None:
            if isinstance(response, dict) and 'error' in response:
                response = ServerError(
                    response['message'], response.get('status_code', 400))
            req.notify(response)
        else:
            logging.info("No request matching response: " + str(response))

    def _handle_notification(self, name, state):
        self.io_loop.spawn_callback(
            self._process_notification, name, state)

    def make_request(self, path, method, args):
        timeout = self.long_running_requests.get(path, self.request_timeout)

        if path == "/printer/gcode":
            script = args.get('script', "")
            base_gc = script.strip().split()[0].upper()
            timeout = self.long_running_gcodes.get(base_gc, timeout)

        base_request = BaseRequest(path, method, args, timeout)
        if path in self.local_endpoints:
            self.io_loop.spawn_callback(
                self.local_endpoints[path], base_request)
        else:
            self.pending_requests[base_request.id] = base_request
            ret = self.klippy_send(base_request.to_dict())
            if not ret:
                self.pending_requests.pop(base_request.id)
                base_request.notify(
                    ServerError("Klippy Host not connected", 503))
        return base_request

    def notify_filelist_changed(self, filename, action):
        self.io_loop.spawn_callback(
            self._request_filelist_and_notify, filename, action)

    @gen.coroutine
    def _request_filelist_and_notify(self, filename, action):
        flist_request = self.make_request("/printer/files", "GET", {})
        filelist = yield flist_request.wait()
        if isinstance(filelist, ServerError):
            filelist = []
        result = {'filename': filename, 'action': action,
                  'filelist': filelist}
        yield self._process_notification('filelist_changed', result)

    @gen.coroutine
    def _handle_machine_request(self, web_request):
        path = web_request.path
        if path == "/machine/shutdown":
            cmd = "sudo shutdown now"
        elif path == "/machine/reboot":
            cmd = "sudo reboot now"
        else:
            web_request.notify(ServerError("Unsupported machine request"))
            return
        ret = yield self._run_shell_command(cmd)
        web_request.notify("ok")

    def _handle_temp_store_request(self, web_request):
        store = {}
        for name, sensor in self.temperature_store.iteritems():
            store[name] = {k: list(v) for k, v in sensor.iteritems()}
        web_request.notify(store)

    def _update_temperature_store(self):
        # XXX - If klippy is not connected, set values to zero
        # as they are unknown
        for sensor, (temp, target) in self.last_temps.iteritems():
            self.temperature_store[sensor]['temperatures'].append(temp)
            self.temperature_store[sensor]['targets'].append(target)

    def _record_last_temp(self, data):
        for sensor in self.temperature_store:
            if sensor in data:
                self.last_temps[sensor] = (
                    round(data[sensor].get('temperature', 0.), 2),
                    data[sensor].get('target', 0.))

    @gen.coroutine
    def _process_notification(self, name, data):
        if name == 'status_update':
            self._record_last_temp(data)
        # Send Event Over Websocket in JSON-RPC 2.0 format.
        resp = json.dumps({
            'jsonrpc': "2.0",
            'method': "notify_" + name,
            'params': [data]})
        yield self.moonraker_app.send_all_websockets(resp)

    @gen.coroutine
    def _kill_server(self):
        logging.info(
            "Shutting Down Webserver")
        self.temp_update_cb.stop()
        self.close_client_sock()
        self.close_server_sock()
        if self.server_running:
            self.server_running = False
            yield self.moonraker_app.close()
            self.io_loop.stop()

    @gen.coroutine
    def _run_shell_command(self, command):
        cmd = shlex.split(os.path.expanduser(command))
        proc = Subprocess(cmd)
        ret = yield proc.wait_for_exit(raise_error=False)
        raise gen.Return(ret)

    def close_client_sock(self):
        self.is_klippy_ready = False
        if self.is_klippy_connected:
            self.is_klippy_connected = False
            logging.info("Klippy Connection Removed")
            try:
                self.klippy_sock.close()
            except socket.error:
                logging.exception("Error Closing Client Socket")
            self._handle_notification(
                "klippy_state_changed", "disconnect")

    def close_server_sock(self):
        try:
            self.remove_server_sock()
            self.klippy_server_sock.close()
            # XXX - remove server sock file (or use abstract?)
        except Exception:
            logging.exception("Error Closing Server Socket")

    def error_exit(self):
        self.close_client_sock()
        self.close_server_sock()
        self.temp_update_cb.stop()
        self.moonraker_app.close()


# Basic WebRequest class, easily converted to dict for json encoding
class BaseRequest:
    def __init__(self, path, method, args, timeout=None):
        self.id = id(self)
        self.path = path
        self.method = method
        self.args = args
        self._timeout = timeout
        self._event = Event()
        self.response = None
        if timeout is not None:
            self._timeout = time.time() + timeout

    @gen.coroutine
    def wait(self):
        # Wait for klippy to process the request or until the timeout
        # has been reached.
        try:
            yield self._event.wait(timeout=self._timeout)
        except TimeoutError:
            logging.info("Request '%s' Timed Out" %
                         (self.method + " " + self.path))
            raise gen.Return(ServerError("Klippy Request Timed Out", 500))
        raise gen.Return(self.response)

    def notify(self, response):
        self.response = response
        self._event.set()

    def to_dict(self):
        return {'id': self.id, 'path': self.path,
                'method': self.method, 'args': self.args}

def main():
    # Parse start arguments
    parser = argparse.ArgumentParser(
        description="Moonraker - Klipper API Server")
    parser.add_argument(
        "-a", "--address", default='0.0.0.0', metavar='<address>',
        help="host name or ip to bind to the Web Server")
    parser.add_argument(
        "-p", "--port", type=int, default=7125, metavar='<port>',
        help="port the Web Server will listen on")
    parser.add_argument(
        "-s", "--socketfile", default="/tmp/moonraker", metavar='<socketfile>',
        help="file name and location for the Unix Domain Socket")
    parser.add_argument(
        "-l", "--logfile", default="/tmp/moonraker.log", metavar='<logfile>',
        help="log file name and location")
    args = parser.parse_args()

    # Setup Logging
    log_file = os.path.normpath(os.path.expanduser(args.logfile))
    args.logfile = log_file
    root_logger = logging.getLogger()
    file_hdlr = logging.handlers.TimedRotatingFileHandler(
        log_file, when='midnight', backupCount=2)
    root_logger.addHandler(file_hdlr)
    root_logger.setLevel(logging.INFO)
    logging.info("="*25 + "Starting Moonraker..." + "="*25)
    formatter = logging.Formatter(
        '%(asctime)s [%(filename)s:%(funcName)s()] - %(message)s')
    file_hdlr.setFormatter(formatter)

    # Start IOLoop and Server
    io_loop = IOLoop.current()
    try:
        server = ServerManager(args)
    except Exception:
        logging.exception("Moonraker Error")
        return
    try:
        server.start()
        io_loop.start()
    except Exception:
        server.error_exit()
        logging.exception("Server Running Error")
    io_loop.close(True)
    logging.info("Server Shutdown")


if __name__ == '__main__':
    main()
