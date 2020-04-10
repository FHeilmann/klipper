# GCode file analysis and metdata extraction helper
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
            'filament_used': None,
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

def load_config(config):
    return GcodeAnalysis(config)
