"""Persistent shell integration owned by the Nova CLI."""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass
from pathlib import Path

from ..core import config

START = "# >>> NovaVisor shell integration >>>"
END = "# <<< NovaVisor shell integration <<<"
HEADER = "# Managed by NovaVisor; remove with `nova completion uninstall`."
_BLOCK = re.compile(rf"(?ms)^{re.escape(START)}\n.*?^{re.escape(END)}\n?")


@dataclass(frozen=True)
class Change:
    action: str
    path: Path
    detail: str


def _completion_path(shell: str) -> Path:
    home = Path.home()
    if shell == "zsh":
        return home / ".zfunc" / "_nova"
    if shell == "bash":
        return home / ".bash_completions" / "nova.sh"
    if shell == "fish":
        return home / ".config" / "fish" / "completions" / "nova.fish"
    return home / ".local" / "share" / "novavisor" / "completion" / "nova.ps1"


def _startup_path(shell: str) -> Path:
    home = Path.home()
    if shell == "zsh":
        return home / ".zshrc"
    if shell == "bash":
        return home / ".bashrc"
    if shell == "fish":
        return home / ".config" / "fish" / "conf.d" / "nova.fish"
    return home / ".config" / "powershell" / "Microsoft.PowerShell_profile.ps1"


def _shell_quote(value: Path) -> str:
    return shlex.quote(str(value))


def _powershell_quote(value: Path) -> str:
    escaped = str(value).replace("'", "''")
    return f"'{escaped}'"


def _integration_block(shell: str, completion: Path) -> str:
    scripts = config.SCRIPTS
    if shell in {"bash", "zsh"}:
        lines = [
            START,
            f"_novavisor_scripts={_shell_quote(scripts)}",
            'case ":${PATH}:" in',
            '    *":${_novavisor_scripts}:"*) ;;',
            '    *) export PATH="${_novavisor_scripts}:${PATH}" ;;',
            "esac",
            "unset _novavisor_scripts",
        ]
        if shell == "zsh":
            lines.extend(
                (
                    "if ! (( $+functions[compdef] )); then",
                    "    autoload -Uz compinit",
                    "    compinit",
                    "fi",
                )
            )
        lines.extend((f"source {_shell_quote(completion)}", END))
        return "\n".join(lines)
    if shell == "fish":
        return "\n".join(
            (
                START,
                f"set -l _novavisor_scripts {_shell_quote(scripts)}",
                "if not contains -- $_novavisor_scripts $PATH",
                "    set -gx PATH $_novavisor_scripts $PATH",
                "end",
                END,
            )
        )
    scripts_ps = _powershell_quote(scripts)
    return "\n".join(
        (
            START,
            f"$novaScripts = {scripts_ps}",
            "if (($env:PATH -split [IO.Path]::PathSeparator) -notcontains $novaScripts) {",
            "    $env:PATH = $novaScripts + [IO.Path]::PathSeparator + $env:PATH",
            "}",
            f". {_powershell_quote(completion)}",
            "Remove-Variable novaScripts",
            END,
        )
    )


def _without_legacy_lines(shell: str, content: str, completion: Path) -> str:
    lines = {
        f"source {_shell_quote(completion)}",
        f"source '{completion}'",
    }
    if shell == "zsh":
        lines.add("fpath+=~/.zfunc; autoload -Uz compinit; compinit")
    return "".join(
        line for line in content.splitlines(keepends=True)
        if line.rstrip("\r\n") not in lines
    )


def _without_integration(shell: str, content: str, completion: Path) -> str:
    return _without_legacy_lines(shell, _BLOCK.sub("", content), completion)


def _with_integration(shell: str, content: str, completion: Path) -> str:
    content = _without_integration(shell, content, completion)
    if content and not content.endswith("\n"):
        content += "\n"
    if content and not content.endswith("\n\n"):
        content += "\n"
    return f"{content}{_integration_block(shell, completion)}\n"


def _write(path: Path, content: str, detail: str) -> Change:
    previous = path.read_text() if path.is_file() else None
    if previous == content:
        return Change("unchanged", path, detail)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    return Change("created" if previous is None else "updated", path, detail)


def install(shell: str, script: str) -> tuple[Change, Change]:
    completion = _completion_path(shell)
    startup = _startup_path(shell)
    completion_change = _write(
        completion,
        f"{HEADER}\n{script.rstrip()}\n",
        f"{shell} completion script",
    )
    startup_content = startup.read_text() if startup.is_file() else ""
    startup_change = _write(
        startup,
        _with_integration(shell, startup_content, completion),
        "PATH registration and completion startup",
    )
    return completion_change, startup_change


def _owned_completion(content: str) -> bool:
    return content.startswith(HEADER) or "_NOVA_COMPLETE=" in content


def _remove_completion(path: Path, shell: str) -> Change:
    if not path.is_file():
        return Change("absent", path, f"no {shell} completion script")
    if not _owned_completion(path.read_text()):
        return Change("preserved", path, "file is not managed by NovaVisor")
    path.unlink()
    return Change("removed", path, f"{shell} completion script")


def _remove_startup(path: Path, shell: str, completion: Path) -> Change:
    if not path.is_file():
        return Change("absent", path, "no shell startup integration")
    content = path.read_text()
    cleaned = _without_integration(shell, content, completion)
    if cleaned == content:
        return Change("unchanged", path, "no managed shell startup integration")
    if cleaned.strip():
        path.write_text(cleaned)
        return Change("updated", path, "removed PATH and completion startup")
    path.unlink()
    return Change("removed", path, "removed managed shell startup file")


def uninstall(shell: str) -> tuple[Change, Change]:
    completion = _completion_path(shell)
    startup = _startup_path(shell)
    return (
        _remove_completion(completion, shell),
        _remove_startup(startup, shell, completion),
    )
