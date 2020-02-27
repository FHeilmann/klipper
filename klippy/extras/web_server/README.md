# Klippy Webserver

## Overview

The Klippy Webserver exposes an API that can be used by web applications to
interact with Klipper.  This implementation runs within Klippy on its own
thread. It depends on Tornado's for the Server, Websocket, and framework.
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
~/klippy-env/bin/pip install tornado
```

You may notice that aside from the addition of the web_server extra, other
changes were made to support its additon. Below is a detailed list of the
changes made outside of the `web_server` folder:
- `webhooks.py` has been added.  This module allows other host modules to
  host modules to register endpoints without trying to load the web_server
  module
- The following changes have been made to `klippy.py`:
  - The webhooks module is instantiated on printer initialization
  - The following endpoints are registered and handled:
    - /printer/info
    - /printer/restart
    - /printer/firmware_restart
  - A "klippy:post_config" event is broadcast immediately after the config
    has been read, before the unused option check.  The server uses this
    event to register all webhooks.
- The following changes have been made to `gcode.py`:
  - The webhooks module is passed into the GCodeParser's constructor. The
    the "/printer/gcode" endpoint is registered and handled by the GCodeParser
    class.
  - A "gcode:respond" event has been added.  The server uses this event to
    broadcast gcode responses over all connected websockets
  - When a write is performed on the pty, the exception handler now checks
    for errno 11 (resource not available).  If this error is found
    termios.tcflush is called to flush the output buffer.  This prevents
    an accumulation of OSErrors from logging and keeps the pty from crashing.
  - `query_endstops.py` now uses the webhooks module to register the
    "/printer/endstops" endpoint
  - `display.py` has been updated initialize all variables in its constructor.
    This fixes an issue some variables used by its get_status() may be accessed
    prior to initialization.

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
#long_running_gcodes:
# BED_MESH_CALIBRATE, 120.
# M104, 200.
#   A list of gcodes that will be assigned their own timeout.  The list should
#   be in the format presented above, where the first item is the gcode name
#   and the second item is the timeout (in seconds).  Each pair should be
#   separated by a newline.  The default is an empty list where no gcodes have
#   a unique timeout.
#long_running_requests:
# /printer/gcode, 60.
# /printer/print/pause, 60.
# /printer/print/resume, 60.
# /printer/print/cancel, 60.
#    A list of requests that will be assigned their own timeout.  The list
#    should be formatted in the same manner as long_running_gcodes.  The
#    default is matches the example shown above.
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

`{result: <response data>}`

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

### Get Klippy host information:
- HTTP command:\
  `GET /printer/info`

- Websocket command:\
  `{jsonrpc: "2.0", method: "get_printer_info", id: <request id>}`

- Returns:\
  An object containing the build version, cpu info, and if the Klippy
  process is ready for operation.  The latter is useful when a client connects
  after the klippy state event has been broadcast.

  `{version: "<version>", cpu: "<cpu_info>", is_ready: <klippy_ready>}`


### Request available printer objects and their attributes:
- HTTP command:\
  `GET /printer/objects`

- Websocket command:\
  `{jsonrpc: "2.0", method: "get_printer_objects", id: <request id>}`

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
  `GET /printer/subscriptions`

- Websocket command:\
  `{jsonrpc: "2.0", method: "get_printer_subscriptions", id: <request id>}`

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
  `GET /printer/status?gcode`

  The above will fetch a status update for all gcode attributes.  The query
  string can contain multiple items, and specify individual attributes:

  `?gcode=gcode_position,busy&toolhead&extruder=target`

- Websocket command:\
  `{jsonrpc: "2.0", method: "get_printer_status", params:
    {gcode: [], toolhead: ["position", "status"]}, id: <request id>}`

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
### Subscribe to a status request or a batch of status requests:
- HTTP command:\
  `POST /printer/subscriptions?gcode=gcode_position,bus&extruder=target`

- Websocket command:\
  `{jsonrpc: "2.0", method: "post_printer_subscriptions", params:
    {gcode: [], toolhead: ["position", "status"]}, id: <request id>}`

- Returns:\
  An acknowledgement that the request has been received:

  `ok`

  The actual status updates will be sent asynchronously over the websocket.

### Run a gcode:
- HTTP command:\
  `POST /printer/gcode?script=<gc>`

  For example,\
  `POST /printer/gcode?script=RESPOND MSG=Hello`\
  Will echo "Hello" to the terminal.

- Websocket command:\
  `{jsonrpc: "2.0", method: "post_printer_gcode",
    params: {script: <gc>}, id: <request id>}`

- Returns:\
  An acknowledgement that the gcode has completed execution:

  `ok`

### Print a file
- HTTP command:\
  `POST /printer/print/start?filename=<file name>`

- Websocket command:\
  `{jsonrpc: "2.0", method: "post_printer_print_start",
    params: {filename: <file name>, id:<request id>}`

- Returns:\
  `ok` on success

### Pause a print
- HTTP command:\
  `POST /printer/print/pause`

- Websocket command:\
  `{jsonrpc: "2.0", method: "post_printer_print_pause", id: <request id>}`

- Returns:\
  `ok`

### Resume a print
- HTTP command:\
  `POST /printer/print/resume`

- Websocket command:\
  `{jsonrpc: "2.0", method: "post_printer_print_resume", id: <request id>}`

- Returns:\
  `ok`

### Cancel a print
- HTTP command:\
  `POST /printer/print/cancel`

- Websocket command:\
  `{jsonrpc: "2.0", method: "post_printer_print_cancel", id: <request id>}`

- Returns:\
  `ok`

### Restart the host
- HTTP command:\
  `POST /printer/restart`

- Websocket command:\
  `{jsonrpc: "2.0", method: "post_printer_restart", id: <request id>}`

- Returns:\
  `ok`

### Restart the firmware (restarts the host and all connected MCUs)
- HTTP command:\
  `POST /printer/firmware_restart`

- Websocket command:\
  `{jsonrpc: "2.0", method: "post_printer_firmware_restart", id: <request id>}`

- Returns:\
  `ok`

## File Operations

File transfer operations.  It should be that the Websocket only supports retreiving
the currrent file list.  It cannot be used to download, upload, or delete files.

### List available Virtual SDCard Files
- HTTP command:\
  `GET /printer/files`

- Websocket command:\
  `{jsonrpc: "2.0", method: "get_printer_files", id: <request id>}`

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

### Query Endstops
- HTTP command:\
  `GET /printer/endstops`

- Websocket command:\
- `{jsonrpc: "2.0", method: "get_printer_endstops", id: <request id>}`

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
In this implementation the Web Server runs in the Klipper Process on
its own thread.  The Server sends requests to Klippy via the reactor,
using the "register_async_callback" method.  The server is able to send
responses back to the server via Tornado's IOLoop.addCallback method.
The host uses the same method for pushing notifications to the server

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
- [ ] If possible, look into solutions that start the server with a few initial
      endpoints registered prior to the configuration being read in klippy.py.
      This would allow clients to connect and issue restart/firmware_restart
      commands in the event that the configuration is invalid.
- [X] Explore solutions for issue where the pty buffer gets full, resulting in errors
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
