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
from authorization import ApiAuth
from api import app

class WSGIManager:
    error = ServerError
    def __init__(self, request_callback, server_config):
        self.send_klippy_cmd = request_callback
        self.hostname = server_config.get('host', '0.0.0.0')
        self.port = server_config.get('port', 7125)
        self.server_running = False
        self.api_key = None
        self.www_path = server_config.get('web_path')
        self.sd_path = server_config.get('sd_path')
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
            'event': self._process_event,
            'kill_server': self._kill_server}
        self.server_thread = threading.Thread(
            target=self.start, args=[server_config])
        self.server_thread.start()

    def start(self, server_config):
        self.server_running = True
        self._init_authorization(server_config)
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

    def _init_authorization(self, server_config):
        app.config.update({
            'api_auth.api_key_path': server_config.get('api_key_path'),
            'api_auth.enabled': server_config.get('require_auth'),
            'api_auth.trusted_ips': server_config.get('trusted_ips'),
            'api_auth.trusted_ranges': server_config.get('trusted_ranges'),
            'api_auth.enable_cors': server_config.get('enable_cors')})
        api_auth = app.install(ApiAuth())
        self.api_key = api_auth.get_api_key()

    def _wsgi_gthread_handler(self):
        self.listener = eventlet.listen((self.hostname, self.port))
        self.listener.setsockopt(
            socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        logging.info(
            "[WEBSERVER]: Starting WSGI Server on (%s, %d)" %
            (self.hostname, self.port))
        wsgi.server(self.listener, app, environ={
            'WSGI_MGR': self, 'WS_HDLR': self.ws_handler}, debug=DEBUG)

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
        data = {'filename': filename, 'action': action,
                'filelist': filelist}
        eventlet.spawn_n(self._process_event, 'file_changed_event', data)

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

    def _process_klippy_response(self, uid, command, data):
        resp = {'command': command, 'data': data}
        evt = self.remove_event(uid)
        if evt is not None:
            evt.send(resp)

    def _process_event(self, event, data):
        if event == 'printer_state_event':
            self.klippy_printing = data.lower() == "printing"
        resp = json_encode({'command': event, 'data': data})
        self.ws_handler.send_all_websockets(resp)

    def _kill_server(self, data=None):
        logging.info(
            "[WEBSERVER]: Shutting Down Webserver at request from Klippy")
        if self.server_running:
            self.server_running = False
            for evt in self.pending_events.values():
                evt.send({"command": "shutdown", "data": "shutdown"})
            self.pending_events = {}
            self.ws_handler.close()
            self.listener.close()
            self.wsgi_gthrd.kill(eventlet.StopServe)
            app.close()

    def run_async_server_cmd(self, cmd, *args):
        self.command_queue.put_nowait((cmd, args))
        try:
            os.write(self._notify_fd, '.')
        except os.error:
            pass

    def get_api_key(self):
        return self.api_key

    def shutdown(self):
        if self.server_thread.is_alive():
            self.run_async_server_cmd("kill_server", [])
            self.server_thread.join()
