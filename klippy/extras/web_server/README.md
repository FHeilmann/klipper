# Klippy Webserver

## Overview

The Klippy Webserver exposes an API that can be used by web applications to
interact with Klipper.  This implementation runs within Klippy on its own
thread. It uses Eventlet's WSGI Server and Websocket implementations, with
Bottle for the framework.

## Installation

Make sure you are running the latest upstream version of Klipper before
proceeding.

Now, install the python dependencies:
```
~/klippy-env/bin/pip install eventlet bottle
```

Now add the `web_server` folder to `klippy/extras`. Update `klippy/gcode.py`,
`klippy/extras/query_endstops.py`, and `klippy/extras/display/display.py` with
the changes located in this repository. The change to gcode.py allows the
server to capture gcode responses, the change to display.py fixes an issue
where some variables used by its get_status() call aren't initialized prior
to being called by web_interface.py.

*A note on query_endstops:*
- `klippy/extras/query_endstops.py` has been updated for compatibility with
  the query_endstops API request.  Unfortunately this could not be done via
  get_status() due to the underlying implementation used to query the endstops,
  each query must be done via MCU request. It would be useful if the endstops
  behaved like *buttons* when they are not used for homing, as the endstop
  state could be cached by the host, and the host would immediately be
  notified of a change.

Configure the [web_server] section in printer.cfg:
```
[web_server]
host: 0.0.0.0
# The host IP to bind the server to.  Defaults to 0.0.0.0, which
# listens on all available interfaces.
port: 7125
# The port to listen on.  Defaults to 7125
web_path:
# The location of the static files to serve.  This will
# likely be removed in the release as it is expected that
# static files will be served by NGINX or another http server.
# The current default is the ./www folder.
api_key_path: ~
# The path to store the API Key.  Defaults to the user's home directory.
# The file name is `.klippy_api_key`, this cannot be changed.
require_auth: True
# Enables Authorization.  When set to true, only trusted clients and
# requests with an API key are accepted.
enable_cors: False
# Enables CORS support.  If serving static files from a different http
# server then CORS  will need to be enabled.
trusted_clients:
# A list of new line separated ip addresses, or ip ranges, that are trusted.
# Trusted clients are given full access to the API.  Note that ranges must
# be expressed in 24-bit CIDR notation, where the last segment is zero:
# 192.168.1.0/24
# The above example will allow 192.168.1.1 - 192.168.1-254.  Note attempting
# to use a non-zero value for the last IP segement or different bit value will
# result in a configuration error.
cancel_gcode:
# The gcode to execute when a print is canceled via the web interface.  Default
# is M25, M26 S0, CLEAR_PAUSE.  This pauses the print and resets the file
# positon to 0.  The pause state is also cleared.
pause_gcode:
# The gcode to execute when a print is paused via the web interface.  Default
# is PAUSE.
resume_gcode:
# The gcode to execute when a print is resumed via the web interface.  Default
# is RESUME.
status_tier_1:
 toolhead
 gcode
status_tier_2:
 fan
status_tier_3:
 extruder
 virtual_sdcard
# Subscription Configuration.  By default items in tier 1 are polled every
# 250 ms, tier 2 every 500 ms, tier 3 every 1s, tier 4 every 2s, tier
# 5 every 4s, tier 6 every 8s.
tick_time: .25
# This is the base interval used for status tier 1.  All other status tiers
# are calculated using the value defined by tick_time (See below for more
# information).  Default is 250ms.
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
All transmissions over the websocket are done via json.  While the websever
expects a json encoded string, one limitation of Eventlet's websocket is that
it doesn't support string encoded frames.  Thus the client will receive data
from the server in the form of a Blob that must be read using a FileReader
object then decoded.

The websocket is located at `ws://host:port/websocket`, for example:
```javascript
var s = new WebSocket("ws://" + location.host + "/websocket");
```

It also should be noted that if authorization is enabled, an untrusted client
must request a "oneshot token" and add that token's value to the websocket's
query string:

```
ws://host:port/websocket?token=32_char_base32_string
```

This is necessary as it isn't currently possible to change
a websocket's header to include `X-Api-Key`.

## API

There are two options for interfacing with the server: communicating solely over a
websocket, or using a combination of a websocket and the HTTP requests.  The
websocket is required for both as it is used to transmit subscribed status
requests and and events initiated from Klippy to the client.  These events
consist of gcode responses, changes in connection status, etc.

Note that all responses are returned as a json encoded object in the form of:

`{command: "<command>", data: <response data>}`

The command matches the original command request, the data is the return
value generated from the request.

If any request results in an error, the websocket response will return
"server_error" as the value to `command`, and the `data` key will contain
an object with the error message and the command that generated it.

`{command: "server_error", data: {message: "Klippy Request Timed Out",
command: "<command>"}}`

HTML requests will recieve a 500 status code on error, accompanied by
the specific error message.

The `www` folder includes a basic test interface with example usage for most
of the requests below.

### Get Klippy Connection info:
- HTTP command:\
  `GET /printer/klippy_info`

- Websocket command:\
  `{get_klippy_info: ""}`

- Returns:\
  An object containing the build version, server host name, and if the Klippy
  process is ready for operation.  The latter is useful when a client connects
  after the klippy state event has been broadcast.

  `{version: "<version>", hostname: "<hostname>", is_ready: <klippy_ready>}`


### Request available status objects and their attributes:
- HTTP command:\
  `GET /printer/objects/`

- Websocket command:\
  `{get_object_info: ""}`

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
  `{get_subscribed: ""}`

- Returns:\
  An object of the similar that above, however the format of the `data`
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
  `GET /printer/objects/?gcode`

  The above will fetch a status update for all gcode attributes.  The query
  string can contain multiple items, and specify individual attributes:

  `?gcode=gcode_position,busy&toolhead&extruder=target`

- Websocket command:\
  `{get_status: {gcode: [], toolhead: ["position", "status"]}`

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
  `{add_subscription: {gcode: [], toolhead: ["position", "status"]}`

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
  `{run_gcode: "<gcode>"}`

- Returns:\
  An acknowledgement that the gcode has completed execution:

  `ok`

### Query Endstops
The query endstops API request can be used to determine the current endstop
state without parsing a response from the `QUERY_ENDSTOPS` gcode.

- HTTP command:\
  `GET /printer/query_endstops`

- Websocket command:\
  `{query_endstops: ""}`

- Returns:\
  An object containing the current endstop state, with each attribute in the
  format of `endstop:<state>`, where "state" can be "open" or "TRIGGERED", for
  example:

```json
  {x: "TRIGGERED",
   y: "open",
   z: "open"}
```

### Print a file
- HTTP command:\
  `POST /printer/print/start/<filename>`

- Websocket command:\
  `{start_print: "file_name"}`

- Returns:\
  `ok` on success

### Pause a print
- HTTP command:\
  `POST /printer/print/pause`

- Websocket command:\
  `{pause_print: ""}`

- Returns:\
  `ok`

### Resume a print
- HTTP command:\
  `POST /printer/print/resume`

- Websocket command:\
  `{resume_print: ""}`

- Returns:\
  `ok`

### Cancel a print
- HTTP command:\
  `POST /printer/print/cancel`

- Websocket command:\
  `{cancel_print: ""}`

- Returns:\
  `ok`

## File Transfer

File transfer operations.  It should be noted that while the file transfer API
is available over the websocket, the client side implementation using the
REST API is quite a bit less complex.  It should be noted that file upload,
download, and delete are not currently allowed while printing, as they could
negatively impact the Klipper process.

### List available Virtual SDCard Files
- HTTP command:\
  `GET /printer/files/`

- Websocket command:\
  `{get_file_list: ""}`

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
  `{download_file: {filename: "file_name"}`

  Note that websocket downloads require extra implementation by the client as
  they are sent in parts.  After the websocket download request, the server
  will prepare to send the file to the client and return the following:

  ```json
  {
    command: download_file,
    data: {
      filename: "file_name",
      size: <total_file_size>,
      chunks: <number of parts>,
      chunk_size: <size of each part (in bytes)>
    }}
  ```

  When the client receives this response it should use this data to prepare to
  receive the file, as immediately following the response the server will start
  sending the binary data over the websocket.  When the server has reached EOF
  it will send the following command:

  `{command: download_file, data: "complete"}`

- Returns:\
  The requested file

### File Upload
- HTTP command:\
  `POST /printer/files/upload`

  The to be uploaded file should be added to the FormData per the XHR spec.

- Websocket command:\
  ```json
  {upload_file: {
    filename: "file_name",
    size: <file_size>,
    chunks: <number of parts>,
    chunk_size: <size of each part (in bytes)>}
  ```

  As with websocket file downloads, file uploads are also done by slicing
  the file in parts.  Parts can be no larger than 8096 bytes.  After the
  initial upload request is sent, the server will respond with:

  `{upload_file: {state: "ready", chunk: <current chunk requested}}`

  The above response will be sent after each chunk is received until the final
  chunk is received, after which the server will respond with:

  `{upload_file: {state: "ready"}}`

- Returns:\
  The HTTP API returns the file name along with a successful response.  The
  websocket API returns `{state: "complete"}` upon file upload completion as
  noted above.

### File Delete

- HTTP command:\
  `DELETE /printer/files/<file_name>`

- Websocket command:\
  `{delete_file: "file_name"}`

- Returns:\
  The HTTP request returns the name of the deleted file.  The websocket returns
  "ok".

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

## Websocket events
The following events occur asynchronously over the websocket.  Websocket
events are forwarded to all connected clients.  Events have the same
structure as responses:

`{command: "event name", data: "<event data>"}`

### Gcode response:
All calls to gcode.respond() are forwarded over the websocket.  They arrive
as a "gcode_response" command type:

`{command: "gcode_response", data: "response"}`

### Status subscriptions:
Status Subscriptions arrive with the command type "poll_status":

`{command: "status_update_event", data: <status_data>}`

The structure of the status data is identical to the structure that is
returned from a status request.

### Printer State Changed:
When the printer changes state, the printer_state_event is broadcast.  The
printer can be in one of the following states:
- ready
- printing
- idle

The event is broadcast in the following format:

`{command: "printer_state_event", data: "<state>"}`

### Klippy Process State Changed:
The following Klippy state changes are broadcast over the websocket:
- ready
- disconnect
- shutdown

Note that Klippy's "ready" is different from the Printer's "ready".  The
Klippy "ready" state is broadcast upon startup after initialization is
complete.  It should also be noted that the websocket will be disconnected
after a "disconnect" event, as that event is broadcast prior to a restart.
Klippy State Event's are broadcast in the following format:

`{command: "klippy_state_event", data: "<state>"}`

### File List Changed
When the client makes a change to the virtual sdcard file list
(via upload or delete) an event is broadcast to notify all connected
clients of the change:

`{command: "file_changed_event", data: [current file list]`

The file list is returned in the same format used for file list request.
See the File Transfer section above for more information.

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
- [ ] Support status query for filament sensors, bed_mesh, etc.
- [ ] Create a file list monitor that montors the virtual sdcard directory
      for external changes to the file list.  Currently file changed events
      are only broadcast if a file change is done by the server itself.
- [ ] Update the websocket API to be more robust.  Currently it isn't possible
      to match requests with responses.  Clients should generate a unique id
      that accompanies each request, corresponding responses should include
      that ID.
- [ ] Start server before the configuration is read if Kevin is okay with it.
      This would allow the server to issue "restart" gcode commands if there
      is a general klippy config error.  This would likely require that the
      sever has its own configuration file, so the idea may be rejected.
- [ ] Check to see if its possible to unload a virtual SD Card file.  Pausing
      and resetting the file position to 0 works when canceled, but the ideal
      solution would be to unload the file.
- [ ] Add events for pause and resume.  Its possible that the printer could be
      paused externally, such as by a filament sensor runout.  The client can
      subscribe to the pause_resume object to recieve the current paused state
      and update itself accordingly, however it would be better to receive this
      via an event.
- [ ] Support server configuration from web clients
- [ ] Update Klipper's install script to include eventlet and bottle deps
