"""
Synthetic camera-metadata sources for the EXIF carry-over tests.

No camera and no test assets: these build, byte by byte, the three containers a
supported RAW can keep its EXIF in — a plain TIFF (Sony, Nikon, Canon CR2,
Olympus, Panasonic, Pentax, Samsung, Hasselblad), Canon's CR3 boxes, and
Fujifilm's RAF-wrapped JPEG — all describing the SAME imaginary frame, so a test
can assert that every container yields the same answer.

The IFD writer here is deliberately its own implementation, sharing nothing with
trichrome.exif: what the tests check is that the module can read a directory it
did not write, and that the file it produces is readable by something other than
itself.
"""
import struct

# The one exposure every fixture in this module describes.
MAKE = "SONY"
MODEL = "ILCE-7RM4"
DATETIME = "2026:05:12 14:33:07"
ARTIST = "Paul Glover"
COPYRIGHT = "(c) 2026 Paul Glover"
ORIENTATION = 6                                  # rotate 90 CW
EXPOSURE_TIME = (1, 125)
F_NUMBER = (80, 10)
ISO = 100
FOCAL_LENGTH = (55, 1)
LENS_MODEL = "FE 55mm F1.8 ZA"
LENS_SPEC = ((55, 1), (55, 1), (18, 10), (18, 10))
BODY_SERIAL = "00123456"
GPS_LATITUDE = ((51, 1), (30, 1), (2664, 100))
# Vendor-private, and full of offsets into the file it was written in — the one
# block that must NOT be carried into a file of a different shape.
MAKER_NOTE = b"SONY DSC \x00\x00\x00\x2a\x00\x00\x10\x00private"

_BYTE, _ASCII, _SHORT, _LONG, _RATIONAL, _UNDEFINED = 1, 2, 3, 4, 5, 7


def _ascii(text):
    return (_ASCII, text.encode() + b"\x00")


def _short(order, *values):
    return (_SHORT, b"".join(struct.pack(order + "H", v) for v in values))


def _long(order, *values):
    return (_LONG, b"".join(struct.pack(order + "I", v) for v in values))


def _rational(order, *pairs):
    return (_RATIONAL, b"".join(struct.pack(order + "II", n, d)
                                for n, d in pairs))


def _count(typ, data):
    return len(data) // {_BYTE: 1, _ASCII: 1, _SHORT: 2, _LONG: 4,
                         _RATIONAL: 8, _UNDEFINED: 1}[typ]


def _ifd(order, entries, base, next_ifd=0):
    """`entries` as one IFD placed at `base`: the tag-ordered directory followed
    by the values too long to fit in it."""
    entries = sorted(entries, key=lambda e: e[0])
    dir_size = 2 + 12 * len(entries) + 4
    body = bytearray()
    out = bytearray(struct.pack(order + "H", len(entries)))
    for tag, (typ, data) in entries:
        if len(data) <= 4:
            field = data.ljust(4, b"\x00")
        else:
            field = struct.pack(order + "I", base + dir_size + len(body))
            body += data + (b"\x00" if len(data) % 2 else b"")
        out += struct.pack(order + "HHI", tag, typ, _count(typ, data)) + field
    out += struct.pack(order + "I", next_ifd)
    return bytes(out + body)


def ifd0_entries(order):
    """The identifying tags a camera puts in IFD0."""
    return [(271, _ascii(MAKE)), (272, _ascii(MODEL)),
            (274, _short(order, ORIENTATION)), (306, _ascii(DATETIME)),
            (315, _ascii(ARTIST)), (33432, _ascii(COPYRIGHT))]


def exif_entries(order, maker_note=True):
    """The EXIF IFD: the exposure, the lens, and a maker note that must not
    travel."""
    entries = [
        (33434, _rational(order, EXPOSURE_TIME)),       # ExposureTime
        (33437, _rational(order, F_NUMBER)),            # FNumber
        (34855, _short(order, ISO)),                    # ISO
        (36864, (_UNDEFINED, b"0232")),                 # ExifVersion
        (36867, _ascii(DATETIME)),                      # DateTimeOriginal
        (37386, _rational(order, FOCAL_LENGTH)),        # FocalLength
        (40962, _long(order, 9999)),                    # PixelXDimension
        (40963, _long(order, 8888)),                    # PixelYDimension
        (40965, _long(order, 8)),                       # InteroperabilityIFD
        (42033, _ascii(BODY_SERIAL)),                   # BodySerialNumber
        (42034, _rational(order, *LENS_SPEC)),          # LensSpecification
        (42036, _ascii(LENS_MODEL)),                    # LensModel
    ]
    if maker_note:
        entries.append((37500, (_UNDEFINED, MAKER_NOTE)))
    return entries


def gps_entries(order):
    return [(0, (_BYTE, bytes((2, 3, 0, 0)))),              # GPSVersionID
            (1, _ascii("N")),                           # GPSLatitudeRef
            (2, _rational(order, *GPS_LATITUDE))]       # GPSLatitude


def exif_tiff(order="<", maker_note=True, ifd0=None, exif=None, gps=None):
    """A complete standalone TIFF — header, IFD0, EXIF IFD, GPS IFD — which is
    what every one of the three containers holds, however it wraps it.

    Pass `exif=[]`/`gps=[]` for a file that names no such directory, as some
    scanner and machine-vision "raws" genuinely do not."""
    ifd0 = ifd0_entries(order) if ifd0 is None else ifd0
    exif = exif_entries(order, maker_note) if exif is None else exif
    gps = gps_entries(order) if gps is None else gps

    def directory(exif_off, gps_off):
        pointers = ([(34665, _long(order, exif_off))] if exif else []) + \
                   ([(34853, _long(order, gps_off))] if gps else [])
        return _ifd(order, ifd0 + pointers, 8)

    exif_off = 8 + len(directory(0, 0))     # pointers are inline: size is fixed
    exif_block = _ifd(order, exif, exif_off)
    gps_off = exif_off + len(exif_block)
    header = (b"II\x2a\x00" if order == "<" else b"MM\x00\x2a")
    return (header + struct.pack(order + "I", 8)
            + directory(exif_off, gps_off) + exif_block
            + _ifd(order, gps, gps_off))


def write_tiff_source(path, order="<", **kwargs):
    """A TIFF-structured raw (`.arw`, `.nef`, `.cr2`, …) carrying the fixture's
    metadata. `order='>'` writes it big-endian, as Nikon does."""
    with open(str(path), "wb") as fh:
        fh.write(exif_tiff(order, **kwargs))
    return str(path)


def _box(kind, payload):
    return struct.pack(">I", len(payload) + 8) + kind + payload


def write_cr3_source(path, order="<"):
    """A CR3: ISO-BMFF, with the EXIF in CMT1/CMT2/CMT4 inside a Canon `uuid`
    box in `moov`. Each CMT box is a complete TIFF of its own, so the fixture's
    one TIFF is split into three here — which is exactly what Canon does."""
    canon_uuid = bytes((0x85, 0xc0, 0xb6, 0x87, 0x82, 0x0f, 0x11, 0xe0,
                        0x81, 0x11, 0xf4, 0xce, 0x46, 0x2b, 0x6a, 0x48))
    header = (b"II\x2a\x00" if order == "<" else b"MM\x00\x2a") + \
        struct.pack(order + "I", 8)

    def tiff(entries):
        return header + _ifd(order, entries, 8)

    uuid_box = _box(b"uuid", canon_uuid
                    + _box(b"CMT1", tiff(ifd0_entries(order)))
                    + _box(b"CMT2", tiff(exif_entries(order)))
                    + _box(b"CMT4", tiff(gps_entries(order))))
    with open(str(path), "wb") as fh:
        fh.write(_box(b"ftyp", b"crx isom") + _box(b"moov", uuid_box))
    return str(path)


def write_raf_source(path, order="<"):
    """A RAF: Fujifilm's container, whose header directory points at a full-size
    JPEG whose APP1 segment holds the EXIF."""
    tiff = exif_tiff(order)
    app1 = b"Exif\x00\x00" + tiff
    jpeg = (b"\xff\xd8" + b"\xff\xe1" + struct.pack(">H", len(app1) + 2)
            + app1 + b"\xff\xda\x00\x08bits" + b"\xff\xd9")
    head = bytearray(b"FUJIFILMCCD-RAW ")
    head += b"0201" + b"FF129502" + b"X-T5".ljust(32, b"\x00")
    head += struct.pack(">I", 0)                 # directory version
    head += b"\x00" * 20
    head += struct.pack(">II", 92, len(jpeg))    # the JPEG's offset and length
    assert len(head) == 92, len(head)
    with open(str(path), "wb") as fh:
        fh.write(bytes(head) + jpeg)
    return str(path)
