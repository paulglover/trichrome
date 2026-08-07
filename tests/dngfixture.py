"""
Synthetic CFA DNG generator for integration tests.

A real trichrome triplet is hundreds of megabytes and camera-specific, so the
integration test builds its own: a minimal single-IFD uncompressed RGGB DNG that
libraw decodes happily. That lets the REAL rawpy path — both channel-extraction
modes, black/white levels, the CFA phase — be tested with no test assets.
"""
import numpy as np
import tifffile


def write_cfa_dng(path, mosaic, white=4095, black=0):
    """Write `mosaic` (2-D uint16 sensor read-out) as an RGGB CFA DNG."""
    extratags = [
        (50706, 'B', 4, (1, 4, 0, 0), True),            # DNGVersion 1.4
        (50707, 'B', 4, (1, 1, 0, 0), True),            # DNGBackwardVersion
        (50708, 's', 0, "synthetic", True),             # UniqueCameraModel
        (33421, 'H', 2, (2, 2), True),                  # CFARepeatPatternDim
        (33422, 'B', 4, (0, 1, 1, 2), True),            # CFAPattern RGGB
        (50710, 'B', 3, (0, 1, 2), True),               # CFAPlaneColor
        (50711, 'H', 1, 1, True),                       # CFALayout rectangular
        (50713, 'B', 2, (2, 2), True),                  # BlackLevelRepeatDim
        (50714, 'H', 1, black, True),                   # BlackLevel
        (50717, 'H', 1, white, True),                   # WhiteLevel
        (50721, '2i', 9, (1, 1, 0, 1, 0, 1, 1, 0, 1,    # ColorMatrix1 (identity)
                          0, 0, 1, 1, 1, 0, 1, 0, 1), True),
        (50778, 'H', 1, 21, True),                      # CalibrationIlluminant1
        (50931, 's', 0, "synthetic", True),             # CameraCalibrationSig
        (50932, 's', 0, "synthetic", True),             # ProfileCalibrationSig
    ]
    tifffile.imwrite(str(path), mosaic.astype(np.uint16), photometric=32803,
                     planarconfig='contig', compression=None,
                     extratags=extratags, metadata=None, software="synthetic")


def rggb_mosaic(h, w, r, g, b):
    """An RGGB read-out whose R sites hold `r`, both G sites `g`, B sites `b`."""
    m = np.zeros((h, w), np.uint16)
    m[0::2, 0::2] = r
    m[0::2, 1::2] = g
    m[1::2, 0::2] = g
    m[1::2, 1::2] = b
    return m


def write_triplet(folder, h=64, w=96, white=4095):
    """Three DNGs simulating a trichrome shoot. Each frame is lit by one colour,
    so its OWN channel is bright and the other two sit at a low floor — a merge
    that took the wrong channel, or leaked between channels, shows up plainly.

    Returns (paths, expected_rgb) where expected_rgb is the 16-bit value each
    output channel must carry."""
    levels = {"frame1.dng": (2000, 100, 100),      # red light
              "frame2.dng": (100, 3000, 100),      # green light
              "frame3.dng": (100, 100, 1000)}      # blue light
    paths = []
    for name, (r, g, b) in levels.items():
        p = str(folder / name) if hasattr(folder, "__truediv__") else \
            f"{folder}/{name}"
        write_cfa_dng(p, rggb_mosaic(h, w, r, g, b), white=white)
        paths.append(p)
    scale = 65535.0 / white
    expected = (int(2000 * scale), int(3000 * scale), int(1000 * scale))
    return paths, expected
