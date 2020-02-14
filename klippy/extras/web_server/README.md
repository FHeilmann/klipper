# Klippy Webserver

## Overview

The Klippy Webserver exposes an API that can be used by web applications to
interact with Klipper.  This implementation runs within Klippy on its own
thread. It uses Eventlet's WSGI Server and Websocket implementations, with
Bottle for the framework.

## Installation

Add the following remote to your Klipper git repo:
```
git remote add arksine https://github.com/Arksine/klipper.git
```
Now fetch and checkout:
```
git fetch arksine
git checkout arksine/work-web_server-20200131
```
Note that you are now in a detached head state and you cannot pull. Any
time you want to update to the latest version of this branch you must
repeat the two commands above.

If you want to switch back to the main repo:
```
git checkout master
```

If you are doing a fresh Klipper install from the web server branch, all
of the web server's dependencies will be added when you run install-octopi.sh.
Otherwise you will need to manually install them:
```
~/klippy-env/bin/pip install eventlet bottle
```

You may notice that aside from the addition of the web_server extra, other
changes were made to support its additon. The change to gcode.py allows the
server to capture gcode responses, the change to display.py fixes an issue
where some variables used by its get_status() call aren't initialized prior
to being called by web_interface.py.  The query_endstops.py module has been
modified to use `register_url` to register an endpoint so that clients
may query endstop state.

A default web_server on port 80 that grants authorization to local clients
on the IP range 192.168.1.0 can be configured as follows in printer.cfg:
```
[web_server]
port: 80
trusted_clients:
 192.168.1.0/24
```

Below is a detailed explanation of all options currently available:
```
#[web_server]
#host: 0.0.0.0
#  The host IP to bind the server to.  Defaults to 0.0.0.0, which
#  listens on all available interfaces.
#port: 7125
#  The port to listen on.  Defaults to 7125
#web_path:
#  The location of the static files to serve.  This will
#  likely be removed in the release as it is expected that
#  static files will be served by NGINX or another http server.
#  The current default is the ./www folder.
#api_key_path: ~
#  The path to store the API Key.  Defaults to the user's home directory.
#  The file name is `.klippy_api_key`, this cannot be changed.
#require_auth: True
#  Enables Authorization.  When set to true, only trusted clients and
#  requests with an API key are accepted.
#enable_cors: False
#  Enables CORS support.  If serving static files from a different http
#  server then CORS  will need to be enabled.
#trusted_clients:
#  A list of new line separated ip addresses, or ip ranges, that are trusted.
#  Trusted clients are given full access to the API.  Note that ranges must
#  be expressed in 24-bit CIDR notation, where the last segment is zero:
#  192.168.1.0/24
#  The above example will allow 192.168.1.1 - 192.168.1-254.  Note attempting
#  to use a non-zero value for the last IP segement or different bit value will
#  result in a configuration error.
#cancel_gcode:
#  The gcode to execute when a print is canceled via the web interface. Default
#  is M25, M26 S0, CLEAR_PAUSE.  This pauses the print and resets the file
#  positon to 0.  The pause state is also cleared.
#pause_gcode:
#  The gcode to execute when a print is paused via the web interface.  Default
#  is PAUSE.
#resume_gcode:
#  The gcode to execute when a print is resumed via the web interface.  Default
#  is RESUME.
#request_timeout: 5.
#  The amount of time (in seconds) a client request has to process before the
#  server returns an error.  This timeout does NOT apply to gcode requests.
#  Default is 5 seconds.
#gcode_timeout: 60.
#  The amount of time (in seconds) a gcode request has to process before the
#  server returns an error.  Default is 60 seconds.
#long_running_gcodes:
# BED_MESH_CALIBRATE, 120.
# M104, 200.
#  A list of gcodes that will be assigned their own timeout.  The list should be
#  in the format presented above, where the first item is the gcode name and the
#  second item is the timeout (in seconds).  Each pair should be separated by a
#  newline.  The default is an empty list where no gcodes have a unique timeout.
#status_tier_1:
# toolhead
# gcode
#status_tier_2:
# fan
#status_tier_3:
# extruder
# virtual_sdcard
#  Subscription Configuration.  By default items in tier 1 are polled every
#  250 ms, tier 2 every 500 ms, tier 3 every 1s, tier 4 every 2s, tier
#  5 every 4s, tier 6 every 8s.
#tick_time: .25
#  This is the base interval used for status tier 1.  All other status tiers
#  are calculated using the value defined by tick_time (See below for more
#  information).  Default is 250ms.
```
By default the server listens on all interfaces, port 7125.  Using 8080 works
well for testing if you want to run Octoprint alongside Klippy's webserver
(just make sure you stop webcamd if you are using it).

The "status tiers" are used to determine how fast each klippy object is allowed
to be polled.  Each tier is calculated using the `tick_time` option.  There are
6 tiers, `tier_1 = tick_time` (.25s), `tier_2 = tick_time*2` (.5s),
`tier_3 = tick_time*4` (1s), `tier_4 = tick_time*8` (2s), `tier_5 = tick_time*16`
(4s), and `tier_6 = tick_time*16` (8s).  This method was chosen to provide some
flexibility for slower hosts while making it easy to batch subscription events
together.

## Websocket setup
All transmissions over the websocket are done via json using the JSON-RPC 2.0
protocol.  While the websever expects a json encoded string, one limitation
of Eventlet's websocket is that it can not send string encoded frames.  Thus
the client will receive data om the server in the form of a binary Blob that
must be read using a FileReader object then decoded.

The websocket is located at `ws://host:port/websocket`, for example:
```javascript
var s = new WebSocket("ws://" + location.host + "/websocket");
```

It also should be noted that if authorization is enabled, an untrusted client
must request a "oneshot token" and add that token's value to the websocket's
query string:

```
ws://host:port/websocket?token=<32 character base32 string>
```

This is necessary as it isn't currently possible to add `X-Api-Key` to a
websocket's request header.

## API

Most API methods are supported over both the Websocket and HTTP.  File
Transfer and adminstrative API methods are available only over HTTP. The
Websocket is required to receive printer generated events.

Note that all HTTP responses are returned as a json encoded object in the form of:

`{command: "<command>", result: <response data>}`

The command matches the original command request, the result is the return
value generated from the request.

Websocket requests are returned in JSON-RPC format:
`{jsonrpc: "2.0", "result": <response data>, id: <request id>}`

HTML requests will recieve a 500 status code on error, accompanied by
the specific error message.

Websocket requests that result in an error will receive a properly formatted
JSON-RPC response:
`{jsonrpc: "2.0", "error": {code: <code>, message: <msg>}, id: <request_id>}`
Note that under some circumstances it may not be possible for the server to
return a request ID, such as an improperly formatted json request.

The `www` folder includes a basic test interface with example usage for most
of the requests below.  It also includes a basic JSON-RPC implementation that
uses promises to return responses and errors (see json-rc.js).

### Get Klippy Connection info:
- HTTP command:\
  `GET /printer/klippy_info`

- Websocket command:\
  `{jsonrpc: "2.0", method: "get_klippy_info", id: <request id>}`

- Returns:\
  An object containing the build version, server host name, and if the Klippy
  process is ready for operation.  The latter is useful when a client connects
  after the klippy state event has been broadcast.

  `{version: "<version>", hostname: "<hostname>", is_ready: <klippy_ready>}`


### Request available status objects and their attributes:
- HTTP command:\
  `GET /printer/objects`

- Websocket command:\
  `{jsonrpc: "2.0", method: "get_object_info", id: <request id>}`

- Returns:\
  An object containing key, value pairs, where the key is the name of the
  Klippy module available for status query, and the value is an array of
  strings containing that module's available attributes.

  ```json
  { gcode: ["busy", "gcode_position", ...],
    toolhead: ["position", "status"...], ...}
  ```

### Request currently subscribed objects:
- HTTP command:
  Same as above, however with the ACCEPT header set to `text/event-stream`

- Websocket command:\
  `{jsonrpc: "2.0", method: "get_subscribed", id: <request id>}`

- Returns:\
  An object of the similar that above, however the format of the `result`
  value is changed to include poll times:

   ```json
  { objects: {
      gcode: ["busy", "gcode_position", ...],
      toolhead: ["position", "status"...],
      ...},
    poll_times: {
      gcode: .25,
      toolhead: .25,
      ...}
    }
  ```

### Request the a status update for an object, or group of objects:
- HTTP command:\
  `GET /printer/objects?gcode`

  The above will fetch a status update for all gcode attributes.  The query
  string can contain multiple items, and specify individual attributes:

  `?gcode=gcode_position,busy&toolhead&extruder=target`

- Websocket command:\
  `{jsonrpc: "2.0", method: "get_status", params:
  [{gcode: [], toolhead: ["position", "status"]}], id: <request id>}`

  Note that an empty array will fetch all available attributes for its key.

- Returns:\
  An object where the top level keys are the requested Klippy objects, as shown
  below:

  ```json
  { gcode: {
      busy: true,
      gcode_position: [0, 0, 0 ,0],
      ...},
    toolhead: {
      position: [0, 0, 0, 0],
      status: "Ready",
      ...},
    ...}
  ```
### Subscribe to a status request, or a group of status requests:
- HTTP command:\
  Same as above, however with the ACCEPT header set to `text/event-stream`

- Websocket command:\
  `{jsonrpc: "2.0", method: "add_subscription", params:
  [{gcode: [], toolhead: ["position", "status"]}], id: <request id>}`

- Returns:\
  An acknowledgement that the request has been received:

  `ok`

  The actual status updates will be sent asynchronously over the websocket.

### Run a gcode:
- HTTP command:\
  `POST /printer/gcode/<gcode>`

  For example,\
  `POST /gcode/RESPOND MSG=Hello`\
  Will echo "Hello" to the terminal.

- Websocket command:\
  `{jsonrpc: "2.0", method: "run_gcode", params: [<gcode>] id: <request id>}`

- Returns:\
  An acknowledgement that the gcode has completed execution:

  `ok`

### Print a file
- HTTP command:\
  `POST /printer/print/start/<filename>`

- Websocket command:\
  `{jsonrpc: "2.0", method: "start_print", params: [<file_name>] id: <request id>}`

- Returns:\
  `ok` on success

### Pause a print
- HTTP command:\
  `POST /printer/print/pause`

- Websocket command:\
  `{jsonrpc: "2.0", method: "pause_print", id: <request id>}`

- Returns:\
  `ok`

### Resume a print
- HTTP command:\
  `POST /printer/print/resume`

- Websocket command:\
  `{jsonrpc: "2.0", method: "resume_print", id: <request id>}`

- Returns:\
  `ok`

### Cancel a print
- HTTP command:\
  `POST /printer/print/cancel`

- Websocket command:\
  `{jsonrpc: "2.0", method: "cancel_print", id: <request id>}`

- Returns:\
  `ok`

### Restart the host
- HTTP command:\
  `POST /printer/restart`

- Websocket command:\
  `{jsonrpc: "2.0", method: "restart", id: <request id>}`

- Returns:\
  `ok`

### Restart the firmware (restarts the host and all connected MCUs)
- HTTP command:\
  `POST /printer/firmware_restart`

- Websocket command:\
  `{jsonrpc: "2.0", method: "firmware_restart", id: <request id>}`

- Returns:\
  `ok`

## File Operations

File transfer operations.  It should be that the Websocket only supports retreiving
the currrent file list.  It cannot be used to download, upload, or delete files.

### List available Virtual SDCard Files
- HTTP command:\
  `GET /printer/files`

- Websocket command:\
  `{jsonrpc: "2.0", method: "get_file_list", id: <request id>}`

- Returns:\
  A list of objects containing file data in the following format:

```json
[
  {filename: "file name",
   size: <file size>,
   modified: "last modified date",
   ...]
```

### File Download
- HTTP command:\
  `GET /printer/files/<file_name>`

- Websocket command:\
  File Download Not Supported

- Returns:\
  The requested file

### File Upload
- HTTP command:\
  `POST /printer/files/upload`

  The file to be uploaded should be added to the FormData per the XHR spec.
  Optionally, a "print" attribute may be added to the form data.  If set
  to "true", Klippy will attempt to start the print after uploading.  Note that
  this value should be a string type, not boolean. This provides compatibility
  with Octoprint's legacy upload API.

- Websocket command:\
  File Upload Not Supported

- Returns:\
  The HTTP API returns the file name along with a successful response.

### File Delete

- HTTP command:\
  `DELETE /printer/files/<file_name>`

- Websocket command:\
  File Delete Not Supported

- Returns:\
  The HTTP request returns the name of the deleted file.

### Download klippy.log
- HTTP command:\
  `GET /printer/log`

- Websocket command:\
  Get Log Not Supported

- Returns:\
  klippy.log, assuming it is located in the default directory (/tmp)

## Dynamically Registered Endpoints
Some klipper modules may contain state that can not be streamed, thus their
state is inaccessable via the `/printer/objects` endpoint.  These modules
may choose to dynamically register endpoints at runtime, where their endpoint
will be placed in the `/printer/extras` path.

### Query Endstops
- HTTP command:\
  `GET /printer/extras/endstops`

- Websocket command:\
- `{jsonrpc: "2.0", method: "get_endstops", id: <request id>}`

- Returns:\
  An object containing the current endstop state, with each attribute in the
  format of `endstop:<state>`, where "state" can be "open" or "TRIGGERED", for
  example:

```json
  {x: "TRIGGERED",
   y: "open",
   z: "open"}
```

## Authorization

Untrusted Clients must use a key to access the API by including it in the
`X-Api-Key` header for each HTTP Request.  The API below allows authorized
clients to receive and change the current API Key.  Note that there is
no websocket API for these functions, they must be done via HTTP.

### Get the Current API Key
- HTTP command:\
  `GET /access/api_key`

- Returns:\
  The current API key

### Generate a New API Key
- HTTP command:\
  `POST /access/api_key`

- Returns:\
  The newly generated API key.  This overwrites the previous key.  Note that
  the API key change is applied immediately, all subsequent HTTP requests
  from untrusted clients must use the new key.

### Generate a Oneshot Token

Some HTTP Requests do not expose the ability the change the headers, which is
required to apply the `X-Api-Key`.  To accomodiate these requests it a client
may ask the server for a Oneshot Token.  Tokens expire in 5 seconds and may
only be used once, making them relatively for inclusion in the query string.

- HTTP command:\
  `GET /access/oneshot_token`

- Returns:\
  A temporary token that may be added to a requests query string for access
  to any API endpoint.  The query string should be added in the form of:
  `?token=randomly_generated_token`

## Websocket notifications
Printer generated events are sent over the websocket as JSON-RPC 2.0
notifications.  These notifications are sent to all connected clients
in the following format:

`{jsonrpc: "2.0", method: <event method name>, params: [<event state>]}`

It is important to keep in mind that the `params` value will always be
wrapped in an array as directed by the JSON-RPC standard.  Currently
all notifications available are broadcast with a single parameter.

### Gcode response:
All calls to gcode.respond() are forwarded over the websocket.  They arrive
as a "gcode_response" notification:

`{jsonrpc: "2.0", method: "notify_gcode_response", params: ["response"]}`

### Status subscriptions:
Status Subscriptions arrive as a "notify_status_update" notification:

`{jsonrpc: "2.0", method: "notify_status_update", params: [<status_data>]}`

The structure of the status data is identical to the structure that is
returned from a status request.

### Printer State Changed:
When the printer changes state, "notify_printer_state_changed" is broadcast.  The
printer can be in one of the following states:
- ready
- printing
- idle

The notification is broadcast in the following format:

`{jsonrpc: "2.0", method: "notify_printer_state_changed", params: [<state>]}`

### Klippy Process State Changed:
The following Klippy state changes are broadcast over the websocket:
- ready
- disconnect
- shutdown

Note that Klippy's "ready" is different from the Printer's "ready".  The
Klippy "ready" state is broadcast upon startup after initialization is
complete.  It should also be noted that the websocket will be disconnected
after the "disconnect" state, as that notification is broadcast prior to a
restart. Klippy State notifications are broadcast in the following format:

`{jsonrpc: "2.0", method: "notify_klippy_state_changed", params: [<state>]}`

### File List Changed
When a client makes a change to the virtual sdcard file list
(via upload or delete) a notification is broadcast to alert all connected
clients of the change:

`{jsonrpc: "2.0", method: "notify_filelist_changed", params: [<file changed info>]}`

The <file changed info> param is an object in the following format:

```json
{action: "<action>", filename: "<file_name>", filelist: [<file_list>]}
```

The `action` is the operation that resulted in a file list change, the `filename`
is the name of the file the action was performed on, and the `filelist` is the current
file list, returned in the same format as `get_file_list`.

### Paused State Changed
When the host's paused state is changed, a notifcation will be broadcast over
the websocket:

`{jsonrpc: "2.0", method: "notify_paused_state_changed", params: [<paused state>]}`

The `paused state` may be one of the following:
- paused
- resumed
- cleared

Client developers should be aware that it is common for CLEAR_PAUSE to be
executed before a print starts, when it ends, and when its canceled.  Thus
it is possible to received multiple notifications that the pause was "cleared",
even if the printer was never paused.

## Communication between Klippy and the Web Server
In this implementation the Web Server runs in the Klippy Process on
its own thread.  The Server sends requests to Klippy via the reactor,
using the "register_async_callback" method.  Likewise, the server mimics
the Reactor's mechanism for receiving data from the main thread.  I had
hoped to use eventlet's "green" Queue, since it yields cooperatively with
eventlet's greenthreads, however it turns out that it isn't python thread
safe.

## Todo:
- [X] Handle print requests.  Either use the virutal_sdcard, or have the
      server implement its own gcode parser. Will need to include functionality
      such as returning a file list, printing a file, uploading and downloading
      files
- [X] Support secure login for web clients
- [X] Update the websocket API to be more robust.  Currently it isn't possible
      to match requests with responses.  Clients should generate a unique id
      that accompanies each request, corresponding responses should include
      that ID.
- [X] Add "register_url" support, where Klippy extra modules can register a
      callback to be executed when an endpoint is accessed.  The request
      should also be registered with the websocket API
- [ ] Start server before the configuration is read if Kevin is okay with it.
      This would allow the server to issue "restart" gcode commands if there
      is a general klippy config error.  This would likely require that the
      sever has its own configuration file, so the idea may be rejected.
- [ ] Explore solutions for issue where the pty buffer gets full, resulting in errors
      logged each time the pty is written to.
- [ ] Check to see if its possible to unload a virtual SD Card file.  Pausing
      and resetting the file position to 0 works when canceled, but the ideal
      solution would be to unload the file.
- [X] Add events for pause and resume.  Its possible that the printer could be
      paused externally, such as by a filament sensor runout.  The client can
      subscribe to the pause_resume object to recieve the current paused state
      and update itself accordingly, however it would be better to receive this
      via an event.
- [ ] Support Klippy configuration from web clients
- [X] Update Klipper's install script to include eventlet and bottle deps
