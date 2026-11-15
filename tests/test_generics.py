from pathlib import Path
import os
import subprocess
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


class GenericFrontendTests(unittest.TestCase):
    def test_rejects_invalid_type_parameters(self):
        cases = [
            ('class Box<T, T> { T value; }', 'duplicate type parameter'),
            ('T identity<T, T>(T value) { return value; }', 'duplicate type parameter'),
            ('class T {} class Box<T> { T value; }', 'conflicts with a type'),
            ('class Box<Box> {}', 'conflicts with a type'),
            ('class Box<T> { int T; }', 'cannot shadow a type parameter'),
            ('T identity<T>(T T) { return T; }', 'cannot shadow a type parameter'),
            ('T identity<T>(T value) { int T = 0; return value; }', 'cannot shadow a type parameter'),
            ('class Box<T> { U convert<U>(T value) { return U{}; } }', 'generic methods are not supported'),
            ('int main<T>() { return 0; }', 'entry point must be int main'),
        ]
        for source, message in cases:
            with self.subTest(source=source), self.assertRaisesRegex(SourceError, message):
                transpile(source)
        for source in ['class Box<> {}', 'class Box<int> {}', 'class Box<T = int> {}',
                       'class Box<3> {}', 'int identity<>() { return 0; }']:
            with self.subTest(source=source), self.assertRaises(SourceError):
                transpile(source)

    def test_type_argument_counts_and_scope(self):
        cases = [
            ('class Box<T> { T value; } int main() { Box value{1}; return 0; }', 'expects 1 type arguments, got 0'),
            ('class Box<T> { T value; } int main() { Box<int, float> value{1}; return 0; }', 'expects 1 type arguments, got 2'),
            ('class Plain {} int main() { Plain<int> value{}; return 0; }', 'expects 0 type arguments, got 1'),
            ('class Box<T> { T value; } int main() { Box<Missing> value{}; return 0; }', 'unknown type'),
            ('class Box<T> { T value; } int main() { Box<void> value{}; return 0; }', 'void is only valid'),
            ('class Box<T> { T<int> value; }', 'does not accept type arguments'),
            ('class Box<T> { T value; } T bad(T value) { return value; }', 'unknown type'),
            ('class Box<T> { Box<T> value; }', 'cyclic value fields'),
        ]
        for source, message in cases:
            with self.subTest(source=source), self.assertRaisesRegex(SourceError, message):
                transpile(source)

    def test_generic_functions_require_exact_explicit_arguments(self):
        prefix = 'T identity<T>(T value) { return value; } '
        for expression, message in [
            ('identity(1)', 'requires explicit type arguments'),
            ('identity<int, float>(1)', 'expects 1 type arguments, got 2'),
            ('identity<Missing>(1)', 'unknown type'),
            ('identity<void>(1)', 'void is only valid'),
            ('missing<int>(1)', 'unknown generic function'),
        ]:
            with self.subTest(expression=expression), self.assertRaisesRegex(SourceError, message):
                transpile(prefix + 'int main() { return ' + expression + '; }')
        with self.assertRaisesRegex(SourceError, 'expects 0 type arguments, got 1'):
            transpile('int plain(int a) { return a; } int main() { return plain<int>(1); }')

    def test_imported_generic_error_location(self):
        with tempfile.TemporaryDirectory() as directory:
            entry = write_project(directory, {
                'main.sc': 'import helpers;',
                'helpers.sc': 'class Box<T> { T value; }\nint broken() { Box<int, float> value{}; return 0; }',
            })
            with self.assertRaisesRegex(SourceError, r'helpers.sc:2:.*expects 1 type arguments, got 2'):
                transpile_project(entry)


class GenericCompileTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.compiler = find_compiler()

    def build(self, entry, output_directory):
        generated = transpile_project(entry)
        cpp = Path(output_directory) / 'program.cpp'
        executable = Path(output_directory) / ('program.exe' if os.name == 'nt' else 'program')
        cpp.write_text(generated.cpp, encoding='utf-8')
        compile_cpp(cpp, executable, self.compiler, generated.libraries)
        return executable

    def run_project(self, files):
        with tempfile.TemporaryDirectory(prefix='simple c generics ') as directory:
            entry = write_project(directory, files)
            executable = self.build(entry, directory)
            result = subprocess.run([str(executable)], capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            return result.stdout

    def test_runnable_example(self):
        with tempfile.TemporaryDirectory() as directory:
            executable = self.build(ROOT / 'examples/generics/main.sc', directory)
            result = subprocess.run([str(executable)], capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, '7\n13\n27\n2.5\n3\n99\n')

    def test_multiple_parameters_defaults_arrays_and_independent_copies(self):
        output = self.run_project({'main.sc': '''
            int main() {
                Pair<int, float> value{3, 4.5};
                Pair<int, float> copy = value;
                copy.first = 8;
                print(value.first);
                print(copy.first);
                print(value.second);
                Counter<int> zero{};
                print(zero.get());
                print(sum_two<float>(1.5, 2.5));
                print(make_pair<int, float>(7, 2.5).first);
                Pair<int, float> values[2] = {value, copy};
                print(values[1].first);
                return 0;
            }
            class Pair<A, B> { A first; B second; }
            class Counter<T> {
                T value = 0;
                T get() { return value; }
            }
            T sum_two<T>(T a, T b) {
                T values[2] = {a, b};
                return values[0] + values[1];
            }
            Pair<A, B> make_pair<A, B>(A a, B b) { return Pair<A, B>{a, b}; }
        '''})
        self.assertEqual(output, '3\n8\n4.5\n0\n4\n7\n8\n')

    def test_nested_types_and_generic_pointer_fields(self):
        output = self.run_project({'main.sc': '''
            class Holder { Box<Box<Point>> value; }
            class Node<T> {
                T value;
                Node<T>* next = nullptr;
                T get() { return value; }
            }
            class Box<T> { T value; T get() { return value; } }
            class Point { int x; }
            int main() {
                Holder holder{Box<Box<Point>>{Box<Point>{Point{12}}}};
                print(holder.value.get().get().x);
                Node<int> first{3};
                Node<int> second{7, &first};
                print(second.next->get());
                Node<int>* p = address<Node<int>>(&second);
                print(p->get());
                Box<Node<int>*> pointer_box{p};
                print(pointer_box.value->get());
                return 0;
            }
            T* address<T>(T* value) { return value; }
        '''})
        self.assertEqual(output, '12\n3\n7\n7\n')

    def test_wrappers_only_require_embedded_arguments_to_be_complete(self):
        output = self.run_project({'main.sc': '''
            class Node { Wrapper<Node> link; int value; }
            class Wrapper<T> { Link<T> link; }
            class Link<T> { T* pointer = nullptr; }
            class Tag<T> { int value; }
            class Tagged { Tag<Tagged> tag; }
            int main() {
                Node first{Wrapper<Node>{Link<Node>{}}, 3};
                Node second{Wrapper<Node>{Link<Node>{&first}}, 7};
                print(second.link.link.pointer->value);
                Tagged tagged{Tag<Tagged>{12}};
                print(tagged.tag.value);
                return 0;
            }
        '''})
        self.assertEqual(output, '3\n12\n')
        with self.assertRaisesRegex(SourceError, 'cyclic'):
            transpile('class Box<T> { T value; } class Node { Box<Node> value; }')

    def test_cross_module_recursion_and_comparison_disambiguation(self):
        output = self.run_project({
            'main.sc': '''
                import algorithms;
                int main() {
                    int a = 1;
                    int b = 2;
                    int c = 0;
                    print(a < b);
                    print(a < b > (c));
                    print(a < b && b > c);
                    a < b > c;
                    print(is_even<int>(8));
                    print(identity<int>(a) < identity<int>(b));
                    return 0;
                }
            ''',
            'algorithms.sc': '''
                import helpers;
                bool is_even<T>(T n) { if (n == 0) return true; return is_odd<T>(n - 1); }
                T identity<T>(T value) { return value; }
            ''',
            'helpers.sc': '''
                import algorithms;
                bool is_odd<T>(T n) { if (n == 0) return false; return is_even<T>(n - 1); }
            ''',
        })
        self.assertEqual(output, '1\n1\n1\n1\n1\n')

    def test_native_type_as_generic_argument(self):
        output = self.run_project({'main.sc': '''
            #native("stdint.h")
            class Box<T> { T value; T get() { return value; } }
            int main() {
                Box<uint32_t> value{42};
                print(value.get());
                return 0;
            }
        '''})
        self.assertEqual(output, '42\n')

    def test_invalid_instantiated_operations_and_missing_fields(self):
        cases = [
            ('class Point { int x; }\nT max<T>(T a, T b) { if (a > b) return a; return b; }\n'
             'int main() { Point p = max<Point>(Point{1}, Point{2}); return 0; }'),
            ('class Pair<T> { T first; T second; }\nint main() { Pair<int> p{1}; return 0; }'),
        ]
        for source in cases:
            with self.subTest(source=source), tempfile.TemporaryDirectory() as directory:
                entry = write_project(directory, {'main.sc': source})
                with self.assertRaises(BuildError) as result:
                    self.build(entry, directory)
                self.assertIn('main.sc', str(result.exception))


if __name__ == '__main__':
    unittest.main()
