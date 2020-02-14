# WSGI Server with Websockets
#
# Copyright (C) 2019 Eric Callahan <arksine.code@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license
import util
import logging
import threading
import Queue
import eventlet
from eventlet import wsgi
from eventlet.green import select, socket, os, time
from ws_handler import WebsocketHandler
from server_util import DEBUG, ServerError, json_encode
from api import RestAPI

class WSGIManager:
    error = ServerError
    def __init__(self, request_callback, server_config):
        self.klippy_request_callback = request_callback
        self.hostname = server_config.get('host', '0.0.0.0')
        self.port = server_config.get('port', 7125)
        self.sd_path = server_config.get('sd_path')

        # Setup command timeouts
        self.base_timeout = server_config.get('base_timeout', 5.)
        self.gcode_timeout = server_config.get('gcode_timeout', 60.)
        self.long_running_gcs = server_config.get('long_running_gcodes', {})

        self.server_running = False
        self.rest_api = None
        self.ws_handler = WebsocketHandler(self)
        self.command_queue = Queue.Queue()
        self._resume_fd, self._notify_fd = os.pipe()
        util.set_nonblock(self._notify_fd)
        util.set_nonblock(self._resume_fd)
        self.wsgi_gthrd = self.kreq_gthrd = None
        self.klippy_printing = False
        self.pending_events = {}
        self.command_callbacks = {
            'response': self._process_klippy_response,
            'notification': self._process_notification,
            'register_ep': self._register_endpoint,
            'kill_server': self._kill_server}
        self.server_thread = threading.Thread(
            target=self.start, args=[server_config])
        self.server_thread.start()

    def start(self, server_config):
        self.server_running = True
        self.rest_api = RestAPI(self, server_config)
        self.kreq_gthrd = eventlet.spawn(self._check_klippy_request)
        self.wsgi_gthrd = eventlet.spawn(self._wsgi_gthread_handler)
        try:
            self.wsgi_gthrd.wait()
        except eventlet.StopServe:
            pass
        except Exception:
            logging.exception("[WEBSERVER]: Server encountered an error")
        # give the request greenthread a chance to exit
        eventlet.sleep(.001)
        if bool(self.kreq_gthrd):
            logging.info(
                "[WEBSERVER]: Request Thread Still Running, "
                "forceably terminating")
            self.kreq_gthrd.kill()

    def _wsgi_gthread_handler(self):
        self.listener = eventlet.listen((self.hostname, self.port))
        self.listener.setsockopt(
            socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        logging.info(
            "[WEBSERVER]: Starting WSGI Server on (%s, %d)" %
            (self.hostname, self.port))
        wsgi.server(self.listener, self.rest_api.get_app(), debug=DEBUG)

    def _check_klippy_request(self):
        # The "green" eventlet queue is not thread safe, so
        # we need to use a standard Queue and mimic the
        # reactor's behavior to transfer data from the main
        # thread to the server thread.
        while self.server_running:
            select.select([self._resume_fd], [], [])
            try:
                os.read(self._resume_fd, 4096)
            except os.error:
                pass
            while 1:
                try:
                    cmd, args = self.command_queue.get_nowait()
                except Queue.Empty:
                    break
                cb = self.command_callbacks.get(cmd, None)
                if cb is not None:
                    cb(*args)

    def forward_klippy_request(self, request, uid, *args):
        timeout = self.base_timeout
        if request == "run_gcode":
            timeout = self._get_gcode_timeout(args[0])
        self.klippy_request_callback(request, uid, *args)
        return timeout

    def _get_gcode_timeout(self, gc):
        base_gc = gc.strip().split()[0].upper()
        return self.long_running_gcs.get(base_gc, self.gcode_timeout)

    def is_printing(self):
        return self.klippy_printing

    def create_event(self):
        evt = eventlet.Event()
        uid = id(evt)
        self.pending_events[uid] = evt
        return evt, uid

    def remove_event(self, evt_id):
        return self.pending_events.pop(evt_id, None)

    def notify_filelist_changed(self, filename, action):
        filelist = self.get_file_list()
        if isinstance(filelist, ServerError):
            filelist = []
        result = {'filename': filename, 'action': action,
                  'filelist': filelist}
        eventlet.spawn_n(
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
            return ServerError('get_file_list', "Unable to create file list")
        return sorted(filelist, key=lambda val: val['filename'].lower())

    def _process_klippy_response(self, uid, command, result):
        resp = {'command': command, 'result': result}
        evt = self.remove_event(uid)
        if evt is not None:
            evt.send(resp)

    def _process_notification(self, name, state):
        if name == 'printer_state_changed':
            self.klippy_printing = (state.lower() == "printing")
        # Send Event Over Websocket in JSON-RPC 2.0 format.
        resp = json_encode({
            'jsonrpc': "2.0",
            'method': "notify_" + name,
            'params': [state]})
        self.ws_handler.send_all_websockets(resp)

    def _register_endpoint(self, request_name, endpoint):
        self.rest_api.register_route(request_name, endpoint)
        self.ws_handler.register_rpc_method(request_name)

    def _kill_server(self, reason=None):
        logging.info(
            "[WEBSERVER]: Shutting Down Webserver at request from Klippy")
        if self.server_running:
            self.server_running = False
            reason = reason or "shutdown"
            for evt in self.pending_events.values():
                evt.send({"command": "shutdown", "result": reason})
            self.pending_events = {}
            self.ws_handler.close()
            self.listener.close()
            self.wsgi_gthrd.kill(eventlet.StopServe)
            self.rest_api.close()

    def run_async_server_cmd(self, cmd, *args):
        self.command_queue.put_nowait((cmd, args))
        try:
            os.write(self._notify_fd, '.')
        except os.error:
            pass

    def get_api_key(self):
        return self.rest_api.get_key()

    def shutdown(self):
        if self.server_thread.is_alive():
            self.run_async_server_cmd("kill_server", [])
            self.server_thread.join()
