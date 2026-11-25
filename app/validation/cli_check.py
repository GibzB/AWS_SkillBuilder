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

__all__ = [
    "CliIssue",
    "UNVERIFIABLE_KINDS",
    "extract_aws_commands",
    "classify_command",
    "check_command",
    "check_article_commands",
]

_SHELL_LANGS = {"bash", "sh", "shell", "zsh", "console", "terminal", ""}

# CLI service names that differ from botocore's.
_SERVICE_ALIASES = {"s3api": "s3", "configservice": "config"}

# High-level / customised CLI commands that are not plain botocore operations.
_CUSTOM_COMMANDS: dict[str, set[str]] = {
    "s3": {"ls", "cp", "mv", "rm", "sync", "mb", "rb", "presign", "website"},
    "cloudformation": {"deploy", "package"},
    "logs": {"tail", "start-live-tail"},
    "configure": {"get", "set", "list", "list-profiles"},
    "eks": {"update-kubeconfig", "get-token"},
    "ecr": {"get-login-password", "get-login"},
    "rds": {"generate-db-auth-token"},
    "cloudtrail": {"validate-logs"},
    "emr": {"create-cluster", "add-steps", "terminate-clusters", "ssh", "put", "get", "socks"},
    "codeartifact": {"login"},
}

# CLI services that are entirely client-side customizations (no botocore model).
_CUSTOM_SERVICES = {"configure", "deploy", "history", "help"}

# Options the AWS CLI adds on top of the API model for specific commands. They
# live in the CLI's own "customizations", not in botocore, so the API model alone
# would wrongly reject them. Keyed by (botocore service, cli command).
_SECGROUP_FLAGS = {"protocol", "port", "cidr", "source-group", "group-owner"}
_EXTRA_FLAGS: dict[tuple[str, str], set[str]] = {
    ("lambda", "create-function"): {"zip-file"},
    ("ec2", "run-instances"): {"count"},
    ("ec2", "authorize-security-group-ingress"): _SECGROUP_FLAGS,
    ("ec2", "authorize-security-group-egress"): _SECGROUP_FLAGS,
    ("ec2", "revoke-security-group-ingress"): _SECGROUP_FLAGS,
    ("ec2", "revoke-security-group-egress"): _SECGROUP_FLAGS,
}

# Required API parameters that a customization lets you satisfy another way:
# {(service, command): {required option: options that can replace it}}.
_REQUIRED_ALTERNATIVES: dict[tuple[str, str], dict[str, set[str]]] = {
    ("lambda", "create-function"): {"code": {"zip-file"}},
    ("ec2", "run-instances"): {"min-count": {"count"}, "max-count": {"count"}},
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
    """A problem with one command: the command text, a short reason and its kind."""

    command: str
    reason: str
    kind: str = ""


def _quotes_balanced(text: str) -> bool:
    """True if every quote in ``text`` is closed (so a command is complete)."""
    try:
        shlex.split(text, comments=True)
    except ValueError:
        return False
    return True


def extract_aws_commands(markdown: str) -> list[str]:
    """Return every ``aws ...`` command from shell-style fenced code blocks.

    Handles ``$ `` prompts, backslash line continuations, ``NAME=$(aws ...)``
    captures, and arguments quoted across several lines (for example a JSON
    ``--change-batch '...'`` written over many lines): such a command keeps
    absorbing lines until its quotes close. A quote that never closes is returned
    as-is so the checker reports it as unparseable.
    """
    commands: list[str] = []
    for match in re.finditer(r"```([^\n`]*)\n(.*?)```", markdown, re.DOTALL):
        lang = match.group(1).strip().lower()
        if lang not in _SHELL_LANGS:
            continue
        joined = re.sub(r"\\\s*\n", " ", match.group(2))
        pending = ""
        for raw in joined.splitlines():
            if pending:
                pending += "\n" + raw
                if _quotes_balanced(pending):
                    commands.append(pending)
                    pending = ""
                continue
            line = re.sub(r"^[$>]\s*", "", raw.strip())
            # Capture form: NAME=$(aws ... --query X --output text)
            captured = re.match(r"^[A-Za-z_]\w*=\"?\$\((aws .*)\)\"?\s*$", line)
            if captured:
                line = captured.group(1)
            if not line.startswith("aws "):
                continue
            if _quotes_balanced(line):
                commands.append(line)
            else:
                pending = line
        if pending:
            commands.append(pending)
    return commands


# Kinds that mean "botocore here may simply be older than the command": the
# command cannot be verified, which is not proof that it is wrong.
UNVERIFIABLE_KINDS = frozenset({"unknown_service", "unknown_operation", "unknown_option"})


def classify_command(command: str) -> tuple[str, str] | None:
    """Return ``(kind, reason)`` if the command fails verification, else ``None``.

    ``unknown_service``, ``unknown_operation`` and ``unknown_option`` are
    *unverifiable* (the checker's botocore may predate the command);
    ``parse``, ``shape`` and ``missing_required`` are hard failures.
    """
    try:
        tokens = shlex.split(command, comments=True)
    except ValueError as error:
        return ("parse", f"cannot parse command ({error})")
    if len(tokens) >= 2 and tokens[0] == "aws" and tokens[1] in _CUSTOM_SERVICES:
        return None
    if len(tokens) < 3 or tokens[0] != "aws":
        return ("shape", "expected 'aws <service> <command>'")

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
        return ("unknown_service", f"unknown service '{service_cli}'")
    model = session.get_service_model(service)

    operations = {xform_name(name, "-"): name for name in model.operation_names}
    if op_cli not in operations:
        return ("unknown_operation", f"'{service_cli}' has no command '{op_cli}'")
    shape = model.operation_model(operations[op_cli]).input_shape

    members = shape.members if shape is not None else {}
    valid = {xform_name(m, "-") for m in members}
    valid |= {f"no-{name}" for name in valid}  # boolean negations
    valid |= _EXTRA_FLAGS.get((service, op_cli), set())
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
                return ("unknown_option", f"'{op_cli}' has no option '--{flag}'")
            given.add(flag)

    skeleton = {"cli-input-json", "cli-input-yaml", "generate-cli-skeleton"} & given
    if not skeleton:
        alternatives = _REQUIRED_ALTERNATIVES.get((service, op_cli), {})
        missing = sorted(
            name
            for name in required - given
            if not (alternatives.get(name, set()) & given)
        )
        if missing:
            return (
                "missing_required",
                "missing required option(s): " + ", ".join(f"--{m}" for m in missing),
            )
    return None


def check_command(command: str) -> str | None:
    """Return a reason the command is invalid, or ``None`` if it checks out."""
    classified = classify_command(command)
    return None if classified is None else classified[1]


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


# A 12-digit run is an AWS account ID (ARNs, queue URLs, ECR registries, ...).
# Articles use dummy ones such as 123456789012 that a reader cannot reproduce.
_ACCOUNT_ARN = re.compile(r"(?<!\d)\d{12}(?!\d)")
# Made-up resource IDs that models write as filler: a run of 123456789 (security
# groups, instances, AMIs, hosted zones), the alphabet-looking ABCDEFGHIJ, or the
# word EXAMPLE. A reader has no such resource, so the command cannot run.
_DUMMY_ID = re.compile(r"123456789|ABCDEFGHIJ|abcdef0123|EXAMPLE")


def find_unreproducible_commands(markdown: str) -> list[str]:
    """Return commands a beginner cannot run as written.

    Two patterns make an example impossible to reproduce even when the command is
    syntactically valid: an ARN containing a 12-digit account ID (the model writes
    a dummy one such as 123456789012, so the reader must guess the real value) and
    a ``fileb://`` binary package (a zip cannot be shown in the article). Articles
    should use read-only commands, or commands whose inputs are all shown.
    """
    return [
        command
        for command in extract_aws_commands(markdown)
        if _ACCOUNT_ARN.search(command)
        or _DUMMY_ID.search(command)
        or "fileb://" in command
    ]


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
        CliIssue(cmd, classified[1], classified[0])
        for cmd in commands
        if (classified := classify_command(cmd)) is not None
    ]
    return len(commands), issues
