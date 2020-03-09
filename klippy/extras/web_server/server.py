# Tornado Server with Websockets
#
# Copyright (C) 2020 Eric Callahan <arksine.code@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license
import os
import time
import logging
import threading
import tornado
from tornado import gen
from tornado.ioloop import IOLoop
from tornado.util import TimeoutError
from tornado.locks import Event
from ws_manager import WebsocketManager
from server_util import ServerError, json_encode
from api import RestAPI
from authorization import AuthManager

class ServerManager:
    def __init__(self, request_callback, server_config):
        self.send_klippy_request = request_callback
        self.host = server_config.get('host', '0.0.0.0')
        self.port = server_config.get('port', 7125)
        self.sd_path = server_config.get('sd_path')
        self.web_path = server_config.get('web_path')

        # Setup command timeouts
        self.request_timeout = server_config.get('request_timeout', 5.)
        self.long_running_gcodes = server_config.get('long_running_gcodes')
        self.long_running_requests = server_config.get('long_running_requests')
        self.server = self.rest_api = self.auth_manager = None
        self.server_running = False
        self.ws_manager = WebsocketManager(self)
        self.is_klippy_printing = False
        self.server_thread = threading.Thread(
            target=self.start, args=[server_config])
        self.server_io_loop = None
        # Do not start the server thread in batch mode
        if not server_config.get('is_fileoutput', False):
            self.server_thread.start()

    def start(self, server_config):
        try:
            self.server_io_loop = IOLoop.current()
            self.auth_manager = AuthManager(server_config)
            enable_cors = server_config.get("enable_cors", False)
            self.rest_api = RestAPI(self, enable_cors)
            app = self.rest_api.get_app()
            logging.info(
                "[WEBSERVER]: Starting Tornado Server on (%s, %d)" %
                (self.host, self.port))
            self.server = app.listen(self.port, address=self.host)
        except Exception:
            logging.exception("[WEBSERVER]: Error starting server")
            if self.auth_manager is not None:
                self.auth_manager.close()
            if self.server_io_loop is not None:
                self.server_io_loop.close(True)
            return
        self.server_running = True
        self.server_io_loop.start()
        self.server_io_loop.close(True)

    def make_request(self, path, method, args):
        timeout = self.long_running_requests.get(
            path, self.request_timeout)

        if path == "/printer/gcode":
            script = args.get('script', "")
            base_gc = script.strip().split()[0].upper()
            timeout = self.long_running_gcodes.get(base_gc, timeout)

        web_request = WebRequest(
            self.server_io_loop, path, method, args, timeout)
        self.send_klippy_request(web_request)
        return web_request

    def is_printing(self):
        return self.is_klippy_printing

    def notify_filelist_changed(self, filename, action):
        filelist = self.get_file_list()
        if isinstance(filelist, ServerError):
            filelist = []
        result = {'filename': filename, 'action': action,
                  'filelist': filelist}
        self.server_io_loop.spawn_callback(
            self._process_notification, 'filelist_changed', result)

    def get_file_list(self):
        filelist = []
        try:
            files = os.listdir(self.sd_path)
            for f in files:
                fullname = os.path.join(self.sd_path, f)
                if os.path.isfile(fullname) and not f.startswith('.'):
                    filelist.append({
                        'filename': f,
                        'size': os.path.getsize(fullname),
                        'modified': time.ctime(os.path.getmtime(fullname))
                    })
        except Exception:
            return ServerError("Unable to create file list")
        return sorted(filelist, key=lambda val: val['filename'].lower())

    @gen.coroutine
    def _process_notification(self, name, state):
        if name == 'printer_state_changed':
            self.is_klippy_printing = (state.lower() == "printing")
        # Send Event Over Websocket in JSON-RPC 2.0 format.
        resp = json_encode({
            'jsonrpc': "2.0",
            'method': "notify_" + name,
            'params': [state]})
        yield self.ws_manager.send_all_websockets(resp)

    def _register_hooks(self, hooks):
        self.ws_manager.register_api_hooks(hooks)
        self.rest_api.register_api_hooks(hooks)

    @gen.coroutine
    def _kill_server(self, reason=None):
        logging.info(
            "[WEBSERVER]: Shutting Down Webserver at request from Klippy")
        if self.server_running:
            self.server_running = False
            self.server.stop()
            yield self.ws_manager.close()
            self.auth_manager.close()
            self.server_io_loop.stop()

    def is_running(self):
        return self.server_running

    def get_api_key(self):
        if self.auth_manager is not None:
            return self.auth_manager.get_api_key()
        else:
            return ""

    # Methods Below allow commuinications from Klippy to Server.
    # The ioloop's "add_callback" method is thread safe

    def send_notification(self, notification, state):
        self.server_io_loop.add_callback(
            self._process_notification, notification, state)

    def send_webhooks(self, hooks):
        self.server_io_loop.add_callback(
            self._register_hooks, hooks)

    def shutdown(self):
        if self.server_thread.is_alive():
            self.server_io_loop.add_callback(self._kill_server)
            self.server_thread.join()


class WebRequest:
    error = ServerError
    def __init__(self, io_loop, path, method, args, timeout):
        self._server_io_loop = io_loop
        self.path = path
        self.method = method
        self.args = args
        self.response = None
        self._event = Event()
        self._timeout = timeout
        if timeout is not None:
            self._timeout = time.time() + timeout

    @gen.coroutine
    def wait(self):
        # Wait for klippy to process the request or until the timeout
        # has been reached.  This should only be called from the
        # server thread
        try:
            yield self._event.wait(timeout=self._timeout)
        except TimeoutError:
            raise gen.Return(ServerError("Klippy Request Timed Out", 500))
        raise gen.Return(self.response)

    def get(self, item):
        if item not in self.args:
            raise ServerError("Invalid Argument [%s]" % item)
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
        self.response = error

    def send(self, data):
        if self.response is not None:
            raise ServerError("Multiple calls to send not allowed")
        self.response = data

    def finish(self):
        if self.response is None:
            # No error was set and the user never executed
            # send, default response is "ok"
            self.response = "ok"
        self._server_io_loop.add_callback(self._event.set)
