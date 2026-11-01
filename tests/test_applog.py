import logging
import os
import tempfile
import unittest

import applog


class AppLogTests(unittest.TestCase):
    def test_setup_writes_to_file_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "sub", "app.log")
            applog._configured = False
            logger = applog.setup(path)
            try:
                self.assertIs(applog.setup(path), logger)
                applog.get("t").info("hello world")
                for handler in logger.handlers:
                    handler.flush()
                with open(path, encoding="utf-8") as f:
                    self.assertIn("hello world", f.read())
            finally:
                for handler in list(logger.handlers):
                    handler.close()
                    logger.removeHandler(handler)
                logger.addHandler(logging.NullHandler())  # restore import-time state
                applog._configured = False


if __name__ == "__main__":
    unittest.main()
