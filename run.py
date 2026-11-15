"""Transpile, compile, and run a Simple C program with a locally installed compiler."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import os
from pathlib import Path
import shutil
import subprocess
import sys

from simplec import SourceError, transpile_project


class BuildError(Exception):
    pass


@dataclass
class Compiler:
    executable: str
    msvc: bool = False
    environment: dict[str, str] | None = None


def find_compiler() -> Compiler:
    for name in ("g++", "clang++", "cl"):
        executable = shutil.which(name)
        if executable:
            return Compiler(executable, name == "cl")
    if os.name == "nt":
        installer = Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"))
        vswhere = installer / "Microsoft Visual Studio/Installer/vswhere.exe"
        if vswhere.exists():
            result = subprocess.run(
                [str(vswhere), "-latest", "-products", "*", "-requires",
                 "Microsoft.VisualStudio.Component.VC.Tools.x86.x64", "-property", "installationPath"],
                capture_output=True, text=True, check=True,
            )
            installation = result.stdout.strip()
            vcvars = Path(installation) / "VC/Auxiliary/Build/vcvars64.bat"
            if installation and vcvars.exists():
                # Only the locally discovered Visual Studio path is passed to cmd.
                # Source and output paths are passed directly to the compiler below.
                command = f'cmd.exe /d /s /c ""{vcvars}" >nul && set"'
                result = subprocess.run(command, capture_output=True, text=True, errors="replace")
                if result.returncode:
                    raise BuildError("Could not initialize Visual Studio:\n" + result.stderr)
                environment = {key.upper(): value for key, value in os.environ.items()}
                for line in result.stdout.splitlines():
                    if "=" in line and not line.startswith("="):
                        key, value = line.split("=", 1)
                        environment[key.upper()] = value
                search_path = environment.get("PATH", "")
                executable = shutil.which("cl", path=search_path)
                if executable:
                    return Compiler(executable, True, environment)
    raise BuildError("No C++ compiler found. Install GCC, Clang, or Visual Studio's C++ build tools.")


def compile_cpp(cpp: Path, executable: Path, compiler: Compiler, libraries=()) -> None:
    cpp, executable = cpp.resolve(), executable.resolve()
    executable.parent.mkdir(parents=True, exist_ok=True)
    if compiler.msvc:
        command = [compiler.executable, "/nologo", "/std:c++17", "/EHsc", "/W4", "/we4715", "/we4716", "/permissive-",
                   "/utf-8", str(cpp), "/Fe:" + str(executable),
                   "/Fo:" + str(executable.with_suffix(".obj"))]
        if libraries:
            command.extend(["/link", *(name + ".lib" for name in libraries)])
    else:
        command = [compiler.executable, "-std=c++17", "-Wall", "-Wextra", "-Werror=return-type", "-pedantic-errors",
                   str(cpp), "-o", str(executable)]
        command.extend("-l" + name for name in libraries)
    result = subprocess.run(command, env=compiler.environment, cwd=executable.parent,
                            capture_output=True, text=True, errors="replace")
    if result.returncode:
        raise BuildError(result.stdout + result.stderr)
    # Preserve warnings even when compilation succeeds.
    if result.stderr:
        print(result.stderr, file=sys.stderr, end="")
    if compiler.msvc:
        for line in result.stdout.splitlines():
            if "warning " in line:
                print(line, file=sys.stderr)


def main() -> int:
    parser = argparse.ArgumentParser(description="Transpile, compile, and run a Simple C program.")
    parser.add_argument("source", type=Path)
    parser.add_argument("--build-dir", type=Path, default=Path("build"))
    parser.add_argument("--build-only", action="store_true", help="compile without running")
    args = parser.parse_args()
    try:
        source = args.source.resolve()
        cpp = (args.build_dir / (source.stem + ".cpp")).resolve()
        executable = cpp.with_suffix(".exe" if os.name == "nt" else "")
        if source in {cpp, executable}:
            raise BuildError("Build outputs must not overwrite the source file.")
        generated = transpile_project(source, output_paths=[cpp, executable, executable.with_suffix(".obj")])
        compiler = find_compiler()
        cpp.parent.mkdir(parents=True, exist_ok=True)
        cpp.write_text(generated.cpp, encoding="utf-8")
        compile_cpp(cpp, executable, compiler, generated.libraries)
        if args.build_only:
            print(executable)
            return 0
        return subprocess.run([str(executable)]).returncode
    except (SourceError, BuildError, OSError, ValueError, subprocess.SubprocessError) as error:
        print(error, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
