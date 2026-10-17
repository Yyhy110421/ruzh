"""List the DLLs a PE file imports directly (pure Python, no deps).

Used to diagnose 'DLL load failed: the specified module could not be found'
by showing which dependency names Windows is looking for.
"""
import struct
import sys


def rva_to_off(sections, rva):
    for va, vsize, raw_off, raw_size in sections:
        if va <= rva < va + max(vsize, raw_size):
            return raw_off + (rva - va)
    return None


def cstr(data, off):
    end = data.index(b"\x00", off)
    return data[off:end].decode("ascii", "replace")


def imports(path):
    data = open(path, "rb").read()
    if data[:2] != b"MZ":
        raise ValueError("not a PE file")
    pe = struct.unpack_from("<I", data, 0x3C)[0]
    if data[pe:pe + 4] != b"PE\x00\x00":
        raise ValueError("bad PE signature")
    machine, nsec = struct.unpack_from("<HH", data, pe + 4)
    opt_size = struct.unpack_from("<H", data, pe + 20)[0]
    opt = pe + 24
    magic = struct.unpack_from("<H", data, opt)[0]
    dd = opt + (96 if magic == 0x10B else 112)
    imp_rva, imp_size = struct.unpack_from("<II", data, dd + 8)  # dir index 1 = import
    sec_off = opt + opt_size
    sections = []
    for i in range(nsec):
        base = sec_off + i * 40
        vsize, va, raw_size, raw_off = struct.unpack_from("<IIII", data, base + 8)
        sections.append((va, vsize, raw_off, raw_size))

    out = []
    if imp_rva:
        off = rva_to_off(sections, imp_rva)
        if off is None:
            return out
        while True:
            entry = data[off:off + 20]
            if len(entry) < 20 or entry == b"\x00" * 20:
                break
            name_rva = struct.unpack_from("<I", entry, 12)[0]
            if name_rva:
                noff = rva_to_off(sections, name_rva)
                if noff is not None:
                    out.append(cstr(data, noff))
            off += 20
    return out


if __name__ == "__main__":
    for target in sys.argv[1:]:
        print(f"== {target}")
        try:
            for name in imports(target):
                print(f"   {name}")
        except Exception as exc:  # noqa: BLE001
            print(f"   !! {exc}")
