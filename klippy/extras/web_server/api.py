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
from ws_manager import WebSocket
from authorization import AuthorizedRequestHandler, AuthorizedFileHandler

# Built-in Query String Parsers
def _default_parser(query_args):
    args = {}
    for key, vals in query_args.iteritems():
        if len(vals) != 1:
            raise tornado.web.HTTPError(404, "Invalid Query String")
        args[key] = vals[0]
    return args

def _object_parser(query_args):
    args = {}
    for key, vals in query_args.iteritems():
        parsed = []
        for v in vals:
            if v:
                parsed += v.split(',')
        args[key] = parsed
    return args

class RestAPI:
    def __init__(self, server_mgr, enable_cors):
        self.server_mgr = server_mgr
        self.web_path = web_path = server_mgr.web_path
        self.sd_path = sd_path = server_mgr.sd_path

        mimetypes.add_type('text/plain', '.log')
        mimetypes.add_type('text/plain', '.gcode')

        self.tornado_app = tornado.web.Application([
            # Printer File management
            (r'/printer/log()', PrinterFileHandler,
             {'server_manager': server_mgr,
              'allow_delete': False, 'path': "/tmp/klippy.log"}),
            (r'/printer/files', FileListHandler,
             {'server_manager': server_mgr}),
            (r'/printer/files/upload', FileUploadHandler,
             {'server_manager': server_mgr}),
            (r'/api/files/local', FileUploadHandler,
             {'server_manager': server_mgr}),
            (r'/printer/files/(.*)', PrinterFileHandler,
             {'server_manager': server_mgr, 'path': sd_path}),
            (r'/api/version', EmulateOctoprintHandler,
             {'server_manager': server_mgr}),
            # Access Control
            (r'/access/api_key', APIKeyRequestHandler,
             {'server_manager': server_mgr}),
            (r'/access/oneshot_token', TokenRequestHandler,
             {'server_manager': server_mgr}),
            # Static Files
            (r'/(fonts/.*)', AuthorizedFileHandler,
             {'server_manager': server_mgr, 'path': web_path}),
            (r'/(img/.*)', AuthorizedFileHandler,
             {'server_manager': server_mgr, 'path': web_path}),
            (r'/(css/.*)', AuthorizedFileHandler,
             {'server_manager': server_mgr, 'path': web_path}),
            (r'/(js/.*)', AuthorizedFileHandler,
             {'server_manager': server_mgr, 'path': web_path}),
            (r'/(favicon\.ico)', AuthorizedFileHandler,
             {'server_manager': server_mgr, 'path': web_path}),
            (r'/[^/]*', IndexHandler, {'server_manager': server_mgr})],
            template_path=web_path,
            serve_traceback=DEBUG,
            websocket_ping_interval=10,
            websocket_ping_timeout=30,
            enable_cors=enable_cors)

    def get_app(self):
        return self.tornado_app

    def register_api_hooks(self, hooks):
        # add the websocket handler here as well.  We don't
        # want it available before the hooks are applied
        # to the websocket
        handlers = [
            (r'/websocket', WebSocket, {'server_manager': self.server_mgr})
        ]

        for (path, methods, parser) in hooks:
            arg_parser = self._get_parser(parser)
            params = {
                'server_manager': self.server_mgr,
                'methods': methods,
                'arg_parser': arg_parser}
            handlers.append((path, KlippyRequestHandler, params))
        self.tornado_app.add_handlers(r'.*', handlers)

    def _get_parser(self, arg_parser):
        if callable(arg_parser):
            return arg_parser

        if arg_parser.lower() == "object":
            return _object_parser
        else:
            return _default_parser

class IndexHandler(AuthorizedRequestHandler):
    def get(self):
        self.render("index.html")

class KlippyRequestHandler(AuthorizedRequestHandler):
    def initialize(self, server_manager, methods, arg_parser,
                   always_allow=False):
        super(KlippyRequestHandler, self).initialize(
            server_manager, always_allow)
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
    def _process_http_request(self, method):
        args = {}
        if self.request.query:
            args = self.query_parser(self.request.query_arguments)
        request = self.manager.make_request(
            self.request.path, method, args)
        result = yield request.wait()
        if isinstance(result, ServerError):
            raise tornado.web.HTTPError(
                result.status_code, result.message)
        self.finish({'result': result})

class PrinterFileHandler(AuthorizedFileHandler):
    def initialize(self, server_manager, path, allow_delete=True,
                   default_filename=None):
        super(PrinterFileHandler, self).initialize(
            server_manager, path, default_filename)
        self.allow_delete = allow_delete

    def validate_absolute_path(self, root, absolute_path, check_delete=False):
        # Validate that we arent printing
        filename = None
        if check_delete:
            # Get the base file name so we can check against the
            # currently loaded file
            filename = os.path.basename(absolute_path)
        msg = self.manager.check_file_operation(filename)
        if msg:
            raise tornado.web.HTTPError(503, msg)

        return super(PrinterFileHandler, self).validate_absolute_path(
            root, absolute_path)

    def set_extra_headers(self, path):
        # The call below shold never return an empty string,
        # as the path should have already been validated to be
        # a file
        basename = os.path.basename(self.absolute_path)
        self.set_header(
            "Content-Disposition", "attachment; filename=%s" % (basename))

    def delete(self, path):
        if not self.allow_delete:
            raise tornado.web.HTTPError(405)

        # Use the same method Tornado uses to validate the path
        self.path = self.parse_url_path(path)
        del path  # make sure we don't refer to path instead of self.path again
        absolute_path = self.get_absolute_path(self.root, self.path)
        self.absolute_path = self.validate_absolute_path(
            self.root, absolute_path, True)
        if self.absolute_path is None:
            return

        os.remove(self.absolute_path)
        filename = os.path.basename(self.absolute_path)
        self.manager.notify_filelist_changed(filename, 'removed')
        self.finish({'result': filename})

class FileListHandler(AuthorizedRequestHandler):
    def get(self):
        filelist = self.manager.get_file_list()
        if isinstance(filelist, ServerError):
            raise tornado.web.HTTPError(400, filelist['result'].message)
        self.finish({'result': filelist})

class FileUploadHandler(AuthorizedRequestHandler):
    @gen.coroutine
    def post(self):
        start_after_upload = False
        print_args = self.request.arguments.get('print', [])
        if print_args:
            start_after_upload = print_args[0].lower() == "true"
        upload = self.get_file()
        filename = "_".join(upload['filename'].strip().split())
        msg = self.manager.check_file_operation(filename)
        if msg:
            raise tornado.web.HTTPError(503, msg)
        try:
            self.copy_file(filename, upload['body'])
            self.manager.notify_filelist_changed(filename, 'added')
        except Exception:
            raise tornado.web.HTTPError(500, "Unable to save file")
        if start_after_upload:
            # Make a Klippy Request to "Start Print"
            request = self.manager.make_request(
                "/printer/print/start", 'POST', {'filename': filename})
            result = yield request.wait()
            if isinstance(result, ServerError):
                raise tornado.web.HTTPError(
                    result.status_code, result.message)
        self.finish({'result': filename, 'print_started': start_after_upload})

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

    def copy_file(self, filename, data):
        destination = os.path.join(self.manager.sd_path, filename)
        with open(destination, 'wb') as fh:
            fh.write(data)

class APIKeyRequestHandler(AuthorizedRequestHandler):
    def get(self):
        api_key = self.auth_manager.get_api_key()
        self.finish({'result': api_key})

    def post(self):
        api_key = self.auth_manager.generate_api_key()
        self.finish({'result': api_key})

class TokenRequestHandler(AuthorizedRequestHandler):
    def get(self):
        token = self.auth_manager.get_access_token()
        self.finish({'result': token})

class EmulateOctoprintHandler(AuthorizedRequestHandler):
    def get(self):
        self.finish({
            'server': "1.1.1",
            'api': "0.1",
            'text': "OctoPrint Upload Emulator"})
