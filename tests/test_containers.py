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
        path.write_text(source, encoding='utf-8')
    return Path(directory) / 'main.sc'


class ContainerBindingTests(unittest.TestCase):
    def test_transitive_import_deduplicates_header_and_needs_no_link_flag(self):
        with tempfile.TemporaryDirectory() as directory:
            entry = write_project(directory, {
                'main.sc': 'import containers; import helpers;',
                'helpers.sc': 'import containers; Vector<int> values() { return Vector<int>{1, 2}; }',
            })
            result = transpile_project(entry)
            header = ROOT / 'bindings/containers.h'
            self.assertEqual(result.cpp.count('#include "' + header.as_posix() + '"'), 1)
            self.assertEqual(result.libraries, [])
            with self.assertRaisesRegex(ValueError, 'must not overwrite'):
                transpile_project(entry, output_paths=[header])

    def test_vector_requires_an_import(self):
        with self.assertRaisesRegex(SourceError, 'unknown type'):
            transpile('int main() { Vector<int> values{}; return 0; }')


class ContainerCompileTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.compiler = find_compiler()

    def build(self, entry, directory):
        generated = transpile_project(entry)
        cpp = Path(directory) / 'program.cpp'
        executable = Path(directory) / ('program.exe' if os.name == 'nt' else 'program')
        cpp.write_text(generated.cpp, encoding='utf-8')
        compile_cpp(cpp, executable, self.compiler, generated.libraries)
        return executable

    def run_project(self, files):
        with tempfile.TemporaryDirectory(prefix='simple c containers ') as directory:
            entry = write_project(directory, files)
            result = subprocess.run([str(self.build(entry, directory))],
                                    capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            return result.stdout

    def test_example_via_runner_from_another_working_directory(self):
        with tempfile.TemporaryDirectory(prefix='simple c containers ') as directory:
            result = subprocess.run([
                sys.executable, str(ROOT / 'run.py'), str(ROOT / 'examples/containers/main.sc'),
                '--build-dir', str(Path(directory) / 'build'),
            ], cwd=directory, capture_output=True, text=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(result.stdout, '60\n10\n99\n2\n3\n1\n')

    def test_assignment_parameters_returns_and_pointer_elements(self):
        output = self.run_project({
            'main.sc': '''
                import helpers;
                int main() {
                    Vector<int> original = make_numbers();
                    Vector<int> assigned{};
                    assigned = original;
                    assigned[0] = 8;
                    print(original[0]);
                    print(assigned[0]);
                    print(change_copy(original));
                    print(original[0]);
                    int number = 12;
                    Vector<int*> pointers{&number};
                    Vector<int*> copied = pointers;
                    *copied[0] = 42;
                    print(number);
                    return 0;
                }
            ''',
            'helpers.sc': '''
                import containers;
                Vector<int> make_numbers() { return Vector<int>{3, 7}; }
                int change_copy(Vector<int> values) { values[0] = 99; return values[0]; }
            ''',
        })
        self.assertEqual(output, '3\n8\n99\n3\n42\n')

    def test_nested_vectors_class_elements_and_generic_fields(self):
        output = self.run_project({'main.sc': '''
            import containers;
            class Store<T> { Vector<T> values; }
            class Point { int x; }
            int main() {
                Vector<Point> points{Point{3}};
                Store<Point> store{points};
                Store<Point> copy = store;
                copy.values[0].x = 99;
                print(store.values[0].x);
                print(copy.values[0].x);
                Vector<Vector<int>> rows = Vector<Vector<int>>{Vector<int>{1, 2}};
                Vector<Vector<int>> copied = rows;
                copied[0][0] = 7;
                print(rows[0][0]);
                print(copied[0][0]);
                return 0;
            }
        '''})
        self.assertEqual(output, '3\n99\n1\n7\n')

    def test_invalid_elements_and_type_argument_counts_fail_compilation(self):
        for body in ['Vector<int> values{}; values.push_back(Point{1});',
                     'Vector<int, float> values{};']:
            with self.subTest(body=body), tempfile.TemporaryDirectory() as directory:
                entry = write_project(directory, {
                    'main.sc': 'import containers; class Point { int x; }\n'
                               'int main() { ' + body + ' return 0; }',
                })
                with self.assertRaises(BuildError) as result:
                    self.build(entry, directory)
                self.assertIn('main.sc', str(result.exception))


if __name__ == '__main__':
    unittest.main()
