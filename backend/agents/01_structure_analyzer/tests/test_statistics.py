from django.test import SimpleTestCase
from .support import module


class StatisticsTests(SimpleTestCase):
    def test_nested_tree_depth_limit_and_stability(self):
        calc = module("components.statistics").calculate_statistics
        records = [{"path":"a/b/x.py", "size_bytes":4}, {"path":"root.txt", "size_bytes":2}]
        langs = {"a/b/x.py":("Python", "extension", "high"), "root.txt":("Unknown", "none", "unknown")}
        one = calc(records, langs, max_directory_depth=4)
        self.assertEqual((one[1], one[2]), (2, 2))
        self.assertEqual(one[0], calc(records, langs, max_directory_depth=4)[0])
        self.assertEqual(calc(records, langs, max_directory_depth=1)[-2], ("a/b/x.py",))
