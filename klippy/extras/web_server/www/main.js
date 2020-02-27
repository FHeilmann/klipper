//  Main javascript for for Klippy Web Server Example
//
//  Copyright (C) 2019 Eric Callahan <arksine.code@gmail.com>
//
//  This file may be distributed under the terms of the GNU GPLv3 license

import JsonRPC from "./json-rpc.js?v=0.1.2";

var paused = false;
var api_type = 'http';
var is_printing = false;
var json_rpc = new JsonRPC();

function round_float (value) {
    if (typeof value == "number" && !Number.isInteger(value)) {
        return value.toFixed(2);
    }
    return value;
}

//****************UI Update Functions****************/
var line_count = 0;
function update_term(msg) {
    let start = '<div id="line' + line_count + '">';
    $("#term").append(start + msg + "</div>");
    line_count++;
    if (line_count >= 50) {
        let rm = line_count - 50
        $("#line" + rm).remove();
    }
    if ($("#cbxAuto").is(":checked")) {
        $("#term").stop().animate({
        scrollTop: $("#term")[0].scrollHeight
        }, 800);
    }
}

const max_stream_div_width = 5;
var stream_div_width = max_stream_div_width;
var stream_div_height = 0;
function update_streamdiv(obj, attr, val) {
    if (stream_div_width >= max_stream_div_width) {
        stream_div_height++;
        stream_div_width = 0;
        $('#streamdiv').append("<div id='sdrow" + stream_div_height +
                               "' style='display: flex'></div>");
    }
    let id = obj + "_" + attr;
    if ($("#" + id).length == 0) {
        $('#sdrow' + stream_div_height).append("<div style='width: 10em; border: 2px solid black'>"
            + obj + " " + attr + ":<div id='" + id + "'></div></div>");
        stream_div_width++;
    }

    let out = "";
    if (val instanceof Array) {
        val.forEach((value, idx, array) => {
            out += round_float(value);
            if (idx < array.length -1) {
                out += ", "
            }
        });
    } else {
        out = round_float(val);
    }
    $("#" + id).text(out);
}

function update_filelist(filelist) {
    $("#filelist").empty();
    for (let file of filelist) {
        $("#filelist").append(
            "<option value='" + file.filename + "'>" +
            file.filename + "</option>");
    }
}

var last_progress = 0;
function update_progress(loaded, total) {
    let progress = parseInt(loaded / total * 100);
    if (progress - last_progress > 1 || progress >= 100) {
        if (progress >= 100) {
            last_progress = 0;
            progress = 100;
            console.log("File transfer complete")
        } else {
            last_progress = progress;
        }
        $('#upload_progress').text(progress);
        $('#progressbar').val(progress);
    }
}

function update_error(cmd, msg) {
    if (msg instanceof Object)
        msg = JSON.stringify(msg);
    // Handle server error responses
    update_term("Command [" + cmd + "] resulted in an error: " + msg);
    console.log("Error processing " + cmd +": " + msg);
}
//***********End UI Update Functions****************/

//***********Websocket-Klipper API Functions (JSON-RPC)************/
function get_file_list() {
    json_rpc.call_method('get_printer_files')
    .then((result) => {
        // result is an "ok" acknowledgment that the gcode has
        // been successfully processed
        update_filelist(result);
    })
    .catch((error) => {
        update_error("get_printer_files", error);
    });
}

function get_klippy_info() {
    // A "get_klippy_info" websocket request.  It returns
    // the hostname (which should be equal to location.host), the
    // build version, and if the Host is ready for commands.  Its a
    // good idea to fetch this information after the websocket connects.
    // If the Host is in a "ready" state, we can do some initialization
    json_rpc.call_method('get_printer_info')
    .then((result) => {
        update_term("Klippy Hostname: " + location.hostname +
                " | CPU: " + result.cpu +
                " | Build Version: " + result.version);
        if (result.is_ready) {
            // We know the host is ready, lets find out if the printer is
            // ready by requesting the idle_timeout status
            get_status({idle_timeout: [], pause_resume: []});
        } else {
            run_gcode("STATUS");
        }

    })
    .catch((error) => {
        update_error("get_printer_info", error);
    });
}

function run_gcode(gcode) {
    json_rpc.call_method_with_kwargs(
        'post_printer_gcode', {script: gcode})
    .then((result) => {
        // result is an "ok" acknowledgment that the gcode has
        // been successfully processed
        update_term(result);
    })
    .catch((error) => {
        update_error("run_gcode", error);
    });
}

function get_status(printer_objects) {
    // Note that this is just an example of one particular use of get_status.
    // In a robust client you would likely pass a callback to this function
    // so that you can respond to various status requests.  It would also
    // be possible to subscribe to status requests and update the UI accordingly
    json_rpc.call_method_with_kwargs('get_printer_status', printer_objects)
    .then((result) => {
        if ("idle_timeout" in result) {
            // Its a good idea that the user understands that some functionality,
            // such as file manipulation, is disabled during a print.  This can be
            // done by disabling buttons or by notifying the user via a popup if they
            // click on an action that is not allowed.
            if ("state" in result.idle_timeout) {
                let state = result.idle_timeout.state.toLowerCase();
                is_printing = (state == "printing");
                $('.toggleable').prop(
                    'disabled', (api_type == 'websocket' || is_printing));
                $('#btnstartprint').prop('disabled', is_printing);
            }
        }
        if ("pause_resume" in result) {
            if ("is_paused" in result.pause_resume) {
                paused = result.pause_resume.is_paused;
                let label = paused ? "Resume Print" : "Pause Print";
                $('#btnpauseresume').text(label);
            }
        }
        console.log(result);
    })
    .catch((error) => {
        update_error("get_printer_status", error);
    });
}

function get_object_info() {
    json_rpc.call_method('get_printer_objects')
    .then((result) => {
        // result will be a dictionary containing all available printer
        // objects available for query or subscription
        console.log(result);
    })
    .catch((error) => {
        update_error("get_printer_objects", error);
    });
}

function add_subscription(printer_objects) {
    json_rpc.call_method_with_kwargs(
        'post_printer_subscriptions', printer_objects)
    .then((result) => {
        // result is simply an "ok" acknowledgement that subscriptions
        // have been added for requested objects
        console.log(result);
    })
    .catch((error) => {
        update_error("post_printer_subscriptions", error);
    });
}

function get_subscribed() {
    json_rpc.call_method('get_printer_subscriptions')
    .then((result) => {
        // result is a dictionary containing all currently subscribed
        // printer objects/attributes
        console.log(result);
    })
    .catch((error) => {
        update_error("get_printer_subscriptions", error);
    });
}

function get_endstops() {
    json_rpc.call_method('get_endstops')
    .then((result) => {
        // A response to a "get_endstops" websocket request.
        // The result contains an object of key/value pairs,
        // where the key is the endstop (ie:x, y, or z) and the
        // value is either "open" or "TRIGGERED".
        console.log(result);
    })
    .catch((error) => {
        update_error("get_endstops", error);
    });
}

function start_print(file_name) {
    json_rpc.call_method_with_kwargs(
        'post_printer_print_start', {'filename': file_name})
    .then((result) => {
        // result is an "ok" acknowledgement that the
        // print has started
        console.log(result);
    })
    .catch((error) => {
        update_error("post_printer_print_start", error);
    });
}

function cancel_print() {
    json_rpc.call_method('post_printer_print_cancel')
    .then((result) => {
        // result is an "ok" acknowledgement that the
        // print has been canceled
        console.log(result);
    })
    .catch((error) => {
        update_error("post_printer_print_cancel", error);
    });
}

function pause_print() {
    json_rpc.call_method('post_printer_print_pause')
    .then((result) => {
        // result is an "ok" acknowledgement that the
        // print has been paused
        console.log("Pause Command Executed")
    })
    .catch((error) => {
        update_error("post_printer_print_pause", error);
    });
}

function resume_print() {
    json_rpc.call_method('post_printer_print_resume')
    .then((result) => {
        // result is an "ok" acknowledgement that the
        // print has been resumed
        console.log("Resume Command Executed")
    })
    .catch((error) => {
        update_error("post_printer_print_resume", error);
    });
}

function restart() {
    // We are unlikely to receive a response from a restart
    // request as the websocket will disconnect, so we will
    // call json_rpc.notify instead of call_function.
    json_rpc.notify('post_printer_restart');
}

function firmware_restart() {
    // As above, we would not likely receive a response from
    // a firmware_restart request
    json_rpc.notify('post_printer_firmware_restart');
}
//***********End Websocket-Klipper API Functions (JSON-RPC)********/

//***********Klipper Event Handlers (JSON-RPC)*********************/

function handle_gcode_response(response) {
    // This event contains all gcode responses that would
    // typically be printed to the terminal.  Its possible
    // That multiple lines can be bundled in one response,
    // so if displaying we want to be sure we split them.
    let messages = response.split("\n");
    for (let msg of messages) {
        update_term(msg);
    }
}
json_rpc.register_method("notify_gcode_response", handle_gcode_response);

function handle_status_update(status) {
    // This is subscribed status data.  Here we do a nested
    // for-each to determine the klippy object name ("name"),
    // the attribute we want ("attr"), and the attribute's
    // value ("val")
    for (let name in status) {
        let obj = status[name];
        for (let attr in obj) {
            let val = obj[attr];
            update_streamdiv(name, attr, val);
        }
    }
}
json_rpc.register_method("notify_status_update", handle_status_update);

function handle_printer_state(state) {
    // Printer State can be "ready", "printing", or "idle".  Here
    // We disable a few buttons when the printer is printing to
    // prevent file manipulation.  The server won't allow it regardless,
    // but its a good idea to make sure the user knows that via the
    // client.
    update_term("Klippy State: " + state);
    is_printing = (state == "printing");
    $('.toggleable').prop(
        'disabled', (api_type == 'websocket' || is_printing));
    $('#btnstartprint').prop('disabled', is_printing);
}
json_rpc.register_method("notify_printer_state_changed", handle_printer_state);

function handle_klippy_state(state) {
    // Klippy state can be "ready", "disconnect", and "shutdown".  This
    // differs from Printer State in that it represents the status of
    // the Host software
    switch(state) {
        case "ready":
            // Printer just came online, now is a good time to
            // intialize/refresh state.  It would also be a good
            // place to subscribe to status updates
            get_file_list();
            get_status({idle_timeout: [], pause_resume: []});

            // Initialize the state of the file transfer buttons.  If
            // we are testing the websocket api they should be disabled,
            // otherwise enabled
            $('.toggleable').prop('disabled', (api_type == 'websocket'));

            if ($("#cbxSub").is(":checked")) {
                // If autosubscribe is check, request the subscription now
                const sub = {
                    gcode: ["gcode_position", "speed", "speed_factor", "extrude_factor"],
                    toolhead: [],
                    virtual_sdcard: [],
                    heater_bed: [],
                    extruder: ["temperature", "target"],
                    fan: []};
                add_subscription(sub);
            }
            break;
        case "disconnect":
            // Klippy has disconnected from the MCU and is prepping to
            // restart.  The client will receive this signal right before
            // the websocket disconnects.  If we need to do any kind of
            // cleanup on the client to prepare for restart this would
            // be a good place.
            update_term("Klippy Disconnected, Preparing for Restart");
            break;
        case "shutdown":
            // Either M112 was entered or there was a printer error.  We
            // probably want to notify the user and disable certain controls.
            update_term("Klipper has shutdown, check klippy.log for info");
            break;
    }
}
json_rpc.register_method("notify_klippy_state_changed", handle_klippy_state);

function handle_file_list_changed(file_info) {
    // This event fires when a client has either added or removed
    // a gcode file.
    update_filelist(file_info.filelist);
}
json_rpc.register_method("notify_filelist_changed", handle_file_list_changed);

function handle_paused_state(state) {
    // The state may be "paused", "resumed", or "cleared"
    if (state == 'paused') {
        // Paused event received, update client state
        $('#btnpauseresume').text("Resume Print");
        paused = true;
    } else {
        // Either the print resumed or the pause was cleared.
        // Note that it is common for printers to clear the
        // pause state in their start, cancel, and/or end gcodes.
        // Clients should be aware that they may receive multiple
        // events with a "cleared" state, even if the printer was
        // never paused.

        $('#btnpauseresume').text("Pause Print");
        paused = false;
    }
    console.log("Paused state changed: " + state);
}
json_rpc.register_method("notify_paused_state_changed", handle_paused_state);
//***********End Klipper Event Handlers (JSON-RPC)*****************/

// The function below is an example of one way to use JSON-RPC's batch send
// method.  Generally speaking it is better and easier to use individual
// requests, as matching requests with responses in a batch requires more
// work from the developer
function send_gcode_batch(gcodes) {
    // The purpose of this function is to provide an example of a JSON-RPC
    // "batch request".  This function takes an array of gcodes and sends
    // them as a batch command.  This would behave like a Klipper Gcode Macro
    // with one signficant difference...if one gcode in the batch requests
    // results in an error, Klipper will continue to process subsequent gcodes.
    // A Klipper Gcode Macro will immediately stop execution of the macro
    // if an error is encountered.

    let batch = [];
    for (let gc of gcodes) {
        batch.push(
            {
                method: 'post_printer_gcode',
                type: 'request',
                params: {script: gc}
            });
    }

    // The batch request returns a promise with all results
    json_rpc.send_batch_request(batch)
    .then((results) => {
        for (let res of results) {
            // Each result is an object with three keys:
            // method:  The method executed for this result
            // index:  The index of the original request
            // result: The successful result


            // Use the index to look up the gcode parameter in the original
            // request
            let orig_gcode = batch[res.index].params[0];
            console.log("Batch Gcode " + orig_gcode +
            " successfully executed with result: " + res.result);
        }
    })
    .catch((err) => {
        // Like the result, the error is an object.  However there
        // is an "error" in place of the "result key"
        let orig_gcode = batch[err.index].params[0];
        console.log("Batch Gcode <" + orig_gcode +
        "> failed with error: " + err.error.message);
    });
}

// The function below demonstrates a more useful method of sending
// a client side gcode macro.  Like a Klipper macro, gcode execution
// will stop immediately upon encountering an error.  The advantage
// of a client supporting their own macros is that there is no need
// to restart the klipper host after creating or deleting them.
async function send_gcode_macro(gcodes) {
    for (let gc of gcodes) {
        try {
            let result = await json_rpc.call_method_with_kwargs(
                'post_printer_gcode', {script: gc});
        } catch (err) {
            console.log("Error executing gcode macro: " + err.message);
            break;
        }
    }
}

// A simple reconnecting websocket
class KlippyWebsocket {
    constructor(addr) {
        this.base_address = addr;
        this.connected = false;
        this.ws = null;
        this.onmessage = null;
        this.onopen = null;
        this.connect();
    }

    connect() {
        // Doing the websocket connection here allows the websocket
        // to reconnect if its closed. This is nice as it allows the
        // client to easily recover from Klippy restarts without user
        // intervention
        this.ws = new WebSocket(this.base_address + "/websocket");
        this.ws.onopen = () => {
            this.connected = true;
            console.log("Websocket connected");
            if (this.onopen != null)
                this.onopen();
        };

        this.ws.onclose = (e) => {
            this.connected = false;
            console.log("Websocket Closed, reconnecting in 1s: ", e.reason);
            setTimeout(() => {
                this.connect();
            }, 1000);
        };

        this.ws.onerror = (err) => {
            console.log("Websocket Error: ", err.message);
            this.ws.close();
        };

        this.ws.onmessage = (e) => {
            // Tornado Server Websockets support text encoded frames.
            // The onmessage callback will send the data straight to
            // JSON-RPC
            this.onmessage(e.data);
        };
    }

    send(data) {
        // Only allow send if connected
        if (this.connected) {
            this.ws.send(data);
        } else {
            console.log("Websocket closed, cannot send data");
        }
    }

};

window.onload = () => {
    let prefix = window.location.protocol == "https" ? "wss://" : "ws://";
    let ws = new KlippyWebsocket(prefix + location.host);
    ws.onopen = () => {
        // Go ahead and do some initialization, the commands below
        // do not need Klippy to be "ready" to return valid info. It
        // isn't necesary to do the request over the websocket, you
        // could just as easily send HTTP GET requests here for the
        // klippy info and the file list.

        // These could be implemented JSON RPC Batch requests and send both
        // at the same time, however it is easier to simply do them
        // individually
        get_klippy_info();
        get_file_list();
    };
    json_rpc.register_transport(ws);

    // Handle changes between the HTTP and Websocket API
    $('.reqws').prop('disabled', true);
    $('input[type=radio][name=test_type]').on('change', function() {
        api_type = $(this).val();
         $('.toggleable').prop(
             'disabled', (api_type == 'websocket' || is_printing));
        $('.reqws').prop('disabled', (api_type == 'http'));
    });

    // Send a gcode.  Note that in the test client nearly every control
    // checks a radio button to see if the request should be sent via
    // the REST API or the Websocket API.  A real client will choose one
    // or the other, so the "api_type" check will be unnecessary
    $('#gcform').submit((evt) => {
        let line = $('#gcform [type=text]').val();
        $('#gcform [type=text]').val('');
        update_term(line);
        if (api_type == 'http') {
            let gc_url = "/printer/gcode?script=" + line
            // send a HTTP "run gcode" command
            $.post(gc_url, (data, status) => {
                update_term(data.result);
            });
        } else {
            // Send a websocket "run gcode" command.
            run_gcode(line);
        }
        return false;
    });

    // Send a command to the server.  This can be either an HTTP
    // get request formatted as the endpoint(ie: /objects) or
    // a websocket command.  The websocket command needs to be
    // formatted as if it were already json encoded.
    $('#apiform').submit((evt) => {
        // Send to a user defined endpoint and log the response
        if (api_type == 'http') {
            let url = $('#apiform [type=text]').val();
            $.get(url, (resp, status) => {
                console.log(resp);
            });
        } else {
            let cmd = $('#apiform [type=text]').val().split(',', 2);
            let method = cmd[0].trim();
            if (cmd.length > 1) {
                let args = cmd[1].trim();
                if (args.startsWith("{")) {
                    args = JSON.parse(args);
                }
                json_rpc.call_method(method, args);
            } else {
                json_rpc.call_method(method);
            }
        }
        return false;
    });

    //  Hidden file element's click is forwarded to the button
    $('#btnupload').click(() => {
        $('#upload-file').click();
    });

    // Uploads a selected file to the server
    $('#upload-file').change(() => {
        update_progress(0, 100);
        let file = $('#upload-file').prop('files')[0];
        if (file) {
            console.log("Sending Upload Request...");
            // It might not be a bad idea to validate that this is
            // a gcode file here, and reject and other files.

            // If you want to allow multiple selections, the below code should be
            // done in a loop, and the 'let file' above should be the entire
            // array of files and not the first element
            if (api_type == 'http') {
                let fdata = new FormData();
                fdata.append("file", file);
                $.ajax({
                    url: "/printer/files/upload",
                    data: fdata,
                    cache: false,
                    contentType: false,
                    processData: false,
                    method: 'POST',
                    xhr: () => {
                        let xhr = new window.XMLHttpRequest();
                        xhr.upload.addEventListener("progress", (evt) => {
                            if (evt.lengthComputable) {
                                update_progress(evt.loaded, evt.total);
                            }
                        }, false);
                        return xhr;
                    },
                    success: (resp, status) => {
                        console.log(resp);
                        return false;
                    }
                });
            } else {
                console.log("File Upload not supported over websocket")
            }
            $('#upload-file').val('');
        }
    });

    // Download a file from the server.  This implementation downloads
    // whatever is selected in the <select> element
    $('#btndownload').click(() => {
        update_progress(0, 100);
        let filename = $("#filelist").val();
        if (filename) {
            if (api_type == 'http') {
                let url = "http://" + location.host + "/printer/files/";
                url += filename
                $('#hidden_link').attr('href', url);
                $('#hidden_link')[0].click();
            } else {
                console.log("File Download not supported over websocket")
            }
        }
    });

    // Delete a file from the server.  This implementation deletes
    // whatever is selected in the <select> element
    $("#btndelete").click(() =>{
        let filename = $("#filelist").val();
        if (filename) {
            if (api_type == 'http') {
                let url = "/printer/files/" + filename;
                $.ajax({
                    url: url,
                    method: 'DELETE',
                    success: (resp, status) => {
                        console.log(resp);
                        return false;
                    }
                });
            } else {
                console.log("File Delete not supported over websocket")
            }
        }
    });

    // Start a print.  This implementation starts the print for the
    // file selected in the <select> element
    $("#btnstartprint").click(() =>{
        let filename = $("#filelist").val();
        if (filename) {
            if (api_type == 'http') {
                let url = "/printer/print/start?filename=" + filename;
                $.post(url, (resp, status) => {
                        console.log(resp);
                        return false;
                });
            } else {
                start_print(filename);
            }
        }
    });

    // Pause/Resume a currently running print.  The specific gcode executed
    // is configured in printer.cfg.
    $("#btnpauseresume").click(() =>{
        if (api_type == 'http') {
            let url = paused ? "/printer/print/resume" : "/printer/print/pause";
            $.post(url, (resp, status) => {
                console.log(resp.result);
                return false;
            });
        } else {
            if (paused) {
                resume_print();
            } else {
                pause_print();
            }
        }
    });

    // Cancel a currently running print. The specific gcode executed
    // is configured in printer.cfg.
    $("#btncancelprint").click(() =>{
        if (api_type == 'http') {
            let url = "/printer/print/cancel";
            $.post(url, (resp, status) => {
                console.log(resp);
                return false;
            });
        } else {
            cancel_print();
        }
    });

    $('#btngetlog').click(() => {
        if (api_type == 'http') {
            let url = "http://" + location.host + "/printer/log";
            $('#hidden_link').attr('href', url);
            $('#hidden_link')[0].click();
        } else {
            console.log("Get Log not supported over websocket")
        }
    });

    $('#btnrestart').click(() => {
        if (api_type == 'http') {
            let url = "/printer/restart";
            $.post(url, (resp, status) => {
                console.log(resp);
                return false;
            });
        } else {
            restart();
        }
    });

    $('#btnfirmwarerestart').click(() => {
        if (api_type == 'http') {
            let url = "/printer/firmware_restart";
            $.post(url, (resp, status) => {
                console.log(resp);
                return false;
            });
        } else {
            firmware_restart();
        }
    });

    // Post Subscription Request
    $('#btnsubscribe').click(() => {
        if (api_type == 'http') {
            const suburl = "/printer/subscriptions?gcode=gcode_position,speed,speed_factor,extrude_factor" +
                    "&toolhead&virtual_sdcard&heater_bed&extruder=temperature,target&fan";
            $.post(suburl, (data, status) => {
                console.log(data);
            });
        } else {
            const sub = {
                gcode: ["gcode_position", "speed", "speed_factor", "extrude_factor"],
                toolhead: [],
                virtual_sdcard: [],
                heater_bed: [],
                extruder: ["temperature", "target"],
                fan: []};
            add_subscription(sub);
        }
    });

    // Get subscription info
    $('#btngetsub').click(() => {
        if (api_type == 'http') {
            $.get("/printer/subscriptions", (resp, status) => {
                console.log(resp);
            });
        } else {
            get_subscribed();
        }
    });

    $('#btnsendbatch').click(() => {
        let default_gcs = "M118 This works,RESPOND TYPE=invalid,M118 Execs Despite an Error";
        let result = window.prompt("Enter a set of comma separated gcodes:", default_gcs);
        if (result == null || result == "") {
            console.log("Batch GCode Send Cancelled");
            return;
        }
        let gcodes = result.trim().split(',');
        send_gcode_batch(gcodes);
    });

    $('#btnsendmacro').click(() => {
        let default_gcs =  "M118 This works,RESPOND TYPE=invalid,M118 Should Not Exec";
        let result = window.prompt("Enter a set of comma separated gcodes:", default_gcs);
        if (result == null || result == "") {
            console.log("Gcode Macro Cancelled");
            return;
        }
        let gcodes = result.trim().split(',');
        send_gcode_macro(gcodes);
    });
};
