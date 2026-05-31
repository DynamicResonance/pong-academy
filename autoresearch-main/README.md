# Autoresearch Servo Controller

This implements `CONTROLLER.md`: a C++ Zenoh subscriber that listens on `command`, reads `timestamp_ns` and `servo_angle_rad`, converts radians to degrees, and drives a servo on Raspberry Pi GPIO 18 using hardware PWM through the pigpio daemon interface.

It also implements `POLICY.md`: a Python Zenoh policy controller that subscribes to `position`, publishes one `command` per position timestamp with `timestamp_ns`, `x`, `y`, and `visible` passed through, and only chooses a new random servo angle at 0.5 second intervals.

## Policy controller

Install and run with uv:

```sh
uv run policy-controller
```

Select the optimized policy explicitly:

```sh
uv run policy-controller --policy optimized
```

Enable verbose debug logs when tracing Zenoh messages and generated commands:

```sh
uv run policy-controller --log-level DEBUG
```

To verify the policy-to-controller Zenoh connection without moving the servo, run these in separate terminals:

```sh
./build/servo_controller --dry-run --debug
uv run policy-controller --log-level DEBUG
uv run policy-controller --publish-test-position --x 0.5 --y 2.0 --visible --timestamp-ns 123456789
```

The policy controller should log a received `position` sample and a published `command`; the dry-run servo controller should log the same `timestamp_ns` and the generated servo angle.

The policy controller expects `position` messages like:

```json
{"x": 0.0, "y": 0.0, "visible": false, "timestamp_ns": 123}
```

It publishes `command` messages like:

```json
{"timestamp_ns":123,"x":0.0,"y":0.0,"visible":false,"servo_angle_rad":1.5707963267948966}
```

Run the Python tests:

```sh
uv run python -m unittest discover -s tests
```

## Episode manager

Collect episode CSV logs from the `command` topic:

```sh
uv run episode-manager
```

Expose the stored episode logs through the MCP stdio server:

```sh
uv run episode-manager --mcp
```

## Policy optimizer

Ask the episode manager MCP tool for completed episode CSVs, send the CSV contents to
an OpenAI model, and emit an `OptimizedPolicy` recommendation:

```sh
uv run policy-optimizer --json
```

The default command calls the LLM and requires `OPENAI_API_KEY`. For a local dry run
that only exercises MCP log discovery and deterministic CSV scoring, use:

```sh
uv run policy-optimizer --json --no-llm
```

To mirror optimizer LLM calls into Raindrop Workshop, run the Workshop and set:

```sh
export RAINDROP_LOCAL_DEBUGGER=http://localhost:5899/v1/
uv run policy-optimizer --json
```

Analyze every completed CSV on Modal with one lightweight worker container, then
synthesize the per-CSV findings locally:

```sh
uv run modal-csv-analyzer --json
```

The Modal analyzer traces the top-level run, every per-CSV remote analysis, and
the synthesis step through Raindrop.

## Build

The parser tests build without Raspberry Pi dependencies:

```sh
cmake -S . -B build -DAUTORESEARCH_BUILD_CONTROLLER=OFF
cmake --build build
ctest --test-dir build
```

On the Raspberry Pi, install pigpio daemon interface headers and Zenoh C headers, then build the controller:

```sh
sudo apt install libpigpiod-if-dev libzenohc-dev pigpio-tools
```

```sh
cmake -S . -B build -DAUTORESEARCH_BUILD_CONTROLLER=ON
cmake --build build
```

Start the pigpio daemon, then run the controller:

```sh
sudo pigpiod
./build/servo_controller
```
