# PanelDue extra
#
# Copyright (C) 2018  Florian Heilmann <Florian.Heilmann@gmx.net>
#
# This file may be distributed under the terms of the GNU GPLv3 license.

import json
import serial
import logging
import kinematics.extruder

class PanelDue:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.toolhead = None
        self.reactor =self.printer.get_reactor()
        self.gcode = self.printer.lookup_object("gcode")

        # setup
        self.ppins = self.printer.lookup_object("pins")
        self.serial_port = config.get('serial')

        # Add BUILD_RESPONSE command

        self.gcode.register_command(
            "BUILD_RESPONSE", self.cmd_BUILD_RESPONSE, desc=self.cmd_BUILD_RESPONSE_help)

    def build_config(self):
        pass

    cmd_BUILD_RESPONSE_help = "Build a M408 style response"
    def cmd_BUILD_RESPONSE(self, params):
        self.toolhead = self.printer.lookup_object("toolhead")
        now = self.reactor.monotonic()
        extruders = kinematics.extruder.get_printer_extruders(self.printer)
        bed = self.printer.lookup_object('heater_bed', None)
        self.toolhead_status = self.toolhead.get_status(now)
        logging.info(str(self.toolhead_status))
        response = {}
        response['status'] = "P" if self.toolhead_status == "Printing" else "I"
        response['myName'] = "Klipper",
        response['firmwareName'] = "Klipper for Duet 2 WiFi/Ethernet"

        if bed is not None:
            status = bed.get_status(now)
            response['heaters'], response['active'], response['standby'], response['hstat'] = \
            [round(status['temperature'],1)], [round(status['target'],1)], [round(status['target'],1)], [2]
        else:
            response['heaters'], response['active'], response['standby'], response['hstat'] = [0.0], [0.0], [0.0], [0.0]
        for ext in extruders:
            # logging.info(str(ext))
            status = ext.get_heater().get_status(now)
            response['heaters'].append(round(status['temperature'],1))
            response['active'].append(round(status['target'],1))
            response['standby'].append(round(status['target'],1))
            response['hstat'].append(2 if self.toolhead.get_extruder() == ext else 0)

        variant = 0
        logging.info(str(response))
        if 'VARIANT' in params:
            variant = self.gcode.get_int('VARIANT', params, minval=0, maxval=3)
        logging.info('BUILD_RESPONSE executed with variant {}'.format(variant))

def load_config(config):
    return PanelDue(config)
