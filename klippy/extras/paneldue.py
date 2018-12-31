# PanelDue extra
#
# Copyright (C) 2018  Florian Heilmann <Florian.Heilmann@gmx.net>
#
# This file may be distributed under the terms of the GNU GPLv3 license.

import json
import serial
import logging
import kinematics.extruder
import util, os, re

class PanelDue:

    def __init__(self, config):
        self.printer = config.get_printer()
        self.toolhead = None
        self.reactor =self.printer.get_reactor()
        self.gcode = self.printer.lookup_object("gcode")
        self.serialdata = ""
        self.current_receive_line = None
        self.gcode_queue = []
        self.info_message_sequence = 0

        # setup
        self.ppins = self.printer.lookup_object("pins")
        self.serial_port = config.get('serial_port')
        self.serial_baudrate = config.get('serial_baudrate')
        self.macro_list = None

        try:
           self.macro_list = config.get('macro_list').split('\n')
        except:
            # If no list supplied then list all registered commands
            self.macro_list = self.gcode.ready_gcode_handlers.keys()

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


    def gcode_respond_callback(self, msg):

        if msg.find("//") == 0:
            for info in msg.split("//"):
                self.info_message_sequence += 1
                infoMsg = {
                    "resp":info.strip(),
                    "seq":self.info_message_sequence
                }
                self.ser.write(json.dumps(infoMsg) + '\r\n')
        elif msg.find("!!") == 0:
            errorMsg = {"message":msg[2:]}
            self.ser.write(json.dumps(errorMsg) + '\r\n')
        elif msg.find("{") == 0:
            self.ser.write(msg + '\r\n')

    def process_pd_data(self, eventtime):

        self.serialdata += os.read(self.fd, 4096)

        readlines = self.serialdata.split('\n')
        for line in readlines[:-1]:
            line = line.strip()

            logging.debug("raw message " + line)

            message = self.parse_pd_message(line)

            if message:
                #logging.info ("executing " + message)
                self.execute_command(message)

        self.serialdata = readlines[-1]   

    # Have any custom commands get executed directly
    # and anything else get added to the gcode queue
    def execute_command(self, command):

        params = self.parse_params(command)

        # Some commands such as RRF's M36 don't parse well.
        cmd = params['#command'].split(" ")[0]

        switcher = {
            "M0": self.cmd_M0,
            "M20": self.cmd_M20,
            "M25": self.cmd_M25,
            "M32": self.cmd_M32,
            "M36": self.cmd_M36,
            "M98": self.cmd_M98,
            "M106": self.cmd_M106,
            "M112": self.cmd_M112,
            "M220": self.cmd_M220,
            "M290": self.cmd_M290,
            "M408": self.cmd_M408,
            "G10": self.cmd_G10
        }     

        func = switcher.get(cmd, lambda x : self.queue_gcode(x["#original"]))
        func(params)

    def queue_gcode(self, script):
        if script is None:
            return
        if not self.gcode_queue:
            reactor = self.printer.get_reactor()
            reactor.register_callback(self.dispatch_gcode)
        self.gcode_queue.append(script)

    def dispatch_gcode(self, eventtime):
        while self.gcode_queue:
            script = self.gcode_queue[0]
            try:
                self.gcode.run_script(script)
            except Exception:
                logging.exception("Script running error")
            self.gcode_queue.pop(0)

    # maintain compatibility with regular registered commands
    # by using the same param parsing as Gcode Parser
    def parse_params(self, line):
                    
        args_r = re.compile('([A-Z_]+|[A-Z*/])')
        
        line = origline = line.strip()
        cpos = line.find(';')
        if cpos >= 0:
            line = line[:cpos]
        # Break command into parts
        parts = args_r.split(line.upper())[1:]
        params = { parts[i]: parts[i+1].strip()
                    for i in range(0, len(parts), 2) }
        params['#original'] = origline
        if parts and parts[0] == 'N':
            # Skip line number at start of command
            del parts[:2]
        if not parts:
            # Treat empty line as empty command
            parts = ['', '']
        params['#command'] = cmd = parts[0] + parts[1].strip()

        return params

    def build_config(self):
        pass

    # Start SD print
    def cmd_M32(self, params):

        path = params['#original'].replace("M32 ", "")

        # Remove the directory since it should already be the active directory
        last_slash = path.rfind("/")
        if last_slash >= 0:
                path = path[last_slash+1:]


        self.sdcard.cmd_M23(self.parse_params("M23 " + path))
        self.sdcard.cmd_M24(self.parse_params("M24"))

        logging.info("Starting SD Print: " + path + " from directory " + self.sdcard.sdcard_dirname)

    # Cancel Print
    def cmd_M0(self, params):
        if self.sdcard is not None:
            self.sdcard.work_timer = None
            self.sdcard.must_pause_work = False
            self.sdcard.current_file = None
            self.sdcard.file_position = self.sdcard.file_size = 0

        # Turn off all heaters
        self.queue_gcode("TURN_OFF_HEATERS")


    # Set fan speed. Bypass gcode queue
    def cmd_M106(self, params):
        self.gcode.cmd_M106(params)  

    # Set print speed. Bypass gcode queue
    def cmd_M220(self, params):
        self.gcode.cmd_M220(params)

    # Emergency stop. A M112 gets klipper into a funky state, a firmware restart
    # seems to be a nicer way to clear everything snd start over
    def cmd_M112(self, params):
        self.gcode.cmd_FIRMWARE_RESTART(params)

    # Baby stepping
    # PD issues a "M290 S#" which supplies relative steps
    # Klipper's equivalent is "SET_GCODE_OFFSET Z#"" but takes absolute steps
    # So some translation is required
    def cmd_M290(self, params):

        relative_babysteps = self.gcode.get_float('S', params)
        gcode_status = self.gcode.get_status(self.reactor.monotonic())
        current_babysteps = gcode_status['homing_zpos']
        new_babysteps = current_babysteps + relative_babysteps

        params = self.parse_params("SET_GCODE_OFFSET Z=%0.2f" % new_babysteps)

        self.gcode.cmd_SET_GCODE_OFFSET(params)     

    # Pause SD print
    def cmd_M25(self, params):

        if self.sdcard is not None:
            self.sdcard.cmd_M25(params)

    # G10 in RRF is used to set standby/active temp. We will map it to 
    # simply set the target temp (M104)
    def cmd_G10(self, params):

        tool = self.gcode.get_int('P', params, 0)
        temp = max(self.gcode.get_float('S', params, 0.), self.gcode.get_float('R', params, 0.))
        params = self.parse_params("M104 T%d S%0.2f" % (tool,temp))
        self.gcode.cmd_M104(params)

    # Execute file, used for macro execution
    def cmd_M98(self, params):

        path = ""

        for param in params['#original'].split(" "):
            logging.info("checking param " + param)
            index = param.find("P")
            if (index == 0):
                path = param[1:]

        if path.lower().find("0:/macros/") == 0:
            macro = path[10:]
            logging.info("Executing macro " + macro)
            self.queue_gcode(macro)

    # List SD card files 
    def cmd_M20(self, params):

        response = {}
        path = ""

        for param in params['#original'].split(" "):
            index = param.find("P")
            if (index == 0):
                path = param[1:]

        response['dir'] = path
        response['files'] = []

        if path == "0:/macros":
            for cmd in self.macro_list:
                if cmd:
                    response['files'].append(cmd)
        elif path.find("0:/gcodes") == 0:
            request_dir = path[9:]
            if self.sdcard is not None:
                self.sdcard.sdcard_dirname = self.sd_base_dir + request_dir
                files = self.sdcard.get_file_list()
                for fname, fsize in files:
                    prefix = ("*" if os.path.isdir(os.path.join(self.sdcard.sdcard_dirname, fname)) else "")
                    if (prefix or fname.endswith('.gcode')):
                        response['files'].append(prefix + str(fname))  
        json_response = json.dumps(response)
        self.gcode_respond_callback(json_response)

    # FileInfo. Lots of work to do here
    def cmd_M36(self, params):

        #example: M36 0:/gcodes/myprint.gcode

        response = {}
        response['err'] = 0
        response['size'] = 500
        response['lastModified'] = ''
        response['height'] = 100
        response['firstLayerHeight'] = 0
        response['layerHeight'] = 0
        response['filament'] = 0
        response['filament'] = []
        response['generatedBy'] = 'Unknown Slicer'

        self.gcode_respond_callback(json.dumps(response))

    def get_axes_homed(self, toolhead):
        kin = toolhead.get_kinematics()
        if not kin.limits:
            return [0.,0.,0.]
        homed = []
        for axis in 'XYZ':
            index = self.gcode.axis2pos[axis]
            homed.append(0 if kin.limits[index][0] > kin.limits[index][1] else 1)
        return homed

    def get_printer_status(self, now, gcode_status):

        if self.sdcard is not None:
            if self.sdcard.must_pause_work:
                # D = pausing, A = paused
                return "D" if self.sdcard.work_timer is not None else "A"
            if self.sdcard.current_file is not None and self.sdcard.work_timer is not None:
                # Printing
                return "P"

        if gcode_status['busy']:
            # B = busy
            return "B"

        toolhead_info = self.toolhead.get_status(now)

        # Seems to be problematic, just return I if we got this far
        # P = printing, I = idle
        #return "P" if toolhead_info['status'] == "Printing" else "I"
        return "I"

    def cmd_M408(self, params):
        self.toolhead = self.printer.lookup_object("toolhead")
        now = self.reactor.monotonic()
        extruders = kinematics.extruder.get_printer_extruders(self.printer)
        bed = self.printer.lookup_object('heater_bed', None)
        gcode_status = self.gcode.get_status(now)
        response = {}
        response['status'] = self.get_printer_status(now, gcode_status)
        response['myName'] = "Klipper"
        response['firmwareName'] = "Klipper for Duet 2 WiFi/Ethernet"
        response['numTools'] = len(extruders)
        response['babystep'] = gcode_status['homing_zpos']
        response['pos'] = []
        response['pos'].append(round(gcode_status['last_xpos']))
        response['pos'].append(round(gcode_status['last_ypos']))
        response['pos'].append(round(gcode_status['last_zpos']))
        response['homed'] = self.get_axes_homed(self.toolhead)
        response['fanPercent'] = []
        response['sfactor'] = round(gcode_status['speed_factor']*100.,1)

        if self.fan is not None:
            fanPercent = self.fan.get_status(now)['speed']*100.
            response['fanPercent'].append(round(fanPercent,1))

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
        #logging.info(json_response)
        if 'VARIANT' in params:
            variant = self.gcode.get_int('VARIANT', params, minval=0, maxval=3)
        self.gcode_respond_callback(json_response)

    def printer_state(self, state):

        logging.info("checking printer state")

        if state == 'ready':

            self.fan = self.printer.lookup_object('fan', None)
            self.sdcard = self.printer.objects.get('virtual_sdcard')
            if self.sdcard is not None: 
                self.sd_base_dir = self.sdcard.sdcard_dirname

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

            self.gcode.register_respond_callback(self.gcode_respond_callback)

def load_config(config):
    return PanelDue(config)
