### Version .04-alpha -TBD
- Add `/printer/gcode/help` endpoint to gcode.py

### Version .03-alpha - 03/09/2020
- Require that the configured port be above 1024.
- Fix hard crash if the webserver fails to start.
- Fix file uploads with names containing whitespace
- Serve static files based on their relative directory, ie a request
  for "/js/main.js" will now look for the files in "<web_path>/js/main.js".
- Fix bug in CORS where DELETE requests raised an exception
- Disable the server when running Klippy in batch mode
- The the `/printer/cancel`, `/printer/pause` and `/printer/resume` gcodes
  are now registed by the pause_resume module.  This results in the following
  changes:
  - The `cancel_gcode`, `pause_gcode`, and `resume_gcode` options have
    been removed from the [web_server] section.
  - The `/printer/pause` and `/printer/resume` endpoints will run the "PAUSE"
    and "RESUME" gcodes respectively.  These gcodes can be overridden by a
    gcode_macro to run custom PAUSE and RESUME commands.  For example:
    ```
    [gcode_macro PAUSE]
    rename_existing: BASE_PAUSE
    gcode:
      {% if not printer.pause_resume.is_paused %}
        M600
      {% endif %}

    [gcode_macro M600]
    default_parameter_X: 50
    default_parameter_Y: 0
    default_parameter_Z: 10
    gcode:
      SET_IDLE_TIMEOUT TIMEOUT=18000
      {% if not printer.pause_resume.is_paused %}
        BASE_PAUSE
      {% endif %}
      G1 E-.8 F2700
      G91
      G1 Z{Z}
      G90
      G1 X{X} Y{Y} F3000
    ```
    If you are calling "PAUSE" in any other macro of config section, please
    remember that it will execute the macro.  If that is not your intention,
    change "PAUSE" in those sections to the renamed version, in the example
    above it is BASE_PAUSE.
  - The cancel endpoint runs a "CANCEL_PRINT" gcode.  Users will need to
    define their own gcode macro for this
  - Remove "notify_paused_state_changed" and "notify_printer_state_changed"
    events.  The data from these events can be fetched via status
    subscriptions.
  - "idle_timeout" and "pause_resume" now default to tier 1 status updates,
    which sets their default refresh time is 250ms.
  - Some additional status attributes have been added to virtual_sdcard.py.  At
    the moment they are experimental and subject to change:
    - 'is_active' - returns true when the virtual_sdcard is processing.  Note
      that this will return false when the printer is paused
    - 'current_file' - The name of the currently loaded file.  If no file is
      loaded returns an empty string.
    - 'print_duration' - The approximate duration (in seconds) of the current
      print.  This value does not include time spent paused.  Returns 0 when
      no file is loaded.
    - 'total_duration' - The total duration of the current print, including time
      spent paused.  This can be useful for approximating the local time the
      print started  Returns 0 when no file is loaded.
    - 'filament_used' - The approximate amount of filament used.  This does not
      include changes to flow rate.  Returns 0 when no file is loaded.
    - 'file_position' - The current position (in bytes) of the loaded file
       Returns 0 when no file is loaded.
    - 'progress' - This attribute already exists, however it has been changed
      to retain its value while the print is paused.  Previously it would reset
      to 0 when paused.  Returns 0 when no file is loaded.

### Version .02-alpha - 02/27/2020
- Migrated Framework and Server from Bottle/Eventlet to Tornado.  This
  resolves an issue where the server hangs for a period of time if the
  network connection abruptly drops.
- A `webhooks` host module has been created.  Other modules can use this
  the webhooks to register endpoints, even if the web_server is not
  configured.
- Two modules have been renamed, subscription_handler.py is now
  status_handler.py and ws_handler.py is now ws_manager.py.  These names
  more accurately reflect their current functionality.
- Tornado Websockets support string encoded frames.  Thus it is no longer
  necessary for clients to use a FileReader object to convert incoming
  websocket data from a Blob into a String.
- The endpoint for querying endstops has changed from `GET
  /printer/extras/endstops` to `GET /printer/endstops`
- Serveral API changes have been made to accomodate the addition of webhooks:
  - `GET /printer/klippy_info` is now `GET /printer/info`.  This endpoint no
    longer  returns host information, as that can be retreived direct via the
    `location` object in javascript.  Instead it returns CPU information.
  - `GET /printer/objects` is no longer used to accomodate multiple request
    types by modifying the "Accept" headers.  Each request has been broken
    down in their their own endpoints:
    - `GET /printer/objects` returns all available printer objects that may
      be queried
    - `GET /printer/status?gcode=gcode_position,speed&toolhead` returns the
      status of the printer objects and attribtues
    - `GET /printer/subscriptions` returns all printer objects that are current
      being subscribed to along with their poll times
    - `POST /printer/subscriptions?gcode&toolhead` requests that the printer
      add the specified objects and attributes to the list of subscribed objects
  - Requests that query the Klippy host with additional parameters can no
    longer use variable paths. For example, `POST /printer/gcode/<gcode>` is no
    longer valid.  Parameters must be added to the query string.  This currently
    affects two endpoints:
    - `POST /printer/gcode/<gcode>` is now `POST /printer/gcode?script=<gcode>`
    - `POST printer/print/start/<filename>` is now
      `POST /printer/print/start?filename=<filename>`
  - The websocket API also required changes to accomodate dynamically registered
    endpoints.  Each method name is now generated from its comparable HTTP
    request.  The new method names are listed below:
    | new method | old method |
    |------------|------------|
    | get_printer_files | get_file_list |
    | get_printer_info | get_klippy_info |
    | get_printer_objects | get_object_info |
    | get_printer_subscriptions | get_subscribed |
    | get_printer_status | get_status |
    | post_printer_subscriptions | add_subscription |
    | post_printer_gcode | run_gcode |
    | post_printer_print_start | start_print |
    | post_printer_print_pause | pause_print |
    | post_printer_print_resume | resume_print |
    | post_printer_print_cancel | cancel_print |
    | post_printer_restart | restart |
    | post_printer_firmware_restart | firmware_restart |
    | get_printer_endstops | get_endstops |
  - As with the http API, a change was necessary to the way arguments are send
    along with the request.  Webocket requests should now send "keyword
    arguments" rather than "variable arguments".  The test client has been
    updated to reflect these changes, see main.js and json-rpc.js, specifically
    the new method `call_method_with_kwargs`.  For status requests this simply
    means that it is no longer necessary to wrap the Object in an Array.  The
    gcode and start print requests now look for named parameters, ie:
    - gcode requests - `{jsonrpc: "2.0", method: "post_printer_gcode",
        params: {script: "M117 FooBar"}, id: <request id>}`
    - start print - `{jsonrpc: "2.0", method: "post_printer_print_start",
        params: {filename: "my_file.gcode"}, id:<request id>}`


### Version .01-alpha - 02/14/2020
- The api.py module has been refactored to contain the bottle application and
  all routes within a class.  Bottle is now imported and patched dynamically
  within this class's constructor.  This resolves an issue where the "request"
  context was lost when the Klippy host restarts.
- Change the Websocket API to use the JSON-RPC 2.0 protocol.  See the test
  client (main.js and json-rpc.js) for an example client side implementation.
- Remove file transfer support from the websocket.  Use the HTTP for all file
  transfer requests.
- Add support for Klippy Host modules to register their own urls.
  Query_endstops.py has been updated with an example.  As a result of this
  change, the endpoint for endstop query has been changed to
  `/printer/extras/endstops`.
- Add support for "paused", "resumed", and "cleared" pause events.
- Add routes for downloading klippy.log, restart, and firmware_restart.
- Remove support for trailing slashes in HTTP API routes.
- Support "start print after upload" requests
- Add support for user configured request timeouts
- The test client has been updated to work with the new changes
