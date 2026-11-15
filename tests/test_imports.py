from pathlib import Path
import os
import subprocess
import sys
import tempfile
import unittest

from run import BuildError, compile_cpp, find_compiler
from simplec import SourceError, transpile, transpile_file


ROOT = Path(__file__).resolve().parents[1]


def write_modules(directory, files):
    for name, source in files.items():
        path = Path(directory) / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")
    return Path(directory) / "main.sc"


class ImportFrontendTests(unittest.TestCase):
    def test_import_errors_report_importing_file_and_location(self):
        with tempfile.TemporaryDirectory() as directory:
            entry = write_modules(directory, {
                "main.sc": "import nested.helper; int main() { return 0; }",
                "nested/helper.sc": "// missing module\nimport missing;",
            })
            with self.assertRaises(SourceError) as result:
                transpile_file(entry)
            message = str(result.exception)
            self.assertIn("helper.sc:2:1", message)
            self.assertIn("cannot import 'missing'", message)
            self.assertIn(str(Path(directory) / "nested/missing.sc"), message)

    def test_errors_in_imported_sources_keep_their_locations(self):
        cases = [
            ("int broken() {\nreturn 0\n}", "helper.sc:3:1", "expected ';'"),
            ("class Broken {\n Missing value;\n}", "helper.sc:2:2", "unknown type"),
            ("/* unfinished", "helper.sc:1:1", "unterminated comment"),
        ]
        for source, location, error in cases:
            with self.subTest(error=error), tempfile.TemporaryDirectory() as directory:
                entry = write_modules(directory, {"main.sc": "import helper;", "helper.sc": source})
                with self.assertRaises(SourceError) as result:
                    transpile_file(entry)
                self.assertIn(location, str(result.exception))
                self.assertIn(error, str(result.exception))

    def test_duplicate_names_report_both_modules(self):
        with tempfile.TemporaryDirectory() as directory:
            entry = write_modules(directory, {
                "main.sc": "import a; import b;",
                "a.sc": "int duplicate() { return 1; }",
                "b.sc": "int duplicate() { return 2; }",
            })
            with self.assertRaises(SourceError) as result:
                transpile_file(entry)
            message = str(result.exception)
            self.assertIn("duplicate declaration 'duplicate'", message)
            self.assertIn("a.sc:1:5", message)
            self.assertIn("b.sc:1:5", message)

    def test_value_cycles_across_modules_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            entry = write_modules(directory, {
                "main.sc": "import a;",
                "a.sc": "import b; class A { B b; }",
                "b.sc": "import a; class B { A a; }",
            })
            with self.assertRaisesRegex(SourceError, "cyclic value fields"):
                transpile_file(entry)

    def test_invalid_import_syntax_is_rejected(self):
        for source in ['import "helper.sc";', "import ../helper;", "import a..b;",
                       "import helper", "int main() { import helper; return 0; }"]:
            with self.subTest(source=source), self.assertRaises(SourceError):
                transpile(source)

    def test_in_memory_imports_require_a_filename(self):
        with self.assertRaisesRegex(SourceError, "require a source filename"):
            transpile("import helper;")

    def test_cli_protects_imported_source_from_output(self):
        with tempfile.TemporaryDirectory() as directory:
            original = "int helper() { return 0; }"
            entry = write_modules(directory, {"main.sc": "import helper;", "helper.sc": original})
            dependency = entry.parent / "helper.sc"
            result = subprocess.run(
                [sys.executable, str(ROOT / "simplec.py"), str(entry), "-o", str(dependency)],
                capture_output=True, text=True, timeout=10,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("must not overwrite a source module", result.stderr)
            self.assertEqual(dependency.read_text(), original)


class ImportCompileTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.compiler = find_compiler()

    def compile(self, entry):
        cpp = entry.with_suffix(".cpp")
        cpp.write_text(transpile_file(entry), encoding="utf-8")
        executable = entry.with_suffix(".exe" if os.name == "nt" else "")
        compile_cpp(cpp, executable, self.compiler)
        return executable

    def test_nested_imports_shared_dependencies_and_class_copies(self):
        with tempfile.TemporaryDirectory(prefix="simple c modules ") as directory:
            entry = write_modules(directory, {
                "main.sc": '''
                    import geometry.wrapper;
                    import geometry.point;
                    import geometry.point;
                    int main() {
                        Wrapper a{Point{3, 4}};
                        Wrapper b = a;
                        b.point.x = 10;
                        print(a.total());
                        print(b.total());
                        print(sum(Point{2, 5}));
                        Point added = a.point + b.point;
                        if (added.x != 13 || added.y != 8) return 1;
                        return 0;
                    }
                ''',
                "geometry/wrapper.sc": '''
                    import point;
                    class Wrapper {
                        Point point;
                        int total() { return sum(point); }
                    }
                ''',
                "geometry/point.sc": '''
                    // Imports can appear after declarations and can reference this file.
                    class Point {
                        int x;
                        int y;
                        Point op_+(Point other) { return Point{x + other.x, y + other.y}; }
                    }
                    import sums;
                ''',
                "geometry/sums.sc": "import point; int sum(Point point) { return point.x + point.y; }",
                # Never imported; its invalid source must not affect the build.
                "unrelated.sc": "not a valid module",
            })
            executable = self.compile(entry)
            result = subprocess.run([str(executable)], capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, "7\n14\n7\n")

    def test_cyclic_imports_and_cross_module_recursion(self):
        with tempfile.TemporaryDirectory() as directory:
            entry = write_modules(directory, {
                "main.sc": "import main; import even; int main() { print(is_even(10)); return 0; }",
                "even.sc": "import odd; bool is_even(int n) { if (n == 0) return true; return is_odd(n - 1); }",
                "odd.sc": "import even; bool is_odd(int n) { if (n == 0) return false; return is_even(n - 1); }",
            })
            executable = self.compile(entry)
            result = subprocess.run([str(executable)], capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, "1\n")

    def test_same_module_basename_in_different_directories(self):
        with tempfile.TemporaryDirectory() as directory:
            entry = write_modules(directory, {
                "main.sc": "import left.part; import right.part; int main() { print(left() + right()); return 0; }",
                "left/part.sc": "import helper; int left() { return left_value(); }",
                "left/helper.sc": "int left_value() { return 2; }",
                "right/part.sc": "import helper; int right() { return right_value(); }",
                "right/helper.sc": "int right_value() { return 3; }",
            })
            executable = self.compile(entry)
            result = subprocess.run([str(executable)], capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, "5\n")

    def test_cpp_errors_point_to_imported_file(self):
        with tempfile.TemporaryDirectory() as directory:
            entry = write_modules(directory, {
                "main.sc": "import helper; int main() { return broken(); }",
                "helper.sc": "int broken() {\n return missing_name;\n}",
            })
            with self.assertRaises(BuildError) as result:
                self.compile(entry)
            self.assertRegex(str(result.exception), r"helper\.sc(?:\(2[,)]|:2:)")

    def test_cli_and_runner_resolve_imports_from_source_not_working_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            entry = ROOT / "examples/modules/main.sc"
            cpp = Path(directory) / "example.cpp"
            generated = subprocess.run(
                [sys.executable, str(ROOT / "simplec.py"), str(entry), "-o", str(cpp)],
                cwd=directory, capture_output=True, text=True, timeout=10,
            )
            self.assertEqual(generated.returncode, 0, generated.stderr)
            self.assertTrue(cpp.exists())
            result = subprocess.run(
                [sys.executable, str(ROOT / "run.py"), str(entry), "--build-dir", "output"],
                cwd=directory, capture_output=True, text=True, timeout=60,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(result.stdout, "Original squared length:\n25\nCopy squared length:\n52\nSquare of 5:\n25\n")


if __name__ == "__main__":
    unittest.main()
