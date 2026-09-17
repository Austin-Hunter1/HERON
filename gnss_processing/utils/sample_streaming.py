import math
import os
from dataclasses import dataclass
from pathlib import Path
from typing import NotRequired, Optional, Generator, TypedDict
import numpy as np
from numpy.typing import NDArray


class SampleParametersDict(TypedDict):
    bit_depth: int
    is_complex: bool
    is_integer: bool
    is_signed: NotRequired[bool]
    is_i_lsb: NotRequired[bool]


@dataclass
class SampleParameters:
    """
    ------------------------------------------------------------------------------------------------
    `bit_depth` -- int, number of bits per numeric value (so an entire complex sample require `2*bit_depth` bits)
    `is_complex` -- bool, whether the samples are complex-valued (versus real-valued)
    `is_integer` -- bool, whether the numeric types are integer (versus float)
    `is_signed` -- bool (default True), whether the numeric types are signed (versus unsigned)
    `is_i_msb` -- bool (default True), whether the I-component occupies the most significant bits of the sample stream
        E.g. for 4-bit samples, specifies whether the I component is in the most significant nibble
    `bytes_per_sample` -- int, number of bytes per sample
    """

    bit_depth: int
    is_complex: bool
    is_integer: bool
    is_signed: bool = True
    is_i_lsb: bool = True

    @property
    def bytes_per_sample(self) -> int:
        if self.is_complex:
            return (self.bit_depth * 2) // 8  # Use integer division
        return self.bit_depth // 8  # Use integer division

    @staticmethod
    def from_dict(sample_parameters_dict: SampleParametersDict) -> "SampleParameters":
        """
        Create a SampleParameters object from a dictionary.
        """
        return SampleParameters(
            bit_depth=sample_parameters_dict["bit_depth"],
            is_complex=sample_parameters_dict["is_complex"],
            is_integer=sample_parameters_dict["is_integer"],
            is_signed=sample_parameters_dict.get("is_signed", True),
            is_i_lsb=sample_parameters_dict.get("is_i_lsb", True),
        )


def get_numpy_dtype(
    is_integer: bool, is_signed: bool, bit_depth: int
) -> Optional[np.dtype]:

    match (is_integer, is_signed, bit_depth):
        case (True, True, 8):
            return np.dtype(np.int8)
        case (True, True, 16):
            return np.dtype(np.int16)
        case (True, True, 32):
            return np.dtype(np.int32)
        # case (True, True, 64):
        #     return np.dtype(np.int64)
        case (True, False, 8):
            return np.dtype(np.uint8)
        case (True, False, 16):
            return np.dtype(np.uint16)
        case (True, False, 32):
            return np.dtype(np.uint32)
        case (True, False, 64):
            return np.dtype(np.uint64)
        case (False, True, 16):
            return np.dtype(np.float16)
        case (False, True, 32):
            return np.dtype(np.float32)
        # case (False, True, 64):  # Note: we cannot deal with 64-bit sample components since we restrict ourselves to 32-bit sample buffers
        #     return np.dtype(np.float64)
        case (_, _, _):
            return None


def compute_sample_array_size_bytes(
    num_samples: int,
    component_bit_depth: int,
    is_complex: bool,
) -> int:
    """
    Compute the number of bytes needes to store `num_samples` samples.
    This function works even for samples with bit depths less than 8 bits.
    """
    bits_per_sample = component_bit_depth * (2 if is_complex else 1)
    buffer_size_bytes = (num_samples * bits_per_sample) // 8
    if buffer_size_bytes * 8 != (num_samples * bits_per_sample):
        buffer_size_bytes += 1
    return buffer_size_bytes


def samples_per_byte_boundary(
    component_bit_depth: int,
    is_complex: bool,
) -> int:
    """
    The sample-count granularity an offset into a packed stream has to respect.

    1 whenever a sample occupies a whole number of bytes, which covers every
    bit depth of 8 and above; 2 for 4-bit real or 2-bit complex samples, 4 for
    2-bit real ones.
    """
    bits_per_sample = component_bit_depth * (2 if is_complex else 1)
    return math.lcm(bits_per_sample, 8) // bits_per_sample


def compute_sample_offset_bytes(
    num_samples: int,
    component_bit_depth: int,
    is_complex: bool,
) -> int:
    """
    The byte offset at which sample `num_samples` begins.

    Not the same rounding as `compute_sample_array_size_bytes`: a *length* is
    rounded up to whole bytes, but an *offset* has to be exact.  Seeking to a
    rounded byte would shift the stream by part of a sample, and every sample
    after it would be assembled from two neighbours' halves -- which does not
    fail, it just decodes noise.  Sub-byte bit depths therefore admit only
    offsets that land on a byte boundary; `samples_per_byte_boundary` gives the
    granularity to snap a requested offset to.
    """
    total_bits = num_samples * component_bit_depth * (2 if is_complex else 1)
    if total_bits % 8:
        granularity = samples_per_byte_boundary(component_bit_depth, is_complex)
        raise ValueError(
            f"a {num_samples}-sample offset does not land on a byte boundary at "
            f"{component_bit_depth}-bit {'complex' if is_complex else 'real'} samples; "
            f"offsets must be a multiple of {granularity} samples."
        )
    return total_bits // 8


def convert_to_complex64_samples(
    input_bytes: bytearray,
    output_sample_array: NDArray[np.complex64],
    sample_params: SampleParameters,
) -> None:
    """
    Convert raw bytes to complex64 samples (each component is float32).
    The input bytes are assumed to be in the format specified by the parameters.
    The output bytes are stored in numpy complex64 format.
    Complex samples are stored in I/Q interleaved format.
    """
    # Unpack parameters
    bit_depth = sample_params.bit_depth
    is_complex = sample_params.is_complex
    is_integer = sample_params.is_integer
    is_signed = sample_params.is_signed
    is_i_lsb = sample_params.is_i_lsb
    
    # Determine sample component dtype; will be None for non-standard bit depths
    input_component_dtype = get_numpy_dtype(is_integer, is_signed, bit_depth)

    # Compute output size in bytes (sanity check)
    if bit_depth >= 8:
        # Use integer division to compute component size
        bytes_per_input_component = bit_depth // 8  
        bytes_per_input_sample = (
            2 * bytes_per_input_component if is_complex else bytes_per_input_component
        )
        num_input_samples = len(input_bytes) // bytes_per_input_sample
        assert (num_input_samples == len(output_sample_array))
    else:
        # For sub-byte samples, we always pack into bytes
        sample_components_per_byte = 8 // bit_depth
        samples_per_byte = (
            sample_components_per_byte // 2 if is_complex else sample_components_per_byte
        )
        num_input_samples = len(input_bytes) * samples_per_byte
        assert (num_input_samples == len(output_sample_array))

    # Need to view as float32 (instead of complex64) so that we can handle I/Q ordering and real samples
    if input_component_dtype is not None:
        raw_sample_array = np.frombuffer(input_bytes, dtype=input_component_dtype)
        if is_complex:
            if is_i_lsb:
                output_sample_array.real = raw_sample_array[0::2]
                output_sample_array.imag = raw_sample_array[1::2]
            else:
                output_sample_array.real = raw_sample_array[1::2]
                output_sample_array.imag = raw_sample_array[0::2]
        else:
            output_sample_array.real = raw_sample_array
            output_sample_array.imag = 0
    elif bit_depth == 4:
        sample_byte_array = np.frombuffer(input_bytes, dtype=np.byte)
        if is_signed:
            comp1 = (sample_byte_array << 4).view(np.int8) >> 4
            comp2 = (sample_byte_array << 0).view(np.int8) >> 4
        else:
            comp1 = (sample_byte_array << 4).view(np.uint8) >> 4
            comp2 = (sample_byte_array << 0).view(np.uint8) >> 4
        if is_complex:
            if is_i_lsb:
                output_sample_array.real = comp1
                output_sample_array.imag = comp2
            else:
                output_sample_array.real = comp2
                output_sample_array.imag = comp1
        else:
            output_sample_array[0::2] = comp1
            output_sample_array[1::2] = comp2

    elif bit_depth == 2:
        sample_byte_array = np.frombuffer(input_bytes, dtype=np.byte)
        # 0xC0 0x30 0x0C 0x03
        if is_signed:
            comp1 = ((sample_byte_array & 0x03) << 6).view(np.int8) >> 6
            comp2 = ((sample_byte_array & 0x0C) << 4).view(np.int8) >> 6
            comp3 = ((sample_byte_array & 0x30) << 2).view(np.int8) >> 6
            comp4 = ((sample_byte_array & 0xC0) << 0).view(np.int8) >> 6
        else:
            comp1 = ((sample_byte_array & 0x03) << 6).view(np.uint8) >> 6
            comp2 = ((sample_byte_array & 0x0C) << 4).view(np.uint8) >> 6
            comp3 = ((sample_byte_array & 0x30) << 2).view(np.uint8) >> 6
            comp4 = ((sample_byte_array & 0xC0) << 0).view(np.uint8) >> 6
        if is_complex:
            if is_i_lsb:
                output_sample_array.real[0::2] = comp1
                output_sample_array.imag[0::2] = comp2
                output_sample_array.real[1::2] = comp3
                output_sample_array.imag[1::2] = comp4
            else:
                output_sample_array.real[0::2] = comp2
                output_sample_array.imag[0::2] = comp1
                output_sample_array.real[1::2] = comp4
                output_sample_array.imag[1::2] = comp3
        else:
            output_sample_array[0::4] = comp1
            output_sample_array[1::4] = comp2
            output_sample_array[2::4] = comp3
            output_sample_array[3::4] = comp4
    else:
        raise NotImplemented()


def mixdown_samples(
        input_samples: NDArray[np.complex64],
        output_samples: NDArray[np.complex64],
        samp_rate: float,
        initial_phase_cycles: float,
        freq_hz: float,
) -> None:
    """
    Mixdown of complex samples by given frequency.

    Args:
        input_samples: Complex samples to mix down.
        output_samples: Output array to store mixed signal.
        samp_rate: Sample rate in Hz.
        initial_phase_cycles: Initial phase offset in cycles.
        freq_hz: Frequency in Hz to mix down by.
    """
    num_samples = input_samples.shape[0]
    time_indices = np.arange(num_samples) / samp_rate
    mixdown_phase = 2.0 * np.pi * (freq_hz * time_indices + initial_phase_cycles)
    output_samples[...] = input_samples[...] * np.exp(-1j * mixdown_phase)
    


class FileSampleStream:
    """
    Buffered reader over a raw sample file.

    `start_sample` skips that many samples before the first buffer, for working
    on a stretch of a long collect other than its beginning.  It is a sample
    count rather than a byte count so that callers reason in the same units the
    rest of the pipeline uses; `compute_sample_offset_bytes` converts, and
    rejects an offset that a sub-byte bit depth cannot express exactly.
    """

    def __init__(
            self,
            filepath: str | Path,
            sample_params: SampleParameters,
            buffer_size_samples: int,
            start_sample: int = 0,
        ) -> None:
        self.filepath = filepath
        self.sample_params = sample_params
        self.buffer_size_samples = buffer_size_samples
        self.buffer_size_bytes = compute_sample_array_size_bytes(
            buffer_size_samples, sample_params.bit_depth, sample_params.is_complex
        )
        if start_sample < 0:
            raise ValueError(f"start_sample must be non-negative, got {start_sample}.")
        self.start_sample = start_sample
        self.start_offset_bytes = compute_sample_offset_bytes(
            start_sample, sample_params.bit_depth, sample_params.is_complex
        )
        self.byte_buffer = bytearray(self.buffer_size_bytes)
        self.sample_buffer = np.zeros(buffer_size_samples, dtype=np.complex64)
    
    def __enter__(self):
        # Checked before opening rather than left to the seek: seeking past the
        # end of a file is legal and silent, so the stream would simply yield
        # nothing and the caller would see an empty run instead of a bad offset.
        file_size_bytes = os.path.getsize(self.filepath)
        if self.start_offset_bytes >= file_size_bytes:
            raise ValueError(
                f"start_sample={self.start_sample} is byte {self.start_offset_bytes} of "
                f"{self.filepath}, which is only {file_size_bytes} bytes long."
            )
        self.file = open(self.filepath, "rb")
        if self.start_offset_bytes:
            self.file.seek(self.start_offset_bytes)
        return self
    
    def __exit__(self, exc_type, exc_value, traceback):
        self.file.close()

    def sample_buffer_generator(self, skip: int = 0) -> Generator[np.ndarray, None, None]:
        while True:
            num_bytes_read = self.file.readinto(self.byte_buffer)
            if num_bytes_read < self.buffer_size_bytes:
                return
            convert_to_complex64_samples(
                self.byte_buffer,
                self.sample_buffer,
                self.sample_params,
            )
            yield self.sample_buffer
            if skip > 1:
                try:
                    if self.file.seekable():
                        self.file.seek((skip - 1) * self.buffer_size_samples * self.sample_params.bytes_per_sample, 1)
                    else:
                        for _ in range(skip - 1):
                            num_bytes_read = self.file.readinto(self.byte_buffer)
                            if num_bytes_read < self.buffer_size_bytes:
                                return
                except OSError:
                    pass
    
    def sample_block_generator(self, block_size_samples: int) -> Generator[np.ndarray, None, None]:
        if self.buffer_size_samples % block_size_samples != 0:
            raise ValueError("Buffer size must be a multiple of block size.")
        num_blocks_in_buffer = self.buffer_size_samples // block_size_samples
        for sample_buffer in self.sample_buffer_generator():
            for block_idx in range(num_blocks_in_buffer):
                start_idx = block_idx * block_size_samples
                end_idx = start_idx + block_size_samples
                yield sample_buffer[start_idx:end_idx]


@dataclass
class SampleBuffer:
    """
    Sample buffer context for passing samples to tracking channels.
    `samples` -- np.ndarray of complex64 samples
    `start_uptime_ms` -- float, uptime in milliseconds of the first sample in the buffer
    `samp_rate` -- float, sample rate in Hz
    """
    samples: NDArray[np.complex64]
    start_uptime_ms: float
    samp_rate: float

    @property
    def num_samples(self) -> int:
        return len(self.samples)
    
    @property
    def stop_uptime_ms(self) -> float:
        return self.start_uptime_ms + (self.num_samples / self.samp_rate) * 1000