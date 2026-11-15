from pathlib import Path
import os
import subprocess
import sys
import tempfile
import unittest

from run import BuildError, compile_cpp, find_compiler
from simplec import SourceError, transpile, transpile_project


ROOT = Path(__file__).resolve().parents[1]


def write_project(directory, files):
    for name, source in files.items():
        path = Path(directory) / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")
    return Path(directory) / "main.sc"


class NativeFrontendTests(unittest.TestCase):
    def test_bundled_binding_is_resolved_transitively(self):
        with tempfile.TemporaryDirectory() as directory:
            entry = write_project(directory, {
                "main.sc": 'import graphics; import d3d11; int main() { return 0; }',
                "graphics.sc": 'import d3d11;',
            })
            result = transpile_project(entry)
            self.assertEqual(result.libraries, ["d3d11"])
            self.assertEqual(result.cpp.count('#include <d3d11.h>'), 1)

    def test_local_module_takes_precedence_over_bundled_binding(self):
        with tempfile.TemporaryDirectory() as directory:
            entry = write_project(directory, {
                "main.sc": 'import d3d11; int main() { return local(); }',
                "d3d11.sc": 'int local() { return 0; }',
            })
            result = transpile_project(entry)
            self.assertEqual(result.libraries, [])
            self.assertNotIn('#include <d3d11.h>', result.cpp)

    def test_duplicate_headers_and_relative_header_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            entry = write_project(directory, {
                "main.sc": 'import native.bridge;',
                "native/bridge.sc": '#native("api.h") #native("api.h"); #native("stdint.h")',
                "native/api.h": 'struct Native {};',
            })
            result = transpile_project(entry)
            self.assertEqual(result.cpp.count('#include "' + (Path(directory) / 'native/api.h').as_posix() + '"'), 1)
            self.assertIn('#include <stdint.h>', result.cpp)

    def test_invalid_native_directives(self):
        for source in ['#native()', '#native(header)', '#native("")', '#native("../api.h")',
                       '#native("/api.h")', '#native("a//b.h")', '#native("a b.h")',
                       '#native("api.h", "other.h")', r'#native("a\nb.h")',
                       'int main() { #native("api.h") return 0; }']:
            with self.subTest(source=source), self.assertRaises(SourceError):
                transpile(source)

    def test_native_types_are_deferred_only_when_a_header_is_imported(self):
        with self.assertRaisesRegex(SourceError, "unknown type"):
            transpile('int main() { Native* p = nullptr; return 0; }')
        cpp = transpile('#native("api.h") int main() { Native* p = nullptr; return 0; }')
        self.assertIn('Native* p = nullptr;', cpp)

    def test_outputs_cannot_overwrite_native_header(self):
        with tempfile.TemporaryDirectory() as directory:
            entry = write_project(directory, {"main.sc": '#native("api.h")', "api.h": '// external header'})
            with self.assertRaisesRegex(ValueError, "must not overwrite"):
                transpile_project(entry, output_paths=[entry.parent / "api.h"])


class NativeCompileTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.compiler = find_compiler()

    def build(self, entry):
        generated = transpile_project(entry)
        cpp = entry.with_suffix(".cpp")
        cpp.write_text(generated.cpp, encoding="utf-8")
        executable = entry.with_suffix(".exe" if os.name == "nt" else "")
        compile_cpp(cpp, executable, self.compiler, generated.libraries)
        return executable

    def test_native_structs_functions_aliases_macros_and_pointer_methods(self):
        with tempfile.TemporaryDirectory(prefix="simple c native ") as directory:
            entry = write_project(directory, {
                "main.sc": '''
                    import native.api;
                    int main() {
                        NativeValue value{};
                        value.number = 21;
                        NativeValue* pointer = &value;
                        NativeValue** indirect = &pointer;
                        (*indirect)->set(42);
                        NativeCount flags = FLAG_ONE | FLAG_TWO;
                        print(pointer->number);
                        print(native_read(pointer));
                        print(HAS_FLAG(flags, FLAG_TWO));
                        print(pointer_identity(pointer)->number);
                        void* opaque = pointer;
                        print(opaque != nullptr);
                        return 0;
                    }
                    NativeValue* pointer_identity(NativeValue* pointer) { return pointer; }
                ''',
                "native/api.sc": '#native("api.h")',
                "native/api.h": '''
                    using NativeCount = int;
                    enum { FLAG_ONE = 1, FLAG_TWO = 2 };
                    #define HAS_FLAG(flags, flag) (((flags) & (flag)) != 0)
                    struct NativeValue {
                        int number;
                        void set(int value) { number = value; }
                    };
                    inline int native_read(NativeValue* value) { return value->number; }
                ''',
            })
            result = subprocess.run([str(self.build(entry))], capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, "42\n42\n1\n42\n1\n")

    def test_raw_pointers_value_cycles_and_arithmetic_disambiguation(self):
        with tempfile.TemporaryDirectory() as directory:
            entry = write_project(directory, {"main.sc": '''
                class Node {
                    int value = 0;
                    Node* next = nullptr;
                    int read() { return value; }
                }
                class Number {
                    int value;
                    Number op_*(Number b) { return Number{value * b.value}; }
                }
                int main() {
                    Node a{};
                    Node b{7, &a};
                    Node* pointer = &b;
                    print(pointer->read());
                    pointer->next->value = 9;
                    print(a.value);
                    int value = 3;
                    int* p = &value;
                    *p += 1;
                    print(value * 5);
                    print(*p * 2);
                    Number n{3};
                    print((n * Number{4}).value);
                    print(1 | 2 & 4);
                    print(7 ^ 2);
                    print(~0 == -1);
                    return 0;
                }
            '''})
            result = subprocess.run([str(self.build(entry))], capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, "7\n9\n20\n8\n12\n1\n5\n1\n")

    def test_native_type_and_call_errors_are_reported_in_original_source(self):
        cases = [
            ('NativeTypo value{};', r"main\.sc(?:\(3[,)]|:3:)"),
            ('native_call(nullptr);', r"main\.sc(?:\(3[,)]|:3:)"),
        ]
        for statement, location in cases:
            with self.subTest(statement=statement), tempfile.TemporaryDirectory() as directory:
                entry = write_project(directory, {
                    "main.sc": '#native("api.h")\nint main() {\n' + statement + '\nreturn 0; }',
                    "api.h": 'inline void native_call(int value) {}',
                })
                with self.assertRaises(BuildError) as result:
                    self.build(entry)
                self.assertRegex(str(result.exception), location)

    def test_missing_native_header_is_a_compiler_error(self):
        with tempfile.TemporaryDirectory() as directory:
            entry = write_project(directory, {"main.sc": '#native("sc_missing_header_82f314.h") int main() { return 0; }'})
            with self.assertRaises(BuildError) as result:
                self.build(entry)
            self.assertIn("sc_missing_header_82f314.h", str(result.exception))

    @unittest.skipUnless(os.name == "nt", "D3D11 requires Windows and its SDK")
    def test_d3d11_window_example_builds_without_launching(self):
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run(
                [sys.executable, str(ROOT / "run.py"), str(ROOT / "examples/d3d11/main.sc"), "--build-only"],
                cwd=directory, capture_output=True, text=True, timeout=60,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertTrue((Path(directory) / "build/main.exe").is_file())

    @unittest.skipUnless(os.name == "nt" and os.environ.get("SIMPLE_C_RUN_GUI_TESTS") == "1",
                         "set SIMPLE_C_RUN_GUI_TESTS=1 to open the three-frame window smoke test")
    def test_d3d11_window_and_swap_chain_from_bundled_bindings(self):
        with tempfile.TemporaryDirectory() as directory:
            environment = dict(os.environ, SIMPLE_C_SMOKE_TEST="1")
            result = subprocess.run(
                [sys.executable, str(ROOT / "run.py"), str(ROOT / "examples/d3d11/main.sc")],
                cwd=directory, env=environment, capture_output=True, text=True, timeout=60,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(result.stdout, "D3D11 red window ready. Close the window to exit.\nPresented red frames:\n3\n")


if __name__ == "__main__":
    unittest.main()
