# Tornado Server with Websockets
#
# Copyright (C) 2020 Eric Callahan <arksine.code@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license
import os
import time
import logging
import multiprocessing
import tornado
import util
from tornado import gen
from tornado.ioloop import IOLoop, PeriodicCallback
from tornado.util import TimeoutError
from tornado.locks import Event
from ws_manager import WebsocketManager
from server_util import ServerError, json_encode
from api import RestAPI
from authorization import AuthManager

LOG_FILE = "klippy_server.log"
PROCESS_CHECK_MS = 100

class ServerManager:
    def __init__(self, pipe, server_config):
        self.host = server_config.get('host', '0.0.0.0')
        self.port = server_config.get('port', 7125)
        self.parent_pid = server_config.get('parent_pid')
        # Setup command timeouts
        self.request_timeout = server_config.get('request_timeout', 5.)
        self.long_running_gcodes = server_config.get('long_running_gcodes')
        self.long_running_requests = server_config.get('long_running_requests')
        self.server = self.rest_api = self.auth_manager = None
        self.server_running = False
        self.ws_manager = WebsocketManager(self)
        self.klippy_pipe = pipe
        self.process_check_cb = None
        self.server_io_loop = None

        # Setup host/server callbacks
        self.events = {}
        self.server_callbacks = {
            'register_hooks': self._register_webhooks,
            'response': self._handle_klippy_response,
            'notification': self._handle_notification,
            'shutdown': self._handle_shutdown_request,
        }

    def start(self, server_config):
        try:
            self.server_io_loop = IOLoop.current()
            self.auth_manager = AuthManager(server_config)
            self.rest_api = RestAPI(self, server_config)
            app = self.rest_api.get_app()
            logging.info(
                "[WEBSERVER]: Starting Tornado Server on (%s, %d)" %
                (self.host, self.port))
            self.server = app.listen(
                self.port, address=self.host,
                max_body_size=200*1024*1024)
        except Exception:
            logging.exception("[WEBSERVER]: Error starting server")
            if self.auth_manager is not None:
                self.auth_manager.close()
            if self.server_io_loop is not None:
                self.server_io_loop.close(True)
            return
        self.server_running = True
        self.server_io_loop.add_handler(
            self.klippy_pipe.fileno(), self._handle_klippy_data,
            IOLoop.READ)
        self.process_check_cb = PeriodicCallback(
            self._check_parent_proc, PROCESS_CHECK_MS)
        self.process_check_cb.start()
        self.server_io_loop.start()
        self.server_io_loop.close(True)
        logging.info("Server Shutdown")

    def _handle_klippy_data(self, fd, events):
        try:
            resp, args = self.klippy_pipe.recv()
        except Exception:
            return
        cb = self.server_callbacks.get(resp)
        if cb is not None:
            cb(*args)

    def _check_parent_proc(self):
        # Looks hacky, but does not actually kill the parent process
        try:
            os.kill(self.parent_pid, 0)
        except OSError:
            logging.info("[WEBSERVER]: Parent process killed, exiting")
            self.server_io_loop.spawn_callback(self._kill_server)

    def _register_webhooks(self, hooks):
        self.server_io_loop.add_callback(
            self._register_hooks, hooks)

    def _handle_klippy_response(self, request_id, response):
        evt = self.events.pop(request_id)
        if evt is not None:
            evt.notify(response)

    def _handle_notification(self, notification, state):
        self.server_io_loop.spawn_callback(
            self._process_notification, notification, state)

    def _handle_shutdown_request(self):
        self.server_io_loop.spawn_callback(self._kill_server)

    def make_request(self, path, method, args):
        timeout = self.long_running_requests.get(
            path, self.request_timeout)

        if path == "/printer/gcode":
            script = args.get('script', "")
            base_gc = script.strip().split()[0].upper()
            timeout = self.long_running_gcodes.get(base_gc, timeout)

        base_request = BaseRequest(path, method, args)
        event = ServerEvent(self.server_io_loop, timeout, base_request)
        self.events[base_request.id] = event
        self.klippy_pipe.send(base_request)
        return event

    def notify_filelist_changed(self, filename, action):
        self.server_io_loop.spawn_callback(
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
    def _process_notification(self, name, data):
        # Send Event Over Websocket in JSON-RPC 2.0 format.
        resp = json_encode({
            'jsonrpc': "2.0",
            'method': "notify_" + name,
            'params': [data]})
        yield self.ws_manager.send_all_websockets(resp)

    def _register_hooks(self, hooks):
        self.ws_manager.register_api_hooks(hooks)
        self.rest_api.register_api_hooks(hooks)

    @gen.coroutine
    def _kill_server(self):
        logging.info(
            "[WEBSERVER]: Shutting Down Webserver")
        self.process_check_cb.stop()
        if self.server_running:
            self.server_running = False
            self.server.stop()
            yield self.ws_manager.close()
            self.auth_manager.close()
            self.server_io_loop.stop()

class ServerEvent:
    def __init__(self, io_loop, timeout, request):
        self._server_io_loop = io_loop
        self._timeout = timeout
        self._event = Event()
        self.request = request
        self.response = None
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
            logging.info("[WEBSERVER]: Request '%s' Timed Out" %
                         (self.request.method + " " + self.request.path))
            raise gen.Return(ServerError("Klippy Request Timed Out", 500))
        raise gen.Return(self.response)

    def notify(self, response):
        self.response = response
        self._server_io_loop.add_callback(self._event.set)

# Basic WebRequest class pass over the pipe to reduce the amount of
# data pickled/unpickled
class BaseRequest:
    error = ServerError
    def __init__(self, path, method, args):
        self.id = id(self)
        self.path = path
        self.method = method
        self.args = args

def _start_server(pipe, config):
    # Set up root logger on new process
    root_logger = logging.getLogger()
    log_file = os.path.join("/tmp", LOG_FILE)
    file_hdlr = logging.handlers.TimedRotatingFileHandler(
        log_file, when='midnight', backupCount=2)
    root_logger.addHandler(file_hdlr)
    root_logger.setLevel(logging.DEBUG)
    logging.info("Starting Klippy Tornado Server...")

    # Start Tornado Server
    try:
        server = ServerManager(pipe, config)
        server.start(config)
    except Exception:
        logging.exception("Error Running Server")

def load_server_process(server_config):
    pp, cp = multiprocessing.Pipe()
    util.set_nonblock(pp.fileno())
    util.set_nonblock(cp.fileno())
    proc = multiprocessing.Process(
        target=_start_server, args=(cp, server_config,))
    proc.start()
    return pp, proc
