"""Minimal, dependency-free QR Code encoder (ISO/IEC 18004).

Byte mode, error-correction level M, versions 1-10 (up to 213 bytes), which is
plenty for the quiz join URL. Produces an SVG suitable for projection.
"""

MAX_VERSION = 10
# Per version (index 1-10), level M: EC codewords per block and number of blocks.
_ECC_PER_BLOCK = (None, 10, 16, 26, 18, 24, 16, 18, 22, 22, 26)
_BLOCKS = (None, 1, 1, 1, 2, 2, 4, 4, 4, 5, 5)
_FORMAT_LEVEL_M = 0

_EXP = [0] * 512
_LOG = [0] * 256
_value = 1
for _i in range(255):
    _EXP[_i] = _value
    _LOG[_value] = _i
    _value <<= 1
    if _value & 0x100:
        _value ^= 0x11D
for _i in range(255, 512):
    _EXP[_i] = _EXP[_i - 255]


def _gf_mul(a, b):
    return 0 if a == 0 or b == 0 else _EXP[_LOG[a] + _LOG[b]]


def _rs_remainder(data, degree):
    generator = [1]
    for i in range(degree):
        generator = [g ^ _gf_mul(c, _EXP[i]) for g, c in zip(generator + [0], [0] + generator)]
    # generator now has degree+1 coefficients, highest power first.
    remainder = [0] * degree
    for byte in data:
        factor = byte ^ remainder.pop(0)
        remainder.append(0)
        for j in range(degree):
            remainder[j] ^= _gf_mul(generator[j + 1], factor)
    return remainder


def _raw_codewords(version):
    modules = (16 * version + 128) * version + 64
    if version >= 2:
        count = version // 7 + 2
        modules -= (25 * count - 10) * count - 55
        if version >= 7:
            modules -= 36
    return modules // 8


def _data_capacity(version):
    return _raw_codewords(version) - _ECC_PER_BLOCK[version] * _BLOCKS[version]


def _alignment_positions(version):
    if version == 1:
        return []
    count = version // 7 + 2
    size = version * 4 + 17
    step = (version * 8 + count * 3 + 5) // (count * 4 - 4) * 2
    return [6] + sorted(size - 7 - i * step for i in range(count - 1))


def _codewords(data, version):
    count_bits = 8 if version < 10 else 16
    bits = [0, 1, 0, 0] + [(len(data) >> i) & 1 for i in reversed(range(count_bits))]
    for byte in data:
        bits += [(byte >> i) & 1 for i in reversed(range(8))]
    capacity = _data_capacity(version) * 8
    bits += [0] * min(4, capacity - len(bits))
    bits += [0] * (-len(bits) % 8)
    words = [int("".join(map(str, bits[i:i + 8])), 2) for i in range(0, len(bits), 8)]
    pad = 0xEC
    while len(words) < capacity // 8:
        words.append(pad)
        pad ^= 0xEC ^ 0x11

    blocks_count, ecc = _BLOCKS[version], _ECC_PER_BLOCK[version]
    raw = _raw_codewords(version)
    short_blocks = blocks_count - raw % blocks_count
    short_data = raw // blocks_count - ecc
    blocks, offset = [], 0
    for index in range(blocks_count):
        length = short_data + (index >= short_blocks)
        blocks.append(words[offset:offset + length])
        offset += length
    result = []
    for i in range(short_data + 1):
        result += [block[i] for block in blocks if i < len(block)]
    ecc_blocks = [_rs_remainder(block, ecc) for block in blocks]
    for i in range(ecc):
        result += [block[i] for block in ecc_blocks]
    return result


def _mask(mask, row, col):
    return (
        (row + col) % 2 == 0,
        row % 2 == 0,
        col % 3 == 0,
        (row + col) % 3 == 0,
        (row // 2 + col // 3) % 2 == 0,
        row * col % 2 + row * col % 3 == 0,
        (row * col % 2 + row * col % 3) % 2 == 0,
        ((row + col) % 2 + row * col % 3) % 2 == 0,
    )[mask]


def _build(version, codewords, mask):
    size = version * 4 + 17
    modules = [[False] * size for _ in range(size)]
    reserved = [[False] * size for _ in range(size)]

    def put(row, col, dark):
        modules[row][col] = dark
        reserved[row][col] = True

    for i in range(size):
        put(6, i, i % 2 == 0)
        put(i, 6, i % 2 == 0)
    for top, left in ((3, 3), (3, size - 4), (size - 4, 3)):
        for dr in range(-4, 5):
            for dc in range(-4, 5):
                if 0 <= top + dr < size and 0 <= left + dc < size:
                    put(top + dr, left + dc, max(abs(dr), abs(dc)) not in (2, 4))
    positions = _alignment_positions(version)
    last = len(positions) - 1
    for a, row in enumerate(positions):
        for b, col in enumerate(positions):
            if (a, b) in ((0, 0), (0, last), (last, 0)):
                continue
            for dr in range(-2, 3):
                for dc in range(-2, 3):
                    put(row + dr, col + dc, max(abs(dr), abs(dc)) != 1)

    data = (_FORMAT_LEVEL_M << 3) | mask
    remainder = data
    for _ in range(10):
        remainder = (remainder << 1) ^ ((remainder >> 9) * 0x537)
    fmt = ((data << 10) | remainder) ^ 0x5412
    bit = [(fmt >> i) & 1 == 1 for i in range(15)]
    for i in range(6):
        put(i, 8, bit[i])
    put(7, 8, bit[6])
    put(8, 8, bit[7])
    put(8, 7, bit[8])
    for i in range(9, 15):
        put(8, 14 - i, bit[i])
    for i in range(8):
        put(8, size - 1 - i, bit[i])
    for i in range(8, 15):
        put(size - 15 + i, 8, bit[i])
    put(size - 8, 8, True)

    if version >= 7:
        remainder = version
        for _ in range(12):
            remainder = (remainder << 1) ^ ((remainder >> 11) * 0x1F25)
        info = (version << 12) | remainder
        for i in range(18):
            dark = (info >> i) & 1 == 1
            a, b = size - 11 + i % 3, i // 3
            put(b, a, dark)
            put(a, b, dark)

    bits = [(word >> i) & 1 == 1 for word in codewords for i in reversed(range(8))]
    index = 0
    right = size - 1
    while right >= 1:
        if right == 6:
            right = 5
        upward = ((right + 1) & 2) == 0
        for vertical in range(size):
            row = size - 1 - vertical if upward else vertical
            for col in (right, right - 1):
                if reserved[row][col]:
                    continue
                dark = bits[index] if index < len(bits) else False
                index += 1
                modules[row][col] = dark ^ _mask(mask, row, col)
        right -= 2
    return modules


def _penalty(modules):
    size = len(modules)
    lines = modules + [list(column) for column in zip(*modules)]
    score = 0
    for line in lines:
        run = 1
        for i in range(1, size + 1):
            if i < size and line[i] == line[i - 1]:
                run += 1
                continue
            if run >= 5:
                score += run - 2
            run = 1
        for i in range(size - 10):
            window = line[i:i + 11]
            if window in (_FINDER_A, _FINDER_B):
                score += 40
    for row in range(size - 1):
        for col in range(size - 1):
            if modules[row][col] == modules[row][col + 1] == modules[row + 1][col] == modules[row + 1][col + 1]:
                score += 3
    dark = sum(map(sum, modules))
    score += int(abs(dark * 100 / size ** 2 - 50) / 5) * 10
    return score


_FINDER_A = [c == "1" for c in "10111010000"]
_FINDER_B = [c == "1" for c in "00001011101"]


def matrix(text, mask=None):
    """Return the QR module matrix (list of rows of booleans; True = dark)."""
    data = text.encode("utf-8")
    for version in range(1, MAX_VERSION + 1):
        count_bits = 8 if version < 10 else 16
        if 4 + count_bits + 8 * len(data) <= _data_capacity(version) * 8:
            break
    else:
        raise ValueError(f"Text is too long for a QR code (max {_data_capacity(MAX_VERSION) - 3} bytes).")
    codewords = _codewords(data, version)
    if mask is not None:
        return _build(version, codewords, mask)
    candidates = [_build(version, codewords, m) for m in range(8)]
    return min(candidates, key=_penalty)


def svg(text, border=4):
    """Return a standalone SVG QR code for text, with a white quiet zone."""
    modules = matrix(text)
    size = len(modules) + 2 * border
    path = []
    for row, line in enumerate(modules):
        col = 0
        while col < len(line):
            if not line[col]:
                col += 1
                continue
            start = col
            while col < len(line) and line[col]:
                col += 1
            path.append(f"M{start + border} {row + border}h{col - start}v1h-{col - start}z")
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {size} {size}" '
        f'shape-rendering="crispEdges"><rect width="{size}" height="{size}" fill="#fff"/>'
        f'<path fill="#000" d="{"".join(path)}"/></svg>'
    )
