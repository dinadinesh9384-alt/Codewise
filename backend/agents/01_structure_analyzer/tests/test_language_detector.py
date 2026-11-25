from django.test import SimpleTestCase
from .support import module


class LanguageDetectorTests(SimpleTestCase):
    def test_extension_and_shebang_signals(self):
        detect = module("components.language_detector").detect_language
        self.assertEqual(detect("src/x.py")[0], "Python")
        self.assertEqual(detect("bin/run", b"#!/usr/bin/env python3\n")[0], "Python")
        self.assertEqual(detect("bin/run", b"#!/bin/bash\n")[0], "Shell")
        self.assertEqual(detect("unknown", b"plain text")[0], "Unknown")
        self.assertEqual(detect("Dockerfile")[0], "Unknown")

    def test_conflicting_shebang_does_not_override_known_extension(self):
        self.assertEqual(module("components.language_detector").detect_language("source.py", b"#!/bin/bash\n")[0], "Python")
