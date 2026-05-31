The objective is to create what I call an "episode manager" to facilitate the collection of data, store it locally, and serve it via an MCP server.
* Subscribe to one topic:
    * "command" with schema {"timestamp_ns": int, "servo_angle_rad": float, "x": float, "y": float, "visible": bool}
* Write to csv with headers with the schema:
    timestamp_ns,x,y,servo_angle_rad
    * On close, deduplicate rows by timestamp_ns and normalize timestamp_ns so the first row in each completed csv is 0 nanoseconds.
* Episode management
    * An episode can either be active or inactive
    * Inactive -> active: 
        * Condition: command/x value < 1.0 and value > 0.0, visible=true
        * Action: open a new csv whose filename is an RFC3339 timestamp of when its creation time
    * Active -> inactive: 
        * Condition: visible=false or command/x value < 0.0
        * Action: Close the csv
* MCP server
    * The server exposes an "endpoint" to retrieve all completed results. Specifically it returns a list of paths of closed csv logs and excludes active csv logs.
