# Simple C

A minimal transpiler for a small C-like language with value classes. It parses `.sc` source files and generates C++17. The frontend is written in Python and uses only the standard library.

The broader language design is in [LANGUAGE_SPEC.md](LANGUAGE_SPEC.md). This implementation covers functions, simple classes, generics, arithmetic operator methods, imports across source files, and native bindings.

## Run the example

Requirements: Python 3.10 or later and a C++17 compiler. The runner finds GCC or Clang on `PATH`, or MSVC on `PATH` or through the Visual Studio Installer. On Windows, it can initialize an installed Visual Studio x64 C++ toolchain automatically.

From the project directory:

```sh
python run.py examples/modules/main.sc
```

This writes `build/main.cpp`, compiles it, and runs `build/main.exe` on Windows or `build/main` elsewhere. The runner returns the program's exit code.

Expected output:

```text
Original squared length:
25
Copy squared length:
52
Square of 5:
25
```

To generate C++ without compiling:

```sh
python simplec.py examples/modules/main.sc -o build/main.cpp
```

Omit `-o` to print the C++ to standard output. To compile without running:

```sh
python run.py examples/modules/main.sc --build-only
```

You can also compile the generated file directly, for example with GCC:

```sh
g++ -std=c++17 build/main.cpp -o build/main
```

## Imports

Each module is a `.sc` file. Use a top-level import to load another file:

```cpp
import math;           // math.sc beside this file
import geometry.point; // geometry/point.sc beside this file
```

Paths are relative to the importing file, independent of the shell's working directory. For example, `import length;` inside `geometry/point.sc` loads `geometry/length.sc`. If no local module exists, the transpiler searches its bundled `bindings/` directory. This makes `import d3d11;` available from any project; a local module takes precedence. Module path components are identifiers; quoted paths, parent-directory paths, aliases, and configurable search paths are not supported yet.

Imports can appear anywhere at the top level. Every reachable file is loaded once, including repeated imports and dependencies shared by multiple modules. Circular imports are allowed; cycles of embedded class values remain invalid.

For now, all loaded classes and functions share one public namespace, including transitive dependencies. Call `square(5.0)` or construct `Point{3.0, 4.0}` directly. There are no qualified names or private exports, and duplicate declarations are errors that identify both source locations. Files outside the import graph are not compiled.

The transpiler emits one combined C++ file with the necessary forward declarations. Users do not write headers. Both the frontend and C++ compiler retain the original source filenames and line numbers in diagnostics.

The runnable [multi-file example](examples/modules/main.sc) imports a class from [geometry/point.sc](examples/modules/geometry/point.sc), its helper from [geometry/length.sc](examples/modules/geometry/length.sc), and a function from [math.sc](examples/modules/math.sc).

## External libraries

Use a top-level `#link` directive in the entry file or any imported module:

```cpp
// graphics.sc
#link("d3d11")
```

```cpp
// main.sc
import graphics;

int main() {
    return 0;
}
```

`run.py` collects library requirements from the complete import graph and passes them to the compiler driver. MSVC receives `/link d3d11.lib`; GCC and Clang receive `-ld3d11` after the generated source. A library must already be installed and available through the compiler's normal library search paths. The directive does not download dependencies or configure SDK paths.

Library names are written without extensions or paths. Names may contain letters, digits, underscores, `+`, and `-`, but cannot start with `+` or `-`. Escapes, whitespace, and arbitrary compiler flags are not accepted. A trailing semicolon is optional.

Exact duplicate names are removed. Order is deterministic: entry-file directives first, then each imported module's directives in depth-first import order. Libraries within a file retain declaration order. Names remain case-sensitive in this metadata; the native linker determines how they resolve. The compiler does not reorder libraries to solve native static-library dependency cycles.

On Windows with the SDK installed, run the [linking example](examples/linking/main.sc):

```sh
python run.py examples/linking/main.sc
```

This verifies linking to D3D11, without calling its API. A missing library produces a linker error and the runner does not execute the program.

`#link` supplies linkage only. Use `#native` in the binding module to expose declarations from the library's existing header, as described below. Other `#` directives remain unsupported.

When using `simplec.py` alone, the generated C++ lists required libraries in a comment. A manual compiler command must supply those libraries explicitly. Python build integrations can call `transpile_project(path)` to obtain both `.cpp` and `.libraries`; `transpile_file(path)` remains the C++-only API.

## Native declarations and D3D11

Application code can import the bundled D3D11 binding:

```cpp
import d3d11;

int main() {
    D3D11_BUFFER_DESC description{};
    description.ByteWidth = 16;
    description.Usage = D3D11_USAGE_DEFAULT;
    description.BindFlags = D3D11_BIND_CONSTANT_BUFFER;

    ID3D11Device* device = nullptr;
    // D3D11CreateDevice(..., &device, ...) can fill this pointer.
    return 0;
}
```

The bundled [bindings/d3d11.sc](bindings/d3d11.sc) is simply:

```cpp
#native("d3d11.h")
#link("d3d11")
```

`#native` includes an existing native header in the generated C++, before user declarations. The actual header supplies types, constants, macros, function signatures, and interface methods. No handwritten application headers or duplicate API declarations are needed. The installed SDK remains the authority for ABI details and calling conventions.

Run the complete [D3D11 example](examples/d3d11/main.sc):

```sh
python run.py examples/d3d11/main.sc --build-dir build/d3d11
```

It opens a fixed 800 x 600 client-area window titled `Simple C - D3D11 Red`, creates a WARP software device and a double-buffered flip-discard swap chain, and clears the render target to red. It presents with VSync until you close the window, then releases the COM objects. Windows and the Windows SDK are required. Resizing and fullscreen mode are disabled for this example.

```text
D3D11 red window ready. Close the window to exit.
```

On exit it also prints the number of presented frames. The example imports `win32` for the window and message loop, and uses the SDK's [D3D11CreateDeviceAndSwapChain](https://learn.microsoft.com/en-us/windows/win32/api/d3d11/nf-d3d11-d3d11createdeviceandswapchain) API. The `win32` binding includes `windows.h` and links `user32`.

To compile without opening a window:

```sh
python run.py examples/d3d11/main.sc --build-dir build/d3d11 --build-only
```

For another library, create a `.sc` binding module with its `#native` and `#link` directives, then import it normally. A native header is resolved beside the declaring module first; otherwise it uses the C++ compiler's include search paths. Relative subdirectories are allowed. Absolute paths, parent-directory components, whitespace, and escapes in header names are not supported. Resolved includes are deduplicated. Generated native builds define `NOMINMAX` unless already defined, to keep Windows min/max macros from rewriting user functions.

When a native header is reachable, the frontend permits external type names and delegates their validation to the C++ compiler. Typos and invalid API calls still fail compilation and reference the original `.sc` source. Running only the transpiler does not validate external declarations. Native types follow the header's C++ initialization and copy behavior, including zero initialization for a native aggregate written as `NativeType{}`.

Raw pointers use familiar syntax: `Type* value = nullptr`, `&value`, `*pointer`, and `pointer->method()`. Pointer-to-pointer types work for native output parameters. Pointer locals require an `=` initializer. Copying a pointer copies its address; it does not copy the object, call `AddRef`, or arrange cleanup. COM objects must be released explicitly, as in the example. Bitwise `&`, `|`, `^`, and `~` are available for native flags with C/C++ precedence.

Fixed-size local arrays can supply native buffer arguments, including the clear color:

```cpp
float red[4] = {1.0, 0.0, 0.0, 1.0};
red[0] = 1.0;
context->ClearRenderTargetView(target, red);
```

Array sizes must be positive integer literals. Brace initialization is required, with or without `=`, and omitted elements are value-initialized. Indexing uses `values[index]` without runtime bounds checks. Arrays decay to pointers when passed to native APIs or pointer parameters. Array fields, multidimensional arrays, and dynamic arrays remain unsupported.

This is a limited native bridge. References, explicit casts, qualified C++ names, and C++ template declarations are not supported. Native types can be arguments to Simple C generics. APIs that need unsupported source constructs will require further language support.

## Supported language

```cpp
int main() {
    Point a{3.0, 4.0};
    Point b = a;
    b.move(1.0, 2.0);
    print(a.x); // 3: copying a class duplicates its fields.
    print(b.x); // 4
    return 0;
}

class Point {
    float x = 0.0;
    float y = 0.0;

    void move(float dx, float dy) {
        x += dx;
        y += dy;
    }
}
```

- Types: `bool`, `char`, `int`, `float`, `double`, user-defined classes, and raw pointers. `void` is allowed as a return type or pointer base type. Native header types are available through bindings. Numeric sizes and arithmetic follow the target C++ compiler.
- Functions: typed parameters passed by value, return values, recursion, and calls to functions declared later. Multiple signatures for the same function or method name are not supported. Executables use `int main()`.
- Classes: public fields and methods, nested class values, and use before declaration. Methods access their fields directly by name. A trailing semicolon after a class is optional.
- Initialization: `Point p{1.0, 2.0}`, `Point p = Point{1.0, 2.0}`, and `Point p = other`. Brace arguments follow field order. Omitted fields must have explicit defaults. Field defaults are scalar literals, optionally signed. Local variables require an initializer.
- Copying: initialization and assignment copy all fields using C++ value semantics. Raw pointers copy their addresses. Native types retain their C++ behavior. Custom Simple C cloning and cleanup are not implemented.
- Expressions: function and method calls, field access, arithmetic, comparisons, logical and bitwise operators, pointer operations, assignment, compound arithmetic assignment, and prefix/postfix increment and decrement. Parentheses control grouping.
- Statements: blocks, `return`, `if`/`else`, `while`, `for`, `break`, and `continue`.
- Literals: decimal numbers, booleans, `nullptr`, character literals, and string literals. Common escapes such as `\n`, `\t`, `\\`, and `\"` are supported. There is no string variable type yet.
- Comments: `//` and `/* ... */`.

`print(value)` is a temporary built-in for examples. It prints one scalar or string literal followed by a newline. Booleans print as `0` or `1`; printing classes is unsupported. Identifiers starting with `_`, containing `__`, and the names `print`, `simple_c_detail`, and `op_` are reserved. The `op_` prefix followed by a supported operator is used for operator methods.

## Generics

Put type parameters directly after a class or free function name:

```cpp
class Box<T> {
    T value;
    T get() { return value; }
}

T max<T>(T a, T b) {
    if (a > b) return a;
    return b;
}

int main() {
    Box<int> number{7};
    print(max<int>(number.get(), 3));
    return 0;
}
```

Classes and functions can have multiple type parameters, such as `Pair<A, B>`. Supply every type argument explicitly at use sites: `Pair<int, float>`, `max<int>(a, b)`. Nested types such as `Box<Pair<int, float>>` and pointer arguments such as `Box<int*>` work. Methods and operator methods inside a generic class can use its type parameters; methods cannot declare their own type parameters yet.

Generics work across imports with the same declaration-order rules as ordinary classes and functions. Initialization and copying follow the existing class rules. The transpiler emits C++ templates, and the C++ compiler checks operations for each instantiated type. For example, `max<Point>` fails to compile if `Point` does not support `>`.

Type parameters are types only. Inference, constraints, default type arguments, specialization, and variadic or value parameters are outside this version. Duplicate parameters, wrong argument counts, and variables that shadow type parameters are rejected by the frontend.

Run [the generic classes and functions example](examples/generics/main.sc):

```sh
python run.py examples/generics/main.sc --build-dir build/generics
```

It imports `Pair<T>`, `max<T>`, and `identity<T>`, exercises an overloaded `+` and nested class values, and prints `7`, `13`, `27`, `2.5`, `3`, and `99` on separate lines.

## Containers

Import the bundled `containers` module to use `Vector<T>`, an alias for C++ `std::vector<T>`:

```cpp
import containers;

int main() {
    Vector<int> numbers{};
    numbers.push_back(10);
    numbers.push_back(20);
    print(numbers[0]);     // 10
    print(numbers.size()); // 2

    Vector<int> copy = numbers;
    copy[0] = 99;
    print(numbers[0]);     // 10
    return 0;
}
```

The binding lives in [containers.sc](bindings/containers.sc) and includes a small [native alias header](bindings/containers.h). Application files need only the import. No extra library installation or `#link` directive is needed with the supported C++ toolchains.

Use the standard vector API through ordinary method calls, including `push_back`, `pop_back`, `size`, `empty`, `reserve`, and `clear`. Empty braces create an empty vector; `Vector<int>{10, 20}` supplies initial elements. `Vector<int>{4}` contains the single value `4`, rather than four elements. Nested vectors, such as `Vector<Vector<int>>`, and vectors of Simple C classes also work.

Vectors manage their storage through C++. Copy initialization, assignment, and passing a vector by value copy its elements into independent storage. Pointer elements copy addresses and do not transfer ownership of the pointed-to objects. Destroying a vector releases its storage and destroys its elements. Indexing with `[]` is unchecked; operations that reallocate storage invalidate pointers into that storage. Methods keep their C++ requirements: for example, growing with `resize(count)` requires default-constructible elements, which a Simple C class with required fields does not provide.

Native type and method errors are checked by the C++ compiler. The binding exposes the existing vector API within the syntax Simple C supports; it adds no separate container implementation.

Run [the container example](examples/containers/main.sc):

```sh
python run.py examples/containers/main.sc --build-dir build/containers
```

It prints `60`, `10`, `99`, `2`, `3`, and `1` on separate lines, covering growth, iteration, copying, and class elements.

## Operator methods

Define binary arithmetic on a class with `op_+`, `op_-`, `op_*`, `op_/`, or `op_%`:

```cpp
class Vec3 {
    float x;
    float y;
    float z;

    Vec3 op_+(Vec3 other) {
        return Vec3{x + other.x, y + other.y, z + other.z};
    }
}

Vec3 a{1, 4, 8};
Vec3 b{4, 8, 7};
Vec3 sum = a + b;
```

The instance is the left operand; the one required parameter is the right operand, passed by value. A method may return a class or scalar value. For example, `Vec3 op_*(float scale)` can scale a vector, while `float op_*(Vec3 other)` can calculate a dot product. Choose one signature per operator per class.

Normal arithmetic precedence applies. Explicit calls such as `a.op_+(b)` also work. Methods may modify the left operand just as ordinary methods can; the example above returns a new value and leaves both operands unchanged.

Only binary arithmetic overloads are supported. `op_+` does not automatically provide `+=` or unary `+`, and `vector * scalar` does not imply `scalar * vector`. Assignment retains its normal value-copy behavior.

Run [the Point and Vec3 example](examples/points.sc) with `python run.py examples/points.sc`. Its exit status also checks that vector addition produces the expected components.

## How it works

`simplec.py` loads the entry file and its imports, tokenizes each source, parses it into an abstract syntax tree, validates declared types, and orders classes by their embedded fields. It emits forward declarations and places method bodies after all class definitions, allowing declarations to appear in any order across the loaded modules.

Classes become C++ structs with generated initialization constructors. These constructors enforce required fields instead of allowing C++ aggregate initialization to silently zero-fill missing fields. Ordinary C++ field copying supplies copy construction and assignment for the supported value types.

Generated `#line` directives map compiler errors back to the `.sc` file. The frontend reports syntax and declaration errors with a filename, line, and column. Expression type checking, name resolution, and return checking mostly remain the C++ compiler's responsibility; those diagnostics can mention generated C++ details.

## Current limits

Each compilation accepts one entry file and follows its imports. References, dynamic or multidimensional arrays, managed resource ownership, custom clone operations, inheritance, and global variables are not implemented. Fixed-size arrays are supported as locals. Native interop supports header declarations and raw pointers within the language's expression syntax. Operator overloads are limited to the binary arithmetic methods above. Unsupported syntax is rejected rather than passed through as arbitrary C++.

Classes cannot contain a cycle of embedded values. Class-valued field defaults are not supported yet; supply these values explicitly in braces. No performance benchmarks or cross-compiler compatibility claims are made by this prototype.

## Tests

```sh
python -m unittest discover -s tests -v
```

The suite checks frontend diagnostics and compiles and executes generated programs. Integration tests require a C++ compiler and fail if none is available. They cover copying, default and required fields, local arrays, declaration order, control flow, recursion, literals, operator methods and precedence, and source locations in compiler errors. Import tests also cover relative lookup, repeated and circular imports, shared dependencies, name collisions, missing files, and protection against overwriting imported sources. Linking tests build a real static library, verify symbol resolution through a native binding, and check transitive link requirements and missing-library failures. They require the MSVC librarian or `ar`, plus D3D11 on Windows or the system math library elsewhere. Native tests cover SDK-style types, pointers, functions, interface methods, and compiler diagnostics.

Generic tests cover multiple and nested type arguments, pointer fields, independent copies, methods and operators, cross-module calls and recursion, comparisons, native types, and invalid instantiations.

Container tests exercise the bundled import from other directories, transitive imports, vector operations, class and nested vector elements, copying through assignment and function parameters, pointer elements, and native compiler diagnostics.

The default suite builds the D3D11 window example on Windows without launching it. The visual smoke test is opt-in and opens a window for three presented frames, then exits. To run it in PowerShell:

```powershell
$env:SIMPLE_C_RUN_GUI_TESTS = "1"
python -m unittest discover -s tests -p test_native.py -v
Remove-Item Env:SIMPLE_C_RUN_GUI_TESTS
```

For a manual three-frame run, set `SIMPLE_C_SMOKE_TEST=1` before launching the example and clear it afterward. A normal run with that variable unset stays open until closed. A successful build alone does not verify the displayed color.

Run only the import tests with:

```sh
python -m unittest discover -s tests -p test_imports.py -v
```
