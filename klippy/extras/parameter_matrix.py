# Allow storing and accessing a n-dimensional matrix of parameters
#
# Copyright (C) 2020 Florian Heilmann <Florian.Heilmann@gmx.net>
#
# This file may be distributed under the terms of the GNU GPLv3 license.

import logging

class ParameterMatrix:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.gcode = self.printer.lookup_object('gcode')
        self.name = name = config.get_name().split()[1]
        self.depth = config.getint("depth", default=1)
        levels = config.get("levels").split('\n')
   
        self.levels = [level.strip() for level in levels if level]
        self.storage = {}
        if len(self.levels) != self.depth:
            raise config.error("Number of specified level labels does not " +
                               "match specified depth in %s: %d, %s" % (self.name, self.depth, self.levels))
        storage = [line for line in config.get("storage").split('\n') if line]

        for matrix_line in storage:
            matrix_line_split = matrix_line.replace(' ', '').split(',')
            loc = matrix_line_split[:-1]
            val = matrix_line_split[-1]

            storage_level = self.storage
            for sub_loc in loc[:-1]:
                if sub_loc not in storage_level:
                    storage_level[sub_loc] = {}
                storage_level = storage_level[sub_loc]
            if loc[-1] in storage_level:
                raise config.error("Can't set %s because it already exists!"
                                   % matrix_line)
            else:
                storage_level[loc[-1]] = val

        self.gcode.register_mux_command("CALL_COMMAND_WITH_STORAGE", "STORAGE",
                                   self.name,
                                   self.cmd_CALL_COMMAND_WITH_STORAGE,
                                   desc=self.cmd_CALL_COMMAND_WITH_STORAGE_help)



    cmd_CALL_COMMAND_WITH_STORAGE_help = "Send a stored value to a gcode macro"
    def cmd_CALL_COMMAND_WITH_STORAGE(self, gcmd):
        tmp_val = self.storage
        try:
            for level in self.levels:
                tmp_val = tmp_val[gcmd.get(level)]
        except KeyError:
            raise gcmd.error("Value %s not found in storage" % gcmd.get(level))
        msg = ""
        if 'COMMAND' in gcmd.get_command_parameters() and 'PARAMETER' in gcmd.get_command_parameters():
            command = gcmd.get('COMMAND')
            msg += "Gcode Command: %s\n" % command
            parameter = gcmd.get('PARAMETER')
            msg += "Parameter: %s\n" % parameter
            if self.gcode.is_traditional_gcode(command):
                command_fmt = "%s %s%%s" % (command, parameter)
            else:
                command_fmt = "%s %s=%%s" % (command, parameter)

            self.gcode.run_script_from_command(command_fmt % tmp_val)
        msg += "Stored Value: %s" % tmp_val
        gcmd.respond_info(msg, log=False)


def load_config_prefix(config):
    return ParameterMatrix(config)
