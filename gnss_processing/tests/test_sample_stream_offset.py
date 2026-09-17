"""
Starting a stream somewhere other than the head of a collect.

A long collect is not uniform, so notebook 01 offers an offset into it.  The
whole value of that offset is that the samples it delivers are *exactly* the
samples at that point: acquisition seeds tracking with a code phase and a
Doppler measured on them, and a stream that started half a sample late would
hand tracking a seed for data it never sees.  These tests therefore compare
against a slice of the decoded file rather than against a length or a checksum.

The byte-boundary cases are the ones worth having.  At 8 bits and above every
sample index is a byte, and an offset cannot be wrong; at 2 and 4 bits it can,
and seeking to a rounded byte does not fail -- it silently decodes each sample
from two neighbours' halves.
"""

from __future__ import annotations

import pathlib

import numpy as np
import pytest

from utils import sample_streaming, tracking_io


EIGHT_BIT_COMPLEX = sample_streaming.SampleParameters(
    bit_depth=8, is_complex=True, is_integer=True
)
FOUR_BIT_COMPLEX = sample_streaming.SampleParameters(
    bit_depth=4, is_complex=True, is_integer=True
)
TWO_BIT_COMPLEX = sample_streaming.SampleParameters(
    bit_depth=2, is_complex=True, is_integer=True
)


def write_collect(tmp_path, num_bytes: int = 4096):
    """A file of distinct bytes, so any misalignment shows up as wrong values."""
    path = tmp_path / "collect.dat"
    rng = np.random.default_rng(0)
    path.write_bytes(rng.integers(-128, 128, size=num_bytes, dtype=np.int8).tobytes())
    return path


def decode_whole_file(path, sample_params):
    raw = bytearray(path.read_bytes())
    bits_per_sample = sample_params.bit_depth * (2 if sample_params.is_complex else 1)
    num_samples = len(raw) * 8 // bits_per_sample
    samples = np.zeros(num_samples, dtype=np.complex64)
    sample_streaming.convert_to_complex64_samples(raw, samples, sample_params)
    return samples


@pytest.mark.parametrize("start_sample", [0, 1, 37, 512])
def test_offset_buffers_are_the_samples_at_that_offset(tmp_path, start_sample):
    path = write_collect(tmp_path)
    expected = decode_whole_file(path, EIGHT_BIT_COMPLEX)

    buffer_size = 100
    with sample_streaming.FileSampleStream(
        path, EIGHT_BIT_COMPLEX, buffer_size, start_sample=start_sample
    ) as stream:
        buffers = [b.copy() for b in stream.sample_buffer_generator()]

    assert len(buffers) == (len(expected) - start_sample) // buffer_size
    for i, buffer in enumerate(buffers):
        begin = start_sample + i * buffer_size
        np.testing.assert_array_equal(buffer, expected[begin : begin + buffer_size])


def test_offset_zero_is_the_same_stream_as_no_offset(tmp_path):
    path = write_collect(tmp_path)
    with sample_streaming.FileSampleStream(path, EIGHT_BIT_COMPLEX, 128) as stream:
        plain = [b.copy() for b in stream.sample_buffer_generator()]
    with sample_streaming.FileSampleStream(
        path, EIGHT_BIT_COMPLEX, 128, start_sample=0
    ) as stream:
        explicit = [b.copy() for b in stream.sample_buffer_generator()]

    assert len(plain) == len(explicit)
    for a, b in zip(plain, explicit):
        np.testing.assert_array_equal(a, b)


def test_four_bit_samples_take_any_offset(tmp_path):
    """One byte per complex sample, so every sample index is a byte boundary."""
    path = write_collect(tmp_path)
    expected = decode_whole_file(path, FOUR_BIT_COMPLEX)

    with sample_streaming.FileSampleStream(
        path, FOUR_BIT_COMPLEX, 64, start_sample=13
    ) as stream:
        first = next(stream.sample_buffer_generator()).copy()

    np.testing.assert_array_equal(first, expected[13:77])


def test_an_offset_between_bytes_is_refused_not_rounded(tmp_path):
    path = write_collect(tmp_path)
    with pytest.raises(ValueError, match="multiple of 2 samples"):
        sample_streaming.FileSampleStream(
            path, TWO_BIT_COMPLEX, 64, start_sample=13
        )


@pytest.mark.parametrize(
    "bit_depth, is_complex, expected",
    [(8, True, 1), (16, False, 1), (4, True, 1), (4, False, 2), (2, True, 2), (2, False, 4)],
)
def test_byte_boundary_granularity(bit_depth, is_complex, expected):
    assert (
        sample_streaming.samples_per_byte_boundary(bit_depth, is_complex) == expected
    )


def test_an_offset_past_the_end_is_refused(tmp_path):
    """Seeking past the end is legal and silent; an empty run is not a good error."""
    path = write_collect(tmp_path, num_bytes=1024)
    with pytest.raises(ValueError, match="bytes long"):
        with sample_streaming.FileSampleStream(
            path, EIGHT_BIT_COMPLEX, 64, start_sample=10_000
        ):
            pass


def test_a_negative_offset_is_refused(tmp_path):
    path = write_collect(tmp_path)
    with pytest.raises(ValueError, match="non-negative"):
        sample_streaming.FileSampleStream(
            path, EIGHT_BIT_COMPLEX, 64, start_sample=-1
        )


def test_tracking_filename_separates_runs_by_span():
    """Notebooks 01 and 02 only meet through this name, so it has to be a function
    of the tracked span -- otherwise a segment overwrites the full-length run."""
    # Start and end, not start and duration: 120 s in, running until 600 s in.
    assert (
        tracking_io.tracking_filename("RX7", "GPS_L1C", 120_000.0, 480_000.0)
        == "RX7_GPS_L1C_120-600s.h5"
    )
    # A run from the head of the collect still says where it ended.
    assert (
        tracking_io.tracking_filename("RX3", "GPS_L5", 0.0, 60_000)
        == "RX3_GPS_L5_0-60s.h5"
    )
    # The offset and the duration each move the name on their own.
    names = {
        tracking_io.tracking_filename("RX3", "GPS_L5", start, duration)
        for start, duration in [(0.0, 60_000), (30_000, 60_000), (0.0, 480_000)]
    }
    assert len(names) == 3
    # Seconds are rounded, so sub-second differences share a file.  That is the
    # resolution of the name, and nothing is configured more finely than it.
    assert tracking_io.tracking_filename("RX3", "GPS_L5", 30_000.4, 60_000) == (
        tracking_io.tracking_filename("RX3", "GPS_L5", 30_000, 60_000)
    )


def test_the_collect_id_loses_its_date_but_only_when_it_can():
    """The folder is already named after the experiment, so the file name carries
    only what tells one collect in it from another."""
    haleakala = [
        "20210611_121000_RX3",
        "20210611_121000_RX5",
        "20210611_121000_RX7",
    ]
    assert tracking_io.collect_label("20210611_121000_RX7", haleakala) == "RX7"
    # Two captures of the same channel differ only in the time the prefix holds,
    # so dropping it would put both runs in one file.  The full id stays.
    surge = ["20230324_092021_BALLOON", "20230324_100757_BALLOON"]
    assert (
        tracking_io.collect_label("20230324_092021_BALLOON", surge)
        == "20230324_092021_BALLOON"
    )
    # An id that never carried the prefix is left alone.
    assert tracking_io.collect_label("RX7", ["RX7"]) == "RX7"


def test_tracking_path_gives_each_experiment_its_own_folder():
    """Mirroring COLLECTS_PATH, so a tracking file sits under the same experiment
    name as the samples it came from."""
    path = tracking_io.tracking_path(
        "/outputs",
        "20210611_121000_011501_HI",
        "20210611_121000_RX7",
        "GPS_L1C",
        120_000.0,
        480_000.0,
    )
    assert path == pathlib.Path(
        "/outputs/tracking/20210611_121000_011501_HI/RX7_GPS_L1C_120-600s.h5"
    )
