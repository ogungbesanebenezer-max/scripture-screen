"""The before-service check: one plain line per thing the service depends on. No windows here, so it is testable."""
from dataclasses import dataclass

OK, WARN, BAD = "ok", "warn", "bad"
SILENCE = 0.003  # same floor the listener uses
MIN_FREE_MB = 200


@dataclass(frozen=True)
class Line:
    name: str
    state: str
    detail: str


def check(*, engine_loaded, engine_error, listening, input_name, phone_on, phone_running, peak, heard_ago, screens,
          screen_name, bible, bible_ok, second, plugged, battery, free_mb, auto):
    lines = []
    if engine_error:
        lines.append(Line("Speech engine", BAD, engine_error))
    elif not engine_loaded:
        lines.append(Line("Speech engine", WARN, "Still loading. Wait a moment."))
    elif not listening:
        lines.append(Line("Speech engine", BAD, "Listening has stopped. Close the app and open it again."))
    else:
        lines.append(Line("Speech engine", OK, "Loaded and listening."))

    if phone_on:
        source = "the phone mic"
    elif input_name:
        source = input_name
    else:
        source = None
    if source is None:
        lines.append(Line("Sound input", BAD, "No microphone or mixer found. Plug one in, or use PHONE MIC."))
    elif peak < SILENCE:
        lines.append(Line("Sound input", WARN, f"Silent on {source}. Speak into the mic: this line must turn green."))
    else:
        lines.append(Line("Sound input", OK, f"Hearing sound from {source}."))

    if engine_loaded and not engine_error:
        if heard_ago is None:
            lines.append(Line("Words heard", WARN, 'Nothing yet. Say "John chapter 3 verse 16" as a test.'))
        else:
            lines.append(Line("Words heard", OK, f"Last sentence {int(heard_ago)} seconds ago."))

    if screens > 1:
        lines.append(Line("TV / projector", OK, f"Verses go to: {screen_name}."))
    else:
        lines.append(Line("TV / projector", BAD, "No second screen found. Plug in the HDMI cable, then press Rescan."))

    if bible_ok:
        lines.append(Line("Bible", OK, f"{bible}" + (f", also showing {second}." if second else ".")))
    else:
        lines.append(Line("Bible", BAD, f"{bible} cannot be read. Pick another Default Bible."))

    if plugged is False:
        lines.append(Line("Power", WARN, f"On battery{f' ({battery}%)' if battery is not None else ''}. Plug in the "
                                         "charger and keep the lid open."))
    elif plugged:
        lines.append(Line("Power", OK, "Charger is plugged in. Keep the lid open."))

    lines.append(Line("Phone mic", OK if phone_on else WARN if not phone_running else OK,
                      "Connected and sending sound." if phone_on else
                      "Ready if you need it (optional)." if phone_running else "Off. The laptop sound input still works."))

    if free_mb is not None and free_mb < MIN_FREE_MB:
        lines.append(Line("Disk space", WARN, f"Only {free_mb} MB free. Delete some files after the service."))

    lines.append(Line("AUTO", OK if auto else WARN, "On: verses go up by themselves." if auto else
                      "Off: someone must press PUSH LIVE for every verse."))
    return lines


def verdict(lines):
    states = {line.state for line in lines}
    if BAD in states:
        return BAD, "NOT READY: FIX THE RED LINES"
    if WARN in states:
        return WARN, "ALMOST READY: CHECK THE YELLOW LINES"
    return OK, "READY FOR SERVICE"
