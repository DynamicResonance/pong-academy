Write a Python script which does the following:
* Receive a video stream on a video port (default is /dev/video2 but specify as an arg to the script)
* Detect 4 aruco tags of the 4x4 family of IDs {0, 1, 2, 3}
    * Set up a coordinate system based on the tags arranged in a rough rectangle. Counterclockwise from top left: 0, 1, 2, 3
    * It isn't a perfect rectangle so best fit the coords
    * x axis is from 1-2
    * y axis is from 1-0
    * Save the tag detections on the first time all 4 are seen. The camera doesn't move so afer the first they dont need to be detected anymore. 
* Detect a bright green circle
    * Color is approximately hsl(171, 48, 43)
    * Transform the center of the circle to normalized coordinates relative to the above rectangle. x=0.0 is on the line joining 0-1 and 1.0 is on the line joining 2-3. Similarly, y=0.0 is on the line joining 1-2 and y=1.0 is on the line joining 0-3
* Publish the coordinates with epoch timestamps based on the camera frame. Use zenoh. Publish on the "position" topic with "x" and "y" and "visible" fields. If the ball is not visible, visible=false and x=0 and y = 0. 
* It needs to run in real time. OK to downsample the incoming high res image to accomplish this.
* Use uv for package management. 