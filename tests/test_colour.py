"""
Tests for the colorimetry the DNG's two colour matrices are built from.

Small module, three claims, and they are the ones dng.py leans on: an RGB -> XYZ
matrix that puts white exactly where the white point says, an adaptation that
carries one white onto another, and the degenerate case of adapting a white to
itself. test_dng.py checks what the file then does with them.
"""
import pytest

from trichrome import colour


def test_the_matrix_maps_white_rgb_exactly_onto_the_white_point():
    m = colour.rgb_to_xyz_matrix(colour.SRGB_PRIMARIES_XY, colour.D65_XY)
    white = [sum(row) for row in m]                  # RGB (1, 1, 1)
    assert white == pytest.approx(colour._xyz_from_xy(*colour.D65_XY),
                                  abs=1e-9)


def test_bradford_adaptation_carries_the_source_white_to_the_destination():
    chad = colour.bradford_adaptation(colour.D65_XY, colour.D50_XY)
    moved = colour._mat_vec(chad, colour._xyz_from_xy(*colour.D65_XY))
    assert moved == pytest.approx(colour._xyz_from_xy(*colour.D50_XY),
                                  abs=1e-9)


def test_adapting_a_white_point_to_itself_is_the_identity():
    chad = colour.bradford_adaptation(colour.D65_XY, colour.D65_XY)
    for i in range(3):
        for j in range(3):
            assert chad[i][j] == pytest.approx(1.0 if i == j else 0.0,
                                               abs=1e-12)


def test_the_srgb_primaries_are_the_rec709_ones():
    # Stated as a convention in colour.py, so the numbers are worth pinning:
    # a silent edit here would move every merged file's colour spec.
    assert colour.SRGB_PRIMARIES_XY == ((0.64, 0.33), (0.30, 0.60),
                                        (0.15, 0.06))
    assert colour.D65_XY == (0.3127, 0.3290)
    assert colour.D50_XY == (0.34567, 0.35850)
