import numpy as np

from nvr.config import DETECT_FPS, DETECT_HEIGHT, DETECT_WIDTH, MOTION_DEFAULTS, merge_motion
from nvr.motion import Detector, build_mask, ffmpeg_args

FRAME_SHAPE = (DETECT_HEIGHT, DETECT_WIDTH)


def settings(**overrides):
    return merge_motion({}, overrides)


def blank(value=40):
    return np.full(FRAME_SHAPE, value, dtype=np.uint8)


def with_box(value=40, box=(0.0, 0.0, 0.5, 0.5), brightness=220):
    """A frame with a bright rectangle covering a fraction of width/height."""
    frame = blank(value)
    x_frac, y_frac, w_frac, h_frac = box
    x0, y0 = int(DETECT_WIDTH * x_frac), int(DETECT_HEIGHT * y_frac)
    x1 = x0 + int(DETECT_WIDTH * w_frac)
    y1 = y0 + int(DETECT_HEIGHT * h_frac)
    frame[y0:y1, x0:x1] = brightness
    return frame


def feed(detector, frames, start=1000.0):
    """Push frames at the detection frame rate; return the events emitted."""
    events = []
    for index, frame in enumerate(frames):
        event = detector.update(frame, start + index / DETECT_FPS)
        if event:
            events.append(event)
    return events


def test_static_scene_produces_no_events():
    detector = Detector("cam", settings())
    events = feed(detector, [blank()] * 40)
    assert events == []
    assert not detector.active


def test_moving_object_produces_one_event():
    detector = Detector("cam", settings(min_duration="0s"))
    quiet = [blank()] * 8
    moving = [with_box(box=(0.1 + 0.02 * i, 0.2, 0.2, 0.3)) for i in range(12)]
    # 15s cooldown at 4 fps needs 60+ quiet frames to close the event.
    frames = quiet + moving + [blank()] * 80

    events = feed(detector, frames)

    assert len(events) == 1
    event = events[0]
    assert event.camera == "cam"
    # The event covers the motion and a short tail: once the object leaves, the
    # background needs a few frames to forget it. It must not run away, though.
    motion_seconds = len(moving) / DETECT_FPS
    assert motion_seconds <= event.duration <= motion_seconds * 3
    assert event.peak_area > MOTION_DEFAULTS["min_area"]
    assert not detector.active


def test_event_stays_open_across_a_short_pause():
    detector = Detector("cam", settings(cooldown="5s", min_duration="0s"))
    frames = (
        [blank()] * 8
        + [with_box()] * 6
        + [blank()] * 12  # 3s gap, under the 5s cooldown
        + [with_box(box=(0.3, 0.3, 0.3, 0.3))] * 6
        + [blank()] * 40
    )
    events = feed(detector, frames)
    assert len(events) == 1
    assert events[0].duration > 5


def test_short_blips_are_discarded_by_min_duration():
    detector = Detector("cam", settings(cooldown="2s", min_duration="10s"))
    events = feed(detector, [blank()] * 8 + [with_box()] * 6 + [blank()] * 20)
    assert events == []


def test_single_frame_glitch_does_not_open_an_event():
    detector = Detector("cam", settings(consecutive=3))
    events = feed(detector, [blank()] * 8 + [with_box()] + [blank()] * 40)
    assert events == []
    assert not detector.active


def test_masked_region_is_ignored():
    masked = Detector("cam", settings(mask=[[0, 0, 100, 50]], min_duration="0s"))
    unmasked = Detector("cam", settings(min_duration="0s"))
    # Motion confined to the top half, which the mask blanks out.
    frames = [blank()] * 8 + [with_box(box=(0.1, 0.05, 0.3, 0.3))] * 10 + [blank()] * 80

    assert feed(masked, frames) == []
    assert len(feed(unmasked, frames)) == 1


def test_object_present_in_the_first_frame_does_not_pin_motion_on():
    """The background model must correct itself, not keep a stale patch forever.

    The detector seeds its background from the first frame it sees. If that
    frame contains something that later leaves, the region it occupied has to
    stop being reported as motion -- otherwise the camera never goes quiet.
    """
    detector = Detector("cam", settings(cooldown="5s", min_duration="0s"))
    frames = [with_box()] * 4 + [blank()] * 200

    events = feed(detector, frames)

    assert not detector.active, "detector never returned to a quiet state"
    assert len(events) == 1


def test_gradual_light_change_is_absorbed_by_the_background():
    detector = Detector("cam", settings())
    frames = [blank(40)] * 8 + [blank(40 + i) for i in range(60)] + [blank(100)] * 40
    assert feed(detector, frames) == []


def test_flush_closes_an_open_event():
    detector = Detector("cam", settings(min_duration="0s"))
    feed(detector, [blank()] * 8 + [with_box()] * 10)
    assert detector.active
    event = detector.flush()
    assert event is not None and event.duration > 0
    assert detector.flush() is None


def test_build_mask_marks_regions_as_ignored():
    mask = build_mask([[0, 0, 50, 100]], 100, 10)
    assert mask is not None
    assert not mask[:, :50].any()  # left half ignored
    assert mask[:, 50:].all()
    assert build_mask([], 100, 10) is None


def test_ffmpeg_args_force_tcp_and_a_fixed_frame_size():
    args = ffmpeg_args("rtsp://host/cam")
    assert "-rtsp_transport" in args and args[args.index("-rtsp_transport") + 1] == "tcp"
    assert f"scale={DETECT_WIDTH}:{DETECT_HEIGHT}" in args[args.index("-vf") + 1]
    assert args[args.index("-pix_fmt") + 1] == "gray"
    assert args[-1] == "-"
