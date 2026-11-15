from pathlib import Path
import os
import subprocess
import sys
import tempfile
import unittest

from run import BuildError, compile_cpp, find_compiler
from simplec import SourceError, transpile


ROOT = Path(__file__).resolve().parents[1]


class FrontendTests(unittest.TestCase):
    def test_invalid_array_declarations(self):
        for declaration in ["int a[0] = {};", "int a[-1] = {};", "int a[2.5] = {};",
                            "int a[count] = {};", "int a[] = {1};", "int a[2];",
                            "int a[1] = {1, 2};", "int a[2] = other;"]:
            with self.subTest(declaration=declaration), self.assertRaises(SourceError):
                transpile("int main() { " + declaration + " return 0; }")

    def test_invalid_operator_declarations(self):
        cases = [
            ("class V { V op_+() { return V{}; } }", "exactly one parameter"),
            ("class V { V op_+(V a, V b) { return a; } }", "exactly one parameter"),
            ("class V { void op_+(V b) {} }", "must return a value"),
            ("class V { V op_+=(V b) { return b; } }", "supported operator methods"),
            ("class V { bool op_==(V b) { return true; } }", "supported operator methods"),
            ("class V { V op_!() { return V{}; } }", "supported operator methods"),
            ("int op_+(int b) { return b; }", "reserved for operator methods"),
            ("class V { int op_+; }", "expected '\\('"),
            ("class V { int op_+(int b) { return b; } int op_+(float b) { return 0; } }",
             "duplicate declaration"),
        ]
        for source, message in cases:
            with self.subTest(source=source), self.assertRaisesRegex(SourceError, message):
                transpile(source)

    def test_rejects_unsupported_features(self):
        sources = [
            "class Box<T> { T value; }",
            "class Child : Parent {}",
            "int main() { int* pointer; }",
            "class Buffer { Buffer clone() { return Buffer{}; } }",
            "#include <stdio.h>",
        ]
        for source in sources:
            with self.subTest(source=source), self.assertRaises(SourceError):
                transpile(source, "bad.sc")

    def test_source_location_on_syntax_error(self):
        with self.assertRaisesRegex(SourceError, r"bad.sc:3:1: error: expected ';'"):
            transpile("int main() {\nreturn 0\n}", "bad.sc")

    def test_rejects_invalid_declarations(self):
        cases = {
            "class A { A value; }": "cyclic value fields",
            "class A { B b; } class B { A a; }": "cyclic value fields",
            "int f() { return 1; } int f() { return 2; }": "duplicate declaration",
            "class A { int x; int x; }": "duplicate declaration",
            "class A { void value; }": "void is only valid",
            "int f(Missing value) { return 0; }": "unknown type",
            "int main() { break; }": "requires a loop",
            "int main() { int x; return 0; }": "require an initializer",
            "class A { int x = make(); }": "defaults must be scalar literals",
            "void main() {}": "entry point must be int main",
            "int main() { print(1, 2); }": "exactly one argument",
            "int main() { return int(1); }": "use braces",
            "int main() { return Missing{}; }": "unknown type",
            "int main() { int __reserved = 0; }": "reserved identifier",
            "/* incomplete": "unterminated comment",
        }
        for source, message in cases.items():
            with self.subTest(source=source), self.assertRaisesRegex(SourceError, message):
                transpile(source)

    def test_cli_does_not_overwrite_source(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "program.sc"
            original = "int main() { return 0; }"
            source.write_text(original)
            result = subprocess.run([sys.executable, str(ROOT / "simplec.py"), str(source), "-o", str(source)],
                                    capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(source.read_text(), original)


class CompileAndRunTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # These are integration tests: absence of a compiler is a failure, not a silent skip.
        cls.compiler = find_compiler()

    def build(self, source, directory):
        original = Path(directory) / "program.sc"
        original.write_text(source, encoding="utf-8")
        cpp = original.with_suffix(".cpp")
        cpp.write_text(transpile(source, str(original)), encoding="utf-8")
        executable = original.with_suffix(".exe" if os.name == "nt" else "")
        compile_cpp(cpp, executable, self.compiler)
        return executable

    def run_source(self, source):
        with tempfile.TemporaryDirectory(prefix="simple c test ") as directory:
            executable = self.build(source, directory)
            result = subprocess.run([str(executable)], capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            return result.stdout

    def test_example(self):
        output = self.run_source((ROOT / "examples/points.sc").read_text())
        self.assertEqual(output, "Original squared length:\n25\nCopy squared length:\n52\nSum from 0 through 4:\n10\n")

    def test_local_arrays_indexing_and_pointer_arguments(self):
        output = self.run_source('''
            float sum(float* values) { return values[0] + values[1] + values[2] + values[3]; }
            class Point { int x; }
            int main() {
                float red[4] = {1.0, 0.0, 0.0, 1.0};
                print(sum(red));
                int values[3]{2};
                values[1] = 4;
                values[2] += 6;
                int total = 0;
                for (int i = 0; i < 3; i++) total += values[i];
                print(total);
                Point points[2] = {Point{7}, Point{8}};
                print(points[1].x);
                int zeroes[2] = {};
                print(zeroes[1]);
                return 0;
            }
        ''')
        self.assertEqual(output, "2\n12\n8\n0\n")

    def test_arithmetic_operator_methods(self):
        output = self.run_source('''
            int main() {
                Number a{17};
                Number b{5};
                print((a + b).value);
                print((a - b).value);
                print((a * 3).value);
                print((a / 2).value);
                print((a % 5).value);
                print((a + b * 3).value);
                print(((a + b) * 3).value);
                print((a - b - b).value);
                print(a.op_+(b).value);
                print((Number{2} + Number{3}).value);
                print(a.value);
                print(b.value);
                return 0;
            }
            class Number {
                int value;
                Number op_+(Number other) { return Number{value + other.value}; }
                Number op_-(Number other) { return Number{value - other.value}; }
                Number op_*(int scalar) { return Number{value * scalar}; }
                Number op_/(int scalar) { return Number{value / scalar}; }
                Number op_%(int scalar) { return Number{value % scalar}; }
            }
        ''')
        self.assertEqual(output, "22\n12\n51\n8\n2\n32\n66\n7\n22\n5\n17\n5\n")

    def test_operator_can_return_a_scalar(self):
        output = self.run_source('''
            class Vector {
                int x;
                int y;
                int op_*(Vector other) { return dot(x, y, other.x, other.y); }
            }
            int main() {
                Vector a{2, 3};
                Vector b{4, 5};
                print(a * b);
                return 0;
            }
            int dot(int ax, int ay, int bx, int by) { return ax * bx + ay * by; }
        ''')
        self.assertEqual(output, "23\n")

    def test_operators_do_not_imply_reverse_unary_or_compound_forms(self):
        for expression in ["2 * a", "-a", "a += a"]:
            with self.subTest(expression=expression), tempfile.TemporaryDirectory() as directory:
                with self.assertRaises(BuildError):
                    self.build('''
                        class Number {
                            int value;
                            Number op_+(Number other) { return Number{value + other.value}; }
                            Number op_*(int scalar) { return Number{value * scalar}; }
                        }
                        int main() {
                            Number a{3};
                    ''' + expression + "; return 0; }", directory)

    def test_forward_types_methods_and_independent_copies(self):
        output = self.run_source('''
            int main() {
                Wrapper a{Point{2, 3}};
                Wrapper b = a;
                b.point.x = 10;
                print(a.total());
                print(b.total());
                a = b;
                a = a;
                b.point.y = 20;
                print(a.total());
                Point defaults{};
                Point partial{7};
                print(defaults.y);
                print(partial.y);
                return 0;
            }
            class Wrapper {
                Point point;
                int total() { return sum(point); }
            }
            int sum(Point value) { return value.x + value.y; }
            class Point { int x = 1; int y = 4; }
        ''')
        self.assertEqual(output, "5\n13\n13\n4\n4\n")

    def test_control_flow_recursion_and_precedence(self):
        output = self.run_source('''
            int main() {
                int total = 0;
                for (int i = 0; i < 6; ++i) {
                    if (i == 2) continue;
                    if (i == 5) break;
                    total += i;
                }
                int n = 2;
                while (n > 0) { total += n; n--; }
                if (false) total = 99;
                else if (true && !(total == 0)) total += 1;
                print(total);
                print(2 + 3 * 4);
                print((2 + 3) * 4);
                print(factorial(5));
                print(even(8));
                int a = 0;
                int b = 0;
                a = b = 3;
                print(a + b);
                return 0;
            }
            int factorial(int n) {
                if (n <= 1) return 1;
                return n * factorial(n - 1);
            }
            bool even(int n) { if (n == 0) return true; return odd(n - 1); }
            bool odd(int n) { if (n == 0) return false; return even(n - 1); }
        ''')
        self.assertEqual(output, "12\n14\n20\n120\n1\n6\n")

    def test_literals_and_empty_class(self):
        output = self.run_source(r'''
            /* This comment includes { and ; */
            class Empty {}
            int main() {
                Empty empty{};
                print("hello // world"); // a real comment
                print('A');
                print(008);
                print(2f + .5);
                print(-2 * 3 + +4);
                print("escaped \"quote\"");
                return 0;
            }
        ''')
        self.assertEqual(output, 'hello // world\nA\n8\n2.5\n-2\nescaped "quote"\n')

    def test_required_fields_cannot_be_implicitly_zero_filled(self):
        with tempfile.TemporaryDirectory() as directory, self.assertRaises(BuildError):
            self.build("class Point { int x; int y; } int main() { Point p{1}; return 0; }", directory)

    def test_compiler_errors_reference_original_source(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(BuildError) as result:
                self.build("int main() {\n    return unknown_name;\n}", directory)
            self.assertIn("program.sc", str(result.exception))
            self.assertRegex(str(result.exception), r"program\.sc(?:\(2[,)]|:2:)")


if __name__ == "__main__":
    unittest.main()
