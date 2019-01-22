# Custom Variable Storage
#
# Copyright (C) 2018  Florian Heilmann <Florian.Heilmann@gmx.net>
#
# This file may be distributed under the terms of the GNU GPLv3 license.

DEFAULT_PREFIX = 'var_'

class VariableStore:
    def __init__(self, config):
        printer = config.get_printer()
        self.gcode = printer.lookup_object('gcode')
        try:
            self.gcode.register_command(
                'SET_VARIABLE', self.cmd_SET_VARIABLE, desc=self.cmd_SET_VARIABLE_desc)
        except self.gcode.error as e:
            raise config.error(str(e))
        self.in_script = False
        self.kwvars = { o[len(DEFAULT_PREFIX):].upper(): config.get(o)
                          for o in config.get_prefix_options(DEFAULT_PREFIX) }
    def get_vars(self):
        return dict(self.kwvars)
    cmd_SET_VARIABLE_desc = "Set variable value in variable store"

    def cmd_SET_VARIABLE(self, params):
        if 'VARIABLE' in params and 'VALUE' in params:
            if params['VARIABLE'] in self.kwvars:
                self.kwvars[params['VARIABLE']] = params['VALUE'].decode('string-escape')

def load_config(config):
    return VariableStore(config)
