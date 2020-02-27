
# Package definition for Klippy Web Server
#
# Copyright (C) 2020 Eric Callahan <arksine.code@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license
from interface import KlippyServerInterface

def load_config(config):
    return KlippyServerInterface(config)
