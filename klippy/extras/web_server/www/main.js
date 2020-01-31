//  Main javascript for for Klippy Web Server Example
//
//  Copyright (C) 2019 Eric Callahan <arksine.code@gmail.com>
//
//  This file may be distributed under the terms of the GNU GPLv3 license

var paused = false;
var subscribe_at_start = false;
const upload_buffer_size = 8096;
var line_count = 0;
function update_term(msg) {
    var start = '<div id="line' + line_count + '">';
    $("#term").append(start + msg + "</div>");
    line_count++;
    if (line_count >= 50) {
        var rm = line_count - 50
        $("#line" + rm).remove();
    }
    if ($("#cbxAuto").is(":checked")) {
        $("#term").stop().animate({
        scrollTop: $("#term")[0].scrollHeight
        }, 800);
    }
}

function round_float (value) {
    if (typeof value == "number" && !Number.isInteger(value)) {
        return value.toFixed(2);
    }
    return value;
};

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
    var id = obj + "_" + attr;
    if ($("#" + id).length == 0) {
        $('#sdrow' + stream_div_height).append("<div style='width: 10em; border: 2px solid black'>"
            + obj + " " + attr + ":<div id='" + id + "'></div></div>");
        stream_div_width++;
    }

    var out = "";
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
    filelist.forEach(file => {
        $("#filelist").append(
            "<option value='" + file.filename + "'>" +
            file.filename + "</option>");
    });
}

// TODO: Change to a progress bar that can be used for downloads and uploads
var last_progress = 0;
function update_progress(loaded, total) {
    var progress = parseInt(loaded / total * 100);
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

// A simple reconnecting websocket with extra helpers to process
// Klippy specific server events.
class KlippyWebsocket {
    constructor(addr) {
        this.base_address = addr;
        this.connected = false;
        this.ws = null;
        this.pending_upload = null;
        this.pending_download = false;
        this.connect();
    }

    connect() {
        // Doing the websocket connection here allows the websocket
        // to reconnect if its closed. This is nice as it allows the
        // client to easily recover from Klippy restarts without user
        // intervention
        this.pending_upload = null;
        this.pending_download = null;
        this.ws = new WebSocket(this.base_address + "/websocket");
        this.ws.binaryType = "blob";
        this.ws.onopen = () => {
            this.connected = true;
            console.log("Websocket connected");
            // Go ahead and do some initialization, the commands below
            // do not need Klippy to be "ready" to return valid info. It
            // isn't necesary to do the request over the websocket, you
            // could just as easily send HTTP GET requests here for the
            // klippy info and the file list.
            this.send((JSON.stringify({get_klippy_info: ""})));
            this.send((JSON.stringify({get_file_list: ""})));
        };

        this.ws.onclose = (e) => {
            this.connected = false;
            console.log("Websocket Closed, reconnecting in .5s: ", e.reason);
            setTimeout(() => {
                this.connect();
            }, 500);
        };

        this.ws.onerror = (err) => {
            console.log("Websocket Error: ", err.message);
            this.ws.close();
        };

        this.ws.onmessage = (e) => {
            if (this.pending_download != null) {
                // Incoming Download, save parts to disk then launch save dialog
                this.process_download(e.data);
            } else {
                // Everything over the websocket arrives as a Binary
                // Blob.  We need to use a FileReader object to read the blob
                // as text, then use JSON.parse() to convert the result into an
                // object
                var reader = new FileReader();
                reader.onload = () => {
                    var response = JSON.parse(reader.result);
                    this.process_command(response);
            };
            reader.readAsText(e.data);
            }
        };
    }

    process_download(bytes) {
        // Process the chunks of binary data received from the server.  They
        // are added to a list.  As shown below, it is possible to update a
        // status indicator here.  When the expected number of chunks are
        // received, the entire file is created from the list and the user
        // is prompted to save it.  It isn't as clean as the REST API, however
        // its good enough for our uses.  Most likely clients would download
        // a copy when the user wants to start a print so a gcode preview can
        // be produced.
        this.pending_download.parts.push(bytes);
        var loaded = this.pending_download.parts.length * this.pending_download.chunk_size;
        update_progress(loaded, this.pending_download.size);
        if (this.pending_download.parts.length >= this.pending_download.chunks) {
            var file = new Blob(this.pending_download.parts);
            var url = URL.createObjectURL(file);
            $('#hidden_link').attr('href', url);
            $('#hidden_link').attr('download', this.pending_download.filename);
            $('#hidden_link')[0].click();
            this.pending_download = null;
        }
    }

    process_command(response) {
        // Handle Server Events and Command Responses
        switch(response.command) {
            case "gcode_response":
                // This event contains all gcode responses that would
                // typically be printed to the terminal.  Its possible
                // That multiple lines can be bundled in one response,
                // so if displaying we want to be sure we split them.
                var messages = response.data.split("\n");
                messages.forEach((msg) => {
                    update_term(msg);
                });
                break;
            case "status_update_event":
                // This is subscribed status data.  Here we do a nested
                // for-each to determine the klippy object name, "cmd",
                // the attribute we want, "attr", and the attribute's
                // value, "val"
                $.each(response.data, (cmd, obj) => {
                    $.each(obj, (attr, val) => {
                        update_streamdiv(cmd, attr, val);
                    });
                });
                break;
            case "printer_state_event":
                // Printer State can be "ready", "printing", or "idle".  Here
                // We disable a few buttons when the printer is printing to
                // prevent file manipulation.  The server won't allow it regardless,
                // but its a good idea to make sure the user knows that via the
                // client.
                update_term("Klippy State: " + response.data);
                $('.toggleable').prop('disabled', response.data == "printing");
                break;
            case "klippy_state_event":
                // Klippy state can be "ready", "disconnect", and "shutdown".  This
                // differs from Printer State in that it represents the status of
                // the Host software
                this.process_klippy_state(response.data);
                break;
            case "file_changed_event":
                // This event fires when a client has either added or removed
                // a gcode file.
                update_filelist(response.data.filelist);
                break;
            case "get_klippy_info":
                // A response to a "get_klippy_info" websocket request.  It returns
                // the hostname (which should be equal to location.host), the
                // build version, and if the Host is ready for commands.  Its a
                // good idea to fetch this information after the websocket connects.
                // If the Host is in a "ready" state, we can do some initialization
                update_term("Klippy Hostname: " + response.data.hostname +
                " | Build Version: " + response.data.version);
                if (response.data.is_ready) {
                    // We know the host is ready, lets find out if the printer is
                    // ready by requesting the idle_timeout status
                    this.send((JSON.stringify({get_status: {idle_timeout: [],
                        pause_resume: []}})));
                } else {
                    this.send((JSON.stringify({run_gcode: "STATUS"})));
                }
                break;
            case "get_status":
                // A response to a "get_status" websocket request
                if ("idle_timeout" in response.data) {
                    // As mentioned above, its a good idea that the user understands
                    // that some functionality, such as file manipulation, is disabled
                    // during a print.  This can be done by disabling buttons or by
                    // notifying the user via a popup if they click on an action that
                    // is not allowed.
                    if ("state" in response.data.idle_timeout) {
                        var state = response.data.idle_timeout.state.toLowerCase();
                        $('.toggleable').prop('disabled', state == "printing");
                    }
                }
                if ("pause_resume" in response.data) {
                    if ("is_paused" in response.data.pause_resume) {
                        paused = response.data.pause_resume.is_paused;
                        var label = paused ? "Resume Print" : "Pause Print";
                        $('#btnpauseresume').text(label);
                    }
                }
            case "query_endstops":
                // A response to a "query_endstops" websocket request.
                // The 'data' attribute contains an object of key/value pairs,
                // where the key is the endstop (ie:x, y, or z) and the value
                // is either "open" or "TRIGGERED".
            case "get_object_info":
                // A response to a "get_object_info" websocket request.
                // The 'data' attribute contains a list of items available for status query
            case "get_subscribed":
                // A response to a "get_subscribed" websocket request.
                // The 'data' attribute contains a list of objects containing information
                // about current subscriptions.
                console.log(response.data);
                break;
            case "get_file_list":
                // A response to a "get_file_list" websocket request.
                update_filelist(response.data);
                break;
            case "upload_file":
                // A response to a "upload_file" websocket request.  See the process_upload
                // function for details on the implementation
                this.process_upload(response.data);
                break;
            case "download_file":
                // A response to a "download_file" websocket request.  The websocket will
                // first send back information about the file requested for download.  Below,
                // that information is stored to "this.pending_download".  Once this is done,
                // the server will send the file in parts.  The websocket's onMessage() callback
                // must be able to diffentiate between commands a binary file data.  When the
                // download is complete, the webserver will emit the "download_file" response,
                // to be processed here as shown below (we simply log that the download is complete)
                if (response.data == "complete") {
                    console.log("Download Complete");
                } else {
                    this.pending_download = response.data;
                    this.pending_download.parts = [];
                }
                break;
            case "delete_file":
                // A resposne to a "delete_file" websocket request.
                if (response.data == "ok") {
                    console.log("File deleted");
                }
                break;
            case "pause_print":
                if (response.data == "ok") {
                    $('#btnpauseresume').text("Resume Print");
                    paused = true;
                }
                break;
            case "resume_print":
                if (response.data == "ok") {
                    $('#btnpauseresume').text("Pause Print");
                    paused = false;
                }
                break;
            case "server_error":
                // If any command results in an error on the server, the server will
                // respond with "server_error".  The error can then be handled by the
                // client accordingly.
                this.process_error(response);
                break;
            default:
                console.log(response.data);
        }
    }

    process_klippy_state(state) {
        switch(state) {
            case "ready":
                // Printer just came online, now is a good time to
                // intialize/refresh state.  It would also be a good
                // place to subscribe to status updates
                this.send((JSON.stringify({get_file_list: ""})));
                this.send((JSON.stringify({get_status: {idle_timeout: [],
                    pause_resume: []}})));
                // Go ahead and make sure none o the buttons are disabled.
                $('.toggleable').prop('disabled', false);

                if ($("#cbxSub").is(":checked")) {
                    const cmd = {add_subscription:
                        {gcode: ["gcode_position", "speed", "speed_factor", "extrude_factor"],
                        toolhead: [],
                        virtual_sdcard: [],
                        heater_bed: [],
                        extruder: ["temperature", "target"],
                        fan: []}};
                    this.send(JSON.stringify(cmd));
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

    process_upload(data) {
        // Here we are handling responses to an "upload_file" request
        // from the client
        switch (data.state) {
            case "ready":
                // The server is ready to receive binary data.  The "chunk"
                // attribute tells us which chunk the server is requesting.
                // We can use this data to slice the blob representing the
                // file upload, we can also use it to update a progrees
                // indicator
                var start = data.chunk * upload_buffer_size;
                var end = start + upload_buffer_size;
                var slice = this.pending_upload.slice(start, end)
                this.ws.send(slice);
                update_progress(end, this.pending_upload.size)
                break;
            case "complete":
                // The server has successfully received the complete upload
                console.log("Websocket Upload Complete")
                this.pending_upload = null;
                break;
            default:
                // This shouldn't be reached.  If it does then there is some kind
                // of logic error on the server.
                this.pending_upload = null;
                update_term(data);
        }
    }

    process_error(response) {
        // Handle server error responses
        switch(response.data.command) {
            case "upload_file":
                // There was an error uploading the file.  We reset
                // the pending upload to null so other parts of the
                // client can send requests over the websocket
                this.pending_upload = null;
                break;
            case "download_file":
                // There was an error processing the file download.
                // Reset to null so the client can receive responses
                this.pending_download = null;
                break;
        }
        update_term("Command [" + response.data.command +
        "] resulted in an error: " + response.data.message);
    }

    send(data) {
        // Only allow send if connected and if there is no current
        // upload pending (we don't want to mix messages during a
        // multi-part upload)
        if (this.connected && this.pending_upload == null) {
            this.ws.send(data);
        } else {
            console.log("Cannot Send data over websocket");
        }
    }

    prepare_file_upload(file) {
        if (this.pending_upload != null) {
            console.log("File Send in progress, cannot send another")
            return;
        }
        // File Uploads over the websocket need to send some information so the server
        // can prepare itself to retrieve the upload.  The upload_buffer_size should be
        // no larger than 8096 bytes, the eventlet websocket does not like larger buffers.
        // Determine how many "chunks" need to be sent, then send the "upload_file" request
        // with the file name, file size, chunk count, and buffer size.
        this.pending_upload = file;
        var chunk_count = Math.ceil(file.size / upload_buffer_size);
        this.ws.send(JSON.stringify({upload_file: {filename: file.name, size: file.size,
                                     chunks: chunk_count, chunk_size: upload_buffer_size}}));
    }
};

window.onload = () => {
    var prefix = this.location.protocol == "https" ? "wss://" : "ws://";
    var ws = new KlippyWebsocket(prefix + location.host);

    // Send a gcode.  Note that in the test client nearly every control
    // checks a radio button to see if the request should be sent via
    // the REST API or the Websocket API.  A real client will choose one
    // or the other, so the "sendtype" check will be unnecessary
    $('#gcform').submit((evt) => {
        var line = $('#gcform [type=text]').val();
        $('#gcform [type=text]').val('');
        update_term(line);
        var sendtype = $('input[name=test_type]:checked').val();
        if (sendtype == 'http') {
            var gc_url = "/printer/gcode/" + line
            // send a HTTP "run gcode" command
            $.post(gc_url, (data, status) => {
                update_term(data.data);
            });
        } else {
            // Send a websocket "run gcode" command.
            var req = JSON.stringify({run_gcode: line});
            ws.send(req);
        }
        return false;
    });

    // Send a command to the server.  This can be either an HTTP
    // get request formatted as the endpoint(ie: /objects/) or
    // a websocket command.  The websocket command needs to be
    // formatted as if it were already json encoded.
    $('#apiform').submit((evt) => {
        // Send to a user defined endpoint and log the response
        var sendtype = $('input[name=test_type]:checked').val();
        if (sendtype == 'http') {
            var url = $('#apiform [type=text]').val();
            $.get(url, (resp, status) => {
                console.log(resp);
            });
        } else {
            var cmd = $('#apiform [type=text]').val();
            ws.send(cmd);
        }
        return false;
    });

    // Subscription Request
    $('#btnsubscribe').click(() => {
        var sendtype = $('input[name=test_type]:checked').val();
        if (sendtype == 'http') {
            // Endpoint is identical to the "get_status" request, however it adds
            // "text/event-stream" to the Accept header
            const suburl = "/printer/objects/?gcode=gcode_position,speed,speed_factor,extrude_factor" +
                    "&toolhead&virtual_sdcard&heater_bed&extruder=temperature,target&fan";
            $.get({
                url: suburl,
                headers: {
                Accept: "text/event-stream"
                },
                success: (data, status) => {
                    console.log(data);
                }
            });
        } else {
            const cmd = {add_subscription:
                {gcode: ["gcode_position", "speed", "speed_factor", "extrude_factor"],
                toolhead: [],
                virtual_sdcard: [],
                heater_bed: [],
                extruder: ["temperature", "target"],
                fan: []}};
            ws.send(JSON.stringify(cmd));
        }
    });

    // Get subscription info, adds "text/event-stream" header
    $('#btngetsub').click(() => {
        var sendtype = $('input[name=test_type]:checked').val();
        if (sendtype == 'http') {
            // Endpoint is identical to the "get_object_info" request, however it adds
            // "text/event-stream" to the Accept header
            $.get({
                url: "/printer/objects/",
                headers: {
                Accept: "text/event-stream"
                },
                success: (resp, status) => {
                    console.log(resp);
                }
            });
        } else {
            ws.send(JSON.stringify({get_subscribed: ""}));
        }
    });

    //  Hidden file element's click is forwarded to the button
    $('#btnupload').click(() => {
        $('#upload-file').click();
    });

    // Uploads a selected file to the server
    $('#upload-file').change(() => {
        update_progress(0, 100);
        var file = $('#upload-file').prop('files')[0];
        if (file) {
            console.log("Sending Upload Request...");
            // It might not be a bad idea to validate that this is
            // a gcode file here, and reject and other files.
            var sendtype = $('input[name=test_type]:checked').val();
            // If you want to allow multiple selections, the below code should be
            // done in a loop, and the 'var file' above should be the entire
            // array of files and not the first element
            if (sendtype == 'http') {
                var fdata = new FormData();
                fdata.append("file", file);
                $.ajax({
                    url: "/printer/files/upload",
                    data: fdata,
                    cache: false,
                    contentType: false,
                    processData: false,
                    method: 'POST',
                    xhr: () => {
                        var xhr = new window.XMLHttpRequest();
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
                ws.prepare_file_upload(file);
            }
            $('#upload-file').val('');
        }
    });

    // Download a file from the server.  This implementation downloads
    // whatever is selected in the <select> element
    $('#btndownload').click(() => {
        update_progress(0, 100);
        var filename = $("#filelist").val();
        if (filename) {
            var sendtype = $('input[name=test_type]:checked').val();
            if (sendtype == 'http') {
                var url = "http://" + location.host + "/printer/files/";
                url += filename
                $('#hidden_link').attr('href', url);
                $('#hidden_link')[0].click();
            } else {
                ws.send(JSON.stringify({download_file: {filename: filename}}));
            }
        }
    });

    // Delete a file from the server.  This implementation deletes
    // whatever is selected in the <select> element
    $("#btndelete").click(() =>{
        var filename = $("#filelist").val();
        if (filename) {
            var sendtype = $('input[name=test_type]:checked').val();
            if (sendtype == 'http') {
                var url = "/printer/files/" + filename;
                $.ajax({
                    url: url,
                    method: 'DELETE',
                    success: (resp, status) => {
                        console.log(resp);
                        return false;
                    }
                });
            } else {
                ws.send(JSON.stringify({delete_file: {filename: filename}}));
            }
        }
    });

    // Start a print.  This implementation starts the print for the
    // file selected in the <select> element
    $("#btnstartprint").click(() =>{
        var filename = $("#filelist").val();
        if (filename) {
            var sendtype = $('input[name=test_type]:checked').val();
            if (sendtype == 'http') {
                var url = "/printer/print/start/" + filename;
                $.post(url, (resp, status) => {
                        console.log(resp);
                        return false;
                });
            } else {
                ws.send(JSON.stringify({start_print: filename}));
            }
        }
    });

    // Pause/Resume a currently running print.  The specific gcode executed
    // is configured in printer.cfg.
    $("#btnpauseresume").click(() =>{
        var sendtype = $('input[name=test_type]:checked').val();
        if (sendtype == 'http') {
            var url = paused ? "/printer/print/resume" : "/printer/print/pause";
            $.post(url, (resp, status) => {
                paused = !paused;
                var label = paused ? "Resume Print" : "Pause Print";
                $('#btnpauseresume').text(label);
                return false;
            });
        } else {
            var cmd = paused ? {resume_print: ""} : {pause_print: ""};
            ws.send(JSON.stringify(cmd));
        }
    });

    // Cancel a currently running print. The specific gcode executed
    // is configured in printer.cfg.
    $("#btncancelprint").click(() =>{
        var sendtype = $('input[name=test_type]:checked').val();
        if (sendtype == 'http') {
            var url = "/printer/print/cancel";
            $.post(url, (resp, status) => {
                console.log(resp);
                return false;
            });
        } else {
            ws.send(JSON.stringify({cancel_print: ""}));
        }
    });
};
