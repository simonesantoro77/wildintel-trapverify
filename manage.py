"""
CamTrap Verify — CLI de gestión.

Uso:
    uv run cli backend serve [dev|prod|debug] [--port 8765]
    uv run cli backend test [unit|integration|all]
    uv run cli backend docs serve
    uv run cli backend docs build
    uv run cli backend docs pdf
    uv run cli frontend dev [--port 5173]
    uv run cli frontend test
    uv run cli frontend docs serve
    uv run cli frontend docs build
    uv run cli frontend docs pdf
    uv run cli docs serve
    uv run cli docs build
    uv run cli docs pdf
    uv run cli dev
    uv run cli package build [--format deb|rpm|appimage|windows|all] [--version 0.1.0]
"""
import json
import os
import shutil
import subprocess
import sys
import threading
from enum import Enum
from pathlib import Path

import typer
from rich.console import Console
from rich.panel import Panel

sys.path.insert(0, str(Path(__file__).parent / "backend" / "src"))
from settings import settings  # noqa: E402

# ── Constantes ────────────────────────────────────────────────────────────────

ROOT_DIR        = Path(__file__).parent
BACKEND_DIR     = ROOT_DIR / "backend"
SRC_DIR         = BACKEND_DIR / "src"
MKDOCS_CFG      = BACKEND_DIR / "mkdocs.yml"
ROOT_MKDOCS_CFG = ROOT_DIR / "mkdocs.yml"
DIST_DIR        = ROOT_DIR / "dist"
DOCS_DIR        = ROOT_DIR / "docs"
FRONTEND_DIR    = ROOT_DIR / "frontend"
FRONTEND_MKDOCS = FRONTEND_DIR / "mkdocs.yml"
COMPOSE_FILE    = ROOT_DIR / "docker-compose.yml"

console = Console()
app     = typer.Typer(help="CamTrap Verify — herramienta de gestión.")


# ── Enums ─────────────────────────────────────────────────────────────────────

class ServeMode(str, Enum):
    dev   = "dev"
    prod  = "prod"
    debug = "debug"

class TestSuite(str, Enum):
    unit        = "unit"
    integration = "integration"
    all         = "all"

class PackageFormat(str, Enum):
    deb      = "deb"
    rpm      = "rpm"
    appimage = "appimage"
    windows  = "windows"
    all      = "all"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _exe(tool: str) -> str:
    """Full path of an executable on PATH, or the bare name if not found.

    On Windows ``npm`` is ``npm.cmd``, which subprocess can't start by its bare
    name; resolving it through ``shutil.which`` works on every OS.
    """
    return shutil.which(tool) or tool


def _run(*args: str, cwd: Path | None = None, env: dict | None = None) -> None:
    result = subprocess.run([_exe(args[0]), *args[1:]], cwd=cwd, env=env)
    if result.returncode != 0:
        raise typer.Exit(result.returncode)


def _pdf_env() -> dict:
    return {**os.environ, "ENABLE_PDF_EXPORT": "1"}


def _require(tool: str, hint: str) -> None:
    if shutil.which(tool) is None:
        console.print(f"[red]✘  '{tool}' no encontrado.[/red]  {hint}")
        raise typer.Exit(1)


def _npm(*args: str) -> None:
    _require("npm", "Instala Node.js desde https://nodejs.org/ (v18+)")
    _run("npm", *args, cwd=FRONTEND_DIR)


# ── docs (manuales de usuario y desarrollador) ───────────────────────────────

docs_app = typer.Typer(help="Genera o sirve los manuales de usuario y desarrollador.")
app.add_typer(docs_app, name="docs")


@docs_app.command("serve")
def docs_serve(
    port: int = typer.Option(8080, "--port", "-p", help="Puerto del servidor de documentación."),
) -> None:
    """Sirve los manuales en local con recarga automática (http://127.0.0.1:<port>)."""
    console.print(f"[green]Documentación en http://127.0.0.1:{port}[/green]\n")
    _run("mkdocs", "serve", "--config-file", str(ROOT_MKDOCS_CFG),
         "--dev-addr", f"127.0.0.1:{port}", cwd=ROOT_DIR)


@docs_app.command("build")
def docs_build() -> None:
    """Genera el sitio estático de los manuales en site/."""
    console.print("[green]Generando manuales...[/green]")
    _run("mkdocs", "build", "--config-file", str(ROOT_MKDOCS_CFG), cwd=ROOT_DIR)
    console.print(f"[green]✔  Sitio generado en {ROOT_DIR / 'site'}[/green]")


@docs_app.command("pdf")
def docs_pdf() -> None:
    """Genera un PDF con los manuales (site/pdf/camtrap-verify.pdf)."""
    console.print("[green]Generando PDF (WeasyPrint)...[/green]")
    _run("mkdocs", "build", "--config-file", str(ROOT_MKDOCS_CFG), cwd=ROOT_DIR, env=_pdf_env())
    console.print(f"[green]✔  PDF generado en {ROOT_DIR / 'site' / 'pdf' / 'camtrap-verify.pdf'}[/green]")


# ── backend ───────────────────────────────────────────────────────────────────

backend_app = typer.Typer(help="Gestiona el backend FastAPI.")
app.add_typer(backend_app, name="backend")

backend_docs_app = typer.Typer(help="Genera o sirve la documentación del backend.")
backend_app.add_typer(backend_docs_app, name="docs")


@backend_app.command("serve")
def backend_serve(
    mode: ServeMode = typer.Argument(ServeMode.dev, help="Modo de ejecución."),
    port: int       = typer.Option(None, "--port", "-p", help="Puerto de escucha (por defecto: CAMTRAP_PORT o 8765)."),
) -> None:
    """Arranca el servidor FastAPI en el modo indicado."""
    effective_port = port or settings.port
    console.print(Panel(
        f"[bold]Modo:[/bold] {mode.value}   [bold]Puerto:[/bold] {effective_port}",
        title="CamTrap Verify — backend",
    ))

    if mode == ServeMode.dev:
        console.print(f"  API:     http://localhost:{effective_port}")
        console.print(f"  Swagger: http://localhost:{effective_port}/docs\n")
        _run("uvicorn", "main:app", "--reload", "--port", str(effective_port),
             "--log-level", settings.log_level.lower(), "--app-dir", "src",
             cwd=BACKEND_DIR)

    elif mode == ServeMode.prod:
        console.print(f"  API:     http://localhost:{effective_port}\n")
        _run("uvicorn", "main:app", "--port", str(effective_port),
             "--log-level", "warning", "--workers", "2", "--app-dir", "src",
             cwd=BACKEND_DIR)

    elif mode == ServeMode.debug:
        _require("debugpy", "Instala con: uv sync --group dev")
        console.print(f"  API:      http://localhost:{effective_port}")
        console.print(f"  Swagger:  http://localhost:{effective_port}/docs")
        console.print("  Debugger: localhost:5678\n")
        _run(
            sys.executable, "-m", "debugpy", "--listen", "5678",
            "-m", "uvicorn", "main:app", "--port", str(effective_port),
            "--log-level", "debug", "--app-dir", "src",
            cwd=BACKEND_DIR,
        )


@backend_app.command("test")
def backend_test(
    suite:   TestSuite  = typer.Argument(TestSuite.all, help="Suite a ejecutar."),
    verbose: bool       = typer.Option(False, "--verbose", "-v", help="Salida detallada (-v de pytest)."),
    keyword: str | None = typer.Option(None, "--keyword", "-k", help="Filtro de tests por nombre (-k de pytest)."),
) -> None:
    """Ejecuta los tests del backend con pytest."""
    paths = {
        TestSuite.unit:        ["tests/unit/"],
        TestSuite.integration: ["tests/integration/"],
        TestSuite.all:         ["tests/unit/", "tests/integration/"],
    }[suite]

    console.print(Panel(
        f"[bold]Suite:[/bold] {suite.value}   [bold]Paths:[/bold] {', '.join(paths)}",
        title="CamTrap Verify — backend tests",
    ))

    cmd = [sys.executable, "-m", "pytest", *paths]
    if verbose:
        cmd.append("-v")
    if keyword:
        cmd.extend(["-k", keyword])

    _run(*cmd, cwd=BACKEND_DIR)


@backend_docs_app.command("serve")
def backend_docs_serve(
    port: int = typer.Option(8000, "--port", "-p", help="Puerto del servidor de documentación."),
) -> None:
    """Sirve la documentación del backend en local con recarga automática."""
    console.print(f"[green]Documentación en http://127.0.0.1:{port}[/green]\n")
    _run("mkdocs", "serve", "--config-file", str(MKDOCS_CFG), "--dev-addr", f"127.0.0.1:{port}",
         cwd=BACKEND_DIR)


@backend_docs_app.command("build")
def backend_docs_build() -> None:
    """Genera el sitio estático de la documentación del backend en backend/site/."""
    console.print("[green]Generando documentación del backend...[/green]")
    _run("mkdocs", "build", "--config-file", str(MKDOCS_CFG), cwd=BACKEND_DIR)
    console.print(f"[green]✔  Sitio generado en {BACKEND_DIR / 'site'}[/green]")


@backend_docs_app.command("pdf")
def backend_docs_pdf() -> None:
    """Genera un PDF con la documentación del backend."""
    console.print("[green]Generando PDF (WeasyPrint)...[/green]")
    _run("mkdocs", "build", "--config-file", str(MKDOCS_CFG), cwd=BACKEND_DIR, env=_pdf_env())
    console.print(f"[green]✔  PDF generado en {BACKEND_DIR / 'site' / 'pdf' / 'camtrap-verify-backend.pdf'}[/green]")


# ── frontend ──────────────────────────────────────────────────────────────────

frontend_app = typer.Typer(help="Gestiona el frontend React (npm).")
app.add_typer(frontend_app, name="frontend")

frontend_docs_app = typer.Typer(help="Genera o sirve la documentación del frontend.")
frontend_app.add_typer(frontend_docs_app, name="docs")


@frontend_app.command("dev")
def frontend_dev(
    port: int = typer.Option(5173, "--port", "-p", help="Puerto del servidor de desarrollo."),
) -> None:
    """Arranca el servidor de desarrollo Vite (hot-reload)."""
    console.print(Panel(
        f"[bold]Frontend:[/bold] http://localhost:{port}",
        title="CamTrap Verify — frontend dev",
    ))
    _npm("run", "dev", "--", "--port", str(port))


@frontend_app.command("build")
def frontend_build() -> None:
    """Compila el frontend para producción → frontend/dist/."""
    console.print("[green]Compilando frontend...[/green]")
    _npm("run", "build")
    console.print(f"[green]✔  Build en {FRONTEND_DIR / 'dist'}[/green]")


@frontend_app.command("preview")
def frontend_preview(
    port: int = typer.Option(4173, "--port", "-p", help="Puerto del servidor de preview."),
) -> None:
    """Sirve el build de producción localmente."""
    console.print(Panel(
        f"[bold]Preview:[/bold] http://localhost:{port}",
        title="CamTrap Verify — frontend preview",
    ))
    _npm("run", "preview", "--", "--port", str(port))


@frontend_app.command("test")
def frontend_test(
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Salida detallada."),
) -> None:
    """Ejecuta los tests del frontend."""
    pkg = json.loads((FRONTEND_DIR / "package.json").read_text())
    if "test" not in pkg.get("scripts", {}):
        console.print("[yellow]⚠  No hay script 'test' configurado en package.json.[/yellow]")
        console.print("   Añade un test runner (ej. vitest) y define el script 'test'.")
        raise typer.Exit(1)
    console.print(Panel("Ejecutando tests del frontend...", title="CamTrap Verify — frontend tests"))
    _npm("run", "test")


@frontend_app.command("lint")
def frontend_lint() -> None:
    """Ejecuta oxlint sobre el código fuente."""
    console.print("[green]Linting...[/green]")
    _npm("run", "lint")


@frontend_docs_app.command("serve")
def frontend_docs_serve(
    port: int = typer.Option(8100, "--port", "-p", help="Puerto del servidor de documentación."),
) -> None:
    """Sirve la documentación del frontend con recarga automática."""
    console.print(f"[green]Documentación frontend en http://127.0.0.1:{port}[/green]\n")
    _run("mkdocs", "serve", "--config-file", str(FRONTEND_MKDOCS),
         "--dev-addr", f"127.0.0.1:{port}", cwd=FRONTEND_DIR)


@frontend_docs_app.command("build")
def frontend_docs_build() -> None:
    """Genera el sitio estático en frontend/site/."""
    console.print("[green]Generando documentación del frontend...[/green]")
    _run("mkdocs", "build", "--config-file", str(FRONTEND_MKDOCS), cwd=FRONTEND_DIR)
    console.print(f"[green]✔  Sitio generado en {FRONTEND_DIR / 'site'}[/green]")


@frontend_docs_app.command("pdf")
def frontend_docs_pdf() -> None:
    """Genera un PDF con la documentación del frontend."""
    console.print("[green]Generando PDF (WeasyPrint)...[/green]")
    _run("mkdocs", "build", "--config-file", str(FRONTEND_MKDOCS), cwd=FRONTEND_DIR, env=_pdf_env())
    console.print(f"[green]✔  PDF generado en {FRONTEND_DIR / 'site' / 'pdf' / 'camtrap-verify-frontend.pdf'}[/green]")


# ── dev (backend + frontend juntos) ──────────────────────────────────────────

@app.command()
def dev(
    backend_port:  int = typer.Option(None,  "--backend-port",  "-b", help="Puerto del backend (por defecto: CAMTRAP_PORT o 8765)."),
    frontend_port: int = typer.Option(5173,  "--frontend-port", "-f", help="Puerto del frontend Vite."),
) -> None:
    """Arranca backend y frontend simultáneamente en modo desarrollo."""
    _require("npm", "Instala Node.js desde https://nodejs.org/ (v18+)")
    effective_port = backend_port or settings.port

    console.print(Panel(
        f"  [bold]Backend:[/bold]  http://localhost:{effective_port}\n"
        f"  [bold]Frontend:[/bold] http://localhost:{frontend_port}\n"
        f"  [bold]API docs:[/bold] http://localhost:{effective_port}/docs\n\n"
        f"  Ctrl+C para detener ambos procesos.",
        title="CamTrap Verify — dev",
    ))

    backend_proc = subprocess.Popen(
        [_exe("uvicorn"), "main:app", "--reload", "--port", str(effective_port),
         "--log-level", settings.log_level.lower(), "--app-dir", "src"],
        cwd=BACKEND_DIR,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )
    frontend_proc = subprocess.Popen(
        [_exe("npm"), "run", "dev", "--", "--port", str(frontend_port)],
        cwd=FRONTEND_DIR,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )

    def _stream(proc: subprocess.Popen, label: str, color: str) -> None:
        for raw in iter(proc.stdout.readline, b""):
            line = raw.decode(errors="replace").rstrip()
            if line:
                console.print(f"[{color}][{label}][/{color}] {line}")

    threads = [
        threading.Thread(target=_stream, args=(backend_proc,  "backend",  "cyan"),  daemon=True),
        threading.Thread(target=_stream, args=(frontend_proc, "frontend", "green"), daemon=True),
    ]
    for t in threads:
        t.start()

    try:
        backend_proc.wait()
        frontend_proc.wait()
    except KeyboardInterrupt:
        console.print("\n[yellow]Deteniendo...[/yellow]")
        for proc in (backend_proc, frontend_proc):
            proc.terminate()
        for proc in (backend_proc, frontend_proc):
            proc.wait()
        console.print("[yellow]✔  Parado.[/yellow]")


# ── package ───────────────────────────────────────────────────────────────────

package_app = typer.Typer(help="Construye paquetes de distribución (.deb / .rpm / .AppImage / .exe).")
app.add_typer(package_app, name="package")


def _get_version(version: str | None) -> str:
    if version:
        return version
    result = subprocess.run(
        ["git", "describe", "--tags", "--exact-match"],
        capture_output=True, text=True, cwd=ROOT_DIR,
    )
    if result.returncode == 0:
        return result.stdout.strip().lstrip("v")
    return "0.0.0~dev"


def _build_linux_package(fmt: str, version: str) -> None:
    _require("docker", "Instala Docker desde https://docs.docker.com/get-docker/")
    DIST_DIR.mkdir(parents=True, exist_ok=True)
    console.print(f"[green]Construyendo paquete [bold]{fmt}[/bold] v{version}...[/green]")
    _run(
        "docker", "build",
        "--build-arg", f"APP_VERSION={version}",
        "--build-arg", f"PKG_FORMAT={fmt}",
        "-f", str(BACKEND_DIR / "Dockerfile.build"),
        "--output", f"type=local,dest={DIST_DIR}",
        str(ROOT_DIR),
    )
    pkgs = list(DIST_DIR.glob(f"*.{fmt}"))
    if not pkgs:
        console.print(f"[red]✘  No se encontró ningún paquete .{fmt} en {DIST_DIR}[/red]")
        raise typer.Exit(1)
    for pkg in pkgs:
        size = pkg.stat().st_size // 1024
        console.print(f"[green]✔  {pkg}  ({size} KB)[/green]")
    install_cmd = "sudo dnf localinstall" if fmt == "rpm" else "sudo apt install"
    console.print(f"\nPara instalar:  {install_cmd} {pkgs[0]}")


def _build_appimage(version: str) -> None:
    if sys.platform == "win32":
        console.print("[red]✘  AppImage solo se puede construir en Linux.[/red]")
        raise typer.Exit(1)
    _require("docker", "Instala Docker desde https://docs.docker.com/get-docker/")
    _require("appimagetool", "Descarga appimagetool desde https://appimage.github.io/appimagetool/")
    DIST_DIR.mkdir(parents=True, exist_ok=True)
    console.print(f"[green]Construyendo AppImage v{version}...[/green]")

    # Build binary via Docker
    _run(
        "docker", "build",
        "--build-arg", f"APP_VERSION={version}",
        "--build-arg", "PKG_FORMAT=none",
        "-f", str(BACKEND_DIR / "Dockerfile.build"),
        "--output", f"type=local,dest={DIST_DIR}",
        str(ROOT_DIR),
    )

    binary_dir = DIST_DIR / "binary"
    if not binary_dir.exists():
        console.print("[red]✘  No se encontró el binario en dist/binary/[/red]")
        raise typer.Exit(1)

    import shutil, tempfile
    with tempfile.TemporaryDirectory() as tmp:
        app_dir = Path(tmp) / "AppDir"
        app_dir.mkdir()
        shutil.copytree(binary_dir, app_dir / "camtrap-verify")
        (app_dir / "AppRun").write_text(
            '#!/bin/sh\nexec "$(dirname "$0")/camtrap-verify/camtrap-verify" "$@"\n'
        )
        (app_dir / "AppRun").chmod(0o755)
        (app_dir / "camtrap-verify.desktop").write_text(
            "[Desktop Entry]\nType=Application\nName=CamTrap Verify\n"
            "Exec=camtrap-verify\nIcon=camtrap-verify\nTerminal=false\nCategories=Science;\n"
        )
        icon_src = ROOT_DIR / "docs" / "img" / "WildINTEL_onlyCircle_25.png"
        if icon_src.exists():
            shutil.copy(icon_src, app_dir / "camtrap-verify.png")
        out = DIST_DIR / f"camtrap-verify-{version}-linux-x86_64.AppImage"
        _run("appimagetool", str(app_dir), str(out), env={**os.environ, "ARCH": "x86_64"})
        size = out.stat().st_size // (1024 * 1024)
        console.print(f"[green]✔  {out}  ({size} MB)[/green]")


def _build_windows_package(version: str) -> None:
    if sys.platform == "win32":
        _build_windows_native(version)
    else:
        _build_windows_docker(version)


def _build_windows_native(version: str) -> None:
    console.print("[green]Construyendo .exe nativo para Windows...[/green]")
    DIST_DIR.mkdir(parents=True, exist_ok=True)
    _run(
        sys.executable, "-m", "PyInstaller", "src/app_entry.py",
        "--onefile",
        "--name", "camtrap-verify",
        "--add-data", "src/camtrap_workflow.py;.",
        "--add-data", "src/settings.py;.",
        "--hidden-import", "uvicorn.logging",
        "--hidden-import", "uvicorn.loops.auto",
        "--hidden-import", "uvicorn.protocols.http.auto",
        "--hidden-import", "uvicorn.protocols.websockets.auto",
        "--hidden-import", "uvicorn.lifespan.on",
        "--hidden-import", "anyio._backends._asyncio",
        "--hidden-import", "pandas",
        "--hidden-import", "pydantic_settings",
        "--collect-submodules", "fastapi",
        "--collect-submodules", "starlette",
        cwd=BACKEND_DIR,
    )
    src_exe = BACKEND_DIR / "dist" / "camtrap-verify.exe"
    dst = DIST_DIR / f"camtrap-verify-{version}-windows-x64.exe"
    dst.write_bytes(src_exe.read_bytes())
    size = dst.stat().st_size // (1024 * 1024)
    console.print(f"[green]✔  {dst}  ({size} MB)[/green]")


def _build_windows_docker(version: str) -> None:
    _require("docker", "Instala Docker desde https://docs.docker.com/get-docker/")
    DIST_DIR.mkdir(parents=True, exist_ok=True)
    console.print("[green]Construyendo .exe para Windows vía Docker + Wine...[/green]")
    console.print("[yellow]  (primera vez puede tardar varios minutos descargando Wine)[/yellow]")
    _run(
        "docker", "build",
        "--build-arg", f"APP_VERSION={version}",
        "-f", str(BACKEND_DIR / "Dockerfile.build.windows"),
        "--output", f"type=local,dest={DIST_DIR}",
        str(BACKEND_DIR),
    )
    exes = list(DIST_DIR.glob("*.exe"))
    if not exes:
        console.print(f"[red]✘  No se encontró ningún .exe en {DIST_DIR}[/red]")
        raise typer.Exit(1)
    for exe in exes:
        size = exe.stat().st_size // (1024 * 1024)
        console.print(f"[green]✔  {exe}  ({size} MB)[/green]")


@package_app.command("build")
def package_build(
    fmt: PackageFormat = typer.Option(PackageFormat.all, "--format", "-f",
                                       help="Formato: deb, rpm, windows, all."),
    version: str | None = typer.Option(None, "--version", "-v",
                                        help="Versión (por defecto: git tag o 0.0.0~dev)."),
) -> None:
    """Construye paquetes de distribución usando Docker."""
    v = _get_version(version)
    console.print(Panel(f"[bold]Versión:[/bold] {v}   [bold]Formato:[/bold] {fmt.value}",
                        title="CamTrap Verify — package"))
    if fmt == PackageFormat.windows:
        _build_windows_package(v)
    elif fmt == PackageFormat.appimage:
        _build_appimage(v)
    elif fmt == PackageFormat.all:
        for f in ["deb", "rpm"]:
            _build_linux_package(f, v)
        _build_appimage(v)
        _build_windows_package(v)
    else:
        _build_linux_package(fmt.value, v)


# ── prod ──────────────────────────────────────────────────────────────────────

prod_app = typer.Typer(help="Gestiona el entorno de producción con Docker Compose.")
app.add_typer(prod_app, name="prod")


def _compose(*args: str) -> None:
    _require("docker", "Instala Docker desde https://docs.docker.com/get-docker/")
    _run("docker", "compose", "-f", str(COMPOSE_FILE), *args, cwd=ROOT_DIR)


@prod_app.command("up")
def prod_up(
    detach: bool = typer.Option(True,  "--detach/--no-detach", "-d/-D", help="Ejecutar en segundo plano."),
    build:  bool = typer.Option(True,  "--build/--no-build",           help="Reconstruir imágenes antes de arrancar."),
) -> None:
    """Arranca backend + frontend + Caddy en modo producción."""
    console.print(Panel(
        "  [bold]App:[/bold]  http://localhost\n"
        "  [bold]API:[/bold]  http://localhost/api\n\n"
        "  Para detener:  uv run cli prod down",
        title="CamTrap Verify — producción",
    ))
    cmd: list[str] = ["up"]
    if build:
        cmd.append("--build")
    if detach:
        cmd.append("-d")
    _compose(*cmd)
    if detach:
        console.print("[green]✔  Servicios arrancados en segundo plano.[/green]")


@prod_app.command("down")
def prod_down(
    volumes: bool = typer.Option(False, "--volumes", "-v", help="Eliminar también los volúmenes persistentes."),
) -> None:
    """Detiene y elimina los contenedores del entorno de producción."""
    cmd: list[str] = ["down"]
    if volumes:
        cmd.append("--volumes")
    _compose(*cmd)
    console.print("[green]✔  Entorno de producción detenido.[/green]")


@prod_app.command("logs")
def prod_logs(
    service: str | None = typer.Argument(None,  help="Servicio concreto: backend, frontend o caddy."),
    follow:  bool       = typer.Option(False, "--follow", "-f", help="Seguir los logs en tiempo real."),
) -> None:
    """Muestra los logs de los servicios de producción."""
    cmd: list[str] = ["logs"]
    if follow:
        cmd.append("-f")
    if service:
        cmd.append(service)
    _compose(*cmd)


# ── entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app()
