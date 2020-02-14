# Websocket Request/Response Handler
#
# Copyright (C) 2019 Eric Callahan <arksine.code@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license

import logging
import eventlet
from eventlet import websocket
from eventlet.green import socket
from eventlet.semaphore import Semaphore
from server_util import ServerError, json_encode, json_loads_byteified

# Dictionary containing APIs that must query the host.  The value is the
# argument count.  As of now client requests take no more than one
# argument
KLIPPY_API_REQUESTS = {
    'get_klippy_info': 0, 'run_gcode': 1, 'get_status': 1,
    'get_object_info': 0, 'add_subscription': 1, 'get_subscribed': 0,
    'start_print': 1, 'cancel_print': 0, 'pause_print': 0,
    'resume_print': 0, 'restart': 0, 'firmware_restart': 0
}

class JsonRPC:
    def __init__(self):
        self.methods = {}

    def register_method(self, name, method):
        self.methods[name] = method

    def dispatch(self, data):
        response = None
        try:
            request = json_loads_byteified(data)
        except Exception:
            msg = "Websocket data not json: %s" % (str(data))
            logging.exception("[WEBSERVER]: " + msg)
            response = self.build_error(-32700, "Parse error")
            return json_encode(response)
        if isinstance(request, list):
            response = []
            for req in request:
                resp = self.process_request(req)
                if resp is not None:
                    response.append(resp)
            if not response:
                response = None
        else:
            response = self.process_request(request)
        if response is not None:
            response = json_encode(response)
        return response

    def process_request(self, request):
        req_id = request.get('id', None)
        rpc_version = request.get('jsonrpc', "")
        method_name = request.get('method', None)
        if rpc_version != "2.0" or not isinstance(method_name, str):
            return self.build_error(-32600, "Invalid Request", req_id)
        method = self.methods.get(method_name, None)
        if method is None:
            return self.build_error(-32601, "Method not found", req_id)
        if 'params' in request:
            params = request['params']
            if isinstance(params, list):
                response = self.execute_method(method, req_id, *params)
            elif isinstance(params, dict):
                response = self.execute_method(method, req_id, **params)
            else:
                return self.build_error(-32600, "Invalid Request", req_id)
        else:
            response = self.execute_method(method, req_id)
        return response

    def execute_method(self, method, req_id, *args, **kwargs):
        try:
            result = method(*args, **kwargs)
        except TypeError as e:
            return self.build_error(-32603, "Invalid params", req_id)
        except Exception as e:
            # TODO: We may need to process different
            # Exception types so we return the correct
            # error
            return self.build_error(-31000, str(e), req_id)
        if isinstance(result, ServerError):
            return self.build_error(result.status_code, result.message, req_id)
        elif req_id is None:
            return None
        else:
            return self.build_result(result, req_id)

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

class WebsocketHandler:
    def __init__(self, wsgi_mgr):
        self.wsgi_mgr = wsgi_mgr
        self.sd_path = wsgi_mgr.sd_path
        self.websockets = {}
        self.ws_lock = Semaphore()
        self.rpc = JsonRPC()
        self._register_api_methods()
        self.__call__ = websocket.WebSocketWSGI(self._handle_websocket)

    def register_rpc_method(self, request_name):
        # Only "GET" methods are allowed, with zero parameters
        func = self._get_method_callback(request_name, 0)
        self.rpc.register_method(request_name, func)

    def _register_api_methods(self):
        # register local methods
        self.rpc.register_method('ping', lambda: "pong")
        self.rpc.register_method('get_file_list', self.wsgi_mgr.get_file_list)

        # Register Klippy methods.  We cannot register lambdas or call
        # _process_websocket_request directly because we must strictly
        # enforce the argument count for JSON-RPC to detect a parameter
        # error
        for request, arg_count in KLIPPY_API_REQUESTS.items():
            func = self._get_method_callback(request, arg_count)
            self.rpc.register_method(request, func)

    def _get_method_callback(self, request, arg_count):
        def func(*args):
            if len(args) != arg_count:
                raise TypeError("Invalid Argument Count")
            return self._process_websocket_request(request, *args)
        return func

    def _handle_websocket(self, ws):
        ws_id = id(ws)
        self.add_websocket(ws, ws_id)
        try:
            while True:
                data = ws.wait()
                if data is None:
                    break
                eventlet.spawn_n(self._handle_request, ws, data)
        except Exception:
            # Don't log the exception we know will be raised when the server
            # closes the socket on shutdown
            if self.wsgi_mgr.server_running:
                logging.exception(
                    "[WEBSERVER]: Error Processing websocket data")
        finally:
            self.remove_websocket(ws, ws_id)

    def _handle_request(self, ws, request):
        try:
            response = self.rpc.dispatch(request)
            if response is not None:
                ws.send(response)
        except Exception:
            logging.exception("[WEBSERVER]: Websocket Command Error")

    def _process_websocket_request(self, request, *args):
        evt, uid = self.wsgi_mgr.create_event()
        timeout = self.wsgi_mgr.forward_klippy_request(request, uid, *args)
        response = evt.wait(timeout=timeout)
        if response is None:
            self.wsgi_mgr.remove_event(uid)
            result = ServerError(request, "Klippy Request Timed Out")
        else:
            result = response['result']
        return result

    def has_websocket(self, ws_id):
        return ws_id in self.websockets

    def add_websocket(self, ws, ws_id):
        with self.ws_lock:
            self.websockets[ws_id] = ws
            logging.info("[WEBSERVER]: New Websocket Added: %d" % ws_id)

    def remove_websocket(self, ws, ws_id):
        with self.ws_lock:
            old_ws = self.websockets.pop(ws_id, None)
            if old_ws is not None:
                logging.info("[WEBSERVER]: Websocket Removed: %d" % ws_id)

    def send_all_websockets(self, data):
        with self.ws_lock:
            for ws in self.websockets.values():
                try:
                    ws.send(data)
                except socket.error:
                    logging.exception(
                        "[WEBSERVER]: Error sending data over websocket")

    def send(self, data, ws_id):
        ws = self.websockets.get(ws_id, None)
        if ws is not None:
            try:
                ws.send(data)
            except socket.error:
                logging.exception(
                    "[WEBSERVER]: Error sending data over websocket")
                return False
            return True
        return False

    def close(self):
        with self.ws_lock:
            for ws in self.websockets.values():
                ws.close()
            self.websockets = {}
