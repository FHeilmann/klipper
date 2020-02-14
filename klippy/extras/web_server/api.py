# Klipper Web Server API
#
# Copyright (C) 2019 Eric Callahan <arksine.code@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license

import sys
import logging
import eventlet
from eventlet.green import os
from server_util import DEBUG, json_encode, endpoint
from authorization import ApiAuth

class RestAPI:
    def __init__(self, wsgi_mgr, server_config):
        # We must force bottle to be re-imported on restart to correctly
        # initialze the local context.
        for module in ['bottle', 'bottle.ext', '__patched_module_bottle']:
            if module in sys.modules:
                del sys.modules[module]
        self.bottle = eventlet.import_patched('bottle')
        self.bottle.debug(DEBUG)
        self.request = self.bottle.request
        self.response = self.bottle.response
        self.static_file = self.bottle.static_file
        self.abort = self.bottle.abort

        self.wsgi_mgr = wsgi_mgr
        self.www_path = server_config.get('web_path')
        self.sd_path = server_config.get('sd_path')

        self.app = self.bottle.Bottle(autojson=False)
        self.app.install(self.bottle.JSONPlugin(json_dumps=json_encode))
        self.app.config.update(self._parse_bottle_config(server_config))
        self._add_routes()
        self.api_auth = self.app.install(ApiAuth(self.bottle))

    def get_app(self):
        return self.app

    def get_key(self):
        return self.api_auth.get_api_key()

    def close(self):
        self.app.close()

    def register_route(self, request_name, endpoint):
        def get_obj():
            return self._process_http_request(request_name)

        route = "/printer/extras/" + endpoint
        self.app.get(route, callback=get_obj)

    def _add_routes(self):
        for attr_name in dir(self):
            func = getattr(self, attr_name)
            if hasattr(func, 'route_dict'):
                routes = func.route_dict
                for route, kwargs in routes.iteritems():
                    self.app.route(route, callback=func, **kwargs)

    def _parse_bottle_config(self, server_config):
        return {
            'api_auth.api_key_path': server_config.get('api_key_path'),
            'api_auth.enabled': server_config.get('require_auth'),
            'api_auth.trusted_ips': server_config.get('trusted_ips'),
            'api_auth.trusted_ranges': server_config.get('trusted_ranges'),
            'api_auth.enable_cors': server_config.get('enable_cors')
        }

    def _prepare_status_request(self, query):
        request_dict = {}
        for key, value in query.iteritems():
            if value:
                request_dict[key] = value.split(',')
            else:
                request_dict[key] = []
        return request_dict

    def _process_http_request(self, command, *args):
        evt, uid = self.wsgi_mgr.create_event()
        timeout = self.wsgi_mgr.forward_klippy_request(command, uid, *args)
        resp = evt.wait(timeout=timeout)
        if resp is None:
            self.wsgi_mgr.remove_event(uid)
            self.abort(500, "Klippy Request Timed Out")
        elif isinstance(resp['result'], self.wsgi_mgr.error):
            self.abort(resp['result'].status_code,
                       resp['result'].message)
        return resp

    # XXX - BEGIN TEMPORARY ROUTES - XXX
    # temporary routes to serve static files.  The complete version
    # will not serve them, it will be up to the reverse proxy
    @endpoint.route('/', api_auth_allow=True)
    def index(self):
        return self.static_file('index.html', root=self.www_path)

    @endpoint.route('/favicon.ico', api_auth_allow=True)
    def load_favicon(self):
        return self.static_file('favicon.ico', root=self.www_path)

    @endpoint.route('/img/<stc_file>', api_auth_allow=True)
    @endpoint.route('/css/<stc_file>', api_auth_allow=True)
    @endpoint.route('/js/<stc_file>', api_auth_allow=True)
    def load_static(self, stc_file):
        return self.static_file(stc_file, root=self.www_path)

    # XXX - END TEMPORARY ROUTES - XXX

    @endpoint.route('/cors', method=['OPTIONS', 'GET'])
    def check_cors(self):
        self.response.headers['Content-type'] = 'application/json'
        return '[1]'

    @endpoint.route('/websocket')
    def open_socket(self):
        environ = self.request.environ
        ws_handler = self.wsgi_mgr.ws_handler

        def sr(status, headers):
            sr.status = int(status.split(' ')[0])
            sr.headers = headers

        body = ws_handler(environ, sr)[0]
        if hasattr(sr, 'status'):
            return self.bottle.HTTPResponse(
                body=body, status=sr.status, headers=sr.headers)
        else:
            return ""

    @endpoint.get('/printer/klippy_info')
    def get_klippy_info(self):
        return self._process_http_request('get_klippy_info')

    @endpoint.get('/printer/log')
    def get_log(self):
        if self.wsgi_mgr.is_printing():
            self.abort(503, "Cannot Download While Printing")
        return self.static_file("klippy.log", root="/tmp/", download=True)

    @endpoint.get('/printer/files')
    def get_virtualsd_file_list(self):
        filelist = self.wsgi_mgr.get_file_list()
        if isinstance(filelist, self.wsgi_mgr.error):
            self.abort(400, filelist['result'].message)
        return {'command': "get_file_list", 'result': filelist}

    @endpoint.get('/printer/files/<filename>')
    def handle_file_download(self, filename):
        if self.wsgi_mgr.is_printing():
            self.abort(503, "Cannot Download While Printing")
        return self.static_file(
            filename, root=self.sd_path, download=True)

    @endpoint.post('/api/files/local')
    @endpoint.post('/printer/files/upload')
    def handle_file_upload(self):
        if self.wsgi_mgr.is_printing():
            self.abort(503, "Cannot Upload While Printing")
        upload = self.request.files.get('file')
        start_after_upload = self.request.forms.get('print', "false")
        try:
            destination = os.path.join(self.sd_path, upload.filename)
            upload.save(destination, overwrite=True)
            self.wsgi_mgr.notify_filelist_changed(upload.filename, 'added')
        except Exception:
            self.abort(500, "Unable to save file")
        # TODO: start after upload must not be boolean when applied
        if start_after_upload.lower() == 'true':
            self._process_http_request('start_print', upload.filename)
        return {'command': "upload_file", 'result': upload.filename,
                'print_started': start_after_upload}

    @endpoint.delete('/printer/files/<filename>')
    def handle_file_delete(self, filename):
        if self.wsgi_mgr.is_printing():
            self.abort(503, "Cannot Delete While Printing")
        filepath = os.path.join(self.sd_path, filename)
        if os.path.isfile(filepath):
            os.remove(filepath)
            self.wsgi_mgr.notify_filelist_changed(filename, 'removed')
            return {'command': "delete_file", 'result': filename}
        else:
            self.abort(400, "Bad Request")

    @endpoint.get('/printer/objects')
    def query_state(self):
        accept_hdr = self.request.get_header(
            "Accept", default="json")
        is_poll_req = (accept_hdr == 'text/event-stream')
        if self.request.query_string:
            req_objs = self._prepare_status_request(self.request.query)
            cmd = 'add_subscription' if is_poll_req else 'get_status'
            return self._process_http_request(cmd, req_objs)
        else:
            cmd = 'get_subscribed' if is_poll_req else 'get_object_info'
            return self._process_http_request(cmd)

    @endpoint.post('/printer/print/start/<filename>')
    def start_print(self, filename):
        return self._process_http_request("start_print", filename)

    @endpoint.post('/printer/print/cancel')
    def cancel_print(self):
        return self._process_http_request("cancel_print")

    @endpoint.post('/printer/print/pause')
    def pause_print(self):
        return self._process_http_request("pause_print")

    @endpoint.post('/printer/print/resume')
    def resume_print(self):
        return self._process_http_request("resume_print")

    @endpoint.post('/printer/gcode/<gcode:path>')
    def run_gcode(self, gcode):
        return self._process_http_request('run_gcode', gcode)

    @endpoint.post('/printer/restart')
    def request_printer_restart(self):
        return self._process_http_request('restart')

    @endpoint.post('/printer/firmware_restart')
    def request_firmware_restart(self):
        return self._process_http_request('firmware_restart')

    @endpoint.get('/access/api_key')
    def request_api_key(self):
        api_key = self.api_auth.get_api_key()
        return {"command": "get_api_key", 'result': api_key}

    @endpoint.post('/access/api_key')
    def generate_api_key(self):
        api_key = self.api_auth.generate_api_key()
        return {'command': "generate_api_key", 'result': api_key}

    # Some http requests cannot add the API key to http headers, such as
    # the websocket and hyperlinks to downloads.  If clients choose not
    # to login they can request one time use tokens that may be applied
    # to the query string or the form data
    @endpoint.get('/access/oneshot_token')
    def request_oneshot_token(self):
        token = self.api_auth.get_access_token()
        return {'command': "get_oneshot_token", 'result': token}

    # Octoprint upload compatibility: This allows applications
    # to test their connection.
    @endpoint.get('/api/version')
    def emulate_octoprint_version(self):
        return {
            'server': "1.1.1",
            'api': "0.1",
            'text': "OctoPrint Upload Emulator"}
