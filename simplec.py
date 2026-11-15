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


@dataclass
class TypeRef(Token):
    name: str = ""
    arguments: list[TypeRef] = field(default_factory=list)
    pointers: int = 0


def type_ref(token: Token, arguments=(), pointers: int = 0) -> TypeRef:
    arguments = list(arguments)
    suffix = "<" + ", ".join(arg.text for arg in arguments) + ">" if arguments else ""
    return TypeRef(token.text + suffix + "*" * pointers, "type", token.line, token.column,
                   token.filename, token.text, arguments, pointers)


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
    type: TypeRef
    name: Token
    initializer: Expr | None = None
    array_size: int | None = None


@dataclass
class Function:
    type: TypeRef
    name: Token
    parameters: list[Variable]
    body: Stmt
    type_parameters: list[Token] = field(default_factory=list)


@dataclass
class Class:
    name: Token
    fields: list[Variable]
    methods: list[Function]
    type_parameters: list[Token] = field(default_factory=list)


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


def scan_symbols(tokens: list[Token]) -> tuple[set[str], set[str]]:
    """Collect top-level type and generic function names before parsing bodies."""
    types, functions = set(), set()
    depth = 0
    for index, token in enumerate(tokens[:-1]):
        if token.text == "{":
            depth += 1
        elif token.text == "}":
            depth -= 1
        elif depth == 0:
            if token.text == "class" and tokens[index + 1].kind == "id":
                types.add(tokens[index + 1].text)
            if token.kind == "id" and tokens[index + 1].text == "<":
                end = index + 2
                while end < len(tokens) and (tokens[end].kind == "id" or tokens[end].text == ","):
                    end += 1
                if end + 1 < len(tokens) and tokens[end].text == ">" and tokens[end + 1].text == "(":
                    functions.add(token.text)
    return types, functions


class Parser:
    PRECEDENCE = {
        "=": 1, "+=": 1, "-=": 1, "*=": 1, "/=": 1, "%=": 1,
        "||": 2, "&&": 3,
        "|": 4, "^": 5, "&": 6, "==": 7, "!=": 7,
        "<": 8, "<=": 8, ">": 8, ">=": 8,
        "+": 9, "-": 9, "*": 10, "/": 10, "%": 10,
    }

    def __init__(self, source: str, filename: str, known_types=(), generic_functions=()):
        self.filename = filename
        self.tokens = lex(source, filename)
        local_types, local_functions = scan_symbols(self.tokens)
        self.known_types = PRIMITIVES | local_types | set(known_types)
        self.generic_functions = local_functions | set(generic_functions)
        self.generic_scope: set[str] = set()
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

    def type_arguments(self) -> list[TypeRef]:
        self.expect("<")
        arguments = [self.type_name()]
        while self.accept(","):
            arguments.append(self.type_name())
        self.expect(">")
        return arguments

    def type_parameters(self) -> list[Token]:
        if not self.accept("<"):
            return []
        parameters = [self.identifier()]
        while self.accept(","):
            parameters.append(self.identifier())
        self.expect(">")
        return parameters

    def type_name(self) -> TypeRef:
        base = self.take() if self.current.text in PRIMITIVES else self.identifier()
        arguments = self.type_arguments() if self.current.text == "<" else []
        pointers = 0
        while self.accept("*"):
            pointers += 1
        return type_ref(base, arguments, pointers)

    def import_declaration(self) -> Import:
        token = self.expect("import")
        parts = [self.identifier().text]
        while self.accept("."):
            parts.append(self.identifier().text)
        self.expect(";")
        return Import(token, parts)

    def scan_imports(self) -> list[Import]:
        imports, depth = [], 0
        while self.current.kind != "eof":
            if depth == 0 and self.current.text == "import":
                imports.append(self.import_declaration())
                continue
            token = self.take()
            if token.text == "{":
                depth += 1
            elif token.text == "}":
                depth -= 1
        return imports

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
                imports.append(self.import_declaration())
                continue
            if self.accept("class"):
                name = self.identifier()
                type_parameters = self.type_parameters()
                self.generic_scope = {parameter.text for parameter in type_parameters}
                self.expect("{")
                fields, methods = [], []
                while self.current.text != "}":
                    type_name, member = self.type_name(), self.member_name()
                    if self.current.text == "<":
                        self.error("generic methods are not supported yet; use a generic free function")
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
                classes.append(Class(name, fields, methods, type_parameters))
                self.generic_scope = set()
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

    def function(self, type_name: TypeRef, name: Token) -> Function:
        type_parameters = self.type_parameters()
        previous_scope = self.generic_scope
        self.generic_scope = previous_scope | {parameter.text for parameter in type_parameters}
        self.expect("(")
        parameters = []
        if self.current.text != ")":
            while True:
                parameters.append(Variable(self.type_name(), self.identifier()))
                if not self.accept(","):
                    break
        self.expect(")")
        body = self.block()
        self.generic_scope = previous_scope
        return Function(type_name, name, parameters, body, type_parameters)

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
        position = self.position
        try:
            declared_type = self.type_name()
            if self.current.kind != "id" or self.current.text in KEYWORDS:
                return False
            if declared_type.pointers:
                return self.tokens[self.position + 1].text == "=" or declared_type.name in PRIMITIVES
            if declared_type.arguments:
                return (declared_type.name in self.known_types | self.generic_scope
                        or self.tokens[self.position + 1].text in {"=", "{", "["})
            return True
        except SourceError:
            return False
        finally:
            self.position = position

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
            specialized = False
            if self.current.text == "<" and (
                name.text in self.known_types | self.generic_scope | self.generic_functions
                or self.tokens[self.position + 1].text in self.known_types | self.generic_scope
            ):
                name = type_ref(name, self.type_arguments())
                specialized = True
            if self.accept("{"):
                left = Expr("construct", name if specialized else type_ref(name), self.arguments("}"))
            else:
                left = Expr("generic_name" if specialized else "name", name)
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
        self.sources: dict[Path, str] = {}
        self.modules: dict[Path, Program] = {}

    def load(self, path: Path, *, source: str | None = None, origin: Import | None = None):
        path = path.resolve()
        if path in self.sources:
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
        # Register before visiting dependencies so cyclic imports terminate.
        self.sources[path] = source
        for dependency in Parser(source, str(path)).scan_imports():
            target = path.parent.joinpath(*dependency.parts).with_suffix(".sc")
            if not target.exists():
                bundled = BINDINGS_DIRECTORY.joinpath(*dependency.parts).with_suffix(".sc")
                if bundled.is_file():
                    target = bundled
            self.load(target, origin=dependency)

    def combined(self) -> Program:
        known_types, generic_functions = set(), set()
        for path, source in self.sources.items():
            types, functions = scan_symbols(lex(source, str(path)))
            known_types.update(types)
            generic_functions.update(functions)
        self.modules = {path: Parser(source, str(path), known_types, generic_functions).parse()
                        for path, source in self.sources.items()}
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
    functions = {item.name.text: item for item in program.functions}
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

    def check_type(token: TypeRef, scope=frozenset(), allow_void=False):
        base = token.name
        if base not in types | scope and not program.native_headers:
            raise SourceError(filename, token, f"unknown type '{token.text}'")
        if base in classes:
            expected = len(classes[base].type_parameters)
            if len(token.arguments) != expected:
                raise SourceError(filename, token,
                                  f"type '{base}' expects {expected} type arguments, got {len(token.arguments)}")
        elif token.arguments and base in PRIMITIVES | scope:
            raise SourceError(filename, token, f"type '{base}' does not accept type arguments")
        if base == "void" and not token.pointers and not allow_void:
            raise SourceError(filename, token, "void is only valid as a function return type")
        for argument in token.arguments:
            check_type(argument, scope)

    def check_expression(expr, scope):
        if expr is None:
            return
        if expr.kind == "construct":
            check_type(expr.token, scope)
        if expr.kind == "name" and expr.token.text in types | scope:
            raise SourceError(filename, expr.token, "use braces to construct a value")
        if expr.kind == "name" and expr.token.text in functions and functions[expr.token.text].type_parameters:
            raise SourceError(filename, expr.token,
                              f"generic function '{expr.token.text}' requires explicit type arguments")
        if expr.kind == "generic_name":
            token = expr.token
            if token.name in types | scope:
                raise SourceError(filename, token, "use braces to construct a value")
            if token.name in functions:
                expected = len(functions[token.name].type_parameters)
                if len(token.arguments) != expected:
                    raise SourceError(filename, token,
                                      f"function '{token.name}' expects {expected} type arguments, got {len(token.arguments)}")
            elif not program.native_headers:
                raise SourceError(filename, token, f"unknown generic function '{token.name}'")
            for argument in token.arguments:
                check_type(argument, scope)
        for argument in expr.args:
            check_expression(argument, scope)

    def check_statement(stmt, scope):
        if stmt.kind == "variable":
            check_type(stmt.value.type, scope)
            if stmt.value.name.text in scope:
                raise SourceError(filename, stmt.value.name, "a variable cannot shadow a type parameter")
            check_expression(stmt.value.initializer, scope)
        elif stmt.kind in {"expression", "return", "if", "while"}:
            check_expression(stmt.value, scope)
        elif stmt.kind == "for":
            initial, condition, step = stmt.value
            if initial:
                check_statement(initial, scope)
            check_expression(condition, scope)
            check_expression(step, scope)
        for child in stmt.children:
            check_statement(child, scope)

    def check_type_parameters(parameters, owner):
        scope = set()
        for parameter in parameters:
            if parameter.text in scope:
                raise SourceError(filename, parameter, f"duplicate type parameter '{parameter.text}'")
            if parameter.text in types or parameter.text == owner.text:
                raise SourceError(filename, parameter, f"type parameter '{parameter.text}' conflicts with a type or its declaration name")
            scope.add(parameter.text)
        return scope

    def check_function(function, scope):
        check_type(function.type, scope, allow_void=True)
        unique(function.parameters)
        for parameter in function.parameters:
            check_type(parameter.type, scope)
            if parameter.name.text in scope:
                raise SourceError(filename, parameter.name, "a parameter cannot shadow a type parameter")
        check_statement(function.body, scope)

    unique([*program.classes, *program.functions])
    for cls in program.classes:
        scope = check_type_parameters(cls.type_parameters, cls.name)
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
            if member.name.text in scope:
                raise SourceError(filename, member.name, "a member cannot shadow a type parameter")
        for variable in cls.fields:
            check_type(variable.type, scope)
            if variable.initializer and variable.type.name in classes and not variable.type.pointers:
                raise SourceError(filename, variable.name, "class-valued field defaults are not supported yet")
        for method in cls.methods:
            check_function(method, scope)
    for function in program.functions:
        scope = check_type_parameters(function.type_parameters, function.name)
        check_function(function, scope)
        if function.name.text == "main" and (function.type.text != "int" or function.parameters or function.type_parameters):
            raise SourceError(filename, function.name, "the entry point must be int main()")

    ordered, active, done = [], set(), set()

    # Only arguments stored by value require complete types. In particular,
    # Link<Node> may be a field of Node when Link<T> stores T*.
    embedded_parameters = {name: set() for name in classes}

    def embedded_arguments(type_name):
        if type_name.name not in classes:
            return type_name.arguments
        parameters = classes[type_name.name].type_parameters
        return [argument for parameter, argument in zip(parameters, type_name.arguments)
                if parameter.text in embedded_parameters[type_name.name]]

    def used_parameters(type_name, scope):
        if type_name.pointers:
            return set()
        used = {type_name.name} & scope
        for argument in embedded_arguments(type_name):
            used.update(used_parameters(argument, scope))
        return used

    changed = True
    while changed:
        changed = False
        for cls in program.classes:
            used = embedded_parameters[cls.name.text]
            previous = len(used)
            scope = {parameter.text for parameter in cls.type_parameters}
            for variable in cls.fields:
                used.update(used_parameters(variable.type, scope))
            changed |= len(used) != previous

    def value_dependencies(type_name):
        if type_name.pointers:
            return
        if type_name.name in classes:
            yield classes[type_name.name]
        for argument in embedded_arguments(type_name):
            yield from value_dependencies(argument)

    def visit(cls):
        name = cls.name.text
        if name in active:
            raise SourceError(filename, cls.name, f"cyclic value fields involving '{name}'")
        if name in done:
            return
        active.add(name)
        for variable in cls.fields:
            for dependency in value_dependencies(variable.type):
                visit(dependency)
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
        if expr.kind in {"name", "generic_name"}:
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

    def template_parameters(self, parameters):
        if parameters:
            self.emit("template <" + ", ".join("typename " + parameter.text for parameter in parameters) + ">")

    @staticmethod
    def class_type(cls):
        if cls.type_parameters:
            return cls.name.text + "<" + ", ".join(parameter.text for parameter in cls.type_parameters) + ">"
        return cls.name.text

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
            self.location(cls.name)
            self.template_parameters(cls.type_parameters)
            self.emit(f"struct {cls.name.text};")
        for function in program.functions:
            self.location(function.name)
            self.template_parameters(function.type_parameters)
            self.emit(self.signature(function) + ";")
        self.emit()
        for cls in classes:
            self.location(cls.name)
            self.template_parameters(cls.type_parameters)
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
            class_type = self.class_type(cls)
            for count in self.constructor_arities(cls):
                self.location(cls.name)
                self.template_parameters(cls.type_parameters)
                self.emit(f"{class_type}::{name}({self.constructor_parameters(cls, count)})")
                initializers = []
                for index, variable in enumerate(cls.fields):
                    value = f"_sc_arg_{index}" if index < count else self.expression(variable.initializer)
                    initializers.append(f"{variable.name.text}({value})")
                if initializers:
                    self.emit(": " + ", ".join(initializers), 1)
                self.emit("{}")
            for method in cls.methods:
                self.location(method.name)
                self.template_parameters(cls.type_parameters)
                self.emit(self.signature(method, class_type + "::"))
                self.body(method.body, 0)
            self.emit()
        for function in program.functions:
            self.location(function.name)
            self.template_parameters(function.type_parameters)
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
    program = loader.combined()
    loader.protect_sources(output_paths)
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
