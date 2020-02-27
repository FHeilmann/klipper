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
