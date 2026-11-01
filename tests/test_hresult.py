import unittest

import comtypes

import hresult


class HresultTests(unittest.TestCase):
    def test_signed_value_from_comerror_is_translated(self):
        exc = comtypes.COMError(-2004287484, "Unknown", (None, None, None, 0, None))
        self.assertEqual(hresult.code_of(exc), 0x88890004)
        self.assertIn("unplugged", hresult.describe(exc))
        self.assertIn("0x88890004", hresult.describe(exc))

    def test_in_use(self):
        exc = comtypes.COMError(0x8889000A - (1 << 32), "x", (None, None, None, 0, None))
        self.assertIn("another app", hresult.describe(exc))

    def test_unknown_code_falls_back_to_hex(self):
        exc = comtypes.COMError(0x12345678, "x", (None, None, None, 0, None))
        self.assertEqual(hresult.describe(exc), "error 0x12345678")

    def test_oserror_with_winerror(self):
        exc = OSError(0, "x", None, 0x80070490 - (1 << 32))  # winerror is signed
        self.assertEqual(hresult.code_of(exc), 0x80070490)
        self.assertIn("not found", hresult.describe(exc))

    def test_plain_exception_uses_its_text(self):
        self.assertEqual(hresult.describe(ValueError("nope")), "nope")

    def test_exception_without_text_uses_class_name(self):
        self.assertEqual(hresult.describe(RuntimeError()), "RuntimeError")


if __name__ == "__main__":
    unittest.main()
