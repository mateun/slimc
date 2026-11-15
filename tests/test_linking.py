from dataclasses import replace
from pathlib import Path
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from run import BuildError, Compiler, compile_cpp, find_compiler
from simplec import SourceError, transpile, transpile_project


ROOT = Path(__file__).resolve().parents[1]


def write_project(directory, files):
    for name, contents in files.items():
        path = Path(directory) / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(contents, encoding="utf-8")
    return Path(directory) / "main.sc"


class LinkDirectiveTests(unittest.TestCase):
    def test_collects_and_deduplicates_links_through_import_graph(self):
        with tempfile.TemporaryDirectory() as directory:
            entry = write_project(directory, {
                "main.sc": '#link("root")\nimport a; import b; #link("root");',
                "a.sc": '#link("first"); import shared; #link("first")',
                "b.sc": 'import shared; #link("second")',
                "shared.sc": '#link("shared") import a;',
                "unused.sc": '#link("unused")',
            })
            result = transpile_project(entry)
            self.assertEqual(result.libraries, ["root", "first", "shared", "second"])
            self.assertIn("// Required link libraries: root, first, shared, second", result.cpp)
            self.assertNotIn('#link(', result.cpp)

    def test_comments_and_strings_are_not_directives(self):
        with tempfile.TemporaryDirectory() as directory:
            entry = write_project(directory, {
                "main.sc": '''
                    // #link("ignored")
                    /* #link("also_ignored") */
                    int main() { print("#link(ignored)"); return 0; }
                ''',
            })
            self.assertEqual(transpile_project(entry).libraries, [])

    def test_rejects_invalid_directives(self):
        invalid = [
            '#link()', '#link(123)', '#link(name)', '#link("a", "b")',
            '#link("")', '#link("../library")', '#link("d3d11.lib")',
            '#link("-flag")', '#link("/option")', '#link("a b")',
            r'#link("a\nb")', '#link("a") + 1;',
            '#include("native.h")', 'int main() { #link("a") return 0; }',
            'class A { #link("a") }',
        ]
        for source in invalid:
            with self.subTest(source=source), self.assertRaises(SourceError):
                transpile(source)

    def test_invalid_imported_directive_keeps_source_location(self):
        with tempfile.TemporaryDirectory() as directory:
            entry = write_project(directory, {
                "main.sc": 'import graphics;',
                "graphics.sc": '// invalid library\n#link("d3d11.lib")',
            })
            with self.assertRaisesRegex(SourceError, r"graphics.sc:2:7:.*without paths, extensions"):
                transpile_project(entry)

    def test_compiler_arguments_use_native_link_flags(self):
        for msvc in (True, False):
            with self.subTest(msvc=msvc), tempfile.TemporaryDirectory() as directory:
                cpp = Path(directory) / "program.cpp"
                executable = Path(directory) / "program.exe"
                with patch("run.subprocess.run") as run:
                    run.return_value = subprocess.CompletedProcess([], 0, "", "")
                    compile_cpp(cpp, executable, Compiler("compiler", msvc), ["alpha", "beta"])
                command = run.call_args.args[0]
                expected = ["/link", "alpha.lib", "beta.lib"] if msvc else ["-lalpha", "-lbeta"]
                self.assertEqual(command[-len(expected):], expected)
                self.assertLess(command.index(str(cpp)), len(command) - len(expected))
                self.assertNotIn("shell", run.call_args.kwargs)


class LinkIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.compiler = find_compiler()

    def test_real_static_library_supplies_required_symbol(self):
        with tempfile.TemporaryDirectory(prefix="simple c link ") as directory:
            work = Path(directory)
            source = work / "native.cpp"
            source.write_text('extern "C" int linked_value() { return 42; }', encoding="utf-8")
            compiler = self.compiler
            if compiler.msvc:
                obj, library = work / "native.obj", work / "sc_link_probe.lib"
                librarian = Path(compiler.executable).with_name("lib.exe")
                commands = [
                    [compiler.executable, "/nologo", "/c", str(source), "/Fo:" + str(obj)],
                    [str(librarian), "/nologo", "/OUT:" + str(library), str(obj)],
                ]
            else:
                obj, library = work / "native.o", work / "libsc_link_probe.a"
                librarian = shutil.which("ar")
                self.assertIsNotNone(librarian, "Static-library integration test requires ar")
                commands = [
                    [compiler.executable, "-c", str(source), "-o", str(obj)],
                    [librarian, "rcs", str(library), str(obj)],
                ]
            for command in commands:
                result = subprocess.run(command, cwd=work, env=compiler.environment,
                                        capture_output=True, text=True, timeout=30)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

            environment = dict(compiler.environment or os.environ)
            variable = "LIB" if compiler.msvc else "LIBRARY_PATH"
            environment[variable] = str(work) + os.pathsep + environment.get(variable, "")
            compiler = replace(compiler, environment=environment)
            entry = write_project(work, {
                "main.sc": 'import bridge; int main() { print(linked_value()); return 0; }',
                "bridge.sc": 'import native_link;',
                "native_link.sc": '#native("probe.h")\n#link("sc_link_probe")',
                "probe.h": 'extern "C" int linked_value();',
            })
            generated = transpile_project(entry)
            cpp = work / "program.cpp"
            cpp.write_text(generated.cpp, encoding="utf-8")
            executable = work / ("program.exe" if os.name == "nt" else "program")
            with self.assertRaises(BuildError):
                compile_cpp(cpp, executable, compiler)
            compile_cpp(cpp, executable, compiler, generated.libraries)
            result = subprocess.run([str(executable)], capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, "42\n")

    def test_runner_links_imported_system_library(self):
        with tempfile.TemporaryDirectory() as directory:
            library = "d3d11" if os.name == "nt" else "m"
            entry = write_project(directory, {
                "main.sc": 'import bridge; int main() { print("linked"); return 0; }',
                "bridge.sc": f'#link("{library}")',
            })
            result = subprocess.run(
                [sys.executable, str(ROOT / "run.py"), str(entry)],
                cwd=directory, capture_output=True, text=True, timeout=60,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(result.stdout, "linked\n")

    def test_missing_imported_library_fails_build_without_running(self):
        with tempfile.TemporaryDirectory() as directory:
            entry = write_project(directory, {
                "main.sc": 'import bridge; int main() { print("must not run"); return 0; }',
                "bridge.sc": '#link("simple_c_missing_library_74b2f0d9")',
            })
            result = subprocess.run(
                [sys.executable, str(ROOT / "run.py"), str(entry)],
                cwd=directory, capture_output=True, text=True, timeout=60,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("simple_c_missing_library_74b2f0d9", result.stderr)
            self.assertNotIn("must not run", result.stdout)


if __name__ == "__main__":
    unittest.main()
