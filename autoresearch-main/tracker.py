#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "opencv-python",
#   "numpy",
#   "eclipse-zenoh",
# ]
# ///

"""
Detects ArUco tags 0-3 (4x4 family) arranged counterclockwise from top-left,
latches the first complete calibration, detects a bright green circle, and
publishes its normalized (x, y) coordinates via zenoh on the "position" topic.

Tag layout (counterclockwise from top-left):
  0 (top-left)    3 (top-right)
  1 (bottom-left) 2 (bottom-right)

Normalized coords:
  x=0.0  left edge  (line 0-1)   x=1.0  right edge  (line 3-2)
  y=0.0  bottom edge (line 1-2)  y=1.0  top edge     (line 0-3)
"""

import argparse
import json
import threading
import time

import cv2
import numpy as np
import zenoh

# ---------------------------------------------------------------------------
# ArUco setup
# ---------------------------------------------------------------------------
ARUCO_DICT = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
DETECTOR = cv2.aruco.ArucoDetector(ARUCO_DICT, cv2.aruco.DetectorParameters())
VALID_IDS = {0, 1, 2, 3}

# Normalized destination corners for the perspective transform.
# tag_id -> (x_norm, y_norm)
NORM_CORNERS = {
    0: (0.0, 1.0),  # top-left
    1: (0.0, 0.0),  # bottom-left
    2: (1.0, 0.0),  # bottom-right
    3: (1.0, 1.0),  # top-right
}

DETECT_WIDTH = 640  # downsample to this width for speed

# ---------------------------------------------------------------------------
# Frame grabber — keeps only the latest frame to avoid latency buildup
# ---------------------------------------------------------------------------
class FrameGrabber(threading.Thread):
    def __init__(self, src: str):
        super().__init__(daemon=True)
        self.cap = cv2.VideoCapture(src, cv2.CAP_V4L2)
        self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        self.cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
        if not self.cap.isOpened():
            raise RuntimeError(f"Could not open {src}")
        self.frame = None
        self.timestamp_ns: int = 0
        self._lock = threading.Lock()
        self._stop = threading.Event()

    def run(self):
        while not self._stop.is_set():
            ret, frame = self.cap.read()
            if ret:
                ts = time.time_ns()
                with self._lock:
                    self.frame = frame
                    self.timestamp_ns = ts

    def latest(self):
        with self._lock:
            if self.frame is None:
                return None, 0
            return self.frame.copy(), self.timestamp_ns

    def stop(self):
        self._stop.set()
        self.cap.release()


# ---------------------------------------------------------------------------
# Coordinate transform helpers
# ---------------------------------------------------------------------------
def tag_centers(corners, ids):
    """Return {tag_id: (cx, cy)} for valid detected tags."""
    centers = {}
    if ids is None:
        return centers
    for c, tid in zip(corners, ids.flatten()):
        if tid in VALID_IDS:
            cx, cy = c[0].mean(axis=0)
            centers[int(tid)] = (float(cx), float(cy))
    return centers


def build_homography(centers: dict, inv_scale: float):
    """Build a 3x3 perspective matrix from pixel space to [0,1]x[0,1]."""
    src_pts, dst_pts = [], []
    for tid, (xn, yn) in NORM_CORNERS.items():
        if tid not in centers:
            return None
        cx, cy = centers[tid]
        src_pts.append([cx * inv_scale, cy * inv_scale])
        dst_pts.append([xn, yn])
    src = np.array(src_pts, dtype=np.float32)
    dst = np.array(dst_pts, dtype=np.float32)
    H, _ = cv2.findHomography(src, dst)
    return H


def apply_homography(H, px: float, py: float):
    """Apply homography to a single point."""
    pt = np.array([[[px, py]]], dtype=np.float32)
    out = cv2.perspectiveTransform(pt, H)
    return float(out[0, 0, 0]), float(out[0, 0, 1])


# ---------------------------------------------------------------------------
# Green circle detection
# ---------------------------------------------------------------------------
def detect_green_circle(small_bgr):
    """
    Return (cx, cy) of the brightest green circle in the downsampled image,
    or None if not found.  Coordinates are in the downsampled frame.
    """
    hsv = cv2.cvtColor(small_bgr, cv2.COLOR_BGR2HSV)
    # HSL(171,48%,43%) → HSV hue≈85, S≈165, V≈162; allow ±15 hue, loose S/V bounds
    mask = cv2.inRange(hsv, (70, 80, 80), (100, 255, 255))
    mask = cv2.erode(mask, None, iterations=2)
    mask = cv2.dilate(mask, None, iterations=2)

    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None

    # Pick the largest contour that is reasonably circular
    best = None
    best_area = 0
    for c in contours:
        area = cv2.contourArea(c)
        if area < 50:
            continue
        perimeter = cv2.arcLength(c, True)
        if perimeter == 0:
            continue
        circularity = 4 * np.pi * area / (perimeter ** 2)
        if circularity > 0.5 and area > best_area:
            best = c
            best_area = area

    if best is None:
        return None

    M = cv2.moments(best)
    if M["m00"] == 0:
        return None
    cx = M["m10"] / M["m00"]
    cy = M["m01"] / M["m00"]
    return float(cx), float(cy)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="ArUco + green circle tracker")
    parser.add_argument("--device", default="/dev/video2", help="video device")
    parser.add_argument("--no-display", action="store_true", help="headless mode")
    args = parser.parse_args()

    grabber = FrameGrabber(args.device)
    grabber.start()

    session = zenoh.open(zenoh.Config())
    pub = session.declare_publisher("position")

    H = None  # latched homography from the first frame containing all 4 tags
    latched_centers = None
    last_visible = None

    print(f"Tracking on {args.device} — press q to quit")

    try:
        while True:
            frame, ts_ns = grabber.latest()
            if frame is None:
                if cv2.waitKey(1) & 0xFF == ord("q"):
                    break
                continue

            h, w = frame.shape[:2]
            scale = DETECT_WIDTH / w
            small = cv2.resize(frame, (DETECT_WIDTH, int(h * scale)))

            inv = 1.0 / scale

            # --- ArUco detection ---
            # The camera is fixed, so calibrate once and then stop spending
            # frame time on marker detection.
            corners = ids = None
            centers = latched_centers or {}
            if H is None:
                gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
                corners, ids, _ = DETECTOR.detectMarkers(gray)
                centers = tag_centers(corners, ids)
                if len(centers) == 4:
                    new_H = build_homography(centers, inv)
                    if new_H is not None:
                        H = new_H
                        latched_centers = centers
                        print("Latched ArUco calibration from first complete tag set")

            # --- Green circle detection ---
            circle = detect_green_circle(small)
            visible = circle is not None and H is not None
            x = y = 0.0
            if circle is not None and H is not None:
                px, py = circle[0] * inv, circle[1] * inv
                x, y = apply_homography(H, px, py)

            payload = json.dumps({
                "x": x,
                "y": y,
                "visible": visible,
                "timestamp_ns": ts_ns,
            })
            pub.put(payload)
            if visible != last_visible:
                if visible:
                    print(f"pos  x={x:.4f}  y={y:.4f}  visible=true")
                else:
                    print("pos  x=0.0000  y=0.0000  visible=false")
                last_visible = visible

            # --- Optional display ---
            if not args.no_display:
                if corners is not None:
                    cv2.aruco.drawDetectedMarkers(frame, corners, ids)
                for tid, (cx_s, cy_s) in centers.items():
                    cx_f, cy_f = int(cx_s * inv), int(cy_s * inv)
                    cv2.circle(frame, (cx_f, cy_f), 6, (0, 255, 0), -1)
                    cv2.putText(frame, str(tid), (cx_f + 8, cy_f),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)
                if circle is not None:
                    cx_f, cy_f = int(circle[0] * inv), int(circle[1] * inv)
                    cv2.circle(frame, (cx_f, cy_f), 12, (0, 0, 255), 2)
                    if H is not None:
                        xn_d, yn_d = apply_homography(H, cx_f, cy_f)
                        cv2.putText(frame, f"({xn_d:.2f},{yn_d:.2f})",
                                    (cx_f + 14, cy_f),
                                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 255), 1)
                n_tags = len(centers)
                color = (0, 255, 0) if H is not None else (0, 165, 255)
                tag_status = "latched" if H is not None else f"{n_tags}/4"
                cv2.putText(frame, f"tags={tag_status}  H={'ok' if H is not None else '--'}",
                            (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.8, color, 2)
                cv2.imshow("tracker", frame)

            if cv2.waitKey(1) & 0xFF == ord("q"):
                break

    finally:
        print("Closing")
        grabber.stop()
        session.close()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
