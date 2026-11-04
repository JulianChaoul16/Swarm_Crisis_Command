"""Generate synthesized test audio (Windows built-in text-to-speech) for the Listener Agent.

Each clip is a short 911 call or radio message read by the built-in voices (Zira for the
dispatcher, David for callers and crews). These are synthetic test inputs, not recordings of
real calls; `scripts.json` holds the exact words, so transcription can be scored.

    python tools/make_test_audio.py
"""

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import wave

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "test_media" / "audio"
DISPATCH, CALLER = "Microsoft Zira Desktop", "Microsoft David Desktop"

# name, description, turns of (voice, text), what the call should be read as
CLIPS = [
    ("01_bus_shockoe", "911 call: city bus taking on water in Shockoe Bottom", [
        (DISPATCH, "Nine one one, what is the address of your emergency?"),
        (CALLER, "I'm driving a city bus on East Eighteenth Street and Main, in Shockoe Bottom. Water is coming in "
                 "through the front doors. It's already over the first step."),
        (DISPATCH, "How many people are on the bus with you?"),
        (CALLER, "Four passengers, plus me. One of them is in a wheelchair and can't get up the steps. The road "
                 "outside is moving like a river. We can't get out."),
        (DISPATCH, "Stay on the bus and keep everyone as high as you can. Help is being sent."),
    ], {"location": "Shockoe Bottom (E 18th St & E Main St)", "people": 5, "needs": ["rescue", "medical"],
        "severity": "critical"}),
    ("02_car_underpass", "911 call: elderly man trapped in his car at the Hull St underpass", [
        (DISPATCH, "Nine one one, what is your emergency?"),
        (CALLER, "My dad is stuck in his car in the Hull Street underpass, right by the Mayo Bridge. The water is "
                 "up to the windows and he can't open the door."),
        (DISPATCH, "How old is he, and is he hurt?"),
        (CALLER, "He's seventy eight. He has a heart condition. He's on the phone with me and he's panicking. "
                 "Please send someone now."),
    ], {"location": "South Junction / Hull St underpass", "people": 1, "needs": ["rescue", "medical"],
        "severity": "critical"}),
    ("03_radio_swift_water", "Radio: Swift Water Team 1 reports Riverside residents safe", [
        (CALLER, "Swift Water Team One to dispatch. We are clear of Riverside Apartments. All five residents are "
                 "out and safe, no injuries. We are heading back to the staging area and are available."),
    ], {"report_type": "resolution", "incident": "Riverside Apartments", "people": 5}),
    ("04_hedged_bridge", "Caller: unsure whether the Mayo Bridge is closed", [
        (CALLER, "Hi, um, I'm not sure who to call. I think the Mayo Bridge might be closed? I saw police cars at "
                 "Fourteenth Street with their lights on, but I didn't see any signs. Is it closed?"),
    ], {"report_type": "road_status", "road": "14th St (Mayo) Bridge", "closed": True, "certainty": "possible"}),
    ("05_road_authority_dock", "Road authority: Dock Street closed", [
        (DISPATCH, "Road authority to all units. Dock Street is closed between Fourteenth and Eighteenth Street. "
                   "Water is over the road, about two feet deep. Use Broad Street to reach Shockoe Bottom."),
    ], {"report_type": "road_status", "road": "Dock St", "closed": True, "certainty": "confirmed"}),
    ("06_rooftop_family", "911 call: family on a roof in Manchester, no count at first", [
        (DISPATCH, "Nine one one, where are you?"),
        (CALLER, "We're on the roof of our house, Seventh and Bainbridge, in Manchester. The water came up so "
                 "fast. Please hurry."),
        (DISPATCH, "How many people are with you?"),
        (CALLER, "It's me, my wife, our two kids, and my mother. My mother can't walk, I had to carry her up."),
    ], {"location": "Manchester (7th St & Bainbridge St)", "people": 5, "needs": ["rescue", "medical"],
        "severity": "critical"}),
]

SPEAK = r"""
Add-Type -AssemblyName System.Speech
$s = New-Object System.Speech.Synthesis.SpeechSynthesizer
$s.SelectVoice($args[0])
$s.Rate = 0
$s.SetOutputToWaveFile($args[2])
$s.Speak([System.IO.File]::ReadAllText($args[1]))
$s.Dispose()
"""


def speak(voice: str, text: str, path: Path, workdir: Path) -> None:
    text_file = workdir / "text.txt"
    text_file.write_text(text, encoding="utf-8")
    script = workdir / "speak.ps1"
    script.write_text(SPEAK, encoding="utf-8")
    subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script),
                    voice, str(text_file), str(path)], check=True, capture_output=True)


def join(parts: list[Path], out: Path, gap_s: float = 0.4) -> float:
    with wave.open(str(parts[0])) as first:
        params = first.getparams()
    silence = b"\x00" * int(params.framerate * gap_s) * params.sampwidth * params.nchannels
    with wave.open(str(out), "wb") as target:
        target.setparams(params)
        for index, part in enumerate(parts):
            with wave.open(str(part)) as source:
                if source.getparams()[:3] != params[:3]:
                    raise SystemExit(f"Format mismatch in {part}")
                target.writeframes(source.readframes(source.getnframes()))
            if index < len(parts) - 1:
                target.writeframes(silence)
    with wave.open(str(out)) as done:
        return done.getnframes() / done.getframerate()


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    manifest = []
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        for name, description, turns, expected in CLIPS:
            parts = []
            for index, (voice, text) in enumerate(turns):
                part = work / f"{name}_{index}.wav"
                speak(voice, text, part, work)
                parts.append(part)
            seconds = join(parts, OUT / f"{name}.wav")
            manifest.append({
                "file": f"{name}.wav", "description": description, "seconds": round(seconds, 1),
                "script": [{"speaker": "dispatcher" if voice == DISPATCH else "caller/crew", "voice": voice,
                            "text": text} for voice, text in turns],
                "expected": expected,
            })
            print(f"{name}.wav  {seconds:4.1f}s  {description}")
    (OUT / "scripts.json").write_text(json.dumps({
        "note": ("SYNTHESIZED TEST AUDIO (Windows built-in text-to-speech: Microsoft Zira as dispatcher, "
                 "Microsoft David as callers and crews). Not recordings of real calls; all events are fictional. "
                 "script holds the exact words; expected holds what the call should be read as."),
        "clips": manifest,
    }, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
