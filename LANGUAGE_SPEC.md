# Simple C language spec

First draft. Simple C is a working name.

## Purpose

A small language with familiar C/C++ syntax, classes, imports, and simple generics. A transpiler generates C++ so existing C++ compilers can build and optimize the program.

The language deliberately supports a limited feature set. Accepting arbitrary C++ is not a goal.

## Basic syntax

Keep C-style types, functions, expressions, braces, semicolons, and control flow. The initial scope includes variables, arithmetic, function calls, `if`, `else`, loops, and `return`.

```cpp
int add(int a, int b) {
    return a + b;
}

int main() {
    int result = add(2, 3);
    return result;
}
```

## Modules instead of headers

Users write source files and import other modules. They do not maintain header files or repeat declarations.

```cpp
import graphics;
```

Each module is a `.sc` source file. `import graphics;` loads `graphics.sc` beside the importing file. Dotted names map to subdirectories: `import geometry.point;` loads `geometry/point.sc`. Lookup is relative to each importing file, not the working directory. If no local module exists, lookup falls back to the transpiler's bundled `bindings/` directory. A bundled `d3d11.sc` provides the Windows SDK binding.

Imports are top-level declarations and can appear before or after other declarations. The transpiler follows imports recursively and loads each resolved file once. Repeated and circular imports are allowed, but cycles of embedded class values are invalid.

For the initial implementation, all reachable declarations are public and share one namespace, including transitive imports. Duplicate class or function names are errors. Qualified names, aliases, private exports, and configurable search paths remain outside this version.

Declaration order across loaded modules does not matter. The transpiler generates one combined C++ file with forward declarations. Diagnostics retain the original source filenames and line numbers. Users maintain no headers.

## Link directives

An entry file or imported module can declare a native library dependency at the top level:

```cpp
#link("d3d11")
#link("dxutil")
```

The argument is a double-quoted library name without an extension or path. Allowed characters are letters, digits, underscores, `+`, and `-`; the first character must be a letter, digit, or underscore. A trailing semicolon is optional.

The transpiler collects requirements from every reachable module and removes exact duplicate names. Entry-file directives come first, followed by each imported module in depth-first import order. Within a module, directive order is preserved. Names are not case-normalized.

The build runner passes names as `name.lib` to the MSVC linker or as `-lname` to GCC/Clang after the generated source. Libraries must be available through the native toolchain's normal search paths. A missing library is a build error.

These directives are build metadata, not a general preprocessor. They do not introduce external declarations, include headers, or install libraries. Binding modules can combine `#link` with `#native` to expose an existing library through an ordinary import.

## Native bindings

A binding module includes an existing native header and declares its link requirements:

```cpp
// Bundled d3d11.sc
#native("d3d11.h")
#link("d3d11")
```

Application code writes `import d3d11;` and uses SDK names directly. The transpiler emits the native header include before generated declarations. The C++ compiler reads the header and checks the native types, constants, macros, function calls, and interface methods. Users do not reproduce those declarations or write application headers.

`#native` is a top-level directive taking one double-quoted header name, with an optional trailing semicolon. Headers are resolved beside the declaring module first, then through the C++ compiler's include paths. Header names may include relative subdirectories, but not absolute paths, `..` components, whitespace, or escapes. Includes are deduplicated. Native builds define `NOMINMAX` unless already defined to avoid Windows min/max macro collisions.

If any native header is reachable, unknown type names are left for the C++ compiler to validate. Native values follow their C++ initialization, copying, and destruction rules. In particular, native aggregates can use `{}` to zero-initialize fields; Simple C classes still require defaults for omitted fields.

Raw pointers support `Type*`, `Type**`, `nullptr`, unary `&` and `*`, and `->` member access. Pointer locals require an `=` initializer. These pointers carry no automatic ownership or reference counting. The caller must follow the native API's lifetime rules, including explicit COM `Release()` calls where needed. Bitwise `&`, `|`, `^`, and `~` support native flags with C/C++ precedence.

References, explicit casts, qualified C++ names, and C++ template declarations remain outside this version. Native types can be arguments to Simple C generics. Native bindings expose declarations without enabling arbitrary C++ source syntax. The bundled `win32` module provides `windows.h` declarations and links `user32` for window creation and message handling.

## Bundled containers

`import containers;` exposes `Vector<T>` through a bundled native header that aliases `std::vector<T>`. It requires no explicit link directive. Applications use the ordinary import, generic type, constructor, indexing, and method-call syntax:

```cpp
Vector<int> values{};
values.push_back(10);
int first = values[0];
Vector<int> copy = values;
```

This is a native binding, not a new language primitive. Vector initialization, allocation, copying, destruction, and method requirements follow C++ semantics. Empty braces produce an empty vector; nonempty braces supply initial elements. Copying duplicates element values into separate storage, while pointer elements retain the same addresses. Indexing is unchecked, and storage reallocation invalidates pointers into the vector. Nested vectors and Simple C class elements are supported. Invalid element operations and native type argument counts are diagnosed by the C++ compiler.

## Local arrays

Fixed-size local arrays use C-style declarations and indexing:

```cpp
float red[4] = {1.0, 0.0, 0.0, 1.0};
red[0] = 1.0;
```

The size must be a positive integer literal. Brace initialization is required, with an optional `=`. Too many initializers are an error; omitted elements are value-initialized by C++, so omitted scalar elements become zero. Class elements must support the required initialization.

Arrays can be indexed and passed to pointer parameters using native C++ array-to-pointer conversion. There are no runtime bounds checks. Array fields, array-valued parameters or returns, multidimensional arrays, and dynamic arrays are not supported yet.

## Classes

Classes group fields and methods. Class instances are values by default. A field containing another class stores that value directly; declaring a class does not imply a heap allocation.

Proposed initial syntax uses brace initialization and field defaults:

```cpp
class Point {
    float x = 0.0;
    float y = 0.0;

    void translate(float dx, float dy) {
        x += dx;
        y += dy;
    }
}

Point origin{};
Point position{1.0, 2.0};
position.translate(3.0, 4.0);
```

For this draft, members are public, brace arguments follow field declaration order, and omitted fields require defaults. More complex initialization rules remain open.

Users do not write C++ copy constructors, move constructors, or assignment operators. The transpiler generates the required machinery.

## Copying and cloning

Initialization from another value and assignment both duplicate the source value. The source remains valid and unchanged.

The duplication rule is determined from the type at compile time:

- Plain values use an automatically generated field-by-field copy.
- A type with a custom clone operation uses that operation to duplicate its owned resources.
- Otherwise, a class duplicates each field using that field's copy or clone operation.
- If any field cannot be duplicated, automatic duplication is unavailable. Attempting to duplicate that class is a compile-time error.

A custom clone takes precedence over generated duplication. Its result must own its duplicated resources independently of the source. The exact declaration syntax for a custom clone remains open.

```cpp
Point a{1.0, 2.0};
Point b = a;       // Copies the fields.
b = a;            // Replaces b with a copy of a.
```

For an owning buffer, the same syntax would invoke its clone operation and may allocate memory. Assignment does not promise a cheap operation.

Replacing an existing value also releases its previous owned resources. Self-assignment must preserve the value. The transpiler handles this bookkeeping.

A class does not need a handwritten copy or clone function when duplication can be generated. A class that cannot be duplicated may still be defined and used where duplication is unnecessary.

## Lifetime and memory

Owned resources are released automatically when their owner is destroyed, normally when it leaves scope. There is no mandatory garbage collector or borrow checker.

The design favors contiguous storage, predictable layouts, and visible allocation decisions. Automatic cloning is one documented operation that may allocate.

Raw pointers have no automatic ownership. Copying one copies the address, not the pointed-to object. The programmer is responsible for keeping the referenced object alive and for releasing resources acquired through native APIs. Independent duplication applies to owned values, not objects reached through raw pointers. References remain unsupported.

Ownership types, allocation syntax, custom cleanup, and explicit ownership transfer remain to be defined.

## Generics

Generic parameters appear directly after a class or function name:

```cpp
class Pair<T> {
    T first;
    T second;
}

T max<T>(T a, T b) {
    if (a > b) {
        return a;
    }
    return b;
}

Pair<int> numbers{3, 7};
int larger = max<int>(numbers.first, numbers.second);
```

Generic classes and free functions are implemented. Parameters are types only, with one or more distinct names: `class Pair<A, B>` or `B convert<A, B>(A value)`. They are in scope throughout the declaration, including a function's return type. They cannot conflict with another declared type or their own declaration name, and fields, methods, parameters, and locals cannot shadow them.

Every use supplies all type arguments explicitly. Nested arguments such as `Box<Pair<int>>`, pointer arguments such as `Box<int*>`, and native types from imported bindings are supported. Use braces for class construction and parentheses for function calls. A generic class must also spell out its arguments when referring to itself, for example `Pair<T> op_+(Pair<T> other)`.

Ordinary methods and operator methods can use their class's type parameters. Methods with their own type parameters, type inference, constraints, default arguments, specialization, variadic parameters, and value parameters are unsupported. Generic definitions may be imported and used before their declarations, just like ordinary classes and functions.

Initialization, copying, and raw pointer behavior are unchanged for instantiated classes. Embedded-value cycles are rejected; a generic wrapper that stores only a pointer to its argument does not create such a cycle.

The transpiler generates C++ templates. It checks type parameter scope and argument counts; the C++ compiler checks that operations are valid for each instantiated type. Invalid uses produce compile-time errors mapped to the original source, although diagnostics may mention generated C++ template details.

## Operator methods

Classes can define binary arithmetic with `op_+`, `op_-`, `op_*`, `op_/`, and `op_%`:

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

Each operator method takes exactly one parameter for the right operand. The left operand is the instance on which the method runs. The parameter is passed by value, and the return type must be a value type, not `void`. It may differ from the class: a vector multiplication method could return a scalar dot product.

Operators have the same precedence and associativity as their built-in equivalents. `a + b` invokes `a`'s `op_+` method; `a.op_+(b)` is also allowed. Operator methods behave like ordinary methods, including the ability to modify the receiving instance. They are not automatically pure or commutative.

Each class can define one method per operator. Free-function operators, unary overloads, comparison overloads, and compound-assignment overloads are not supported yet. Defining `op_+` does not define `+=`, and defining multiplication by a scalar does not define multiplication with the operands reversed. The standalone identifier `op_` is reserved for this syntax.

The transpiler translates these methods into C++ member operators. Assignment continues to use the language's copy/clone rules and cannot be replaced by an operator method.

## Initial boundaries

Leave inheritance, user-defined implicit conversions, template specialization, and general template metaprogramming out of the initial language. Use composition to build larger types.

The generated C++ should be readable. Diagnostics should identify the original source location wherever possible.

Error handling and managed ownership of native resources still need design decisions. This draft does not define a standard library or a complete grammar.
