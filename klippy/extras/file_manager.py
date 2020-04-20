# Enhanced gcode file management and analysis
#
# Copyright (C) 2020 Eric Callahan <arksine.code@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.
import re
import os
import time
import logging

def strip_quotes(string):
    quotes = string[0] + string[-1]
    if quotes in ['""', "''"]:
        return string[1:-1]
    return string


VALID_GCODE_EXTS = ['gcode', 'g']
DEFAULT_READ_SIZE = 32 * 1024

# Helper to extract gcode metadata from a .gcode file
class SlicerTemplate:
    def __init__(self, config):
        self.name = config.get_name().split()[-1]
        self.printer = config.get_printer()
        self.header_read_size = config.getint(
            'header_read_size', DEFAULT_READ_SIZE, minval=DEFAULT_READ_SIZE)
        self.footer_read_size = config.getint(
            'footer_read_size', DEFAULT_READ_SIZE, minval=DEFAULT_READ_SIZE)
        self.name_pattern = strip_quotes(config.get('name_pattern'))
        self.templates = {
            'object_height': None,
            'first_layer_height': None,
            'layer_height': None,
            'filament_total': None,
            'estimated_time': None}
        gcode_macro = self.printer.try_load_module(config, 'gcode_macro')
        for name in self.templates:
            if config.get(name + "_script", None) is not None:
                self.templates[name] = gcode_macro.load_template(
                    config, name + "_script")

    def _regex_find_floats(self, pattern, data, strict=False):
        # If strict is enabled, pattern requires a floating point
        # value, otherwise it can be an integer value
        fptrn = r'\d+\.\d*' if strict else r'\d+\.?\d*'
        matches = re.findall(pattern, data)
        if matches:
            # return the maximum height value found
            try:
                return [float(h) for h in re.findall(
                    fptrn, " ".join(matches))]
            except Exception:
                pass
        return []

    def _regex_find_ints(self, pattern, data):
        matches = re.findall(pattern, data)
        if matches:
            # return the maximum height value found
            try:
                return [int(h) for h in re.findall(
                    r'\d+', " ".join(matches))]
            except Exception:
                pass
        return []

    def _regex_findall(self, pattern, data):
        return re.findall(pattern, data)

    def check_slicer_name(self, file_data):
        return re.search(self.name_pattern, file_data) is not None

    def parse_metadata(self, file_data, file_path):
        metadata = {'slicer': self.name}
        context = {
            'file_data': file_data,
            'regex_find_floats': self._regex_find_floats,
            'regex_find_ints': self._regex_find_ints,
            'regex_findall': self._regex_findall}
        for name, template in self.templates.iteritems():
            if template is None:
                continue
            try:
                result = float(template.render(context))
                metadata[name] = result
            except Exception:
                logging.info(
                    "gcode_meta: Unable to extract '%s' from file '%s'"
                    % (name, file_path))
        return metadata

    def get_slicer_name(self):
        return self.name

    def get_read_size(self):
        return self.header_read_size, self.footer_read_size

class GcodeAnalysis:
    def __init__(self, config):
        self.slicers = {}
        printer = config.get_printer()
        pconfig = printer.lookup_object('configfile')
        filename = os.path.join(
            os.path.dirname(__file__), '../../config/slicers.cfg')
        try:
            sconfig = pconfig.read_config(filename)
        except Exception:
            raise printer.config_error(
                "Cannot load slicer config '%s'" % (filename,))

        # get sections in primary configuration
        st_sections = config.get_prefix_sections('slicer_template ')
        scfg_st_sections = sconfig.get_prefix_sections('slicer_template ')
        main_st_names = [s.get_name() for s in st_sections]
        st_sections += [s for s in scfg_st_sections
                        if s.get_name() not in main_st_names]
        for scfg in st_sections:
            st = SlicerTemplate(scfg)
            self.slicers[st.get_slicer_name()] = st

    def get_metadata(self, file_path):
        if not os.path.isfile(file_path):
            raise IOError("File Not Found: %s" % (file_path))
        file_data = None
        size = os.path.getsize(file_path)
        last_modified = time.ctime(os.path.getmtime(file_path))
        metadata = {
            'size': size,
            'modified': last_modified}

        slicer = None
        with open(file_path, 'rb') as f:
            # read the default size, which should be enough to
            # identify the slicer
            file_data = f.read(DEFAULT_READ_SIZE)
            for stemplate in self.slicers.values():
                if stemplate.check_slicer_name(file_data):
                    slicer = stemplate
                    break
            if slicer is not None:
                hsize, fsize = slicer.get_read_size()
                hremaining = hsize - DEFAULT_READ_SIZE
                if size > DEFAULT_READ_SIZE:
                    if size > hsize + fsize:
                        if hremaining:
                            file_data += f.read(hremaining)
                        file_data += '\n'
                        f.seek(-fsize, os.SEEK_END)
                    file_data += f.read()
                metadata.update(slicer.parse_metadata(file_data, file_path))
            else:
                logging.info(
                    "Unable to detect Slicer Template for file '%s'"
                    % (file_path))
        return metadata

    def update_metadata(self, file_path, metadata):
        if not os.path.isfile(file_path):
            raise IOError("File Not Found: %s" % (file_path))
        size = os.path.getsize(file_path)
        last_modified = time.ctime(os.path.getmtime(file_path))
        if metadata.get('size', 0) == size and \
                metadata.get('modified', '') == last_modified:
            # File has not changed
            logging.debug(
                "gcode_meta: No changes detected to file '%s',"
                " using current metadata" % (file_path))
            return metadata
        return self.get_metadata(file_path)

class FileManager:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.gcode = self.printer.lookup_object('gcode')
        self.gca = GcodeAnalysis(config)
        sd = config.get('path', None)
        if sd is None:
            vsdcfg = config.getsection('virtual_sdcard')
            sd = vsdcfg.get('path')
        self.sd_path = os.path.normpath(os.path.expanduser(sd))
        self.file_info = None
        self.gcode.register_command(
            "GET_FILE_LIST", self.cmd_GET_FILE_LIST,
            desc=self.cmd_GET_FILE_LIST_help)
        self._update_file_list()
        webhooks = self.printer.lookup_object('webhooks')
        webhooks.register_endpoint(
            "/printer/files", self._handle_remote_filelist_request)
        webhooks.register_endpoint(
            "/printer/files/upload", self._handle_remote_file_request,
            params={'handler': 'FileUploadHandler', 'path': self.sd_path})
        # Endpoint for compatibility with Octoprint's legacy upload API
        webhooks.register_endpoint(
            "/api/files/local", self._handle_remote_file_request,
            params={'handler': 'FileUploadHandler', 'path': self.sd_path})
        webhooks.register_endpoint(
            "/printer/files/(.*)", self._handle_remote_file_request,
            methods=['GET', 'DELETE'],
            params={'handler': 'FileRequestHandler', 'path': self.sd_path})

    def _handle_remote_filelist_request(self, web_request):
        try:
            filelist = self.get_file_list()
        except Exception:
            raise web_request.error("Unable to retreive file list")
        flist = []
        for fname in sorted(filelist, key=str.lower):
            fdict = {'filename': fname}
            fdict.update(filelist[fname])
            flist.append(fdict)
        web_request.send(flist)

    def _handle_remote_file_request(self, web_request):
        # The actual file operation is performed by the server, however
        # the server must check in with the Klippy host to make sure
        # the operation is safe
        requested_file = web_request.get('filename')
        vsd = self.printer.lookup_object('virtual_sdcard', None)
        print_ongoing = None
        current_file = ""
        if vsd is not None:
            eventtime = self.printer.get_reactor().monotonic()
            sd_status = vsd.get_status(eventtime)
            current_file = sd_status['current_file']
            print_ongoing = sd_status['total_duration'] > 0.000001
            full_path = os.path.join(self.sd_path, current_file)
            if full_path == requested_file:
                raise web_request.error("File currently in use")
        web_request.send({'print_ongoing': print_ongoing})

    def get_sd_directory(self):
        return self.sd_path

    def _update_file_list(self):
        file_names = self._get_file_names(self.sd_path)
        if self.file_info is None:
            self.file_info = {}
            for fname in file_names:
                fpath = os.path.join(self.sd_path, fname)
                try:
                    self.file_info[fname] = self.gca.get_metadata(fpath)
                except Exception:
                    logging.exception("FileManager: File Detection Error")
        else:
            new_file_info = {}
            for fname in file_names:
                fpath = os.path.join(self.sd_path, fname)
                metadata = self.file_info.get(fname, None)
                try:
                    if metadata is not None:
                        new_file_info[fname] = self.gca.update_metadata(
                            fpath, metadata)
                    else:
                        new_file_info[fname] = self.gca.get_metadata(fpath)
                except Exception:
                    logging.exception("FileManager: File Detection Error")
            self.file_info = new_file_info

    def get_file_list(self):
        self._update_file_list()
        return dict(self.file_info)

    def _get_file_names(self, path):
        file_names = []
        try:
            for fname in os.listdir(path):
                if fname[0] == '.':
                    continue
                full_path = os.path.join(path, fname)
                if os.path.isdir(full_path):
                    sublist = self._get_file_names(full_path)
                    for sf in sublist:
                        file_names.append(os.path.join(fname, sf))
                elif os.path.isfile(full_path):
                    ext = fname[fname.rfind('.')+1:]
                    if ext in VALID_GCODE_EXTS:
                        file_names.append(fname)
        except Exception:
            msg = "FileManager: unable to generate file list"
            logging.exception(msg)
            if self.file_info is None:
                # File Info not initialized, error occured during config
                raise self.printer.config_error(msg)
            else:
                raise self.gcode.error(msg)
        return file_names

    cmd_GET_FILE_LIST_help = "Show Detailed GCode File Information"
    def cmd_GET_FILE_LIST(self, params):
        self._update_file_list()
        msg = "Available GCode Files:\n"
        for fname in sorted(self.file_info, key=str.lower):
            msg += "File: %s\n" % (fname)
            for item in sorted(self.file_info[fname], key=str.lower):
                msg += "** %s: %s\n" % (item, str(self.file_info[fname][item]))
            msg += "\n"
        self.gcode.respond_info(msg)

def load_config(config):
    return FileManager(config)
