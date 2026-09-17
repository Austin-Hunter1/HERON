"""
The catalogues the notebooks print above their configuration cells.

Both catalogues exist to stop a reader guessing an index, so what is tested here is
the indexing: that `EXPERIMENT_INDEX` and `CHANNEL_INDEX` mean what the printed
table says they mean, and that a wrong one fails with the list it should have been
chosen from rather than an `IndexError` from somewhere deeper.
"""

from __future__ import annotations

import textwrap

import pytest

from utils import catalog
from utils.sample_streaming import SampleParameters


def _write_experiment(root, name, collects, samp_rate=5e6, bands=("L1",)):
    """One experiment directory: a `metadata.yml` plus an empty file per collect."""
    directory = root / name
    directory.mkdir(parents=True)
    band_block = "\n".join(
        f"  {band}:\n    center_freq: 1575420000.0\n    inter_freq: 0.0" for band in bands
    )
    collect_block = "\n".join(
        f"  {collect_id}:\n    channel_config: rx\n    filename: {collect_id}.sc8"
        for collect_id in collects
    )
    (directory / "metadata.yml").write_text(
        textwrap.dedent(
            f"""\
            band_configurations:
            {{band_block}}
            channel_configurations:
              rx:
                samp_rate: {samp_rate:.0f}
                bands: [{", ".join(bands)}]
                sample_format:
                  bit_depth: 8
                  is_complex: true
                  is_integer: true
                  is_signed: true
            collections:
            {{collect_block}}
            """
        ).format(band_block=band_block, collect_block=collect_block)
    )
    for collect_id in collects:
        (directory / f"{collect_id}.sc8").write_bytes(b"\x00" * 2000)
    return directory


@pytest.fixture
def collects_dir(tmp_path):
    _write_experiment(tmp_path, "20210101_000000_A", ["20210101_000000_RX7"])
    _write_experiment(
        tmp_path, "20210102_000000_B", ["20210102_000000_RX3"],
        samp_rate=25e6, bands=("L5",),
    )
    return tmp_path


def test_a_collect_carrying_two_bands_is_two_channels(tmp_path):
    """A band is what a signal rides on, so it is the band -- not the collect -- that
    a notebook is choosing."""
    _write_experiment(tmp_path, "20210101_000000_A", ["20210101_000000_RX7"],
                      bands=("L1", "L2"))
    channels = catalog.list_channels(tmp_path)
    assert [(c.channel_index, c.band_id) for c in channels] == [(0, "L1"), (1, "L2")]
    assert {c.collect_id for c in channels} == {"20210101_000000_RX7"}


def test_experiment_index_matches_the_printed_experiment_order(collects_dir):
    channels = catalog.list_channels(collects_dir)
    assert [(c.experiment_index, c.experiment_name) for c in channels] == [
        (0, "20210101_000000_A"),
        (1, "20210102_000000_B"),
    ]


def test_duration_comes_from_the_file_size_and_its_sample_format(collects_dir):
    channel = catalog.select_channel(collects_dir, 0, 0)
    # 2000 bytes of complex 8-bit is 1000 samples, at 5 Msps.
    assert channel.duration_s == pytest.approx(1000 / 5e6)


def test_a_missing_raw_file_is_reported_rather_than_crashing(collects_dir):
    channel = catalog.select_channel(collects_dir, 0, 0)
    channel.filepath.unlink()
    assert channel.size_bytes == 0
    assert channel.duration_s == 0.0


def test_selecting_by_band_is_what_notebook_01_needs(collects_dir):
    assert catalog.select_channel_for_band(collects_dir, 1, "L5").band_id == "L5"


def test_asking_an_experiment_for_a_band_it_does_not_carry_says_who_does(collects_dir):
    with pytest.raises(RuntimeError) as excinfo:
        catalog.select_channel_for_band(collects_dir, 0, "L5")
    message = str(excinfo.value)
    assert "carries bands ['L1']" in message
    # And where to find one that does, since that is the next thing to do.
    assert "20210102_000000_B" in message


def test_a_bad_experiment_index_lists_the_experiments(collects_dir):
    with pytest.raises(IndexError, match="20210101_000000_A"):
        catalog.select_channel(collects_dir, 7)


def test_a_bad_channel_index_lists_that_experiment_s_channels(collects_dir):
    with pytest.raises(IndexError, match=r"0 \(20210101_000000_RX7: L1\)"):
        catalog.select_channel(collects_dir, 0, 7)


def test_a_directory_without_metadata_costs_an_index_but_contributes_nothing(tmp_path):
    """`EXPERIMENT_INDEX` counts directories, so a directory that is not an experiment
    still occupies its index -- skipping the index instead would renumber everything
    after it."""
    (tmp_path / "20200101_000000_junk").mkdir()
    _write_experiment(tmp_path, "20210101_000000_A", ["20210101_000000_RX7"])
    channels = catalog.list_channels(tmp_path)
    assert [c.experiment_index for c in channels] == [1]


def test_the_catalogue_names_each_experiment_once(collects_dir, capsys):
    _write_experiment(collects_dir, "20210103_000000_C",
                      ["20210103_000000_RX1", "20210103_000000_RX2"])
    catalog.print_collect_catalog(collects_dir)
    lines = capsys.readouterr().out.splitlines()
    assert sum("20210103_000000_C" in line for line in lines) == 1


def test_the_catalogue_can_be_narrowed_to_one_band(collects_dir, capsys):
    catalog.print_collect_catalog(collects_dir, band="L5")
    out = capsys.readouterr().out
    assert "20210102_000000_B" in out
    assert "20210101_000000_A" not in out


def test_sample_format_label_is_the_name_the_files_go_by():
    complex_signed_8 = SampleParameters(bit_depth=8, is_complex=True, is_integer=True)
    assert catalog.sample_format_label(complex_signed_8) == "sc8"
    real_unsigned_4 = SampleParameters(
        bit_depth=4, is_complex=False, is_integer=True, is_signed=False
    )
    assert catalog.sample_format_label(real_unsigned_4) == "ur4"


# ---------------------------------------------------------------------------
# Tracking runs
# ---------------------------------------------------------------------------


def _write_tracking_file(path, signal_ids):
    import h5py

    path.parent.mkdir(parents=True, exist_ok=True)
    with h5py.File(path, "w") as f:
        for signal_id in signal_ids:
            f.create_group(signal_id)


def test_a_tracking_run_is_described_by_its_name_and_its_groups(tmp_path, collects_dir):
    tracking = tmp_path / "tracking" / "20210101_000000_A"
    _write_tracking_file(tracking / "RX7_GPS_L1CA_0-120s.h5", ["G02", "G06"])
    runs = catalog.list_tracking_runs(tmp_path, collects_dir)
    assert len(runs) == 1
    run = runs[0]
    assert run.collect_label == "RX7"
    assert run.signal_type_id == "GPS_L1CA"
    assert (run.start_offset_ms, run.duration_ms) == (0.0, 120_000.0)
    assert run.signal_ids == ["G02", "G06"]
    # The experiment index is what notebook 02's configuration wants.
    assert run.experiment_index == 0


def test_the_span_in_the_name_is_start_and_end_not_start_and_length(tmp_path):
    _write_tracking_file(
        tmp_path / "tracking" / "exp" / "RX7_GPS_L1C_115-600s.h5", ["G14"]
    )
    run = catalog.list_tracking_runs(tmp_path)[0]
    assert run.start_offset_ms == 115_000.0
    assert run.duration_ms == 485_000.0


def test_appledouble_sidecars_are_not_tracking_runs(tmp_path):
    """macOS writes `._name` beside every file on an exFAT volume; they match the
    glob and are not HDF5."""
    tracking = tmp_path / "tracking" / "exp"
    _write_tracking_file(tracking / "RX7_GPS_L5_0-120s.h5", ["G06"])
    (tracking / "._RX7_GPS_L5_0-120s.h5").write_bytes(b"\x00" * 64)
    assert [r.signal_type_id for r in catalog.list_tracking_runs(tmp_path)] == ["GPS_L5"]


def test_an_empty_outputs_directory_says_where_runs_come_from(tmp_path, capsys):
    assert catalog.print_tracking_catalog(tmp_path) == []
    assert "notebook 01" in capsys.readouterr().out
