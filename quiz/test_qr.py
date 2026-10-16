import unittest

import qr

try:  # Optional reference implementation; not needed to run or deploy the quiz.
    import qrcode
    from qrcode.util import MODE_8BIT_BYTE, QRData
except ImportError:
    qrcode = None

SAMPLES = [
    "https://a.io",
    "https://a.io/join",
    "https://tokenwars-quiz.example.com/join",
    "https://tokenwars-quiz.wonderfulsea-1a2b3c4d.spaincentral.azurecontainerapps.io/join",
    "http://192.168.1.50:8080/join",
] + ["https://example.com/" + "x" * n for n in (0, 20, 45, 70, 100, 130, 150, 180, 193)]


class QrTests(unittest.TestCase):
    def test_structure_and_capacity(self):
        for text, size in (("https://a.io", 21), ("x" * 213, 57)):
            modules = qr.matrix(text)
            self.assertEqual(len(modules), size)
            self.assertTrue(all(len(row) == size for row in modules))
            for top, left in ((0, 0), (0, size - 7), (size - 7, 0)):
                self.assertTrue(all(modules[top][left + i] for i in range(7)))
                self.assertFalse(modules[top + 1][left + 1])
                self.assertTrue(modules[top + 3][left + 3])
        with self.assertRaises(ValueError):
            qr.matrix("x" * 214)

    def test_svg_has_quiet_zone_and_path(self):
        svg = qr.svg("https://a.io")
        self.assertTrue(svg.startswith('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 29 29"'))
        self.assertIn('<rect width="29" height="29" fill="#fff"/>', svg)
        self.assertIn('M4 4h7v1h-7z', svg)

    @unittest.skipIf(qrcode is None, "install qrcode to compare with the reference encoder")
    def test_matches_reference_encoder_for_every_mask(self):
        for text in SAMPLES:
            data = text.encode()
            for mask in range(8):
                reference = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M,
                                          border=0, mask_pattern=mask)
                reference.add_data(QRData(data, mode=MODE_8BIT_BYTE))
                reference.make(fit=True)
                with self.subTest(length=len(data), version=reference.version, mask=mask):
                    self.assertEqual(qr.matrix(text, mask), [[bool(m) for m in row] for row in reference.modules])

    @unittest.skipIf(qrcode is None, "install qrcode to compare with the reference encoder")
    def test_selected_mask_is_a_reference_encoding_with_lowest_spec_penalty(self):
        # qrcode scores masks before adding format bits; this encoder scores the full symbol.
        for text in SAMPLES:
            references = []
            for mask in range(8):
                reference = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M,
                                          border=0, mask_pattern=mask)
                reference.add_data(QRData(text.encode(), mode=MODE_8BIT_BYTE))
                reference.make(fit=True)
                references.append([[bool(m) for m in row] for row in reference.modules])
            chosen = qr.matrix(text)
            with self.subTest(text=text):
                self.assertIn(chosen, references)
                self.assertEqual(qr._penalty(chosen), min(map(qr._penalty, references)))


if __name__ == "__main__":
    unittest.main()
