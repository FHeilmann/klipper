# Klipper Web Server Rest API
#
# Copyright (C) 2020 Eric Callahan <arksine.code@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license

import os
import mimetypes
import logging
import tornado
from tornado import gen
from server_util import DEBUG, ServerError
from ws_manager import WebsocketManager, WebSocket
from authorization import AuthorizedRequestHandler, AuthorizedFileHandler
from authorization import Authorization

# Max Upload Size of 200MB
MAX_UPLOAD_SIZE = 200 * 1024 * 1024

# Built-in Query String Parser
def _default_parser(request):
    query_args = request.query_arguments
    args = {}
    for key, vals in query_args.iteritems():
        if len(vals) != 1:
            raise tornado.web.HTTPError(404, "Invalid Query String")
        args[key] = vals[0]
    return args

class TornadoApp:
    def __init__(self, server_mgr, server_config):
        self.server_mgr = server_mgr
        self.tornado_server = None
        self.registered_hooks = []
        enable_cors = server_config.get('enable_cors', False)
        initial_hooks = server_config.get('initial_hooks', [])

        # Set Up Websocket and Authorization Managers
        self.websocket_manager = WebsocketManager(server_mgr)
        self.send_all_websockets = self.websocket_manager.send_all_websockets
        self.auth = Authorization(server_config)

        mimetypes.add_type('text/plain', '.log')
        mimetypes.add_type('text/plain', '.gcode')

        self.request_handlers = {
            'KlippyRequestHandler': KlippyRequestHandler,
            'FileRequestHandler': FileRequestHandler,
            'FileUploadHandler': FileUploadHandler,
            'TokenRequestHandler': TokenRequestHandler}

        app_handlers = [
            (r'/websocket', WebSocket,
             {'ws_manager': self.websocket_manager, 'auth': self.auth}),
            (r'/api/version', EmulateOctoprintHandler,
             {'server_manager': server_mgr, 'auth': self.auth})]
        app_handlers += self.prepare_handlers(initial_hooks)

        self.app = tornado.web.Application(
            app_handlers,
            serve_traceback=DEBUG,
            websocket_ping_interval=10,
            websocket_ping_timeout=30,
            enable_cors=enable_cors)

    def listen(self, host, port):
        self.tornado_server = self.app.listen(
            port, address=host, max_body_size=MAX_UPLOAD_SIZE,
            xheaders=True)

    @gen.coroutine
    def close(self):
        if self.tornado_server is not None:
            self.tornado_server.stop()
        yield self.websocket_manager.close()
        self.auth.close()

    def register_api_hooks(self, hooks):
        handlers = self.prepare_handlers(hooks)
        self.app.add_handlers(r'.*', handlers)

    def prepare_handlers(self, hooks):
        handlers = []
        for (pattern, methods, params) in hooks:
            if pattern in self.registered_hooks:
                # Don't allow multiple hooks
                continue
            self.registered_hooks.append(pattern)
            self.websocket_manager.register_api_hook(
                pattern, methods, params)
            request_type = params.pop('handler', 'KlippyRequestHandler')
            request_hdlr = self.request_handlers.get(request_type)
            hdlr_params = dict(params)
            if request_hdlr is not None:
                hdlr_params['server_manager'] = self.server_mgr
                hdlr_params['auth'] = self.auth
                if request_type == "KlippyRequestHandler":
                    # Base Klippy Requests require additional params
                    hdlr_params['methods'] = methods
                    params.setdefault('arg_parser', _default_parser)
                elif request_type == "FileRequestHandler":
                    hdlr_params['methods'] = methods
                    hdlr_params['pattern'] = pattern
                handlers.append((pattern, request_hdlr, hdlr_params))
        return handlers

class KlippyRequestHandler(AuthorizedRequestHandler):
    def initialize(self, server_manager, auth, methods, arg_parser):
        super(KlippyRequestHandler, self).initialize(server_manager, auth)
        self.methods = methods
        self.query_parser = arg_parser

    @gen.coroutine
    def get(self):
        if 'GET' in self.methods:
            yield self._process_http_request('GET')
        else:
            raise tornado.web.HTTPError(405)

    @gen.coroutine
    def post(self):
        if 'POST' in self.methods:
            yield self._process_http_request('POST')
        else:
            raise tornado.web.HTTPError(405)

    @gen.coroutine
    def delete(self):
        if 'DELETE' in self.methods:
            yield self._process_http_request('DELETE')
        else:
            raise tornado.web.HTTPError(405)

    @gen.coroutine
    def _process_http_request(self, method):
        args = {}
        if self.request.query:
            args = self.query_parser(self.request)
        request = self.manager.make_request(
            self.request.path, method, args)
        result = yield request.wait()
        if isinstance(result, ServerError):
            raise tornado.web.HTTPError(
                result.status_code, result.message)
        self.finish({'result': result})

class FileRequestHandler(AuthorizedFileHandler):
    def initialize(self, server_manager, auth, path, methods,
                   pattern=None, default_filename=None):
        super(FileRequestHandler, self).initialize(
            server_manager, auth, path, default_filename)
        self.methods = methods
        self.main_pattern = pattern

    def set_extra_headers(self, path):
        # The call below shold never return an empty string,
        # as the path should have already been validated to be
        # a file
        basename = os.path.basename(self.absolute_path)
        self.set_header(
            "Content-Disposition", "attachment; filename=%s" % (basename))

    @gen.coroutine
    def delete(self, path):
        if 'DELETE' not in self.methods:
            raise tornado.web.HTTPError(405)

        # Use the same method Tornado uses to validate the path
        self.path = self.parse_url_path(path)
        del path  # make sure we don't refer to path instead of self.path again
        absolute_path = self.get_absolute_path(self.root, self.path)
        self.absolute_path = self.validate_absolute_path(
            self.root, absolute_path)

        # Make sure the file isn't currently loaded
        request = self.manager.make_request(
            self.main_pattern, self.request.method,
            {'filename': self.absolute_path})
        result = yield request.wait()
        if isinstance(result, ServerError):
            raise tornado.web.HTTPError(
                503, "File is loaded, DELETE not permitted")

        os.remove(self.absolute_path)
        filename = os.path.basename(self.absolute_path)
        self.manager.notify_filelist_changed(filename, 'removed')
        self.finish({'result': filename})

class FileUploadHandler(AuthorizedRequestHandler):
    def initialize(self, server_manager, auth, path):
        super(FileUploadHandler, self).initialize(server_manager, auth)
        self.file_path = path

    @gen.coroutine
    def post(self):
        start_print = False
        print_args = self.request.arguments.get('print', [])
        if print_args:
            start_print = print_args[0].lower() == "true"
        upload = self.get_file()
        filename = "_".join(upload['filename'].strip().split())
        full_path = os.path.join(self.file_path, filename)
        # Make sure the file isn't currently loaded
        request = self.manager.make_request(
            self.request.path, self.request.method,
            {'filename': full_path})
        result = yield request.wait()
        if isinstance(result, ServerError):
            raise tornado.web.HTTPError(
                503, "File is loaded, upload not permitted")
        # Don't start if a print is currently in progress
        start_print = start_print and not result['print_ongoing']
        try:
            with open(full_path, 'wb') as fh:
                fh.write(upload['body'])
            self.manager.notify_filelist_changed(filename, 'added')
        except Exception:
            raise tornado.web.HTTPError(500, "Unable to save file")
        if start_print:
            # Make a Klippy Request to "Start Print"
            request = self.manager.make_request(
                "/printer/print/start", 'POST', {'filename': filename})
            result = yield request.wait()
            if isinstance(result, ServerError):
                raise tornado.web.HTTPError(
                    result.status_code, result.message)
        self.finish({'result': filename, 'print_started': start_print})

    def get_file(self):
        # File uploads must have a single file request
        if len(self.request.files) != 1:
            raise tornado.web.HTTPError(
                400, "Bad Request, can only process a single file upload")
        f_list = self.request.files.values()[0]
        if len(f_list) != 1:
            raise tornado.web.HTTPError(
                400, "Bad Request, can only process a single file upload")
        return f_list[0]

class TokenRequestHandler(AuthorizedRequestHandler):
    def get(self):
        token = self.auth.get_access_token()
        self.finish({'result': token})

class EmulateOctoprintHandler(AuthorizedRequestHandler):
    def get(self):
        self.finish({
            'server': "1.1.1",
            'api': "0.1",
            'text': "OctoPrint Upload Emulator"})
