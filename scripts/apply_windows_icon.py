from __future__ import annotations

import argparse
import ctypes
import struct
from pathlib import Path
from typing import NamedTuple


RT_ICON = 3
RT_GROUP_ICON = 14
LANG_EN_US = 0x0409


class IconEntry(NamedTuple):
    width: int
    height: int
    color_count: int
    reserved: int
    planes: int
    bit_count: int
    bytes_in_res: int
    image_offset: int
    image: bytes


def make_int_resource(value: int) -> ctypes.c_void_p:
    return ctypes.c_void_p(value)


def parse_ico(path: Path) -> list[IconEntry]:
    data = path.read_bytes()
    reserved, ico_type, count = struct.unpack_from("<HHH", data, 0)
    if reserved != 0 or ico_type != 1 or count <= 0:
        raise ValueError(f"not a valid .ico file: {path}")
    entries: list[IconEntry] = []
    offset = 6
    for _ in range(count):
        width, height, color_count, entry_reserved, planes, bit_count, bytes_in_res, image_offset = struct.unpack_from(
            "<BBBBHHII", data, offset
        )
        offset += 16
        image = data[image_offset : image_offset + bytes_in_res]
        entries.append(
            IconEntry(
                width,
                height,
                color_count,
                entry_reserved,
                planes,
                bit_count,
                bytes_in_res,
                image_offset,
                image,
            )
        )
    return entries


def group_icon_bytes(entries: list[IconEntry], first_id: int = 1) -> bytes:
    out = bytearray(struct.pack("<HHH", 0, 1, len(entries)))
    for index, entry in enumerate(entries):
        out += struct.pack(
            "<BBBBHHIH",
            entry.width,
            entry.height,
            entry.color_count,
            entry.reserved,
            entry.planes,
            entry.bit_count,
            entry.bytes_in_res,
            first_id + index,
        )
    return bytes(out)


def check_ok(ok: int, action: str) -> None:
    if ok:
        return
    err = ctypes.get_last_error()
    raise ctypes.WinError(err, action)


def apply_icon(exe: Path, ico: Path, group_id: int = 1) -> None:
    entries = parse_ico(ico)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    begin = kernel32.BeginUpdateResourceW
    begin.argtypes = [ctypes.c_wchar_p, ctypes.c_bool]
    begin.restype = ctypes.c_void_p
    update = kernel32.UpdateResourceW
    update.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ushort, ctypes.c_void_p, ctypes.c_uint32]
    update.restype = ctypes.c_bool
    end = kernel32.EndUpdateResourceW
    end.argtypes = [ctypes.c_void_p, ctypes.c_bool]
    end.restype = ctypes.c_bool

    handle = begin(str(exe), False)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error(), f"BeginUpdateResourceW failed: {exe}")
    discard = True
    try:
        buffers: list[ctypes.Array[ctypes.c_char]] = []
        for index, entry in enumerate(entries):
            resource_id = 1 + index
            buf = ctypes.create_string_buffer(entry.image)
            buffers.append(buf)
            ok = update(
                handle,
                make_int_resource(RT_ICON),
                make_int_resource(resource_id),
                LANG_EN_US,
                ctypes.cast(buf, ctypes.c_void_p),
                len(entry.image),
            )
            check_ok(ok, f"UpdateResourceW RT_ICON {resource_id}")

        group = group_icon_bytes(entries, 1)
        group_buf = ctypes.create_string_buffer(group)
        buffers.append(group_buf)
        ok = update(
            handle,
            make_int_resource(RT_GROUP_ICON),
            make_int_resource(group_id),
            LANG_EN_US,
            ctypes.cast(group_buf, ctypes.c_void_p),
            len(group),
        )
        check_ok(ok, "UpdateResourceW RT_GROUP_ICON")
        discard = False
    finally:
        ok = end(handle, discard)
        check_ok(ok, "EndUpdateResourceW")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--exe", required=True, type=Path)
    parser.add_argument("--ico", required=True, type=Path)
    args = parser.parse_args()
    apply_icon(args.exe.resolve(), args.ico.resolve())
    print(f"applied icon {args.ico} -> {args.exe}")


if __name__ == "__main__":
    main()
