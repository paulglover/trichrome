"""
CLI surface tests: argument wiring, the dry run, what is refused before
anything is read, and the destructive path.
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


def test_merge_writes_the_film_id_dng_and_reports_its_size(tmp_path, capsys,
                                                           fake_decode):
    files = raws(tmp_path, 3)
    assert cli.main(["-i", "S0123-10", *files]) == 0
    out = capsys.readouterr().out
    assert f"wrote {tmp_path / 'S0123-10.dng'}  (8x6, uint16)" in out   # WxH
    assert os.path.exists(str(tmp_path / "S0123-10.dng"))


def test_the_long_film_id_option_is_filmid(tmp_path, fake_decode):
    files = raws(tmp_path, 3)
    assert cli.main(["--filmid", "S0123-10", *files]) == 0
    assert os.path.exists(str(tmp_path / "S0123-10.dng"))


def test_the_film_id_is_required(tmp_path, capsys):
    with pytest.raises(SystemExit) as e:
        cli.main(raws(tmp_path, 3))
    assert e.value.code == 2
    assert "-i/--filmid" in capsys.readouterr().err


def test_out_writes_elsewhere(tmp_path, fake_decode):
    files = raws(tmp_path, 3, folder="src")
    assert cli.main(["-i", "S0123-10", "--out", str(tmp_path / "merged"),
                     *files]) == 0
    assert os.path.exists(str(tmp_path / "merged" / "S0123-10.dng"))


def test_photosite_flag_turns_demosaic_off(tmp_path, fake_decode):
    cli.main(["-i", "S0123-10", *raws(tmp_path, 3), "--photosite"])
    assert fake_decode.calls[0]["demosaic"] is False


def test_demosaic_is_the_default(tmp_path, fake_decode):
    cli.main(["-i", "S0123-10", *raws(tmp_path, 3)])
    assert fake_decode.calls[0]["demosaic"] is True


def test_dry_run_reports_the_plan_without_writing(tmp_path, capsys, fake_decode):
    files = raws(tmp_path, 3)
    assert cli.main(["-i", "S0123-10", *files, "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "img001.arw + img002.arw + img003.arw" in out
    assert "S0123-10.dng" in out and "Dry run" in out
    assert not os.path.exists(str(tmp_path / "S0123-10.dng"))
    assert fake_decode.calls == []


def test_delete_originals_deletes_without_asking(tmp_path, capsys, fake_decode):
    files = raws(tmp_path, 3)
    assert cli.main(["-i", "S0123-10", *files, "--delete-originals"]) == 0
    assert "deleted 3 source RAW(s)" in capsys.readouterr().out
    assert not any(os.path.exists(f) for f in files)


def test_delete_originals_deletes_nothing_on_a_dry_run(tmp_path, capsys,
                                                       fake_decode):
    files = raws(tmp_path, 3)
    assert cli.main(["-i", "S0123-10", *files, "--delete-originals",
                     "--dry-run"]) == 0
    assert "Dry run" in capsys.readouterr().out
    assert all(os.path.exists(f) for f in files)
    assert fake_decode.calls == []


def test_a_bad_light_order_fails_before_anything_is_read(tmp_path, capsys):
    assert cli.main(["-i", "S0123-10", *raws(tmp_path, 3),
                     "--order", "RGX"]) == 2
    assert "light order" in capsys.readouterr().err


@pytest.mark.parametrize("n", [2, 4, 6])
def test_a_count_other_than_three_is_reported_not_crashed(tmp_path, capsys, n):
    assert cli.main(["-i", "S0123-10", *raws(tmp_path, n)]) == 2
    assert "exactly 3" in capsys.readouterr().err


def test_a_folder_is_refused(tmp_path, capsys):
    raws(tmp_path, 3)
    assert cli.main(["-i", "S0123-10", str(tmp_path)]) == 2
    assert "not folders" in capsys.readouterr().err


def test_an_existing_output_is_refused_before_anything_is_read(tmp_path, capsys,
                                                               fake_decode):
    files = raws(tmp_path, 3)
    (tmp_path / "S0123-10.dng").write_bytes(b"already here")
    assert cli.main(["-i", "S0123-10", *files, "--delete-originals"]) == 2
    assert "already exists" in capsys.readouterr().err
    assert fake_decode.calls == []
    assert all(os.path.exists(f) for f in files)


def test_a_failed_merge_makes_the_command_exit_nonzero(tmp_path, capsys,
                                                       fake_decode):
    fake_decode.boom = "img002"
    assert cli.main(["-i", "S0123-10", *raws(tmp_path, 3)]) == 1
    assert "FAILED" in capsys.readouterr().err
