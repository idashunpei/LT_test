#!/usr/bin/env python3
"""
Convert whitespace-separated 0/1 sequences (one signal per .txt file) to LTspice PWL files.

Edit the USER SETTINGS section below, then run:
    python make_pwl.py

Input example (S11.txt):
    0 0 1 1 0 1

For each bit, one period is allocated:
  0 -> stays LOW for the whole period
  1 -> goes HIGH at the start of the period and returns LOW after PULSE_WIDTH

Output:
  S11.txt -> S11.pwl, S12.txt -> S12.pwl, S13.txt -> S13.pwl
"""

from pathlib import Path


# ============================================================
# USER SETTINGS — edit these values in one place
# ============================================================

INPUT_DIR = "."             # Folder containing input .txt files
FILE_PATTERN = "S*.txt"     # Input files are named S11.txt, S12.txt, S13.txt

PERIOD = "10u"              # Period between bit starts, e.g. "20u"
PULSE_WIDTH = "9u"          # HIGH pulse width, e.g. "5u"; must be <= PERIOD
EDGE_TIME = "1n"             # Transition time, e.g. "1n"; set to "0" for ideal edges

LOW_VOLTAGE = 0.0            # Voltage for logic 0, in volts
HIGH_VOLTAGE = 5.0           # Voltage for logic 1, in volts

# ============================================================
# End of user settings
# ============================================================

import re


TIME_RE = re.compile(
    r"^\s*(?P<num>(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)\s*(?P<unit>[fpnumkKMGT]?)\s*$"
)
SCALE = {
    "f": 1e-15,
    "p": 1e-12,
    "n": 1e-9,
    "u": 1e-6,
    "m": 1e-3,
    "k": 1e3,
    "K": 1e3,
    "M": 1e6,
    "G": 1e9,
    "T": 1e12,
    "": 1.0,
}


def parse_time(value: str) -> float:
    match = TIME_RE.match(str(value))
    if not match:
        raise ValueError(
            f"Invalid time '{value}'. Use e.g. '20u', '2.5u', '1n', or '0.00002'."
        )
    return float(match.group("num")) * SCALE[match.group("unit")]


def fmt(value: float) -> str:
    return f"{value:.12g}"


def read_bits(path: Path) -> list[int]:
    bits = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        line = line.split("#", 1)[0].split("//", 1)[0]
        for token in line.split():
            if token not in ("0", "1"):
                raise ValueError(
                    f"{path.name}:{line_no}: expected only 0/1, got '{token}'"
                )
            bits.append(int(token))
    if not bits:
        raise ValueError(f"{path.name}: no 0/1 data found")
    return bits


def make_points(
    bits: list[int],
    period: float,
    width: float,
    edge: float,
    low: float,
    high: float,
) -> list[tuple[float, float]]:
    """Build a PWL list with strictly increasing times and an explicit final time."""
    if period <= 0:
        raise ValueError("PERIOD must be > 0")
    if width <= 0 or width > period:
        raise ValueError("PULSE_WIDTH must be > 0 and <= PERIOD")
    if edge < 0:
        raise ValueError("EDGE_TIME must be >= 0")

    # Construct the intended piecewise-constant waveform.
    events: list[tuple[float, float]] = [(0.0, high if bits[0] else low)]
    for i, bit in enumerate(bits):
        start = i * period
        end = start + period
        # At the beginning of each bit period, establish its level.
        level = high if bit else low
        events.append((start, level))
        # A '1' returns LOW after pulse width (unless width == period).
        if bit and width < period:
            events.append((start + width, low))
        # At each next period, the next bit's level is set on the next iteration.
        if i == len(bits) - 1:
            events.append((end, low if (bit and width < period) or not bit else high))

    # Sort and collapse same-time events (keep the last value at that time).
    events.sort(key=lambda item: item[0])
    collapsed: list[tuple[float, float]] = []
    for t, v in events:
        if collapsed and abs(t - collapsed[-1][0]) < 1e-18:
            collapsed[-1] = (t, v)
        else:
            collapsed.append((t, v))

    # Add edge shaping: move the old value to edge_time before each transition.
    if edge > 0:
        shaped: list[tuple[float, float]] = [collapsed[0]]
        for t, v in collapsed[1:]:
            old_v = shaped[-1][1]
            if v != old_v and t > 0:
                edge_start = max(shaped[-1][0], t - edge)
                if edge_start > shaped[-1][0]:
                    shaped.append((edge_start, old_v))
                shaped.append((t, v))
            elif t > shaped[-1][0]:
                shaped.append((t, v))
            else:
                shaped[-1] = (t, v)
        collapsed = shaped

    # Ensure final time is strictly later than the preceding point and has a value.
    final_time = len(bits) * period
    if not collapsed or collapsed[-1][0] < final_time:
        final_level = collapsed[-1][1] if collapsed else low
        collapsed.append((final_time, final_level))
    if collapsed[-1][0] <= collapsed[-2][0] if len(collapsed) > 1 else True:
        collapsed.append((final_time + max(edge, 1e-12), collapsed[-1][1]))

    # Final validation: LTspice needs time/value pairs and increasing time values.
    cleaned: list[tuple[float, float]] = []
    for t, v in collapsed:
        if cleaned and t <= cleaned[-1][0]:
            if t == cleaned[-1][0]:
                cleaned[-1] = (t, v)
            else:
                raise ValueError("Generated PWL times are not increasing.")
        else:
            cleaned.append((t, v))
    if len(cleaned) < 2 or cleaned[-1][0] <= cleaned[-2][0]:
        cleaned.append((cleaned[-1][0] + max(edge, 1e-12), cleaned[-1][1]))
    return cleaned


def main() -> None:
    period = parse_time(PERIOD)
    width = parse_time(PULSE_WIDTH)
    edge = parse_time(EDGE_TIME)

    input_dir = Path(INPUT_DIR)
    files = sorted(input_dir.glob(FILE_PATTERN))
    if not files:
        raise SystemExit(
            f"No input files matching {FILE_PATTERN!r} in {input_dir.resolve()}"
        )

    for src in files:
        try:
            bits = read_bits(src)
            points = make_points(bits, period, width, edge, LOW_VOLTAGE, HIGH_VOLTAGE)
            out = src.with_suffix(".pwl")
            with out.open("w", encoding="ascii", newline="\n") as f:
                for t, v in points:
                    f.write(f"{fmt(t)} {fmt(v)}\n")
            print(f"{src.name} -> {out.name} ({len(bits)} bits, {len(points)} points)")
        except (ValueError, OSError) as exc:
            print(f"ERROR: {exc}")


if __name__ == "__main__":
    main()
