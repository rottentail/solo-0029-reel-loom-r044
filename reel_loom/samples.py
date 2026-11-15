from array import array
import math
import sys
from .timebase import TICKS_PER_SECOND


def read_pcm(path):
    samples = array("h")
    with path.open("rb") as source:
        samples.frombytes(source.read())
    if sys.byteorder != "little":
        samples.byteswap()
    return samples


def amplitude_db(value):
    return 20 * math.log10(max(value, 1e-12))


def describe_samples(samples, rate):
    if not samples:
        return {"samples": 0, "duration_tick": 0, "peak_dbfs": -240.0, "rms_dbfs": -240.0,
                "clipped_samples": 0, "dc_offset": 0.0}
    peak = max(abs(value) for value in samples)/32768
    mean_square = sum((value/32768)**2 for value in samples)/len(samples)
    return {"samples": len(samples), "duration_tick": round(len(samples)*TICKS_PER_SECOND/rate),
            "peak_dbfs": round(amplitude_db(peak), 4),
            "rms_dbfs": round(amplitude_db(math.sqrt(mean_square)), 4),
            "clipped_samples": sum(abs(value) >= 32767 for value in samples),
            "dc_offset": round(sum(samples)/len(samples)/32768, 8)}


def silence_intervals(samples, rate, threshold_db, min_seconds):
    window = max(1, rate//100)
    minimum = round(min_seconds*rate)
    start, intervals = None, []
    for offset in range(0, len(samples), window):
        part = samples[offset:offset+window]
        rms = math.sqrt(sum((x/32768)**2 for x in part)/max(1,len(part)))
        quiet = amplitude_db(rms) <= threshold_db
        if quiet and start is None:
            start = offset
        elif not quiet and start is not None:
            if offset-start >= minimum:
                intervals.append({"start_tick": round(start*TICKS_PER_SECOND/rate),
                                  "end_tick": round(offset*TICKS_PER_SECOND/rate)})
            start = None
    if start is not None and len(samples)-start >= minimum:
        intervals.append({"start_tick": round(start*TICKS_PER_SECOND/rate),
                          "end_tick": round(len(samples)*TICKS_PER_SECOND/rate)})
    return intervals


def waveform_buckets(samples, count):
    if not samples:
        return []
    result = []
    count = min(count, len(samples))
    for index in range(count):
        a, b = index*len(samples)//count, (index+1)*len(samples)//count
        part = samples[a:b]
        result.append({"start_sample": a, "end_sample": b,
                       "min": round(min(part)/32768, 6), "max": round(max(part)/32768, 6),
                       "rms": round(math.sqrt(sum((x/32768)**2 for x in part)/len(part)), 6)})
    return result
