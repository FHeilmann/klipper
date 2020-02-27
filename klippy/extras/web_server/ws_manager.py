# Websocket Request/Response Handler
#
# Copyright (C) 2020 Eric Callahan <arksine.code@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license

import logging
import tornado
from tornado import gen
from tornado.websocket import WebSocketHandler
from tornado.concurrent import Future
from server_util import ServerError, json_encode, json_loads_byteified

class JsonRPC:
    def __init__(self):
        self.methods = {}

    def register_method(self, name, method):
        self.methods[name] = method

    @gen.coroutine
    def dispatch(self, data):
        response = None
        try:
            request = json_loads_byteified(data)
        except Exception:
            msg = "Websocket data not json: %s" % (str(data))
            logging.exception("[WEBSERVER]: " + msg)
            response = self.build_error(-32700, "Parse error")
            raise gen.Return(json_encode(response))
        if isinstance(request, list):
            response = []
            for req in request:
                resp = yield self.process_request(req)
                if resp is not None:
                    response.append(resp)
            if not response:
                response = None
        else:
            response = yield self.process_request(request)
        if response is not None:
            response = json_encode(response)
        raise gen.Return(response)

    @gen.coroutine
    def process_request(self, request):
        req_id = request.get('id', None)
        rpc_version = request.get('jsonrpc', "")
        method_name = request.get('method', None)
        if rpc_version != "2.0" or not isinstance(method_name, str):
            raise gen.Return(
                self.build_error(-32600, "Invalid Request", req_id))
        method = self.methods.get(method_name, None)
        if method is None:
            raise gen.Return(
                self.build_error(-32601, "Method not found", req_id))
        if 'params' in request:
            params = request['params']
            if isinstance(params, list):
                response = yield self.execute_method(method, req_id, *params)
            elif isinstance(params, dict):
                response = yield self.execute_method(method, req_id, **params)
            else:
                raise gen.Return(
                    self.build_error(-32600, "Invalid Request", req_id))
        else:
            response = yield self.execute_method(method, req_id)
        raise gen.Return(response)

    @gen.coroutine
    def execute_method(self, method, req_id, *args, **kwargs):
        try:
            result = yield method(*args, **kwargs)
        except TypeError as e:
            raise gen.Return(
                self.build_error(-32603, "Invalid params", req_id))
        except Exception as e:
            raise gen.Return(self.build_error(-31000, str(e), req_id))
        if isinstance(result, ServerError):
            raise gen.Return(
                self.build_error(result.status_code, result.message, req_id))
        elif req_id is None:
            raise gen.Return(None)
        else:
            raise gen.Return(self.build_result(result, req_id))

    def build_result(self, result, req_id):
        return {
            'jsonrpc': "2.0",
            'result': result,
            'id': req_id
        }

    def build_error(self, code, msg, req_id=None):
        return {
            'jsonrpc': "2.0",
            'error': {'code': code, 'message': msg},
            'id': req_id
        }

class WebsocketManager:
    def __init__(self, server_mgr):
        self.server_mgr = server_mgr
        self.sd_path = server_mgr.sd_path
        self.websockets = {}
        self.ws_lock = tornado.locks.Lock()
        self.rpc = JsonRPC()
        # The file list request is the only method that is not
        # processed by the klippy host
        self._register_file_list()

    def register_api_hooks(self, hooks):
        for (path, methods, parser) in hooks:
            for method in methods:
                # Format the endpoint into something more json friendly
                cmd = method.lower() + path.replace('/', '_')
                rpc_cb = self._generate_callback(path, method)
                self.rpc.register_method(cmd, rpc_cb)

    def _generate_callback(self, path, method):
        @gen.coroutine
        def func(**kwargs):
            request = self.server_mgr.make_request(path, method, kwargs)
            result = yield request.wait()
            raise gen.Return(result)
        return func

    def _register_file_list(self):
        def handle_file_list_request():
            future = Future()
            file_list = self.server_mgr.get_file_list()
            future.set_result(file_list)
            return future

        self.rpc.register_method('get_printer_files', handle_file_list_request)

    def has_websocket(self, ws_id):
        return ws_id in self.websockets

    @gen.coroutine
    def add_websocket(self, ws):
        with (yield self.ws_lock.acquire()):
            self.websockets[ws.uid] = ws
            logging.info("[WEBSERVER]: New Websocket Added: %d" % ws.uid)

    @gen.coroutine
    def remove_websocket(self, ws):
        with (yield self.ws_lock.acquire()):
            old_ws = self.websockets.pop(ws.uid, None)
            if old_ws is not None:
                logging.info("[WEBSERVER]: Websocket Removed: %d" % ws.uid)

    @gen.coroutine
    def send_all_websockets(self, data):
        with (yield self.ws_lock.acquire()):
            for ws in self.websockets.values():
                try:
                    ws.write_message(data)
                except Exception:
                    logging.exception(
                        "[WEBSERVER]: Error sending data over websocket")

    @gen.coroutine
    def close(self):
        with (yield self.ws_lock.acquire()):
            for ws in self.websockets.values():
                ws.close()
            self.websockets = {}

class WebSocket(WebSocketHandler):
    def initialize(self, server_manager):
        self.ws_manager = server_manager.ws_manager
        self.auth_manager = server_manager.auth_manager
        self.rpc = self.ws_manager.rpc
        self.uid = id(self)

    def open(self):
        self.ws_manager.add_websocket(self)

    @gen.coroutine
    def on_message(self, message):
        try:
            response = yield self.rpc.dispatch(message)
            if response is not None:
                self.write_message(response)
        except Exception:
            logging.exception("[WEBSERVER]: Websocket Command Error")

    def on_close(self):
        self.ws_manager.remove_websocket(self)

    def check_origin(self, origin):
        if self.settings['enable_cors']:
            # allow CORS
            return True
        else:
            return super(WebSocket, self).check_origin(origin)

    # Check Authorized User
    def prepare(self):
        if not self.auth_manager.check_authorized(self.request):
            raise tornado.web.HTTPError(401, "Unauthorized")
