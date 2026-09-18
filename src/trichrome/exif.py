"""
Camera metadata carried from a triplet's first source RAW into the merged DNG.

Why the file needs it
---------------------
A merged file is the only thing left after `--delete-originals`, and a raw
converter is not just a viewer: Lightroom, Capture One and darktable sort,
filter and group by capture time, camera body and lens, and a file with none of
that lands outside every collection it belongs to. The pixels are the merge, but
"shot on 12 May at f/8 with the 55 mm" is information that exists ONLY in the
source RAWs, and deleting them is what destroys it.

DNG has somewhere to put it — an EXIF IFD, which every converter reads — so the
merged file carries it, and stays a photograph rather than becoming an anonymous
grid of pixels.

Which frame it comes from
-------------------------
The FIRST frame of the triplet, whose name the merged file already takes. The
three frames of a trichrome shot differ only in which light was on: same body,
same lens, same aperture, seconds apart. Picking one is therefore not a
compromise between three answers, it is the one answer written three times —
and picking the first keeps the file's name, its `OriginalRawFileName` and its
capture time all pointing at the same frame.

What is copied
--------------
The source's whole EXIF IFD, verbatim, tag for tag — exposure, aperture, ISO,
metering, focal length, lens, the date and time, and everything else the body
recorded — plus its GPS IFD, and the identifying tags from its IFD0 (`Make`,
`Model`, `DateTime`, `Artist`, `Copyright`, `Orientation`).

"The source's EXIF" is not always one directory. A raw may carry a preview JPEG
with EXIF of its own, and some bodies put MORE there than in the raw: a
Panasonic DC-G9's RW2 has a 16-tag ExifIFD with no ISO in it at all, while the
40-tag one — ISO, sensitivity type, 35 mm-equivalent focal length, scene tags —
is in the preview's APP1. So every EXIF this module can find in the file is
read, and they are unioned: the raw's own directories win every tag they state,
and a preview only fills in what they left out.

Verbatim means the bytes: each tag keeps its own type and count, and is
byte-swapped only when the source's endianness differs from the DNG's (Nikon
writes big-endian NEFs; tifffile writes the DNG in the host's order). Nothing is
parsed into a value and re-encoded, so nothing is lost in a round trip through a
Python type.

The exceptions, and why each one is not "everything":

* **MakerNote** (37500) is dropped. A maker note's internal offsets are measured
  from a base that differs by vendor — the original file's TIFF header, the
  enclosing EXIF block's, a TIFF header of the note's own — so the block is only
  meaningful where it was written; copied into a file of another shape it
  points at whatever now sits at those addresses. Adobe's converter carries
  notes with per-vendor fixups this tool does not attempt.

  The LENS is rescued from it first, because some bodies record the lens
  nowhere else — the DC-G9 above has no `LensModel` in either of its EXIF
  directories, only Panasonic's own tag in the note. For the layouts this
  module knows (see _lens_from_maker_note), the note is read while its base is
  still at hand and the lens is restated in standard EXIF: `LensModel` and
  `LensSerialNumber` for Panasonic, `LensModel` for Canon, `LensSpecification`
  (and so the DNG's `LensInfo`) for Nikon. A standard tag the EXIF already has
  always wins; a placeholder (empty, all zeros, dashes, a zero-aperture lens) is
  refused rather than written; and an unknown layout yields nothing, never a
  guess.
* **The Interoperability IFD pointer** (40965) is dropped, as a pointer whose
  target this module does not relocate.
* **PixelXDimension / PixelYDimension** (40962/40963) are REPLACED with the
  merged image's own dimensions. They describe the image in the file, and a
  photosite merge is half the source's width and height.
* **ColorSpace** (40961) is REPLACED with Uncalibrated. A source says sRGB —
  true of the preview JPEG that EXIF usually describes, false of linear
  camera-native data, which is what Uncalibrated exists to say.
* **ComponentsConfiguration** and **CompressedBitsPerPixel** (37121/37122) are
  dropped. EXIF defines them for compressed data only; arriving from a preview,
  they describe how that JPEG was compressed, and this file is not.
* IFD0's structural tags — `ImageWidth`, `StripOffsets`, `PhotometricInterpret-
  ation`, the source's own DNG colour tags when it is itself a DNG — are not
  copied at all. They describe the source's pixels, and copying them would
  describe the merge wrongly or corrupt it. IFD0 gets the allowlist above and
  nothing else.

Three DNG-native tags are also filled from what the EXIF says, because a
converter looks for them here rather than in the EXIF IFD:
`CameraSerialNumber` (from `BodySerialNumber`), `LensInfo` (from
`LensSpecification`), and `OriginalRawFileName`, which is the first frame's
filename — the merged file's own record of what it was made from, and the one
piece of provenance that would otherwise die with the deleted RAWs.

`UniqueCameraModel` is NOT touched, and that is the point of the split: it stays
`Trichrome 3-way RGB merge` (see dng.py), so a converter still resolves its
colour profile to the embedded matrices and not to a profile for the body in
`Make`/`Model`. The camera tags say what took the frames; `UniqueCameraModel`
says what the file is.

Orientation is copied, so a sideways-mounted body's frames come up the right way
in the converter exactly as its own RAWs would. The merge is unrotated sensor
data (`user_flip=0`), which is precisely what the source's `Orientation` is a
claim about.

XMP is not copied. Unlike EXIF it is not a record of the exposure but a record
of EDITS — crop, white balance, tone, develop settings keyed to the source's
raw pipeline — and those describe a different image than the merge. The
merged file's only XMP is the film ID dng.py writes.

How it gets in
--------------
tifffile writes the DNG but cannot write an EXIF IFD ("Specifically, ExifIFD and
GPSIFD tags are not supported"), so this module appends one to the finished file
and re-points IFD0 at it:

1. the EXIF IFD, the GPS IFD and any oversized IFD0 values are appended at the
   end of the file;
2. a NEW IFD0 — every entry tifffile wrote, copied as its raw 12 bytes so the
   offsets it already contains stay valid, plus the camera tags and the two
   sub-IFD pointers — is appended after them;
3. the 4-byte "first IFD" pointer in the TIFF header is patched to the new IFD0.

The old IFD0 is left where it was, unreferenced: 400-odd dead bytes in a
145 MB file, in exchange for never rewriting the image data. Nothing else in the
file moves, so every offset already written — the thumbnail's strips, the
SubIFD, the colour matrices — remains exactly as tifffile put it down.

Reading the source
------------------
Every supported RAW keeps its EXIF in a TIFF IFD; the three containers differ
only in how you find it.

* **TIFF-structured** (`.arw .nef .cr2 .dng .orf .pef .srw .rw2 .3fr`) — the file
  IS a TIFF. Some use a private version number in place of 42 (Panasonic's 85,
  Olympus's `RO`/`RS`), which is ignored here: only the byte order and the
  offsets matter. The EXIF pointer is looked for in each top-level IFD and each
  SubIFD, because not every vendor puts it in the first one — and then any
  preview JPEG embedded in the file is read too, found by its signature rather
  than by a per-vendor pointer tag (see _embedded_jpeg_exif).
* **CR3** (`.cr3`) is ISO-BMFF, not TIFF. The EXIF survives inside a Canon
  `uuid` box in `moov` as the boxes `CMT1` (the IFD0 tags), `CMT2` (the EXIF
  IFD), `CMT3` (the maker note) and `CMT4` (GPS), each a complete little TIFF of
  its own.
* **RAF** (`.raf`) wraps a full-size JPEG whose `APP1` segment holds the EXIF,
  at the offset the RAF header's directory gives.

Any failure here — an unreadable file, a container with no EXIF in it — raises
ValueError, which the caller turns into a warning. A merge must never be lost
over its metadata; see dng.write_linear_dng.
"""
import mmap
import os
import struct
from dataclasses import dataclass, field
from typing import Dict, List, NamedTuple, Optional, Sequence, Tuple

# TIFF field types, by the size in bytes of ONE component.
_TYPE_SIZE = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 6: 1, 7: 1, 8: 2, 9: 4, 10: 8,
              11: 4, 12: 8, 13: 4}

# The size of the unit a value of each type is byte-swapped in: a RATIONAL is a
# PAIR of 4-byte longs, not an 8-byte quantity, and swapping it as one would
# exchange its numerator and denominator.
_SWAP_UNIT = {3: 2, 8: 2, 4: 4, 9: 4, 11: 4, 13: 4, 5: 4, 10: 4, 12: 8}

_TYPE_ASCII = 2
_TYPE_SHORT = 3
_TYPE_LONG = 4
_TYPE_RATIONAL = 5
# TIFF's own type for a sub-IFD pointer, which a vendor may use in place of LONG.
_TYPE_IFD = 13

# TIFF/EXIF tags this module names.
_TAG_IMAGE_DESCRIPTION = 270
_TAG_MAKE = 271
_TAG_MODEL = 272
_TAG_ORIENTATION = 274
_TAG_DATETIME = 306
_TAG_ARTIST = 315
_TAG_SUB_IFDS = 330
_TAG_COPYRIGHT = 33432
_TAG_EXIF_IFD = 34665
_TAG_GPS_IFD = 34853
_TAG_DATETIME_ORIGINAL = 36867
_TAG_MAKER_NOTE = 37500
_TAG_COMPONENTS_CONFIGURATION = 37121
_TAG_COMPRESSED_BITS_PER_PIXEL = 37122
_TAG_COLOR_SPACE = 40961
_TAG_PIXEL_X_DIMENSION = 40962
_TAG_PIXEL_Y_DIMENSION = 40963
_TAG_INTEROPERABILITY_IFD = 40965
_TAG_BODY_SERIAL_NUMBER = 42033
_TAG_LENS_SPECIFICATION = 42034
_TAG_LENS_MODEL = 42036
_TAG_LENS_SERIAL_NUMBER = 42037

# Where three vendors keep the lens inside a maker note, for bodies that do not
# also state it in the EXIF IFD proper (see _lens_from_maker_note). Each layout
# is a header, where the note's own directory starts, and what its offsets are
# measured from — the part that differs, and the reason a maker note cannot
# simply be read as a plain IFD.
#
# Panasonic: "Panasonic\0\0\0", then a directory addressed from the ENCLOSING
# block's TIFF header. Verified against a DC-G9 RW2, whose full EXIF (and this
# note) sit in the preview JPEG rather than the raw's own ExifIFD.
_PANASONIC_HEADER = b"Panasonic\x00\x00\x00"
_PANASONIC_LENS_TYPE = 0x0051
_PANASONIC_LENS_SERIAL_NUMBER = 0x0052
# Canon: no header at all — the note IS a directory, addressed from the
# enclosing TIFF header (a CR2's file, or a CR3's own CMT3 box).
_CANON_LENS_MODEL = 0x0095
# Nikon, type 3: "Nikon\0\2" and a version, then a complete TIFF of its own at
# +10, every offset measured from THAT header. Nikon records no lens name in the
# note, but its Lens tag is the same four rationals as LensSpecification.
_NIKON_HEADER = b"Nikon\x00\x02"
_NIKON_TIFF_AT = 10
_NIKON_LENS = 0x0084

# DNG tags filled from the EXIF, which a converter looks for in IFD0.
_TAG_CAMERA_SERIAL_NUMBER = 50735
_TAG_LENS_INFO = 50736
_TAG_ORIGINAL_RAW_FILE_NAME = 50827

# The only tags taken from the source's IFD0: what took the picture and when,
# who owns it, and which way up it goes. Everything else there describes the
# SOURCE's pixels (see the module docstring).
_IFD0_COPY = (_TAG_IMAGE_DESCRIPTION, _TAG_MAKE, _TAG_MODEL, _TAG_ORIENTATION,
              _TAG_DATETIME, _TAG_ARTIST, _TAG_COPYRIGHT,
              _TAG_CAMERA_SERIAL_NUMBER, _TAG_LENS_INFO)

# Dropped from the copied EXIF IFD: a block whose internal offsets do not
# survive the move, a pointer to an IFD this module does not relocate, the two
# dimensions that are restated for the merged image, and two tags that EXIF
# defines only for COMPRESSED data — they arrive from a preview JPEG and say
# how that JPEG was compressed, which is no description of an uncompressed DNG.
_EXIF_DROP = frozenset((_TAG_MAKER_NOTE, _TAG_INTEROPERABILITY_IFD,
                        _TAG_EXIF_IFD, _TAG_GPS_IFD,
                        _TAG_PIXEL_X_DIMENSION, _TAG_PIXEL_Y_DIMENSION,
                        _TAG_COMPONENTS_CONFIGURATION,
                        _TAG_COMPRESSED_BITS_PER_PIXEL))

# EXIF's ColorSpace code for "not sRGB, and not describable here": what a raw
# file states, and what this one has to state (see _plan_entries).
_COLOR_SPACE_UNCALIBRATED = 0xFFFF

# Sanity bounds for parsing a source's directories. A real EXIF IFD has a few
# dozen entries and no value anywhere near this size; anything past these is a
# misparse (a "TIFF" offset landing in image data reads as a plausible-looking
# directory), and would otherwise have this module allocating from garbage.
_MAX_IFD_ENTRIES = 512
# How many JPEG signatures in a raw are worth testing for EXIF, and how far past
# one to look for its APP1. A raw carries a preview or two; anything beyond that
# is the image data coincidentally spelling the signature.
_MAX_EMBEDDED_JPEGS = 8
_MAX_APP1_SPAN = 1 << 20
_MAX_VALUE_BYTES = 1 << 20
_MAX_IFDS = 32

# BigTIFF's version number. tifffile writes classic TIFF for anything this tool
# produces, and no camera writes a BigTIFF raw.
_BIGTIFF_VERSION = 43


class Entry(NamedTuple):
    """One IFD entry, kept as the bytes the source held: `data` is the value
    exactly as it was stored, in `SourceMetadata.byteorder`.

    `offset` is where that value sat in the block it was read from, or -1 for an
    entry this module made itself. It matters for exactly one tag: a maker
    note's own directory is found by its position, not by its bytes."""
    tag: int
    type: int
    count: int
    data: bytes
    offset: int = -1


@dataclass
class SourceMetadata:
    """What was read out of one source RAW: its identifying IFD0 tags, its whole
    EXIF IFD, its GPS IFD, and the byte order all three are stored in."""
    ifd0: List[Entry] = field(default_factory=list)
    exif: List[Entry] = field(default_factory=list)
    gps: List[Entry] = field(default_factory=list)
    byteorder: str = "<"
    filename: str = ""

    def __bool__(self) -> bool:
        return bool(self.ifd0 or self.exif or self.gps)


# --------------------------------------------------------------------------- #
# Reading an IFD out of a source
# --------------------------------------------------------------------------- #
class _Tiff:
    """A TIFF structure inside `buf` — the whole file for a raw that is a TIFF,
    or the extracted block for one that merely contains one."""

    def __init__(self, buf: bytes):
        if len(buf) < 8:
            raise ValueError("too short to be a TIFF structure")
        if buf[:2] == b"II":
            self.byteorder = "<"
        elif buf[:2] == b"MM":
            self.byteorder = ">"
        else:
            raise ValueError("no TIFF byte-order marker")
        self.buf = buf
        # The version word is NOT checked against 42: Panasonic (85) and Olympus
        # ('RO'/'RS') put their own there and are otherwise ordinary TIFFs.
        # BigTIFF is the one that would misparse, and is rejected.
        if self.u16(2) == _BIGTIFF_VERSION:
            raise ValueError("BigTIFF sources are not supported")
        self.first_ifd = self.u32(4)

    def u16(self, off: int) -> int:
        return struct.unpack_from(self.byteorder + "H", self.buf, off)[0]

    def u32(self, off: int) -> int:
        return struct.unpack_from(self.byteorder + "I", self.buf, off)[0]

    def ifd(self, offset: int) -> Tuple[List[Entry], int]:
        """`(entries, next_ifd_offset)` for the IFD at `offset`. Entries whose
        type or size is not sane are skipped rather than failing the read: one
        unreadable tag must not cost the file its metadata."""
        if not 8 <= offset < len(self.buf) - 2:
            raise ValueError(f"IFD offset {offset} is outside the file")
        count = self.u16(offset)
        if not 0 < count <= _MAX_IFD_ENTRIES:
            raise ValueError(f"implausible IFD entry count {count}")
        end = offset + 2 + 12 * count
        if end + 4 > len(self.buf):
            raise ValueError("IFD runs past the end of the file")
        entries = []
        for i in range(count):
            p = offset + 2 + 12 * i
            tag, typ, n = self.u16(p), self.u16(p + 2), self.u32(p + 4)
            size = _TYPE_SIZE.get(typ, 0) * n
            if size == 0 or size > _MAX_VALUE_BYTES:
                continue
            if size <= 4:
                voff = p + 8
            else:
                voff = self.u32(p + 8)
                if voff + size > len(self.buf):
                    continue
            data = self.buf[voff:voff + size]
            if len(data) == size:
                entries.append(Entry(tag, typ, n, bytes(data), voff))
        return entries, self.u32(end)

    def directories(self) -> List[List[Entry]]:
        """Every top-level IFD and every SubIFD of one, in the order found —
        which is where the EXIF pointer might be. Vendors disagree: most put it
        in IFD0, some hang the full-resolution data (and its tags) off a SubIFD
        and leave IFD0 as a thumbnail."""
        found: List[List[Entry]] = []
        seen = set()
        pending = [self.first_ifd]
        while pending and len(found) < _MAX_IFDS:
            offset = pending.pop(0)
            if offset in seen or offset == 0:
                continue
            seen.add(offset)
            try:
                entries, nxt = self.ifd(offset)
            except (ValueError, struct.error):
                continue
            found.append(entries)
            pending.append(nxt)
            for e in entries:
                if e.tag == _TAG_SUB_IFDS and e.type in (_TYPE_LONG, _TYPE_IFD):
                    for i in range(e.count):
                        pending.append(struct.unpack_from(
                            self.byteorder + "I", e.data, 4 * i)[0])
        return found


def _pointer(entries: Sequence[Entry], tag: int, byteorder: str) -> Optional[int]:
    """The offset a sub-IFD pointer tag holds, or None when it is absent."""
    for e in entries:
        if (e.tag == tag and len(e.data) >= 4
                and e.type in (_TYPE_LONG, _TYPE_IFD)):
            return struct.unpack_from(byteorder + "I", e.data, 0)[0]
    return None


def _clean_ascii(data: bytes) -> Optional[str]:
    """A maker note's text value as a string fit to write into EXIF, or None.

    Vendors pad with NULs, and write placeholders where there is nothing to
    record — an empty string, a run of zeros for an unrecorded serial, dashes
    for an unidentified adapted lens. None of those is a lens, and a lens tag
    that says "0000000" is worse than no lens tag, so they are refused along
    with anything that is not printable ASCII."""
    text = bytes(data).split(b"\x00", 1)[0].strip()
    if not text or any(b < 0x20 or b > 0x7E for b in text):
        return None
    if not text.strip(b"0- "):
        return None
    return text.decode("ascii")


def _sane_lens_specification(data: bytes, byteorder: str) -> bool:
    """Whether four rationals read as a real lens: focal lengths in order and
    in range, apertures in range, no zero denominators. A manual or adapted
    lens is often recorded as zeros here, which is "unknown", not f/0."""
    if len(data) != 32:
        return False
    vals = struct.unpack(byteorder + "8I", data)
    if any(vals[i] == 0 for i in (1, 3, 5, 7)):
        return False
    focal_min, focal_max, f_wide, f_tele = (vals[i] / vals[i + 1]
                                            for i in (0, 2, 4, 6))
    return (1 <= focal_min <= focal_max <= 5000
            and 0.5 <= f_wide <= 64 and 0.5 <= f_tele <= 64)


def _lens_entries(vendor: Sequence[Entry], vendor_order: str, byteorder: str,
                  model: Optional[int] = None,
                  serial: Optional[int] = None,
                  spec: Optional[int] = None) -> List[Entry]:
    """Standard EXIF lens entries made from a maker note's directory, in
    `byteorder`. Only tags that validate come back; a vendor tag that is
    missing, mistyped or a placeholder simply contributes nothing."""
    found = {e.tag: e for e in vendor}
    out: List[Entry] = []
    for vendor_tag, exif_tag in ((model, _TAG_LENS_MODEL),
                                 (serial, _TAG_LENS_SERIAL_NUMBER)):
        e = found.get(vendor_tag) if vendor_tag is not None else None
        if e is not None and e.type == _TYPE_ASCII:
            text = _clean_ascii(e.data)
            if text:
                out.append(_ascii(exif_tag, text))
    e = found.get(spec) if spec is not None else None
    if (e is not None and e.type == _TYPE_RATIONAL and e.count == 4
            and _sane_lens_specification(e.data, vendor_order)):
        data = e.data if vendor_order == byteorder else _swapped(e.data, e.type)
        out.append(Entry(_TAG_LENS_SPECIFICATION, _TYPE_RATIONAL, 4, data))
    return out


def _make_of(entries: Sequence[Entry]) -> str:
    """The `Make` a directory names, or "" when it names none."""
    for e in entries:
        if e.tag == _TAG_MAKE and e.type == _TYPE_ASCII:
            return bytes(e.data).split(b"\x00", 1)[0].decode("ascii", "replace")
    return ""


def _lens_from_maker_note(tiff: "_Tiff", exif: Sequence[Entry],
                          make: str) -> List[Entry]:
    """The lens, as standard EXIF entries, out of the maker note in `exif` —
    for the bodies that record it nowhere else.

    The maker note itself is still not carried into the DNG (see the module
    docstring): its offsets are measured from a base that differs per vendor,
    and copied into a file of another shape it reads as nonsense. The lens is
    the part worth rescuing from it, so this reads the note HERE, while the
    block its offsets are measured from is still at hand, and restates what it
    finds in the standard tags every converter reads. A layout this does not
    know, or a value that does not validate, yields nothing — never a guess."""
    note = next((e for e in exif if e.tag == _TAG_MAKER_NOTE), None)
    if note is None or note.offset < 0:
        return []
    try:
        if note.data.startswith(_PANASONIC_HEADER):
            vendor = tiff.ifd(note.offset + len(_PANASONIC_HEADER))[0]
            return _lens_entries(vendor, tiff.byteorder, tiff.byteorder,
                                 model=_PANASONIC_LENS_TYPE,
                                 serial=_PANASONIC_LENS_SERIAL_NUMBER)
        if note.data.startswith(_NIKON_HEADER):
            inner = _Tiff(note.data[_NIKON_TIFF_AT:])
            vendor = inner.ifd(inner.first_ifd)[0]
            return _lens_entries(vendor, inner.byteorder, tiff.byteorder,
                                 spec=_NIKON_LENS)
        if make.upper().startswith("CANON"):
            vendor = tiff.ifd(note.offset)[0]
            return _lens_entries(vendor, tiff.byteorder, tiff.byteorder,
                                 model=_CANON_LENS_MODEL)
    except (ValueError, struct.error):
        pass
    return []


def _merged(primary: List[Entry], extra: List[Entry]) -> List[Entry]:
    """`primary`, plus any tag from `extra` it does not already carry. The raw's
    own directories win every tag they state; the rest is what the preview knew
    and the raw did not."""
    have = {e.tag for e in primary}
    return primary + [e for e in extra if e.tag not in have]


def _reordered(meta: SourceMetadata, byteorder: str) -> SourceMetadata:
    """`meta` with every value rewritten in `byteorder`. A raw and the JPEG
    inside it are usually written the same way round, but nothing guarantees
    it."""
    def convert(entries):
        return [Entry(e.tag, e.type, e.count, _swapped(e.data, e.type))
                for e in entries]
    return SourceMetadata(ifd0=convert(meta.ifd0), exif=convert(meta.exif),
                          gps=convert(meta.gps), byteorder=byteorder,
                          filename=meta.filename)


def _from_tiff(buf, filename: str, embedded: bool = True) -> SourceMetadata:
    """Pull the metadata out of a TIFF-structured block.

    `embedded=True` also unions in the EXIF of any JPEG carried inside `buf` —
    which is where Panasonic, and it is not alone, keeps the tags its own
    ExifIFD leaves out. Set False when parsing a block that IS such a JPEG's
    EXIF, so the search does not recurse."""
    tiff = _Tiff(buf)
    dirs = tiff.directories()
    if not dirs:
        raise ValueError("no readable IFD")

    exif: List[Entry] = []
    gps: List[Entry] = []
    for entries in dirs:
        if not exif:
            off = _pointer(entries, _TAG_EXIF_IFD, tiff.byteorder)
            if off:
                try:
                    exif = tiff.ifd(off)[0]
                except (ValueError, struct.error):
                    exif = []
        if not gps:
            off = _pointer(entries, _TAG_GPS_IFD, tiff.byteorder)
            if off:
                try:
                    gps = tiff.ifd(off)[0]
                except (ValueError, struct.error):
                    gps = []

    # The identifying tags come from whichever directory actually names the
    # camera — IFD0 for most raws, but a thumbnail-first layout leaves them in
    # the directory that carries the real image.
    def named(entries):
        return {e.tag for e in entries} & {_TAG_MAKE, _TAG_MODEL, _TAG_DATETIME}
    main = next((d for d in dirs if named(d)), dirs[0])
    ifd0 = [e for e in main if e.tag in _IFD0_COPY]
    # EXIF proper wins: a body that states its lens in LensModel keeps that,
    # and the maker note only fills what the EXIF IFD left out.
    exif = _merged(exif, _lens_from_maker_note(tiff, exif, _make_of(main)))

    if embedded:
        for preview in _embedded_jpeg_exif(buf, filename):
            if preview.byteorder != tiff.byteorder:
                # Both are about to be written in ONE directory, so they have to
                # agree on byte order before they are merged, not after.
                preview = _reordered(preview, tiff.byteorder)
            exif = _merged(exif, preview.exif)
            gps = _merged(gps, preview.gps)
            ifd0 = _merged(ifd0, preview.ifd0)
    return SourceMetadata(ifd0=ifd0, exif=exif, gps=gps,
                          byteorder=tiff.byteorder, filename=filename)


def _boxes(data: bytes, start: int, end: int):
    """Walk ISO-BMFF boxes in `data[start:end]`, yielding (type, payload_start,
    payload_end)."""
    pos = start
    while pos + 8 <= end:
        size = struct.unpack_from(">I", data, pos)[0]
        kind = data[pos + 4:pos + 8]
        body = pos + 8
        if size == 1:                       # 64-bit extended size
            if body + 8 > end:
                return
            size = struct.unpack_from(">Q", data, body)[0]
            body += 8
        elif size == 0:                     # extends to the end of the file
            size = end - pos
        if size < 8 or pos + size > end:
            return
        yield kind, body, pos + size
        pos += size


def _from_cr3(data: bytes, filename: str) -> SourceMetadata:
    """CR3: ISO-BMFF. The EXIF lives in a Canon `uuid` box inside `moov`, as the
    boxes CMT1 (IFD0 tags), CMT2 (the EXIF IFD), CMT3 (the maker note) and CMT4
    (GPS) — each a complete little TIFF in its own right, so each is parsed as
    one and the results are combined."""
    canon_uuid = bytes((0x85, 0xc0, 0xb6, 0x87, 0x82, 0x0f, 0x11, 0xe0,
                        0x81, 0x11, 0xf4, 0xce, 0x46, 0x2b, 0x6a, 0x48))
    blocks: Dict[bytes, bytes] = {}

    def scan(start, end, depth=0):
        if depth > 3:
            return
        for kind, body, stop in _boxes(data, start, end):
            if kind == b"moov":
                scan(body, stop, depth + 1)
            elif kind == b"uuid" and data[body:body + 16] == canon_uuid:
                scan(body + 16, stop, depth + 1)
            elif kind in (b"CMT1", b"CMT2", b"CMT3", b"CMT4"):
                blocks.setdefault(kind, data[body:stop])

    scan(0, len(data))
    if not blocks:
        raise ValueError("no Canon metadata boxes (CMT1-CMT4) in the CR3")

    meta = SourceMetadata(filename=filename)
    if b"CMT1" in blocks:
        ifd0 = _from_tiff(blocks[b"CMT1"], filename)
        meta.ifd0 = ifd0.ifd0
        meta.byteorder = ifd0.byteorder
    for box, attr in ((b"CMT2", "exif"), (b"CMT4", "gps")):
        if box in blocks:
            tiff = _Tiff(blocks[box])
            setattr(meta, attr, tiff.ifd(tiff.first_ifd)[0])
            meta.byteorder = tiff.byteorder
    # CMT3 is the maker note, as a TIFF of its own rather than a tag in CMT2,
    # so its directory is simply that TIFF's first.
    if b"CMT3" in blocks and _make_of(meta.ifd0).upper().startswith("CANON"):
        try:
            note = _Tiff(blocks[b"CMT3"])
            lens = _lens_entries(note.ifd(note.first_ifd)[0], note.byteorder,
                                 meta.byteorder, model=_CANON_LENS_MODEL)
            meta.exif = _merged(meta.exif, lens)
        except (ValueError, struct.error):
            pass
    return meta


def _jpeg_exif_block(data, start: int, end: int) -> Optional[bytes]:
    """The TIFF block inside the APP1 segment of the JPEG at `start`, or None
    when that JPEG carries no EXIF. Walks the marker segments rather than
    trusting APP1 to be first, because it need not be."""
    pos = start + 2                                     # past SOI
    while pos + 4 <= end and data[pos] == 0xFF:
        marker = data[pos + 1]
        if marker in (0xD8, 0xD9) or 0xD0 <= marker <= 0xD7:
            pos += 2
            continue
        seglen = struct.unpack_from(">H", data, pos + 2)[0]
        if marker == 0xE1 and data[pos + 4:pos + 10] == b"Exif\x00\x00":
            return bytes(data[pos + 10:pos + 2 + seglen])
        if marker == 0xDA:                              # image data; no EXIF
            return None
        pos += 2 + seglen
    return None


def _embedded_jpeg_exif(data, filename: str) -> List[SourceMetadata]:
    """The metadata of every EXIF-bearing JPEG embedded in `data`, in the order
    they appear.

    A raw's own directories are not always where its EXIF is. Panasonic writes a
    SPARSE ExifIFD into the RW2 — no ISO, no sensitivity, no scene tags — and the
    full one into the APP1 of the preview JPEG it carries; several other vendors
    keep a preview with equally good EXIF. Rather than learn each vendor's
    preview pointer (Panasonic's JpgFromRaw, a SubIFD's strips, an IFD1
    thumbnail, …), this scans for the JPEG signature itself, which is
    vendor-independent, and then makes every candidate prove it is one: SOI +
    APP1, an `Exif\x00\x00` marker, a parseable TIFF with a readable IFD. Raw
    image data that happens to contain the four signature bytes fails that and
    costs one rejected parse."""
    found: List[SourceMetadata] = []
    at = 0
    while len(found) < _MAX_EMBEDDED_JPEGS:
        at = data.find(b"\xff\xd8\xff\xe1", at)
        if at < 0:
            break
        block = _jpeg_exif_block(data, at, min(len(data), at + _MAX_APP1_SPAN))
        at += 4
        if not block:
            continue
        try:
            found.append(_from_tiff(block, filename, embedded=False))
        except (ValueError, struct.error):
            continue
    return found


def _from_raf(data, filename: str) -> SourceMetadata:
    """RAF: Fujifilm's own container, whose header directory points at a
    full-size JPEG. The EXIF is that JPEG's APP1 segment."""
    if len(data) < 92:
        raise ValueError("RAF header is truncated")
    jpeg_off, jpeg_len = struct.unpack_from(">II", data, 84)
    if jpeg_off <= 0 or jpeg_off + 4 > len(data):
        raise ValueError("RAF names no embedded JPEG")
    end = min(len(data), jpeg_off + (jpeg_len or len(data)))
    block = _jpeg_exif_block(data, jpeg_off, end)
    if block is None:
        raise ValueError("no EXIF segment in the RAF's embedded JPEG")
    return _from_tiff(block, filename)


def read_source_metadata(path: str) -> SourceMetadata:
    """The camera metadata of one source RAW, ready to be copied into a DNG.

    Dispatch is on the file's own magic bytes rather than its extension, so a
    misnamed file is read for what it is. Raises ValueError when the file cannot
    be read or holds no EXIF — the caller turns that into a warning, never into
    a failed merge."""
    name = os.path.basename(path)
    try:
        with open(path, "rb") as fh:
            if os.fstat(fh.fileno()).st_size < 16:
                raise ValueError(f"{name} is too small to hold metadata")
            # Mapped, not read: a raw is tens of megabytes of image data around
            # a few kilobytes of tags, and the tags are reached by offsets a
            # vendor may put anywhere in the file. Mapping hands the parsers the
            # whole file at the cost of only the pages they actually touch.
            with mmap.mmap(fh.fileno(), 0, access=mmap.ACCESS_READ) as data:
                head = data[:16]
                if head[:15] == b"FUJIFILMCCD-RAW":
                    meta = _from_raf(data, name)
                elif head[4:8] == b"ftyp":
                    meta = _from_cr3(data, name)
                else:
                    meta = _from_tiff(data, name)
    except OSError as e:
        raise ValueError(f"cannot read {name}: {e}") from e
    except struct.error as e:
        raise ValueError(f"malformed metadata in {name}: {e}") from e
    if not meta:
        raise ValueError(f"no camera metadata found in {name}")
    return meta


# --------------------------------------------------------------------------- #
# Writing it into a finished DNG
# --------------------------------------------------------------------------- #
def _swapped(data: bytes, typ: int) -> bytes:
    """`data` with each component reversed, for copying a value between files of
    opposite byte order. ASCII, BYTE and UNDEFINED have 1-byte components and
    come back unchanged."""
    unit = _SWAP_UNIT.get(typ, 1)
    if unit == 1 or len(data) % unit:
        return data
    return b"".join(data[i:i + unit][::-1]
                    for i in range(0, len(data), unit))


def _ascii(tag: int, text: str) -> Entry:
    raw = text.encode("utf-8", "replace") + b"\x00"
    return Entry(tag, _TYPE_ASCII, len(raw), raw)


def _long(tag: int, value: int, byteorder: str) -> Entry:
    return Entry(tag, _TYPE_LONG, 1, struct.pack(byteorder + "I", value))


def _serialize_ifd(entries: Sequence[Entry],
                   raw_records: Sequence[Tuple[int, bytes]], base: int,
                   byteorder: str, next_ifd: int = 0) -> bytes:
    """One complete IFD as bytes, to be written AT `base`: the directory, then
    the values too big to sit inside it.

    `entries` are values this module is writing; `raw_records` are 12-byte
    directory entries copied verbatim from an IFD already in the file, whose
    value offsets still point where they always did. Both are interleaved into
    one tag-ascending directory, as TIFF requires."""
    records = list(raw_records)
    count = len(records) + len(entries)
    dir_size = 2 + 12 * count + 4
    overflow = bytearray()
    for e in entries:
        if len(e.data) <= 4:
            field_bytes = e.data + b"\x00" * (4 - len(e.data))
        else:
            field_bytes = struct.pack(byteorder + "I",
                                      base + dir_size + len(overflow))
            overflow += e.data
            if len(overflow) % 2:
                overflow += b"\x00"         # keep every value word-aligned
        records.append((e.tag, struct.pack(byteorder + "HHI", e.tag, e.type,
                                           e.count) + field_bytes))
    records.sort(key=lambda r: r[0])
    out = bytearray(struct.pack(byteorder + "H", count))
    for _tag, record in records:
        out += record
    out += struct.pack(byteorder + "I", next_ifd)
    return bytes(out + overflow)


def _plan_entries(meta: SourceMetadata, byteorder: str,
                  pixel_size: Optional[Tuple[int, int]],
                  present: Sequence[int]
                  ) -> Tuple[List[Entry], List[Entry], List[Entry]]:
    """`(ifd0, exif, gps)` entries to add to a DNG whose IFD0 already holds the
    tags in `present`, with every value put into `byteorder`.

    A tag tifffile already wrote is never added twice — the file's own
    `Software` marker and its colour tags win over anything of the same number
    from the source."""
    def convert(entries, drop=()):
        out: Dict[int, Entry] = {}
        for e in entries:
            if e.tag in drop or e.tag in out:   # a repeat: keep the first
                continue
            data = (e.data if meta.byteorder == byteorder
                    else _swapped(e.data, e.type))
            out[e.tag] = Entry(e.tag, e.type, e.count, data)
        return list(out.values())

    exif = convert(meta.exif, _EXIF_DROP)
    gps = convert(meta.gps, (_TAG_EXIF_IFD, _TAG_GPS_IFD,
                             _TAG_INTEROPERABILITY_IFD))
    ifd0 = convert([e for e in meta.ifd0 if e.tag not in present])

    if exif and pixel_size:
        # Restated, not copied: these describe the image in THIS file, and a
        # photosite merge is half the source's dimensions.
        height, width = pixel_size
        exif.append(_long(_TAG_PIXEL_X_DIMENSION, int(width), byteorder))
        exif.append(_long(_TAG_PIXEL_Y_DIMENSION, int(height), byteorder))
    if exif:
        # Also restated rather than copied. A source's ColorSpace says sRGB —
        # true of the preview JPEG it usually comes from, and false of linear
        # camera-native data, which is exactly what Uncalibrated exists to say.
        exif = [e for e in exif if e.tag != _TAG_COLOR_SPACE]
        exif.append(Entry(_TAG_COLOR_SPACE, _TYPE_SHORT, 1,
                          struct.pack(byteorder + "H",
                                      _COLOR_SPACE_UNCALIBRATED)))

    by_tag = {e.tag: e for e in ifd0}
    source_exif = {e.tag: e for e in exif}

    def add(entry):
        if entry.tag not in by_tag and entry.tag not in present:
            by_tag[entry.tag] = entry

    # DateTime is where a file browser looks for the capture time; a raw always
    # has it, but fall back to the EXIF's DateTimeOriginal if this one did not.
    if _TAG_DATETIME not in by_tag and _TAG_DATETIME_ORIGINAL in source_exif:
        original = source_exif[_TAG_DATETIME_ORIGINAL]
        add(Entry(_TAG_DATETIME, original.type, original.count, original.data))
    # The DNG spellings of two EXIF facts, where a converter goes looking.
    if _TAG_BODY_SERIAL_NUMBER in source_exif:
        serial = source_exif[_TAG_BODY_SERIAL_NUMBER]
        add(Entry(_TAG_CAMERA_SERIAL_NUMBER, serial.type, serial.count,
                  serial.data))
    spec = source_exif.get(_TAG_LENS_SPECIFICATION)
    if spec is not None and spec.type == _TYPE_RATIONAL and spec.count == 4:
        add(Entry(_TAG_LENS_INFO, spec.type, spec.count, spec.data))
    if meta.filename:
        add(_ascii(_TAG_ORIGINAL_RAW_FILE_NAME, meta.filename))
    return list(by_tag.values()), exif, gps


def copy_into_dng(path: str, meta: SourceMetadata,
                  pixel_size: Optional[Tuple[int, int]] = None) -> None:
    """Append `meta` to the DNG at `path` as an EXIF IFD (plus GPS and the
    camera tags in IFD0), and re-point the file at the enlarged IFD0.

    Only the tail of the file is written and four bytes of its header patched;
    the image data, the thumbnail and every offset tifffile wrote are untouched.
    Raises ValueError if the file is not the classic little/big-endian TIFF this
    tool writes, and IOError if the append fails — see write_linear_dng, which
    keeps either from costing the merge."""
    if not meta:
        return
    with open(path, "r+b") as fh:
        head = fh.read(8)
        if len(head) < 8 or head[:2] not in (b"II", b"MM"):
            raise ValueError(f"not a TIFF-structured file: {path}")
        byteorder = "<" if head[:2] == b"II" else ">"
        if struct.unpack(byteorder + "H", head[2:4])[0] == _BIGTIFF_VERSION:
            raise ValueError(f"cannot add EXIF to a BigTIFF: {path}")
        ifd0_offset = struct.unpack(byteorder + "I", head[4:8])[0]

        # IFD0 as tifffile left it. Its entries are taken as raw 12-byte records
        # so that the offsets inside them — strips, SubIFDs, the matrices —
        # continue to point at the bytes they always did.
        fh.seek(ifd0_offset)
        count = struct.unpack(byteorder + "H", fh.read(2))[0]
        directory = fh.read(12 * count)
        tail = fh.read(4)
        if len(directory) != 12 * count or len(tail) != 4:
            raise ValueError(f"truncated IFD0 in {path}")
        next_ifd = struct.unpack(byteorder + "I", tail)[0]
        records = []
        for i in range(count):
            record = directory[12 * i:12 * i + 12]
            tag, typ = struct.unpack_from(byteorder + "HH", record, 0)
            if tag == _TAG_SUB_IFDS and typ == _TYPE_IFD:
                # SubIFDs may be LONG or IFD, and tifffile writes IFD — but
                # tag 330 is ALSO Sony's private A100DataOffset, a LONG, and a
                # reader that dispatches on Make (exiftool does) now sees a
                # Sony file, because Make is about to say so. Stating the
                # offset as the LONG it is costs nothing and leaves nothing to
                # misread. Done here only because this is the one moment the
                # directory is being rewritten anyway.
                record = struct.pack(byteorder + "HH", tag,
                                     _TYPE_LONG) + record[4:]
            records.append((tag, record))

        ifd0, exif, gps = _plan_entries(meta, byteorder, pixel_size,
                                        [tag for tag, _ in records])

        fh.seek(0, os.SEEK_END)
        end = fh.tell()
        pad = end % 2                       # every IFD starts word-aligned
        cursor = end + pad
        parts: List[bytes] = []

        def append(entries) -> int:
            """Place one sub-IFD at the end of what is queued, and return the
            offset it will have once written."""
            nonlocal cursor
            block = _serialize_ifd(entries, (), cursor, byteorder)
            parts.append(block)
            at = cursor
            cursor += len(block)
            return at

        if exif:
            ifd0.append(_long(_TAG_EXIF_IFD, append(exif), byteorder))
        if gps:
            ifd0.append(_long(_TAG_GPS_IFD, append(gps), byteorder))
        # A pointer written now REPLACES any entry of the same tag already in
        # the directory rather than joining it — an IFD holding one tag twice is
        # malformed, and a reader that takes the first would follow the stale
        # pointer. (Only reachable when a DNG is stamped a second time.)
        written = {e.tag for e in ifd0}
        records = [r for r in records if r[0] not in written]
        new_ifd0 = cursor
        parts.append(_serialize_ifd(ifd0, records, cursor, byteorder, next_ifd))

        # The pointer goes in LAST, and only once the directories it names are
        # on disk. Until that four-byte write the file still reads as exactly
        # the DNG it was, with some unreferenced bytes on the end — so an
        # interruption here leaves a valid file either way, never a half-linked
        # one.
        try:
            fh.write(b"\x00" * pad + b"".join(parts))
            fh.flush()
            fh.seek(4)
            fh.write(struct.pack(byteorder + "I", new_ifd0))
        except OSError as e:
            raise IOError(f"failed to add EXIF to {path}: {e}") from e
