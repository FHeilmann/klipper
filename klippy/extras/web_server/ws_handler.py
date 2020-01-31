# Websocket Request/Response Handler
#
# Copyright (C) 2019 Eric Callahan <arksine.code@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license

import logging
import eventlet
from eventlet import websocket
from eventlet.green import os, socket
from eventlet.semaphore import Semaphore
from server_util import ServerError, json_encode, json_loads_byteified

BUFFER_SIZE = 65536

class FileUpload:
    def __init__(self, sd_path, data, ws_id):
        self.file_name = self.get_param('filename', data)
        self.file_size = self.get_param('size', data)
        self.total_chunks = self.get_param('chunks', data)
        self.chunk_size = self.get_param('chunk_size', data)
        self.current_chunk = 0
        self.name = os.path.join(sd_path, self.file_name)
        self.ws_id = ws_id
        try:
            self.filep = open(self.name, 'wb')
        except Exception:
            raise ServerError(
                'upload_file', "Unable to open file for writing '%s'"
                % (self.name))

    def get_param(self, name, data):
        param = data.get(name)
        if param is None:
            raise ServerError('upload_file', "Upload Missing '%s'" % (name))
        return param

    def write_file(self, data):
        try:
            while data:
                self.filep.write(data[:BUFFER_SIZE])
                data = data[BUFFER_SIZE:]
        except Exception:
            self.filep.close()
            raise ServerError('upload_file', "Error Writing File")
        self.current_chunk += 1
        if self.current_chunk >= self.total_chunks:
            self.filep.close()
            size = os.path.getsize(self.name)
            if size != self.file_size:
                raise ServerError(
                    'upload_file', "File Size mismatch,"
                    "expected: %d | actual: %d" %
                    (self.file_size, size))
            else:
                logging.info(
                    "[WEBSERVER]: Uploaded file from websocket: %s Size: %d"
                    % (self.name, self.file_size))
            return True
        return False

# XXX - Add ability to response to a PING command.  Also add ability to handle
# unique identifiers recieved from the websocket (so it can guarantee responses
# match requests).  Also add "command_type" to the dictionary.  The types can
# be "event", "response", "error".  Also, rather than send back a "data"
# attribute, it may be better to just put the data in the top level dictionary.
# The API documentation can explain how each response should be formatted.
# This would affect the REST API as well.
class WebsocketHandler:
    def __init__(self, wsgi_mgr):
        self.wsgi_mgr = wsgi_mgr
        self.send_klippy_cmd = wsgi_mgr.send_klippy_cmd
        self.sd_path = wsgi_mgr.sd_path
        self.websockets = {}
        self.ws_lock = Semaphore()
        self.pending_uploads = {}
        self.server_commands = {
            'get_file_list': self._get_file_list,
            'download_file': self._process_download,
            'upload_file': self._prepare_upload,
            'delete_file': self._process_delete
        }
        self.__call__ = websocket.WebSocketWSGI(self._handle_websocket)

    def _handle_websocket(self, ws):
        ws_id = id(ws)
        self.add_websocket(ws, ws_id)
        try:
            while True:
                data = ws.wait()
                if data is None:
                    break
                if ws_id in self.pending_uploads:
                    fupload = self.pending_uploads[ws_id]
                    self._process_upload(data, fupload)
                else:
                    self.process_command(data, ws_id)
        except Exception:
            # Don't log the exception we know will be raised when the server
            # closes the socket on shutdown
            if self.wsgi_mgr.server_running:
                logging.exception("[WEBSERVER]: Error Processing websocket data")
        finally:
            self.remove_websocket(ws, ws_id)

    def process_command(self, data, ws_id):
        try:
            data = json_loads_byteified(data)
        except ValueError:
            msg = "Websocket data not json: %s" % (str(data))
            logging.exception("[WEBSERVER]: " + msg)
            self.send(self._build_error("unknown", msg), ws_id)
            return
        # Add websocket ID to commands
        for cmd, payload in data.iteritems():
            if cmd in self.server_commands:
                self.server_commands[cmd](payload, ws_id)
            else:
                self._send_klippy_request(cmd, payload, ws_id)

    def _build_error(self, cmd, msg):
        err = ServerError(cmd, msg)
        return self._build_response('server_error', err)

    def _build_response(self, cmd, data):
        return json_encode({'command': cmd, 'data': data})

    def _get_file_list(self, data, ws_id):
        filelist = self.wsgi_mgr.get_file_list()
        if isinstance(filelist, ServerError):
            resp = self._build_response('server_error', filelist)
        else:
            resp = self._build_response('get_file_list', filelist)
        self.send(resp, ws_id)

    def _process_download(self, data, ws_id):
        err = None
        if self.wsgi_mgr.klippy_printing:
            err = self._build_error(
                'download_file', "Cannot download while Klippy is Printing")
        else:
            filename = data.get('filename', "")
            fullname = os.path.join(self.sd_path, filename)
            if os.path.isfile(fullname):
                # Remove the current websocket from the websockets dict.
                # We do not want klippy events sent between download packets
                with self.ws_lock:
                    ws = self.websockets.pop(ws_id)
                try:
                    size = os.path.getsize(fullname)
                    chunks = abs(-size // BUFFER_SIZE)
                    finfo = self._build_response(
                        'download_file',
                        {'filename': filename,
                            'size': size,
                            'chunks': chunks,
                            'chunk_size': BUFFER_SIZE})
                    with open(fullname, 'rb') as filep:
                        ws.send(finfo)
                        while chunks:
                            ws.send(filep.read(BUFFER_SIZE))
                            chunks -= 1
                except Exception:
                    err = self._build_error(
                        'download_file', "Unable to send file <%s>" % (data))
                finally:
                    # add the websocket back to the list
                    with self.ws_lock:
                        self.websockets[ws_id] = ws
            else:
                err = self._build_error(
                    'download_file', "File <%s> does not exist" % (data))
        resp = err or self._build_response('download_file', "complete")
        self.send(resp, ws_id)

    def _prepare_upload(self, data, ws_id):
        # XXX - Should launch a greenthead with a timeout that cancels
        # the upload.  Timeout should be calculated by Chunk Size
        err = None
        if self.wsgi_mgr.klippy_printing:
            err = self._build_error(
                'upload_file', "Cannot upload while Klippy is Printing")
        else:
            try:
                self.pending_uploads[ws_id] = FileUpload(
                    self.sd_path, data, ws_id)
            except ServerError as e:
                err = self._build_response('server_error', e)
        resp = err or self._build_response(
            'upload_file', {'state': "ready", "chunk": 0})
        self.send(resp, ws_id)

    def _process_upload(self, data, fupload):
        err = None
        try:
            done = fupload.write_file(data)
        except ServerError as e:
            done = True
            err = self._build_response('server_error', e)
        if done:
            del self.pending_uploads[fupload.ws_id]
            self.send(err or self._build_response(
                'upload_file', {'state': "complete"}), fupload.ws_id)
            if err is None:
                # Notify all connected clients that the filelist has changed
                self.wsgi_mgr.notify_filelist_changed(
                    fupload.file_name, 'added')
        else:
            return_data = {'state': "ready", "chunk": fupload.current_chunk}
            resp = self._build_response('upload_file', return_data)
            self.send(resp, fupload.ws_id)

    def _process_delete(self, data, ws_id):
        err = None
        if self.wsgi_mgr.klippy_printing:
            err = self._build_error(
                'delete_file', "Cannot delete while Klippy is Printing")
        else:
            filename = data.get('filename', "")
            filename = os.path.join(self.sd_path, filename)
            if os.path.isfile(filename):
                try:
                    os.remove(filename)
                except OSError:
                    err = self._build_error(
                        'delete_file', "Unable to Delete file <%s>" % (data))
            else:
                err = self._build_error(
                    'delete_file', "File <%s> does not exist" % (data))
        resp = err or self._build_response('delete_file', "ok")
        self.send(resp, ws_id)
        if err is None:
            self.wsgi_mgr.notify_filelist_changed(filename, 'removed')

    def _send_klippy_request(self, request, data, ws_id):
        evt, uid = self.wsgi_mgr.create_event()
        self.send_klippy_cmd(request, uid, data)
        result = evt.wait(timeout=60.)
        if result is None:
            resp = self._build_error(request, "Klippy Request Timed Out")
        else:
            if isinstance(result['data'], ServerError):
                result['command'] = "server_error"
            resp = json_encode(result)
        self.wsgi_mgr.remove_event(uid)
        self.send(resp, ws_id)

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
