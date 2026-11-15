"""Minimal Simple C frontend and C++17 generator. No third-party dependencies."""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from pathlib import Path
import re
import sys


@dataclass
class Token:
    text: str
    kind: str
    line: int
    column: int
    filename: str = ""


class SourceError(Exception):
    def __init__(self, filename: str, token: Token, message: str):
        filename = token.filename or filename
        super().__init__(f"{filename}:{token.line}:{token.column}: error: {message}")


TOKEN = re.compile(
    r"(?P<space>\s+)|(?P<comment>//[^\n]*|/\*[\s\S]*?\*/)"
    r"|(?P<number>(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?[fF]?)"
    r'''|(?P<string>"(?:\\[\\"'nrt0]|[^"\\\r\n])*")'''
    r"|(?P<char>'(?:\\[\\\"'nrt0]|[^'\\\r\n])')"
    r"|(?P<id>[A-Za-z_][A-Za-z_0-9]*)"
    r"|(?P<symbol>->|==|!=|<=|>=|&&|\|\||\+=|-=|\*=|/=|%=|\+\+|--|[\[\]#{}();,.+*/%!=<>\-&|^~])"
)

PRIMITIVES = {"void", "bool", "char", "int", "float", "double"}
BINARY_OPERATORS = {"+", "-", "*", "/", "%"}
BINDINGS_DIRECTORY = Path(__file__).resolve().parent / "bindings"
KEYWORDS = set("""
alignas alignof and and_eq asm auto bitand bitor bool break case catch char
char8_t char16_t char32_t class co_await co_return co_yield compl concept const
consteval constexpr constinit const_cast continue decltype default delete do
double dynamic_cast else enum explicit export extern false float for friend
goto if import inline int long module mutable namespace new noexcept not not_eq
nullptr operator or or_eq override final private protected public register
reinterpret_cast requires return short signed sizeof static static_assert
static_cast struct switch template this thread_local throw true try typedef
typeid typename union unsigned using virtual void volatile wchar_t while xor xor_eq
""".split())


def lex(source: str, filename: str) -> list[Token]:
    tokens = []
    offset, line, column = 0, 1, 1
    while offset < len(source):
        if source.startswith("/*", offset) and "*/" not in source[offset + 2:]:
            raise SourceError(filename, Token("/*", "", line, column), "unterminated comment")
        match = TOKEN.match(source, offset)
        if not match:
            raise SourceError(filename, Token(source[offset], "", line, column),
                              f"unsupported character or malformed literal: {source[offset]!r}")
        value = match.group()
        kind = match.lastgroup
        if kind not in {"space", "comment"}:
            tokens.append(Token(value, kind, line, column, filename))
        if "\n" in value:
            line += value.count("\n")
            column = len(value.rsplit("\n", 1)[1]) + 1
        else:
            column += len(value)
        offset = match.end()
    tokens.append(Token("<end>", "eof", line, column, filename))
    return tokens


@dataclass
class Expr:
    kind: str
    token: Token
    args: list[Expr] = field(default_factory=list)


@dataclass
class Stmt:
    kind: str
    token: Token
    value: object = None
    children: list[Stmt] = field(default_factory=list)


@dataclass
class Variable:
    type: Token
    name: Token
    initializer: Expr | None = None
    array_size: int | None = None


@dataclass
class Function:
    type: Token
    name: Token
    parameters: list[Variable]
    body: Stmt


@dataclass
class Class:
    name: Token
    fields: list[Variable]
    methods: list[Function]


@dataclass
class Import:
    token: Token
    parts: list[str]


@dataclass
class Link:
    token: Token
    library: str


@dataclass
class NativeHeader:
    token: Token
    name: str

    def local_path(self) -> Path | None:
        if self.token.filename and not self.token.filename.startswith("<"):
            candidate = Path(self.token.filename).resolve().parent / self.name
            if candidate.is_file():
                return candidate.resolve()
        return None


@dataclass
class Program:
    classes: list[Class]
    functions: list[Function]
    imports: list[Import] = field(default_factory=list)
    links: list[Link] = field(default_factory=list)
    native_headers: list[NativeHeader] = field(default_factory=list)


@dataclass
class TranspileResult:
    cpp: str
    libraries: list[str]


def linked_libraries(program: Program) -> list[str]:
    return list(dict.fromkeys(link.library for link in program.links))


class Parser:
    PRECEDENCE = {
        "=": 1, "+=": 1, "-=": 1, "*=": 1, "/=": 1, "%=": 1,
        "||": 2, "&&": 3,
        "|": 4, "^": 5, "&": 6, "==": 7, "!=": 7,
        "<": 8, "<=": 8, ">": 8, ">=": 8,
        "+": 9, "-": 9, "*": 10, "/": 10, "%": 10,
    }

    def __init__(self, source: str, filename: str):
        self.filename = filename
        self.tokens = lex(source, filename)
        self.position = 0
        self.loop_depth = 0

    @property
    def current(self) -> Token:
        return self.tokens[self.position]

    def error(self, message: str, token: Token | None = None):
        raise SourceError(self.filename, token or self.current, message)

    def take(self) -> Token:
        token = self.current
        if token.kind != "eof":
            self.position += 1
        return token

    def accept(self, text: str) -> bool:
        if self.current.text == text:
            self.take()
            return True
        return False

    def expect(self, text: str) -> Token:
        if self.current.text != text:
            self.error(f"expected '{text}', found '{self.current.text}'")
        return self.take()

    def identifier(self) -> Token:
        token = self.current
        if token.text == "op_":
            self.error("'op_' is reserved for operator methods")
        if token.kind != "id" or token.text in KEYWORDS:
            self.error(f"expected an identifier, found '{token.text}'")
        if token.text.startswith("_") or "__" in token.text or token.text in {"print", "simple_c_detail"}:
            self.error(f"reserved identifier '{token.text}'")
        return self.take()

    def member_name(self) -> Token:
        if self.current.text != "op_":
            return self.identifier()
        prefix = self.take()
        operator = self.current
        if operator.text not in BINARY_OPERATORS:
            self.error("supported operator methods are op_+, op_-, op_*, op_/, and op_%", operator)
        self.take()
        return Token("op_" + operator.text, "operator", prefix.line, prefix.column, prefix.filename)

    def type_name(self) -> Token:
        base = self.take() if self.current.text in PRIMITIVES else self.identifier()
        name = base.text
        while self.accept("*"):
            name += "*"
        return Token(name, base.kind, base.line, base.column, base.filename)

    def parse(self) -> Program:
        classes, functions, imports, links, native_headers = [], [], [], [], []
        while self.current.kind != "eof":
            if self.accept("#"):
                directive = self.current
                if directive.text not in {"link", "native"}:
                    self.error(f"unknown directive '#{directive.text}'; use #link or #native")
                self.take()
                self.expect("(")
                library = self.current
                if library.kind != "string":
                    self.error(f"#{directive.text} requires one name in double quotes")
                self.take()
                name = library.text[1:-1]
                if directive.text == "link" and not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_+\-]*", name):
                    self.error("#link requires a library name without paths, extensions, whitespace, or escapes", library)
                if directive.text == "native" and (
                    not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_+./\-]*", name)
                    or any(part in {"", ".", ".."} for part in name.split("/"))
                ):
                    self.error("#native requires a relative header name without parent paths, whitespace, or escapes", library)
                self.expect(")")
                self.accept(";")
                if directive.text == "link":
                    links.append(Link(directive, name))
                else:
                    native_headers.append(NativeHeader(directive, name))
                continue
            if self.current.text == "import":
                token = self.take()
                parts = [self.identifier().text]
                while self.accept("."):
                    parts.append(self.identifier().text)
                self.expect(";")
                imports.append(Import(token, parts))
                continue
            if self.accept("class"):
                name = self.identifier()
                self.expect("{")
                fields, methods = [], []
                while self.current.text != "}":
                    type_name, member = self.type_name(), self.member_name()
                    if self.current.text == "(" or member.kind == "operator":
                        if member.text == "clone":
                            self.error("custom cloning is not supported in this first version", member)
                        methods.append(self.function(type_name, member))
                    else:
                        initializer = self.expression() if self.accept("=") else None
                        self.expect(";")
                        if initializer and not self.is_literal(initializer):
                            self.error("field defaults must be scalar literals, optionally signed", initializer.token)
                        fields.append(Variable(type_name, member, initializer))
                self.expect("}")
                self.accept(";")
                classes.append(Class(name, fields, methods))
            else:
                type_name, name = self.type_name(), self.identifier()
                functions.append(self.function(type_name, name))
        return Program(classes, functions, imports, links, native_headers)

    @staticmethod
    def is_literal(expr: Expr) -> bool:
        return (expr.kind == "literal" and expr.token.kind != "string") or (
            expr.kind == "unary" and expr.token.text in {"+", "-"}
            and expr.args[0].kind == "literal" and expr.args[0].token.kind == "number"
        )

    def function(self, type_name: Token, name: Token) -> Function:
        self.expect("(")
        parameters = []
        if self.current.text != ")":
            while True:
                parameters.append(Variable(self.type_name(), self.identifier()))
                if not self.accept(","):
                    break
        self.expect(")")
        return Function(type_name, name, parameters, self.block())

    def block(self) -> Stmt:
        start = self.expect("{")
        statements = []
        while self.current.text != "}":
            if self.current.kind == "eof":
                self.error("expected '}' before end of source")
            statements.append(self.statement())
        self.expect("}")
        return Stmt("block", start, children=statements)

    def declaration_ahead(self) -> bool:
        if self.current.kind != "id" or self.current.text in KEYWORDS - PRIMITIVES:
            return False
        index = self.position + 1
        while self.tokens[index].text == "*":
            index += 1
        if self.tokens[index].kind != "id" or self.tokens[index].text in KEYWORDS:
            return False
        if index == self.position + 1:
            return True
        # Pointer locals use '='. Do not misparse 'value * Other{}' as a declaration.
        return self.tokens[index + 1].text == "=" or self.current.text in PRIMITIVES

    def variable(self) -> Stmt:
        type_name, name = self.type_name(), self.identifier()
        if self.accept("["):
            size = self.current
            if size.kind != "number" or not re.fullmatch(r"[0-9]+", size.text) or int(size.text) < 1:
                self.error("array size must be a positive integer literal")
            self.take()
            self.expect("]")
            self.accept("=")
            self.expect("{")
            values = self.arguments("}")
            if len(values) > int(size.text):
                self.error("too many array initializers", name)
            initializer = Expr("array_init", name, values)
            return Stmt("variable", type_name, Variable(type_name, name, initializer, int(size.text)))
        if self.accept("="):
            initializer = self.expression()
        elif self.accept("{"):
            if type_name.text.endswith("*"):
                self.error("pointer variables require an '=' initializer", name)
            initializer = Expr("construct", type_name, self.arguments("}"))
        else:
            self.error("local variables require an initializer")
        return Stmt("variable", type_name, Variable(type_name, name, initializer))

    def statement(self) -> Stmt:
        token = self.current
        if token.text == "{":
            return self.block()
        if self.accept("return"):
            value = None if self.current.text == ";" else self.expression()
            self.expect(";")
            return Stmt("return", token, value)
        if self.accept("if"):
            self.expect("(")
            condition = self.expression()
            self.expect(")")
            children = [self.statement()]
            if self.accept("else"):
                children.append(self.statement())
            return Stmt("if", token, condition, children)
        if self.accept("while"):
            self.expect("(")
            condition = self.expression()
            self.expect(")")
            self.loop_depth += 1
            body = self.statement()
            self.loop_depth -= 1
            return Stmt("while", token, condition, [body])
        if self.accept("for"):
            self.expect("(")
            initial = None
            if self.current.text != ";":
                initial = self.variable() if self.declaration_ahead() else Stmt("expression", self.current, self.expression())
            self.expect(";")
            condition = None if self.current.text == ";" else self.expression()
            self.expect(";")
            step = None if self.current.text == ")" else self.expression()
            self.expect(")")
            self.loop_depth += 1
            body = self.statement()
            self.loop_depth -= 1
            return Stmt("for", token, (initial, condition, step), [body])
        if token.text in {"break", "continue"}:
            self.take()
            if not self.loop_depth:
                self.error(f"'{token.text}' requires a loop", token)
            self.expect(";")
            return Stmt(token.text, token)
        if self.accept(";"):
            return Stmt("empty", token)
        result = self.variable() if self.declaration_ahead() else Stmt("expression", token, self.expression())
        self.expect(";")
        return result

    def arguments(self, closing: str) -> list[Expr]:
        arguments = []
        if self.current.text != closing:
            while True:
                arguments.append(self.expression())
                if not self.accept(","):
                    break
        self.expect(closing)
        return arguments

    def expression(self, minimum: int = 1) -> Expr:
        token = self.current
        if token.text in {"+", "-", "!", "++", "--", "&", "*", "~"}:
            self.take()
            left = Expr("unary", token, [self.expression(11)])
        elif self.accept("("):
            left = self.expression()
            self.expect(")")
        elif token.kind in {"number", "char", "string"} or token.text in {"true", "false", "nullptr"}:
            left = Expr("literal", self.take())
        elif token.text == "print":
            left = Expr("name", self.take())
            if self.current.text != "(":
                self.error("print must be called with one argument")
        elif token.text in PRIMITIVES or (token.kind == "id" and token.text not in KEYWORDS):
            name = self.take() if token.text in PRIMITIVES else self.identifier()
            left = Expr("construct", name, self.arguments("}")) if self.accept("{") else Expr("name", name)
        else:
            self.error(f"expected an expression, found '{token.text}'")
        while True:
            token = self.current
            if self.accept("("):
                arguments = self.arguments(")")
                if left.kind == "name" and left.token.text == "print" and len(arguments) != 1:
                    self.error("print expects exactly one argument", left.token)
                left = Expr("call", token, [left, *arguments])
            elif self.accept("."):
                left = Expr("member", self.member_name(), [left])
            elif self.accept("->"):
                left = Expr("pointer_member", self.member_name(), [left])
            elif self.accept("["):
                index = self.expression()
                self.expect("]")
                left = Expr("index", token, [left, index])
            elif token.text in {"++", "--"}:
                left = Expr("postfix", self.take(), [left])
            elif token.text in self.PRECEDENCE and self.PRECEDENCE[token.text] >= minimum:
                self.take()
                precedence = self.PRECEDENCE[token.text]
                right = self.expression(precedence if precedence == 1 else precedence + 1)
                left = Expr("binary", token, [left, right])
            else:
                break
        return left


class ModuleLoader:
    """Load the reachable module graph once, including cycles and shared imports."""

    def __init__(self):
        self.modules: dict[Path, Program] = {}

    def load(self, path: Path, *, source: str | None = None, origin: Import | None = None):
        path = path.resolve()
        if path in self.modules:
            return
        if source is None:
            try:
                source = path.read_text(encoding="utf-8-sig")
            except (OSError, UnicodeError) as error:
                if origin:
                    name = ".".join(origin.parts)
                    raise SourceError(origin.token.filename, origin.token,
                                      f"cannot import '{name}' from '{path}': {error}") from error
                raise
        program = Parser(source, str(path)).parse()
        # Register before visiting dependencies so cyclic imports terminate.
        self.modules[path] = program
        for dependency in program.imports:
            target = path.parent.joinpath(*dependency.parts).with_suffix(".sc")
            if not target.exists():
                bundled = BINDINGS_DIRECTORY.joinpath(*dependency.parts).with_suffix(".sc")
                if bundled.is_file():
                    target = bundled
            self.load(target, origin=dependency)

    def combined(self) -> Program:
        return Program(
            [cls for module in self.modules.values() for cls in module.classes],
            [function for module in self.modules.values() for function in module.functions],
            links=[link for module in self.modules.values() for link in module.links],
            native_headers=[header for module in self.modules.values() for header in module.native_headers],
        )

    def protect_sources(self, output_paths):
        native_sources = {header.local_path() for module in self.modules.values() for header in module.native_headers}
        for output in output_paths:
            if Path(output).resolve() in self.modules or Path(output).resolve() in native_sources:
                raise ValueError(f"output must not overwrite a source module: {output}")


def validate(program: Program, filename: str) -> list[Class]:
    """Validate declared types and order classes by their embedded value fields."""
    classes = {item.name.text: item for item in program.classes}
    types = PRIMITIVES | classes.keys()

    def unique(items):
        seen = {}
        for item in items:
            if item.name.text in seen:
                previous = seen[item.name.text]
                location = f"{previous.filename or filename}:{previous.line}:{previous.column}"
                raise SourceError(filename, item.name,
                                  f"duplicate declaration '{item.name.text}'; first declared at {location}")
            seen[item.name.text] = item.name

    def check_type(token, allow_void=False):
        base = token.text.rstrip("*")
        if base not in types and not program.native_headers:
            raise SourceError(filename, token, f"unknown type '{token.text}'")
        if token.text == "void" and not allow_void:
            raise SourceError(filename, token, "void is only valid as a function return type")

    def check_expression(expr):
        if expr is None:
            return
        if expr.kind == "construct":
            check_type(expr.token)
        if expr.kind == "name" and expr.token.text in types:
            raise SourceError(filename, expr.token, "use braces to construct a value")
        if expr.kind == "call" and expr.args[0].kind == "name" and expr.args[0].token.text in types:
            raise SourceError(filename, expr.args[0].token, "use braces to construct a value")
        for argument in expr.args:
            check_expression(argument)

    def check_statement(stmt):
        if stmt.kind == "variable":
            check_type(stmt.value.type)
            check_expression(stmt.value.initializer)
        elif stmt.kind in {"expression", "return", "if", "while"}:
            check_expression(stmt.value)
        elif stmt.kind == "for":
            initial, condition, step = stmt.value
            if initial:
                check_statement(initial)
            check_expression(condition)
            check_expression(step)
        for child in stmt.children:
            check_statement(child)

    unique([*program.classes, *program.functions])
    for cls in program.classes:
        unique([*cls.fields, *cls.methods])
        for method in cls.methods:
            if method.name.kind == "operator":
                if len(method.parameters) != 1:
                    raise SourceError(filename, method.name,
                                      "binary operator methods require exactly one parameter for the right operand")
                if method.type.text == "void":
                    raise SourceError(filename, method.type, "arithmetic operator methods must return a value")
        for member in [*cls.fields, *cls.methods]:
            if member.name.text == cls.name.text:
                raise SourceError(filename, member.name, "a member cannot have its class's name")
        for variable in cls.fields:
            check_type(variable.type)
            if variable.initializer and variable.type.text in classes:
                raise SourceError(filename, variable.name, "class-valued field defaults are not supported yet")
    for function in [*program.functions, *(method for cls in program.classes for method in cls.methods)]:
        check_type(function.type, allow_void=True)
        unique(function.parameters)
        for parameter in function.parameters:
            check_type(parameter.type)
        check_statement(function.body)
    for function in program.functions:
        if function.name.text == "main" and (function.type.text != "int" or function.parameters):
            raise SourceError(filename, function.name, "the entry point must be int main()")

    ordered, active, done = [], set(), set()

    def visit(cls):
        name = cls.name.text
        if name in active:
            raise SourceError(filename, cls.name, f"cyclic value fields involving '{name}'")
        if name in done:
            return
        active.add(name)
        for variable in cls.fields:
            if variable.type.text in classes:
                visit(classes[variable.type.text])
        active.remove(name)
        done.add(name)
        ordered.append(cls)

    for cls in program.classes:
        visit(cls)
    return ordered


class Generator:
    def __init__(self, filename: str):
        self.filename = filename
        self.lines = []

    def emit(self, line="", indent=0):
        self.lines.append("    " * indent + line)

    def location(self, token):
        filename = (token.filename or self.filename).replace("\\", "/").replace('"', '\\"')
        self.emit(f'#line {token.line} "{filename}"')

    def expression(self, expr: Expr) -> str:
        text = expr.token.text
        arguments = [self.expression(arg) for arg in expr.args]
        if expr.kind == "name":
            return "simple_c_detail::print" if text == "print" else text
        if expr.kind == "literal":
            # Decimal integer spelling must not turn into C++ octal.
            if expr.token.kind == "number":
                if re.fullmatch(r"[0-9]+", text):
                    return str(int(text, 10))
                if re.fullmatch(r"[0-9]+[fF]", text):
                    return str(int(text[:-1], 10)) + ".0f"
            return text
        if expr.kind == "construct":
            return text + "{" + ", ".join(arguments) + "}"
        if expr.kind == "array_init":
            return "{" + ", ".join(arguments) + "}"
        if expr.kind == "index":
            return f"({arguments[0]})[{arguments[1]}]"
        if expr.kind == "call":
            return arguments[0] + "(" + ", ".join(arguments[1:]) + ")"
        if expr.kind in {"member", "pointer_member"}:
            if expr.token.kind == "operator":
                text = "operator" + text[3:]
            separator = "->" if expr.kind == "pointer_member" else "."
            return f"({arguments[0]}){separator}{text}"
        if expr.kind == "unary":
            return f"({text}{arguments[0]})"
        if expr.kind == "postfix":
            return f"({arguments[0]}{text})"
        return f"({arguments[0]} {text} {arguments[1]})"

    def inline_statement(self, stmt):
        if stmt is None:
            return ""
        if stmt.kind == "variable":
            variable = stmt.value
            suffix = f"[{variable.array_size}]" if variable.array_size is not None else ""
            return f"{variable.type.text} {variable.name.text}{suffix} = {self.expression(variable.initializer)}"
        return self.expression(stmt.value)

    def body(self, stmt, indent):
        self.emit("{", indent)
        children = stmt.children if stmt.kind == "block" else [stmt]
        for child in children:
            self.statement(child, indent + 1)
        self.emit("}", indent)

    def statement(self, stmt, indent):
        self.location(stmt.token)
        if stmt.kind == "block":
            self.body(stmt, indent)
        elif stmt.kind in {"variable", "expression"}:
            self.emit(self.inline_statement(stmt) + ";", indent)
        elif stmt.kind == "return":
            self.emit("return" + (" " + self.expression(stmt.value) if stmt.value else "") + ";", indent)
        elif stmt.kind in {"break", "continue", "empty"}:
            self.emit(("" if stmt.kind == "empty" else stmt.kind) + ";", indent)
        elif stmt.kind in {"if", "while"}:
            self.emit(f"{stmt.kind} ({self.expression(stmt.value)})", indent)
            self.body(stmt.children[0], indent)
            if len(stmt.children) == 2:
                self.emit("else", indent)
                self.body(stmt.children[1], indent)
        elif stmt.kind == "for":
            initial, condition, step = stmt.value
            condition_text = self.expression(condition) if condition else ""
            step_text = self.expression(step) if step else ""
            self.emit(f"for ({self.inline_statement(initial)}; {condition_text}; {step_text})", indent)
            self.body(stmt.children[0], indent)

    @staticmethod
    def signature(function, owner=""):
        parameters = ", ".join(f"{p.type.text} {p.name.text}" for p in function.parameters)
        name = function.name.text
        if function.name.kind == "operator":
            name = "operator" + name[3:]
        return f"{function.type.text} {owner}{name}({parameters})"

    @staticmethod
    def constructor_arities(cls):
        # Omitted fields must have explicit defaults; C++ aggregate zero-fill is not used.
        return [count for count in range(len(cls.fields) + 1)
                if all(variable.initializer is not None for variable in cls.fields[count:])]

    @staticmethod
    def constructor_parameters(cls, count):
        return ", ".join(f"{variable.type.text} _sc_arg_{index}" for index, variable in enumerate(cls.fields[:count]))

    def generate(self, program, classes):
        self.emit("// Generated by Simple C. Compile as C++17 or later.")
        if program.links:
            self.emit("// Required link libraries: " + ", ".join(linked_libraries(program)))
        self.emit("#include <iostream>")
        if program.native_headers:
            # Avoid Windows SDK min/max macros changing ordinary user functions.
            self.emit("#ifndef NOMINMAX\n#define NOMINMAX\n#endif")
        seen_headers = set()
        for header in program.native_headers:
            local = header.local_path()
            target = ('"' + local.as_posix().replace('"', '\\"') + '"') if local else '<' + header.name + '>'
            if target not in seen_headers:
                seen_headers.add(target)
                self.location(header.token)
                self.emit("#include " + target)
        self.emit("namespace simple_c_detail {")
        self.emit("template<class T> void print(const T& value) { std::cout << value << '\\n'; }", 1)
        self.emit("}")
        self.emit()
        for cls in classes:
            self.emit(f"struct {cls.name.text};")
        for function in program.functions:
            self.location(function.name)
            self.emit(self.signature(function) + ";")
        self.emit()
        for cls in classes:
            self.location(cls.name)
            self.emit(f"struct {cls.name.text} {{")
            for variable in cls.fields:
                self.location(variable.name)
                self.emit(f"{variable.type.text} {variable.name.text};", 1)
            for count in self.constructor_arities(cls):
                self.location(cls.name)
                self.emit(f"explicit {cls.name.text}({self.constructor_parameters(cls, count)});", 1)
            for method in cls.methods:
                self.location(method.name)
                self.emit(self.signature(method) + ";", 1)
            self.emit("};")
            self.emit()
        for cls in classes:
            name = cls.name.text
            for count in self.constructor_arities(cls):
                self.location(cls.name)
                self.emit(f"{name}::{name}({self.constructor_parameters(cls, count)})")
                initializers = []
                for index, variable in enumerate(cls.fields):
                    value = f"_sc_arg_{index}" if index < count else self.expression(variable.initializer)
                    initializers.append(f"{variable.name.text}({value})")
                if initializers:
                    self.emit(": " + ", ".join(initializers), 1)
                self.emit("{}")
            for method in cls.methods:
                self.location(method.name)
                self.emit(self.signature(method, name + "::"))
                self.body(method.body, 0)
            self.emit()
        for function in program.functions:
            self.location(function.name)
            self.emit(self.signature(function))
            self.body(function.body, 0)
            self.emit()
        return "\n".join(self.lines) + "\n"


def transpile(source: str, filename: str = "<input>") -> str:
    if filename.startswith("<"):
        program = Parser(source, filename).parse()
        if program.imports:
            raise SourceError(filename, program.imports[0].token,
                              "imports require a source filename for relative module lookup")
    else:
        loader = ModuleLoader()
        loader.load(Path(filename), source=source)
        program = loader.combined()
    classes = validate(program, filename)
    return Generator(filename).generate(program, classes)


def transpile_project(path: Path, *, output_paths=()) -> TranspileResult:
    """Return generated C++ and link requirements for the complete import graph."""
    loader = ModuleLoader()
    loader.load(Path(path))
    loader.protect_sources(output_paths)
    program = loader.combined()
    classes = validate(program, str(path))
    return TranspileResult(Generator(str(path)).generate(program, classes), linked_libraries(program))


def transpile_file(path: Path, *, output_paths=()) -> str:
    """Generate C++ only. Build tools should use transpile_project for link metadata."""
    return transpile_project(path, output_paths=output_paths).cpp


def main() -> int:
    parser = argparse.ArgumentParser(description="Transpile a Simple C file and its imports to C++17.")
    parser.add_argument("source", type=Path)
    parser.add_argument("-o", "--output", type=Path, help="output .cpp file (default: standard output)")
    args = parser.parse_args()
    try:
        if args.output and args.source.resolve() == args.output.resolve():
            parser.error("the output must not overwrite the source file")
        cpp = transpile_file(args.source, output_paths=[args.output] if args.output else [])
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(cpp, encoding="utf-8")
        else:
            sys.stdout.write(cpp)
        return 0
    except (SourceError, OSError, ValueError) as error:
        print(error, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
