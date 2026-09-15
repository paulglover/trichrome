"""
CLI surface tests: argument wiring, the dry-run and list commands, and the
confirmation gate on the destructive path.
"""
import os

import numpy as np
import pytest

from trichrome import bake, cli

from test_bake import FakeDecoder, raws


@pytest.fixture
def fake_decode(monkeypatch):
    decoder = FakeDecoder()
    monkeypatch.setattr(bake.merge_mod, "merge_raw_channels", decoder)
    return decoder


def test_list_shows_each_triplet_and_its_output(tmp_path, capsys):
    raws(tmp_path, 6)
    assert cli.main(["list", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "2 triplet(s)" in out
    assert "img001.arw + img002.arw + img003.arw" in out
    assert "img001_RGB.tif" in out


def test_merge_writes_tiffs_and_reports_their_size(tmp_path, capsys, fake_decode):
    raws(tmp_path, 3)
    assert cli.main(["merge", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "8x6, uint16" in out                    # WxH
    assert os.path.exists(str(tmp_path / "img001_RGB.tif"))


def test_photosite_flag_turns_demosaic_off(tmp_path, fake_decode):
    raws(tmp_path, 3)
    cli.main(["merge", str(tmp_path), "--photosite"])
    assert fake_decode.calls[0]["demosaic"] is False


def test_demosaic_is_the_default(tmp_path, fake_decode):
    raws(tmp_path, 3)
    cli.main(["merge", str(tmp_path)])
    assert fake_decode.calls[0]["demosaic"] is True


def test_dry_run_reports_the_plan_without_writing(tmp_path, capsys, fake_decode):
    raws(tmp_path, 3)
    assert cli.main(["merge", str(tmp_path), "--dry-run"]) == 0
    assert "Dry run" in capsys.readouterr().out
    assert not os.path.exists(str(tmp_path / "img001_RGB.tif"))
    assert fake_decode.calls == []


def test_delete_originals_deletes_without_asking(tmp_path, capsys, fake_decode):
    files = raws(tmp_path, 3)
    assert cli.main(["merge", str(tmp_path), "--delete-originals"]) == 0
    assert "deleted 3 source RAW(s)" in capsys.readouterr().out
    assert not any(os.path.exists(f) for f in files)


def test_delete_originals_deletes_nothing_on_a_dry_run(tmp_path, capsys,
                                                       fake_decode):
    files = raws(tmp_path, 3)
    assert cli.main(["merge", str(tmp_path), "--delete-originals",
                     "--dry-run"]) == 0
    assert "Dry run" in capsys.readouterr().out
    assert all(os.path.exists(f) for f in files)
    assert fake_decode.calls == []


def test_a_bad_light_order_fails_before_anything_is_read(tmp_path, capsys):
    raws(tmp_path, 3)
    assert cli.main(["merge", str(tmp_path), "--order", "RGX"]) == 2
    assert "light order" in capsys.readouterr().err


def test_a_bad_file_count_is_reported_not_crashed(tmp_path, capsys):
    raws(tmp_path, 4)
    assert cli.main(["merge", str(tmp_path)]) == 2
    assert "multiple of 3" in capsys.readouterr().err


def test_an_empty_folder_is_reported(tmp_path, capsys):
    assert cli.main(["merge", str(tmp_path)]) == 2
    assert "No RAW files found" in capsys.readouterr().err


def test_a_failed_triplet_makes_the_command_exit_nonzero(tmp_path, capsys,
                                                         fake_decode):
    raws(tmp_path, 3)
    fake_decode.boom = "img002"
    assert cli.main(["merge", str(tmp_path)]) == 1
    assert "FAILED" in capsys.readouterr().err


# --- despeck wiring --------------------------------------------------------

def test_despeck_is_off_unless_asked_for(tmp_path, fake_decode, capsys):
    raws(tmp_path, 3)
    assert cli.main(["merge", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    # (the tmp_path name contains "despeck" here, so match the real output)
    assert "despeck:" not in out and "despeck (threshold" not in out


def test_despeck_reports_its_settings_and_result(tmp_path, fake_decode, capsys):
    raws(tmp_path, 3)
    assert cli.main(["merge", str(tmp_path), "--despeck",
                     "--despeck-threshold", "0.2",
                     "--despeck-radius", "5"]) == 0
    out = capsys.readouterr().out
    assert "despeck (threshold 0.2 D, radius 5 px, floor p99.9)" in out
    assert "despeck: 0 defect(s)" in out        # the fake decode is flat


def test_despeck_mask_is_written_and_named(tmp_path, fake_decode, capsys):
    raws(tmp_path, 3)
    assert cli.main(["merge", str(tmp_path), "--despeck",
                     "--despeck-mask"]) == 0
    assert "img001_RGB_mask.tif" in capsys.readouterr().out
    assert os.path.exists(tmp_path / "img001_RGB_mask.tif")


@pytest.mark.parametrize("flag", [
    ["--despeck-threshold", "0.2"],
    ["--despeck-radius", "6"],
    ["--despeck-neutrality", "0.5"],
    ["--despeck-floor-percentile", "99.0"],
    ["--despeck-mask"],
])
def test_tuning_without_despeck_is_an_error_not_a_silent_no_op(
        tmp_path, fake_decode, flag, capsys):
    """The flags do nothing on their own, and a run that quietly ignored them
    would look like a despeck that found nothing."""
    raws(tmp_path, 3)
    assert cli.main(["merge", str(tmp_path)] + flag) == 2
    assert "only applies with --despeck" in capsys.readouterr().err

