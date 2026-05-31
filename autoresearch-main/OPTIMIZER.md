This optimizer updates ONLY an OptimizedPolicy class within src/policy_controller/policy.py.

This is a separate script which wraps LLM calls to the OpenAI API. The policy
optimizer sends the completed CSV contents to the remote model for analysis.

It must be instrumented with raindrop to show LLM tool call traces.

Inputs:
Completed CSV log paths returned by the episode manager MCP server tool
`list_episode_logs`. The optimizer reads those CSVs and includes their contents in
the LLM request.

Outputs:
Recommendations passed back to a local code editor. In other words, the remote data analysis is separate from the recommendations. Once the recommendations are received, this is passed to a local code editor tool. The local code editor can modify any code within an OptimizedPolicy class within src/policy_controller/policy.py. No other modifications are allowed. 

Objective of the policy:
* In the period of time where x velocity > 0, maximize x position
    * There are phases where x velocity < 0 with high x. Ignore this part
* Minimize absolute angle travelled - less important, but it represents that efficiency in movement is a factor
    * This is appropriate if any servo_angle_rad will not yield an increase in x position
The servo_angle_rad is the only variable which may be changed. The policy should probably vary make this a function of time.

Interfaces:
MCP server given in src/episode_manager/app.py. The optimizer must call that MCP
server's `list_episode_logs` tool as its only episode log source.
