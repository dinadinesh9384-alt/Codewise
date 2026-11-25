from django.test import SimpleTestCase

from .support import module


class FileClassifierTests(SimpleTestCase):
    def test_common_language_extensions(self):
        classify = module("components.file_classifier").classify_file
        cases = {"a.py":"Python", "a.js":"JavaScript", "a.ts":"TypeScript", "A.java":"Java", "a.c":"C",
            "a.cpp":"C++", "a.cs":"C#", "a.go":"Go", "a.rs":"Rust", "a.php":"PHP", "a.rb":"Ruby",
            "a.swift":"Swift", "a.kt":"Kotlin", "a.dart":"Dart", "a.html":"HTML", "a.css":"CSS",
            "a.scss":"SCSS", "a.sql":"SQL", "a.sh":"Shell", "a.ps1":"PowerShell", "a.r":"R",
            "a.scala":"Scala", "a.lua":"Lua", "a.vue":"Vue", "a.svelte":"Svelte"}
        for path, language in cases.items(): self.assertEqual(classify(path)[0], language)

    def test_special_and_unknown_files_remain_explicit(self):
        classify = module("components.file_classifier").classify_file
        self.assertEqual(classify("Dockerfile")[0], "Dockerfile")
        self.assertEqual(classify("Makefile")[0], "Makefile")
        self.assertEqual(classify("weird.bin"), ("Unknown", "none", "unknown"))
        self.assertEqual(classify("include/header.h")[0], "Unknown")
