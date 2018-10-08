# PanelDue extra
#
# Copyright (C) 2018  Florian Heilmann <Florian.Heilmann@gmx.net>
#
# This file may be distributed under the terms of the GNU GPLv3 license.

import json
import serial
import logging
import kinematics.extruder
import util
import os

class PanelDue:

    def __init__(self, config):
        self.printer = config.get_printer()
        self.toolhead = None
        self.reactor =self.printer.get_reactor()
        self.gcode = self.printer.lookup_object("gcode")
        self.serialdata = ""
        self.current_receive_line = None

        # setup
        self.ppins = self.printer.lookup_object("pins")
        self.serial_port = config.get('serial_port')
        self.serial_baudrate = config.get('serial_baudrate')

        logging.info("PanelDue initializing serial port " + self.serial_port + " at baudrate " + self.serial_baudrate)

        self.ser = serial.Serial(
            port=self.serial_port,
            baudrate=int(self.serial_baudrate),
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            bytesize=serial.EIGHTBITS
        )

        self.fd = self.ser.fileno()
        util.set_nonblock(self.fd)
        self.fd_handle = self.reactor.register_fd(self.fd, self.process_pd_data)

        # TODO: Find a better way to intercept gcode responses to delay them to the PD.
        self.gcode.respond = self.gcode_respond_override

        # Add BUILD_RESPONSE command

        self.gcode.register_command(
            "M408", self.cmd_M408, desc=self.cmd_M408_help)


    def parse_pd_message (self, rawmsg):

        checksum_index = rawmsg.rfind("*")
        expected_checksum = rawmsg[checksum_index+1:]

        try:
             expected_checksum = int(expected_checksum)
        except:
            expected_checksum = -1

        line_index = rawmsg.find(" ")
        line_number = -1

        try:
            line_number = int(rawmsg[1:line_index])
        except:
            line_number = -1

        gcodemsg = rawmsg[line_index+1:checksum_index]

        if self.current_receive_line and self.current_receive_line+1 != line_number:
                logging.warn("Received line number not sequential. Discarding message")
                gcodemsg = ""

        calculated_checksum = 0

        # checksum is calculated by XORing everything but the checksum itself
        for chr in rawmsg[:checksum_index]:
                calculated_checksum ^= ord(chr)

        if expected_checksum != calculated_checksum:
                #logging.warn("Raw message:" + rawmsg)
                logging.warn("Checksum validation failed. Discarding message")
                gcodemsg = ""

        self.current_receive_line = line_number

        return gcodemsg

    def gcode_respond_override(self, msg):
        if self.gcode.is_fileinput:
            return
        try:
            logging.info("respond msg: " + msg)
            os.write(self.gcode.fd, msg+"\n")
            self.ser.write(msg)
        except os.error:
            logging.exception("Write g-code response")

    def process_pd_data(self, eventtime):

        self.serialdata += os.read(self.fd, 4096)

        readlines = self.serialdata.split('\n')
        for line in readlines[:-1]:
            line = line.strip()

            logging.info("raw message " + line)

            message = self.parse_pd_message(line)

            if message:
                logging.info ("executing " + message)
                self.printer.lookup_object('gcode').run_script(message)

        self.serialdata = readlines[-1]        

    def build_config(self):
        pass

    cmd_M408_help = "M408 style response"
    def cmd_M408(self, params):
        self.toolhead = self.printer.lookup_object("toolhead")
        now = self.reactor.monotonic()
        extruders = kinematics.extruder.get_printer_extruders(self.printer)
        bed = self.printer.lookup_object('heater_bed', None)
        self.toolhead_status = self.toolhead.get_status(now)
        logging.info(str(self.toolhead_status))
        response = {}
        response['status'] = "P" if self.toolhead_status == "Printing" else "I"
        response['myName'] = "Klipper"
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

        json_response = json.dumps(response)
        logging.info(json_response)
        if 'VARIANT' in params:
            variant = self.gcode.get_int('VARIANT', params, minval=0, maxval=3)
        logging.info('BUILD_RESPONSE executed with variant {}'.format(variant))
        self.gcode.respond(json_response)

def load_config(config):
    return PanelDue(config)
