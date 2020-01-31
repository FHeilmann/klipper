# Klipper Web Server API
#
# Copyright (C) 2019 Eric Callahan <arksine.code@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license

import logging
import eventlet
from eventlet.green import os
from server_util import DEBUG, json_encode
from authorization import ApiAuth

bottle = eventlet.import_patched('bottle')
request = bottle.request
response = bottle.response
static_file = bottle.static_file

bottle.debug(DEBUG)
app = bottle.Bottle(autojson=False)
app.install(bottle.JSONPlugin(json_dumps=json_encode))

# HTTP API Helpers
def _prepare_status_request(query):
    request_dict = {}
    for key, value in query.iteritems():
        if value:
            request_dict[key] = value.split(',')
        else:
            request_dict[key] = []
    return request_dict

def _process_http_request(command, data):
    mgr = request.environ['WSGI_MGR']
    evt, uid = mgr.create_event()
    mgr.send_klippy_cmd(command, uid, data)
    # TODO:  I need to vary the timeout.   A "long_running_commands"
    # option should be added to the config with a specified timeout.
    # The default should be shorter. 20 seconds should be long enough
    # for most moves to complete
    result = evt.wait(timeout=60.)
    if result is None:
        mgr.remove_event(uid)
        bottle.abort(500, "Klippy Request Timed Out")
    elif isinstance(result['data'], mgr.error):
        bottle.abort(result['data'].status_code, result['data'].message)
    return result

# XXX - BEGIN TEMPORARY ROUTES - XXX
# temporary routes to serve static files.  The complete version
# will not serve them, it will be up to the reverse proxy
@app.route('/', api_auth_allow=True)
def index():
    mgr = request.environ['WSGI_MGR']
    return static_file('index.html', root=mgr.www_path)

@app.route('/favicon.ico', api_auth_allow=True)
def load_favicon():
    mgr = request.environ['WSGI_MGR']
    return static_file('favicon.ico', root=mgr.www_path)

@app.route('/img/<stc_file>', api_auth_allow=True)
@app.route('/css/<stc_file>', api_auth_allow=True)
@app.route('/js/<stc_file>', api_auth_allow=True)
def load_static(stc_file):
    mgr = request.environ['WSGI_MGR']
    return static_file(stc_file, root=mgr.www_path)

# XXX - END TEMPORARY ROUTES - XXX


@app.route('/cors', method=['OPTIONS', 'GET'])
def check_cors():
    response.headers['Content-type'] = 'application/json'
    return '[1]'

@app.route('/websocket')
def open_socket():
    environ = request.environ
    ws_handler = environ['WS_HDLR']

    def sr(status, headers):
        sr.status = int(status.split(' ')[0])
        sr.headers = headers

    body = ws_handler(environ, sr)[0]
    if hasattr(sr, 'status'):
        return bottle.HTTPResponse(
            body=body, status=sr.status, headers=sr.headers)
    else:
        return ""

@app.get('/printer/klippy_info')
@app.get('/printer/klippy_info/')
def get_klippy_info():
    return _process_http_request('get_klippy_info', "")

@app.get('/printer/files')
@app.get('/printer/files/')
def get_virtualsd_file_list():
    mgr = request.environ['WSGI_MGR']
    filelist = mgr.get_file_list()
    if isinstance(filelist, mgr.error):
        bottle.abort(400, filelist['data'].message)
    return {'command': "get_file_list", 'data': filelist}

@app.get('/printer/files/<filename>')
def handle_file_download(filename):
    # XXX - It might be ok to allow item deletion during a print,
    # but not the current item printing
    mgr = request.environ['WSGI_MGR']
    if mgr.klippy_printing:
        bottle.abort(503, "Cannot Download While Printing")
    return static_file(filename, root=mgr.sd_path, download=True)

@app.get('/printer/log')
@app.get('/printer/log/')
def get_log():
    mgr = request.environ['WSGI_MGR']
    if mgr.klippy_printing:
        bottle.abort(503, "Cannot Download While Printing")
    return static_file("klippy.log", root="/tmp/", download=True)

@app.post('/api/files/local')
@app.post('/printer/files/upload')
def handle_file_upload():
    mgr = request.environ['WSGI_MGR']
    if mgr.klippy_printing:
        bottle.abort(503, "Cannot Upload While Printing")
    upload = request.files.get('file')
    try:
        destination = os.path.join(mgr.sd_path, upload.filename)
        upload.save(destination, overwrite=True)
        mgr.notify_filelist_changed(upload.filename, 'added')
    except Exception:
        bottle.abort(500, "Unable to save file")
    return {'command': "upload_file", 'data': upload.filename}

@app.delete('/printer/files/<filename>')
def handle_file_delete(filename):
    mgr = request.environ['WSGI_MGR']
    if mgr.klippy_printing:
        bottle.abort(503, "Cannot Delete While Printing")
    filepath = os.path.join(mgr.sd_path, filename)
    if os.path.isfile(filepath):
        os.remove(filepath)
        mgr.notify_filelist_changed(filename, 'removed')
        return {'command': "delete_file", 'data': filename}
    else:
        bottle.abort(400, "Bad Request")

@app.get('/printer/objects')
@app.get('/printer/objects/')
def query_state():
    accept_hdr = request.get_header(
        "Accept", default="json")
    is_poll_req = accept_hdr == 'text/event-stream'
    req_data = ""
    if request.query_string:
        req_data = _prepare_status_request(request.query)
        cmd = 'add_subscription' if is_poll_req else 'get_status'
    else:
        cmd = 'get_subscribed' if is_poll_req else 'get_object_info'
    return _process_http_request(cmd, req_data)

@app.post('/printer/print/start/<filename>')
def start_print(filename):
    return _process_http_request("start_print", filename)

@app.post('/printer/print/cancel')
def cancel_print():
    return _process_http_request("cancel_print", "")

@app.post('/printer/print/pause')
def pause_print():
    return _process_http_request("pause_print", "")

@app.post('/printer/print/resume')
def resume_print():
    return _process_http_request("resume_print", "")

@app.get('/printer/endstops')
@app.get('/printer/endstops/')
def query_endstops():
    return _process_http_request("query_endstops", "")

@app.post('/printer/gcode/<gcode>')
def run_gcode(gcode):
    return _process_http_request('run_gcode', gcode)

@app.get('/access/api_key')
def get_api_key():
    api_manager = ApiAuth.get_manager()
    api_key = api_manager.get_api_key()
    return {"command": "get_api_key", "data": api_key}

@app.post('/access/api_key')
def generate_api_key():
    mgr = request.environ['WSGI_MGR']
    api_manager = ApiAuth.get_manager()
    api_key = api_manager.generate_api_key()
    mgr.api_key = api_key
    return {'command': "generate_api_key", "data": api_key}

# Some http requests cannot add the API key to http headers, such as
# the websocket and hyperlinks to downloads.  If clients choose not
# to login they can request one time use tokens that may be applied
# to the query string or the form data
@app.get('/access/oneshot_token')
@app.get('/access/oneshot_token/')
def request_oneshot_token():
    api_manager = ApiAuth.get_manager()
    token = api_manager.get_access_token()
    return {'command': "get_oneshot_token", 'data': token}

# Octoprint upload compatibility: This allows applications
# to test their connection.
@app.get('/api/version')
def emulate_octoprint_version():
    return {
        'server': "1.1.1",
        'api': "0.1",
        'text': "OctoPrint Upload Emulator"}
