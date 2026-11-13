"""Offline verification of AWS CLI commands found in a generated article.

An article that teaches an AWS service should contain a command the reader can
actually run. Models sometimes invent operations or flags, so every ``aws``
command in the article's shell code blocks is checked against botocore's own
service definitions (the same data the AWS CLI is built from):

* the service exists,
* the operation exists on that service,
* every ``--flag`` is a real parameter of that operation (or a global option),
* every REQUIRED parameter is present.

This is schema verification, not execution: it proves the command is
well-formed for the real API without needing credentials or touching any
account. It never runs the command.
"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass

from botocore import xform_name
from botocore.session import get_session

__all__ = ["CliIssue", "extract_aws_commands", "check_command", "check_article_commands"]

_SHELL_LANGS = {"bash", "sh", "shell", "zsh", "console", "terminal", ""}

# CLI service names that differ from botocore's.
_SERVICE_ALIASES = {"s3api": "s3", "configservice": "config"}

# High-level / customised CLI commands that are not plain botocore operations.
_CUSTOM_COMMANDS: dict[str, set[str]] = {
    "s3": {"ls", "cp", "mv", "rm", "sync", "mb", "rb", "presign", "website"},
    "cloudformation": {"deploy", "package"},
    "logs": {"tail", "start-live-tail"},
    "configure": {"get", "set", "list", "list-profiles"},
}

# Options accepted on every command.
_GLOBAL_FLAGS = {
    "region", "profile", "output", "query", "endpoint-url", "debug", "no-verify-ssl",
    "no-paginate", "paginate", "max-items", "starting-token", "page-size", "color",
    "no-sign-request", "ca-bundle", "cli-read-timeout", "cli-connect-timeout",
    "no-cli-pager", "cli-auto-prompt", "no-cli-auto-prompt", "cli-input-json",
    "cli-input-yaml", "generate-cli-skeleton", "version", "help", "filters",
}


@dataclass(frozen=True)
class CliIssue:
    """A problem with one command: the command text and a short reason."""

    command: str
    reason: str


def extract_aws_commands(markdown: str) -> list[str]:
    """Return every ``aws ...`` command from shell-style fenced code blocks.

    Handles ``$ `` prompts and backslash line continuations.
    """
    commands: list[str] = []
    for match in re.finditer(r"```([^\n`]*)\n(.*?)```", markdown, re.DOTALL):
        lang = match.group(1).strip().lower()
        if lang not in _SHELL_LANGS:
            continue
        joined = re.sub(r"\\\s*\n", " ", match.group(2))
        for line in joined.splitlines():
            line = line.strip()
            line = re.sub(r"^[$>]\s*", "", line)
            if line.startswith("aws "):
                commands.append(line)
    return commands


def check_command(command: str) -> str | None:
    """Return a reason the command is invalid, or ``None`` if it checks out."""
    try:
        tokens = shlex.split(command, comments=True)
    except ValueError as error:
        return f"cannot parse command ({error})"
    if len(tokens) < 3 or tokens[0] != "aws":
        return "expected 'aws <service> <command>'"

    # Skip leading global flags between 'aws' and the service (rare).
    service_cli, op_cli = tokens[1], tokens[2]
    rest = tokens[3:]

    if service_cli in _CUSTOM_COMMANDS and op_cli in _CUSTOM_COMMANDS[service_cli]:
        return None
    if op_cli in {"wait", "help"}:
        return None

    service = _SERVICE_ALIASES.get(service_cli, service_cli)
    session = get_session()
    if service not in session.get_available_services():
        return f"unknown service '{service_cli}'"
    model = session.get_service_model(service)

    operations = {xform_name(name, "-"): name for name in model.operation_names}
    if op_cli not in operations:
        return f"'{service_cli}' has no command '{op_cli}'"
    shape = model.operation_model(operations[op_cli]).input_shape

    members = shape.members if shape is not None else {}
    valid = {xform_name(m, "-") for m in members}
    valid |= {f"no-{name}" for name in valid}  # boolean negations
    required = {xform_name(m, "-") for m in (shape.required_members if shape else [])}

    # Only '--flag' tokens are inspected. Values and the few trailing positional
    # arguments (for example the output file of 'lambda invoke') are ignored,
    # because telling a boolean flag from a value-taking one needs the CLI's own
    # argument parser and a false rejection would block a good article.
    given: set[str] = set()
    for token in rest:
        if token.startswith("--"):
            flag = token[2:].split("=", 1)[0]
            if flag not in valid and flag not in _GLOBAL_FLAGS:
                return f"'{op_cli}' has no option '--{flag}'"
            given.add(flag)

    skeleton = {"cli-input-json", "cli-input-yaml", "generate-cli-skeleton"} & given
    if not skeleton:
        missing = sorted(required - given)
        if missing:
            return "missing required option(s): " + ", ".join(f"--{m}" for m in missing)
    return None


def find_unshown_files(markdown: str) -> list[str]:
    """Return local files an ``aws`` command reads via ``file://`` but the article never shows.

    A reader can only reproduce a command like ``--template-body
    file://stack.json`` if the article also shows that file. The filename must
    therefore appear at least twice in the body: once in the command and at
    least once where the file is introduced (for example "Save this as
    ``stack.json``" above its code block). Binary ``fileb://`` inputs (such as a
    zip) cannot be shown as text and are not checked.
    """
    names: list[str] = []
    for command in extract_aws_commands(markdown):
        for match in re.finditer(r"file://([^\s'\"]+)", command):
            name = match.group(1).rsplit("/", 1)[-1]
            if name and name not in names:
                names.append(name)
    return [name for name in names if markdown.count(name) < 2]


_TERRAFORM_COMMAND = re.compile(
    r"(?m)^\s*(?:[$>]\s*)?terraform\s+(?:init|plan|apply|destroy)\b"
)
_TERRAFORM_CONFIG_FENCE = re.compile(r"```\s*(?:hcl|terraform|tf)\b", re.IGNORECASE)


def terraform_config_missing(markdown: str) -> bool:
    """True if the article runs ``terraform`` commands but shows no Terraform config."""
    return bool(_TERRAFORM_COMMAND.search(markdown)) and not _TERRAFORM_CONFIG_FENCE.search(
        markdown
    )


def check_article_commands(markdown: str) -> tuple[int, list[CliIssue]]:
    """Return (number of aws commands found, list of issues)."""
    commands = extract_aws_commands(markdown)
    issues = [
        CliIssue(cmd, reason)
        for cmd in commands
        if (reason := check_command(cmd)) is not None
    ]
    return len(commands), issues
