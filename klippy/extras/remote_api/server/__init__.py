# Package definition for API Server
#
# Copyright (C) 2020 Eric Callahan <arksine.code@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license
import os
import multiprocessing
import logging
import util
from server_util import ServerError, PipeLoggingHandler
from server_manager import ServerManager

def _start_server(pipe, config):
    # Set up root logger on new process
    root_logger = logging.getLogger()
    pipe_hdlr = PipeLoggingHandler(pipe)
    root_logger.addHandler(pipe_hdlr)
    root_logger.setLevel(logging.INFO)

    # Start Tornado Server
    try:
        server = ServerManager(pipe, config)
        server.start()
    except Exception:
        logging.exception("Error Running Server")

def load_server_process(server_config):
    pp, cp = multiprocessing.Pipe()
    util.set_nonblock(pp.fileno())
    util.set_nonblock(cp.fileno())
    proc = multiprocessing.Process(
        target=_start_server, args=(cp, server_config,))
    proc.start()
    return pp, proc
