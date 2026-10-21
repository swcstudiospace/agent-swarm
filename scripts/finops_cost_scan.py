#!/usr/bin/env python3
"""A20 - scan IaC and Kubernetes manifests in a repository for cost smells.

Rules (each finding carries its `rule`):
  k8s-missing-requests       container without cpu or memory requests (major): the scheduler cannot bin-pack it and
                             allocation falls back to node share, unless a LimitRange or admission policy injects
                             defaults. A limit with no request counts as the request (Kubernetes copies it).
  k8s-missing-limits         container without a memory limit (minor); CPU limits are deliberately not required
  iac-oversized-instance     AWS instance, database or cache size >= --max-xlarge xlarge (minor), >= twice that (major);
                             any `metal` size is minor
  iac-previous-generation-instance  EC2 type of a family on the AWS previous-generation list, or cache.t2 (minor);
                             RDS db. classes are not judged (that page carries no such label)
  iac-previous-generation-volume    EBS standard (AWS's previous generation type), and gp2/io1 as newer-type advice
                             (minor); StorageClass `type` only under an AWS EBS provisioner
  iac-missing-lifecycle      S3 bucket without lifecycle rules, CloudWatch log group without retention, ECR repository
                             without a lifecycle policy (minor)
  iac-untagged-resource      taggable resource missing a --required-tags key (rules of finops_tag_coverage)

Reads Terraform (.tf, .tf.json), CloudFormation (.template: JSON when its text is valid JSON, otherwise YAML) and Kubernetes manifests (.json, and .yaml/.yml when PyYAML imports;
otherwise the YAML-dependent checks are reported as `skipped:tool-missing` and JSON/Terraform keep working). HCL is
read by a lexer for comments, heredocs and quoted strings with nested `${ }` templates, then regular expressions over
the resource blocks: variables, `count` and dynamic blocks are not evaluated, a module block is an incomplete input unless its local source directory was analysed, and a resource block whose
braces do not balance is reported under `skipped` (`terraform-resources`), never dropped silently.
Directories: .git, .hg, .svn, node_modules, .venv, venv, __pycache__, .tox, .mypy_cache, .swarm and .terraform are never
read and add no reason: they hold VCS metadata, dependency caches and tool state, not repository IaC. build, dist and vendor
are read like any other directory, because they can hold real IaC. A directory that cannot be listed, a symlinked
directory, and the file-count and size limits each add a reason, so the scan never drops input silently.
Top-level documents: Terraform JSON and CloudFormation both require a top-level object, and a Kubernetes manifest is an object
too. A JSON or YAML document whose top level is an array or a scalar is therefore not an IaC document: it is passed over with
no reason, so a JSON data file in a repository does not fail the gate. An empty YAML document holds no content and is passed
over the same way. A file that is only passed over is not counted in files_scanned, but it was read and found not to be IaC,
so it counts as analysed for the rule that a run with no analysed input is incomplete. A tree with no candidate files at all
is still incomplete.
A top-level array is passed over whatever it contains, because Terraform JSON and CloudFormation both require a top-level
object, so an array is not an IaC document of either kind: a CloudFormation mapping inside a top-level array is out of scope.
A .yaml or .yml file that PyYAML rejects is read as JSON when its text is valid JSON, as a .template is.
A top-level `module` key is a Terraform module block only in a file named *.tf.json, and only when its value has that shape: a mapping
with at least one entry that is a block (a mapping, or a one-element list holding one, with a string `source`). Each block entry is a
module block, reported as not evaluated; each other entry of that mapping is named as an unevaluated module entry. In any other .json
file, and for a module value with no block entry such as {"a": 1}, the key is an ordinary JSON object and gives no reason.
Duplicate keys: a key written twice in a mapping is compared by its parsed value, so the YAML keys 1 and "1" differ, and 0x1 and 1
are the same key. A document is IaC when its ROOT mapping says so: a CloudFormation Resources mapping (any file), or in a *.tf.json
file only a Terraform JSON resource block or a Terraform module (above), a Kubernetes workload, StorageClass or List kind, or a
repeated Resources key (any file) or resource key (*.tf.json only) at the root. A key nested
at any depth does not decide it. Once a document is IaC, a repeated key at any depth in it is reported as `duplicate key "<key>"` in
a scan-incomplete reason, so the status is fail; the parsers keep only the last value, so the earlier value is not evaluated. A
repeated key in any other document is passed over silently, like the rest of that document.
A YAML integer longer than MAX_INT_DIGITS digits is compared by its value, so +1... and 1... of the same digits are one key. A YAML
integer with a leading zero is octal, as PyYAML reads it, so it is compared by that octal value: 0111 and 73 are one key.
Limitation: a hex or octal literal of more than 4000 decimal digits is not compared with a decimal key of the same value, so a
repeat of the two is not reported (such a key needs over 3,000 hex digits in an IaC mapping).
YAML and JSON floats keep the text they were read from, so a float prints as a float (1.0e+2 prints 100.0, 1.50 prints 1.5), and
5.0e-400 is an underflow, not zero. PyYAML reads YAML 1.1, where a float needs a dot and a signed exponent: a float written without a
dot, such as 1e2, is read as the string "1e2", so a YAML RetentionInDays of 1e2 is not a literal and fails closed, although JSON reads
1e2 as the number 100. The remedy is to write 100 or 1.0e+2 (1.0e2, without the sign, is also a string in PyYAML, so it is not a
remedy). A YAML base-60 value such as 1:30.5 is read exactly as PyYAML reads it (90.5), and as a retention it is not a literal.
Retention: zero is never-expire in Terraform, so `retention_in_days = 0` in a .tf file, `retention_in_days: 0` in a .tf.json file and
`RetentionInDays: 0` in a JSON or YAML template each give the same minor set-retention finding, the one HCL gives today. A negative
number, null, a boolean, a non-literal (a Ref, a Fn:: call, an expression string), NaN or infinity, a value that underflows to zero
(5e-400), an integer too large for a float, and an integer longer than 4000 digits each give the retention reason and fail the scan.
A quoted digit string is read as decimal, so "08" is 8 and "0180" is 180; an unquoted YAML integer with a leading zero is read as
PyYAML reads it, which is octal, so an unquoted 030 is 24. For the same digits the two readings can differ, but both give the same
zero and positivity result, so the retention verdict does not depend on which reading is used.
Bounded: files over 1 MiB are skipped, at most 2000 candidate files are read, symlinks are never followed out of
--root, binary and non-regular files are skipped, a malformed manifest is counted under `parse_error`. It reads files
under --root and writes nothing; findings carry paths, resource references, container names and instance or volume
types only, never file contents. Status is `fail` when any finding is major or worse, or when an in-scope input was not
analysed or not evaluated: every such input (a file, a resource block, a tag, a type value that is an interpolation, variable or intrinsic reference, a `count`, `for_each` or `dynamic` block, a module block not covered by an analysed local directory) is a reason in one major `scan-incomplete` finding (swarm/scan_status.py). `--dry-run` prints deterministic
canned output and reads nothing. No external binary is required.
"""
from __future__ import annotations

import json
import os
import posixpath
import re
import stat
import sys
from bisect import bisect_left
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from swarm.errors import ErrorCode, SwarmError  # noqa: E402
from swarm.script_base import AgentScript  # noqa: E402
from swarm.tools.a20_tools import tag_coverage  # noqa: E402
from swarm.scan_status import incomplete_findings, scan_status  # noqa: E402

try:
    import yaml
except ImportError:  # the YAML-dependent checks are then reported as skipped
    yaml = None

MAX_FILE_BYTES = 1024 * 1024
MAX_FILES = 2000
MAX_FINDINGS = 1000
TAG_CHUNK = 50  # tag_coverage reports one finding per resource, capped at 100
SEVERITY_RANK = {"info": 0, "minor": 1, "major": 2, "blocker": 3}
SILENT_DIRS = {".git", ".hg", ".svn", "node_modules", ".venv", "venv", "__pycache__", ".tox", ".mypy_cache", ".swarm", ".terraform"}
DEFAULT_REQUIRED_TAGS = "owner,environment"

K8S_WORKLOADS = {"Deployment", "StatefulSet", "DaemonSet", "ReplicaSet", "ReplicationController", "Job", "CronJob", "Pod"}
# AWS EC2 "previous generation instances": exactly the families listed on
# https://docs.aws.amazon.com/ec2/latest/instancetypes/pg.html (checked 2026-10-20). T2 and G2 are not on that page.
# The page covers EC2 types only: a db. (RDS) class is never judged, and ElastiCache labels only cache.t2 as previous
# generation (https://docs.aws.amazon.com/AmazonElastiCache/latest/dg/CacheNodes.SupportedTypes.html).
PREVIOUS_GEN_FAMILIES = {"a1", "c1", "c3", "c4", "g3", "i2", "m1", "m2", "m3", "m4", "p3", "p3dn", "r3", "r4", "t1"}
PREVIOUS_GEN_CACHE_FAMILIES = {"t2"}
# Volume types and their newer type. Only `standard` (Magnetic) is labelled previous generation by AWS
# (https://docs.aws.amazon.com/ebs/latest/userguide/ebs-volume-types.html); gp2 -> gp3 and io1 -> io2 are cost advice.
PREVIOUS_GEN_VOLUMES = {"gp2": "gp3", "io1": "io2", "standard": "gp3"}
VOLUME_ADVICE = {
    "gp2": "gp3 is the newer general purpose SSD type and usually costs less per GB-month: compare the regional price",
    "io1": "io2 is the newer provisioned IOPS SSD type (same list price per GB and per IOPS up to 32,000 IOPS in us-east-1: check your region)",
    "standard": "AWS lists Magnetic (standard) as a previous generation volume type: gp3 is the replacement",
}
EBS_PROVISIONERS = {"ebs.csi.aws.com", "kubernetes.io/aws-ebs"}  # a StorageClass `type` is an EBS type only under these
INSTANCE_KEYS = ("instance_type", "instance_class", "node_type", "instance_types", "InstanceType", "InstanceTypes", "DBInstanceClass", "CacheNodeType")
VOLUME_KEYS = ("volume_type", "VolumeType")
_INSTANCE = re.compile(r"^(?:(db|cache|ml)\.)?([a-z][a-z0-9-]*)\.(?:(\d*)xlarge|(metal[a-z0-9-]*)|nano|micro|small|medium|large)$")

TAGGABLE_TF = {
    "aws_instance", "aws_ebs_volume", "aws_s3_bucket", "aws_db_instance", "aws_rds_cluster", "aws_lb", "aws_alb",
    "aws_eks_cluster", "aws_eks_node_group", "aws_elasticache_cluster", "aws_elasticache_replication_group",
    "aws_nat_gateway", "aws_lambda_function", "aws_sqs_queue", "aws_sns_topic", "aws_dynamodb_table",
    "aws_ecr_repository", "aws_cloudwatch_log_group", "aws_efs_file_system", "aws_kinesis_stream",
}
TAGGABLE_CFN = {
    "AWS::EC2::Instance", "AWS::EC2::Volume", "AWS::S3::Bucket", "AWS::RDS::DBInstance", "AWS::RDS::DBCluster",
    "AWS::ElasticLoadBalancingV2::LoadBalancer", "AWS::EKS::Cluster", "AWS::EKS::Nodegroup",
    "AWS::ElastiCache::CacheCluster", "AWS::ElastiCache::ReplicationGroup", "AWS::EC2::NatGateway",
    "AWS::Lambda::Function", "AWS::SQS::Queue", "AWS::SNS::Topic", "AWS::DynamoDB::Table", "AWS::ECR::Repository",
    "AWS::Logs::LogGroup", "AWS::EFS::FileSystem", "AWS::Kinesis::Stream",
}
LIFECYCLE_TF = {"aws_s3_bucket": "s3", "aws_cloudwatch_log_group": "logs", "aws_ecr_repository": "ecr"}
LIFECYCLE_CFN = {"AWS::S3::Bucket": "s3", "AWS::Logs::LogGroup": "logs", "AWS::ECR::Repository": "ecr"}
LIFECYCLE_ADVICE = {
    "s3": "add a lifecycle rule (transition cold data, abort incomplete multipart uploads; any expiry is the data owner's decision)",
    "logs": "set retention_in_days (the owner chooses it): a log group without retention keeps data forever",
    "ecr": "add a lifecycle policy for untagged and old images (the owner chooses what expires)",
}
CANNED_COUNTS = {"info": 0, "minor": 0, "major": 0, "blocker": 0}

_UNKNOWN = "(expression)"


# --- Terraform (HCL) -------------------------------------------------------------------------------------------


def _template_end(text: str, i: int) -> int:
    """Index just past the `}` that closes a template sequence whose body starts at `i` (after `${` or `%{`); strings
    inside it are lexed as strings, so a brace or a quote in them does not count. Unterminated: len(text)."""
    depth, n = 1, len(text)
    while i < n:
        ch = text[i]
        if ch == '"':
            i = _string_end(text, i)
        elif ch == "{":
            depth += 1
            i += 1
        elif ch == "}":
            depth -= 1
            i += 1
            if depth == 0:
                return i
        else:
            i += 1
    return n


def _string_end(text: str, i: int) -> int:
    """Index just past the quoted string that opens at `text[i]`. A string cannot span lines, so a stray quote ends at
    the line end; `${ ... }` and `%{ ... }` sequences may nest quoted strings and span lines; `$${` and `%%{` are literal."""
    j, n = i + 1, len(text)
    while j < n:
        ch = text[j]
        if ch == "\\":
            j += 2
        elif ch == '"':
            return j + 1
        elif ch == "\n":
            return j
        elif ch in "$%" and text.startswith(ch + "{", j + 1):
            j += 3  # escaped template opener: literal text
        elif ch in "$%" and text.startswith("{", j + 1):
            j = _template_end(text, j + 2)
        else:
            j += 1
    return n


def _mask_hcl(text: str) -> tuple[str, str]:
    """(masked, skeleton), both the same length as `text`. masked blanks comments and heredoc bodies; skeleton also
    blanks string contents (quotes kept), so brace depth can be counted without being fooled by "${...}"."""
    masked, skeleton = list(text), list(text)
    i, n = 0, len(text)

    def blank(start: int, stop: int, both: bool = True) -> None:
        for k in range(start, stop):
            if text[k] != "\n":
                skeleton[k] = " "
                if both:
                    masked[k] = " "

    while i < n:
        ch = text[i]
        if ch == "#" or text.startswith("//", i):
            end = text.find("\n", i)
            end = n if end < 0 else end
            blank(i, end)
            i = end
        elif text.startswith("/*", i):
            end = text.find("*/", i + 2)
            end = n if end < 0 else end + 2
            blank(i, end)
            i = end
        elif ch == '"':
            end = _string_end(text, i)
            closed = end - 1 if end <= n and end > i + 1 and text[end - 1] == '"' else end
            blank(i + 1, closed, both=False)
            i = end
        else:
            heredoc = re.match(r"<<[-~]?([A-Za-z_][A-Za-z0-9_]*)[ \t]*\r?\n", text[i:i + 80]) if ch == "<" else None
            if heredoc:
                tag = heredoc.group(1)
                body_start = i + heredoc.end()
                end_match = re.compile(rf"^[ \t]*{re.escape(tag)}[ \t\r]*$", re.M).search(text, body_start)
                end = end_match.end() if end_match else n
                blank(body_start, end)
                i = end
            else:
                i += 1
    return "".join(masked), "".join(skeleton)


def _close_index(skeleton: str, open_index: int) -> int:
    """Index of the bracket closing the one at `open_index`, or -1."""
    depth = 0
    for k in range(open_index, len(skeleton)):
        ch = skeleton[k]
        if ch in "{[(":
            depth += 1
        elif ch in "}])":
            depth -= 1
            if depth == 0:
                return k
    return -1


def _top_level_attr(masked: str, skeleton: str, name: str) -> str | None:
    """Raw value text of a depth-0 attribute `name = value` inside a block body, else None."""
    depth = 0
    pos = 0
    pattern = re.compile(rf"[ \t]*{re.escape(name)}[ \t]*=[ \t]*")
    for line in skeleton.split("\n"):
        if depth == 0:
            match = pattern.match(line)
            if match:
                start = pos + match.end()
                if start < len(masked) and masked[start] in "{[(":
                    end = _close_index(skeleton, start)
                    return masked[start:end + 1] if end >= 0 else masked[start:pos + len(line)]
                return masked[start:pos + len(line)].strip()
        depth += sum(line.count(c) for c in "{[(") - sum(line.count(c) for c in "}])")
        pos += len(line) + 1
    return None


def _has_top_level_block(skeleton: str, name: str) -> bool:
    depth = 0
    pattern = re.compile(rf"[ \t]*{re.escape(name)}\b[^=\n{{]*\{{")
    for line in skeleton.split("\n"):
        if depth == 0 and pattern.match(line):
            return True
        depth += sum(line.count(c) for c in "{[(") - sum(line.count(c) for c in "}])")
    return False


_HCL_TAG_ENTRY = re.compile(r'(?:"((?:[^"\\]|\\.)*)"|([A-Za-z0-9_.:/-]+))[ \t]*=[ \t]*("(?:[^"\\]|\\.)*"|[^,\n}]+)')
_HCL_INTERPOLATED = re.compile(r"[$%]\{")


def _hcl_tag_entries(raw: str) -> list[tuple[str, str]]:
    """(key, raw value) of each entry of a literal tags map. A quoted key is taken exactly, so `"app environment"` is one key."""
    return [(entry.group(1) if entry.group(1) is not None else entry.group(2), entry.group(3)) for entry in _HCL_TAG_ENTRY.finditer(raw[1:-1])]


def _literal_map(raw: str | None) -> bool:
    """Whether a tags value is written as a literal map: a `{` that does not open a map comprehension, which is computed like merge(...)."""
    return raw is not None and raw.startswith("{") and re.match(r"\{\s*for\b", raw) is None


def _hcl_tags(masked: str, skeleton: str) -> dict | None:
    """Literal tag map of the block, {} when there is no tags attribute, None when it is not a literal map or a tag key is
    an interpolation (the key is then unknown, so the map cannot be judged; _hcl_tag_key_reasons names the key)."""
    raw = _top_level_attr(masked, skeleton, "tags")
    if raw is None:
        return {}
    if not _literal_map(raw):
        return None
    entries = _hcl_tag_entries(raw)
    if any(_HCL_INTERPOLATED.search(key) for key, _ in entries):
        return None
    tags: dict[str, str] = {}
    for key, value in entries:
        quoted = re.fullmatch(r'"(.*)"', value)
        tags[key] = quoted.group(1) if quoted and not _HCL_INTERPOLATED.search(quoted.group(1)) else _UNKNOWN
    return tags


def _hcl_tag_key_reasons(rtype: str, body_m: str, body_s: str) -> list[str]:
    """One reason per interpolated tag key of a taggable resource, naming the key and never its value."""
    if rtype not in TAGGABLE_TF:
        return []
    raw = _top_level_attr(body_m, body_s, "tags")
    if not _literal_map(raw):
        return []
    return [f'tag key "{key}" is an interpolation and was not evaluated' for key, _ in _hcl_tag_entries(raw) if _HCL_INTERPOLATED.search(key)]




MAX_INT_DIGITS = 4000  # longer integer literals are kept as text: Python's int() rejects more than 4300 digits


class _NumText(float):
    """A float that carries the text it was read from. It compares, orders and hashes as a float, and str() and repr() print the
    float, so 1.0e+2 prints 100.0 and 1.50 prints 1.5, as they always have. The retention check reads `text` to tell an exact zero
    (0.0) from an underflow (5e-400)."""

    def __new__(cls, text: str):
        value = super().__new__(cls, float(text))
        value.text = text
        return value


class _TooLongInt:
    """An integer literal longer than MAX_INT_DIGITS, kept as its text. It is never converted to an int and renders as its text;
    the retention check reads it as a non-literal."""

    def __init__(self, text: str):
        self.text = text

    def __str__(self) -> str:
        return self.text

    def __repr__(self) -> str:
        return self.text

    def __format__(self, spec: str) -> str:
        return format(self.text, spec)

    def _normal(self) -> tuple[bool, str]:
        """(negative, digits) of the integer the text spells: the sign and the leading zeros are stripped, and zero is never negative."""
        digits = self.text.lstrip("+-").lstrip("0") or "0"
        return self.text.startswith("-") and digits != "0", digits

    def __eq__(self, other):
        if isinstance(other, _TooLongInt):
            return self._normal() == other._normal()
        if isinstance(other, int) and not isinstance(other, bool):  # a plain int of the same value (up to MAX_INT_DIGITS digits)
            negative, digits = self._normal()
            return len(digits) <= MAX_INT_DIGITS and other == int(("-" if negative else "") + digits)
        return NotImplemented  # a float or a string is never equal to a marker

    def __hash__(self):
        negative, digits = self._normal()
        if len(digits) <= MAX_INT_DIGITS:
            return hash(int(("-" if negative else "") + digits))  # the same hash as the equal int
        return hash(("_TooLongInt", negative, digits))


def _json_int(text: str):
    """The parse_int hook of json.loads: an integer longer than MAX_INT_DIGITS is a _TooLongInt, so json.loads does not reject it."""
    return _TooLongInt(text) if len(text.lstrip("-")) > MAX_INT_DIGITS else int(text)


def _doc_retention(value) -> bool | None:
    """The retention of a JSON or YAML log group: True for a positive literal, False for a zero literal (Terraform's never-expire,
    the same minor finding as HCL's `retention_in_days = 0`), and None for anything else: null, a boolean, a negative number, a
    value that underflows to zero (5e-400, read from the float's text), NaN or infinity (1e400, .inf), an integer too large for a
    float, an integer longer than MAX_INT_DIGITS, a plain float (a base-60 YAML value such as 1:30.5), and any non-literal (a Ref,
    a Fn:: call, an expression string, a list or an object)."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, _NumText):  # a float literal, with its text
        if value != value or abs(value) == float("inf"):
            return None
        if value == 0:  # an exact zero, unless the text is a nonzero literal that underflowed
            return None if re.search(r"[1-9]", re.split(r"[eE]", value.text)[0]) else False
        return True if value > 0 else None
    if isinstance(value, str) and re.fullmatch(r"\d+", value.strip()):
        try:
            value = int(value.strip())
        except ValueError:  # more digits than Python converts
            return None
    if isinstance(value, int):
        if value == 0:
            return False
        if value < 0:
            return None
        try:
            float(value)  # OverflowError: an integer too large for a float
        except OverflowError:
            return None
        return True
    return None  # a string that is not a digit run, a plain float, a marker, or any other value


def _retention_of(mapping: dict, key: str) -> bool | None:
    """The retention of a log group: False when the key is absent (never set), else _doc_retention of its value."""
    return _doc_retention(mapping[key]) if key in mapping else False


def _retention_reason(kind: str | None, lifecycle) -> list[str]:
    """One reason when a log group's retention is not a literal: the lifecycle is unknown (None), never read as absent."""
    return [RETENTION_REASON] if kind == "logs" and lifecycle is None else []


def _dir_key(rel: str) -> str:
    """Terraform directory (root or module) that a repository-relative file belongs to."""
    return posixpath.normpath(posixpath.dirname(rel) or ".")


def _blocks(masked: str, skeleton: str, kind: str) -> list[tuple[str, str]]:
    """(masked, skeleton) bodies of every `kind "label" { ... }` block whose braces balance."""
    bodies = []
    for head in re.finditer(rf'^[ \t]*{kind}[ \t]+"[^"]*"[ \t]*\{{', masked, re.M):
        open_index = head.end() - 1
        close = _close_index(skeleton, open_index)
        if close >= 0:
            bodies.append((masked[open_index + 1:close], skeleton[open_index + 1:close]))
    return bodies


_HCL_PROVIDER_PASS = re.compile(r"([A-Za-z0-9_-]+)\s*=\s*[A-Za-z0-9_-]+(?:\.([A-Za-z0-9_-]+))?")


def _provider_alias(raw) -> str | None:
    """The provider a resource names: None when it names none (its directory's default provider applies), "" for the unaliased provider,
    the alias otherwise, and "*" when the reference is not a plain provider or alias, so any tagged provider of its directory may apply."""
    if raw is None:
        return None
    if not isinstance(raw, str):
        return "*"
    match = re.fullmatch(r"\s*([A-Za-z0-9_-]+)(?:\.([A-Za-z0-9_-]+))?\s*", raw)
    return (match.group(2) or "") if match else "*"


def _module_providers(raw):
    """None when a module names no providers, so it takes its parent's default provider. Otherwise the parent alias each local provider
    name is passed from ("" for the unaliased provider); "*" when the argument is not a literal map, so any parent provider may reach it."""
    if raw is None:
        return None
    if not raw.startswith("{"):
        return "*"
    return {key: alias for key, alias in _HCL_PROVIDER_PASS.findall(raw)}


def _hcl_scope(directory: str, masked: str, skeleton: str) -> tuple[set[str], bool, list[tuple[str, object]]]:
    """The provider aliases of the file that declare default_tags ("" for the unaliased provider), whether it declares an unaliased provider
    of its own (Terraform does not pass the default provider down to such a module), and the local child modules it calls, each as its
    directory and the providers it is passed (see _module_providers). Only a default_tags block of a provider counts: a variable or local
    with that name tags nothing."""
    tagged: set[str] = set()
    own_provider = False
    for bm, bs in _blocks(masked, skeleton, "provider"):
        alias = _top_level_attr(bm, bs, "alias")
        if alias is None:
            own_provider = True
        if _has_top_level_block(bs, "default_tags"):
            tagged.add((alias or "").strip('"'))
    calls = []
    for bm, bs in _blocks(masked, skeleton, "module"):
        local = re.fullmatch(r'"(\.\.?/[^"$]*)"', _top_level_attr(bm, bs, "source") or "")
        if local:
            calls.append((posixpath.normpath(posixpath.join(directory, local.group(1))), _module_providers(_top_level_attr(bm, bs, "providers"))))
    return tagged, own_provider, calls


def _hcl_facts(path: str, text: str) -> tuple[list[dict], tuple[set[str], bool, list[tuple[str, object]]], int]:
    """Resource facts of one .tf file, its provider and module scope (see _hcl_scope), and how many resource blocks could
    not be delimited (unbalanced braces) and were therefore not evaluated."""
    masked, skeleton = _mask_hcl(text)
    facts = []
    unclosed = 0
    newlines = [index for index, char in enumerate(text) if char == "\n"]
    for head in re.finditer(r'^[ \t]*resource[ \t]+"([A-Za-z0-9_]+)"[ \t]+"([^"]+)"[ \t]*\{', masked, re.M):
        open_index = head.end() - 1
        close = _close_index(skeleton, open_index)
        if close < 0:
            unclosed += 1
            continue
        body_m, body_s = masked[open_index + 1:close], skeleton[open_index + 1:close]
        rtype, name = head.group(1), head.group(2)
        instance_types = [m.group(2) for m in re.finditer(r'\b(instance_type|instance_class|node_type)[ \t]*=[ \t]*"([^"$]+)"', body_m)]
        listed = _top_level_attr(body_m, body_s, "instance_types")
        if listed:
            instance_types += re.findall(r'"([^"$]+)"', listed)
        volume_types = [m.group(1) for m in re.finditer(r'\bvolume_type[ \t]*=[ \t]*"([^"$]+)"', body_m)]
        if rtype == "aws_ebs_volume":
            own = _top_level_attr(body_m, body_s, "type")
            if own and re.fullmatch(r'"[^"$]+"', own):
                volume_types.append(own.strip('"'))
        retention = _top_level_attr(body_m, body_s, "retention_in_days")
        facts.append({
            "file": path, "line": bisect_left(newlines, head.start()) + 1, "ref": f"{rtype}.{name}", "type": rtype, "name": name,
            "source": "terraform", "instance_types": instance_types, "volume_types": volume_types,
            "tags": _hcl_tags(body_m, body_s) if rtype in TAGGABLE_TF else None,
            "provider_alias": _provider_alias(_top_level_attr(body_m, body_s, "provider")),
            "lifecycle": _hcl_lifecycle(rtype, body_m, body_s, retention),
            "body": body_m,
            "unevaluated": _hcl_unevaluated(rtype, body_m, body_s) + _retention_reason(LIFECYCLE_TF.get(rtype), _hcl_lifecycle(rtype, body_m, body_s, retention)) + _hcl_tag_key_reasons(rtype, body_m, body_s),
        })
    for head in re.finditer(r'^[ \t]*module[ \t]+"([^"]+)"[ \t]*\{', masked, re.M):
        line = bisect_left(newlines, head.start()) + 1
        close = _close_index(skeleton, head.end() - 1)
        if close < 0:  # unbalanced braces: the source is unknown, so the block is an unanalysed input
            facts.append(_module_fact(path, head.group(1), line, None, []))
            continue
        body_m, body_s = masked[head.end():close], skeleton[head.end():close]
        source = re.fullmatch(r'"(\.\.?/[^"$]*)"', _top_level_attr(body_m, body_s, "source") or "")
        child = posixpath.normpath(posixpath.join(_dir_key(path), source.group(1))) if source else None
        constructs = [reason for key, reason in (("count", COUNT_REASON), ("for_each", FOR_EACH_REASON)) if _top_level_attr(body_m, body_s, key) is not None]
        facts.append(_module_fact(path, head.group(1), line, child, constructs))
    return facts, _hcl_scope(_dir_key(path), masked, skeleton), unclosed


def _hcl_lifecycle(rtype: str, body_m: str, body_s: str, retention: str | None) -> bool | None:
    kind = LIFECYCLE_TF.get(rtype)
    if kind == "s3":
        return _has_top_level_block(body_s, "lifecycle_rule")
    if kind == "logs":
        if retention is None:
            return False
        literal = retention.strip()
        if re.fullmatch(r"\d+", literal):
            return int(literal) > 0
        return None  # a variable, interpolation or expression: unknown, reported by _hcl_unevaluated
    if kind == "ecr":
        return False  # decided by a matching aws_ecr_lifecycle_policy in the same directory
    return None


# --- JSON / YAML documents (Terraform JSON, CloudFormation, Kubernetes) ----------------------------------------


def _walk(value, keys, found: list, seen: set | None = None) -> None:
    """Collect the string values stored under `keys`; shared YAML aliases are visited once."""
    seen = set() if seen is None else seen
    if not isinstance(value, (dict, list)) or id(value) in seen:
        return
    seen.add(id(value))
    if isinstance(value, dict):
        for key, child in value.items():
            if key in keys:
                found.extend(item for item in (child if isinstance(child, list) else [child]) if isinstance(item, str))
            _walk(child, keys, found, seen)
    else:
        for child in value:
            _walk(child, keys, found, seen)


def _strings(value, seen: set | None = None) -> list[str]:
    """Every string below `value`; shared YAML aliases are visited once, so an alias bomb cannot multiply the output."""
    seen = set() if seen is None else seen
    if isinstance(value, str):
        return [value]
    if not isinstance(value, (dict, list)) or id(value) in seen:
        return []
    seen.add(id(value))
    out: list[str] = []
    for child in (value.values() if isinstance(value, dict) else value):
        out.extend(_strings(child, seen))
    return out


def _tag_map(raw) -> dict | None:
    """Tags as {key: value} of a literal list or map; None when the value is an expression (a CloudFormation `Ref` or
    `Fn::` function, a YAML short-form intrinsic read as null) and so cannot be judged."""
    if isinstance(raw, dict):
        if any(key == "Ref" or str(key).startswith("Fn::") for key in raw):
            return None
        return {str(k): v if isinstance(v, str) else _UNKNOWN for k, v in raw.items()}
    if isinstance(raw, list):
        tags: dict[str, str] = {}
        for item in raw:
            key = item.get("Key", item.get("key")) if isinstance(item, dict) else None
            if not isinstance(key, str):
                return None
            value = item.get("Value", item.get("value"))
            tags[key] = value if isinstance(value, str) else _UNKNOWN
        return tags
    return None


def _declared_tags(container: dict, key: str) -> dict | None:
    """{} when the resource declares no tags at all, else the literal tags or None (unknown)."""
    return _tag_map(container[key]) if key in container else {}


COUNT_REASON = "count is not evaluated: the instance count is unknown"
FOR_EACH_REASON = "for_each is not evaluated: the instance count is unknown"
MODULE_REASON = "module block is not evaluated: the resources it builds and its inputs were not analysed"
RETENTION_REASON = "retention_in_days is not a literal and was not evaluated"
TF_VALUE_KEYS = ("instance_type", "instance_class", "node_type", "instance_types", "volume_type")
CFN_VALUE_KEYS = ("InstanceType", "InstanceTypes", "DBInstanceClass", "CacheNodeType", "VolumeType")


def _value_problems(key: str, value) -> list[str]:
    """Reasons for a type value that is not a plain string: an interpolation, an intrinsic function or another
    expression. The value itself is never echoed."""
    out = []
    for item in value if isinstance(value, list) else [value]:
        if isinstance(item, str) and "${" not in item and "%{" not in item:
            continue
        if isinstance(item, str):
            out.append(f"{key} is an interpolation and was not evaluated")
        elif isinstance(item, dict) and any(k == "Ref" or str(k).startswith("Fn::") for k in item):
            out.append(f"{key} is an intrinsic function and was not evaluated")
        else:
            out.append(f"{key} is an expression and was not evaluated")
    return out


def _doc_unevaluated(node, keys: tuple[str, ...], structural: bool) -> list[str]:
    """Reasons for every type value under `keys`, at any depth, that is not a plain string; with `structural`, every
    Terraform `dynamic` block as well. Shared YAML aliases are visited once."""
    out: list[str] = []
    stack, seen = [node], set()
    while stack:
        current = stack.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        if isinstance(current, dict):
            for key, child in current.items():
                if key in keys:
                    out.extend(_value_problems(str(key), child))
                if structural and key == "dynamic" and isinstance(child, (dict, list)):
                    out.append("dynamic block is not evaluated")
                stack.append(child)
        elif isinstance(current, list):
            stack.extend(current)
    return out


def _tf_json_tag_key_reasons(rtype: str, attrs: dict) -> list[str]:
    """One reason per interpolated key of a Terraform JSON tags object, naming the key and never its value."""
    tags = attrs.get("tags")
    if rtype not in TAGGABLE_TF or not isinstance(tags, dict):
        return []
    return [f'tag key "{key}" is an interpolation and was not evaluated' for key in tags if _HCL_INTERPOLATED.search(str(key))]


def _tf_json_tag_values(tags: dict | None) -> dict | None:
    """Terraform JSON tag values with an interpolation or template (`${...}`, `%{...}`) are unknown, as the HCL path reads them,
    so a required tag holding one is never counted as present."""
    if tags is None:
        return None
    return {key: _UNKNOWN if isinstance(value, str) and _HCL_INTERPOLATED.search(value) else value for key, value in tags.items()}


def _tf_json_unevaluated(rtype: str, attrs: dict) -> list[str]:
    out = _doc_unevaluated(attrs, TF_VALUE_KEYS, structural=True)
    if rtype == "aws_ebs_volume" and "type" in attrs:
        out.extend(_value_problems("type", attrs["type"]))
    if "count" in attrs:
        out.append(COUNT_REASON)
    if "for_each" in attrs:
        out.append(FOR_EACH_REASON)
    out.extend(_tf_json_tag_key_reasons(rtype, attrs))
    return list(dict.fromkeys(out))


_HCL_LITERAL = re.compile(r'"[^"$%]*"[ \t]*(?:\}|$)')
_HCL_LIST = re.compile(r'\[\s*(?:"[^"$%]*"\s*,?\s*)*\]')
_HCL_SCALARS = ("instance_type", "instance_class", "node_type", "volume_type")


def _hcl_family(raw: str) -> str:
    if "${" in raw or "%{" in raw:
        return "an interpolation"
    if re.search(r"\b(?:var|local|module|data)\.", raw):
        return "a variable reference"
    return "an expression"


def _hcl_unevaluated(rtype: str, body_m: str, body_s: str) -> list[str]:
    """Constructs in one resource body that A20 does not evaluate, as reasons without their values. Attribute names are
    found in the skeleton, so text inside strings and comments is never read as an attribute."""
    out: list[str] = []
    for name in _HCL_SCALARS:
        for match in re.finditer(rf"\b{name}[ \t]*=[ \t]*", body_s):
            end = body_m.find("\n", match.end())
            raw = body_m[match.end(): len(body_m) if end < 0 else end].strip()
            if not _HCL_LITERAL.match(raw):
                out.append(f"{name} is {_hcl_family(raw)} and was not evaluated")
    listed = _top_level_attr(body_m, body_s, "instance_types")
    if listed is not None and not _HCL_LIST.fullmatch(listed):
        out.append(f"instance_types is {_hcl_family(listed)} and was not evaluated")
    if rtype == "aws_ebs_volume":
        own = _top_level_attr(body_m, body_s, "type")
        if own is not None and not _HCL_LITERAL.match(own):
            out.append(f"type is {_hcl_family(own)} and was not evaluated")
    if _top_level_attr(body_m, body_s, "count") is not None:
        out.append(COUNT_REASON)
    if _top_level_attr(body_m, body_s, "for_each") is not None:
        out.append(FOR_EACH_REASON)
    if re.search(r'\bdynamic[ \t]+"', body_s):
        out.append("dynamic block is not evaluated")
    return list(dict.fromkeys(out))

def _unevaluated_fact(path: str, name: str, reason: str) -> dict:
    """A resource that could not be evaluated at all: it carries only its reason, so no other check can judge it."""
    return {"file": path, "line": 0, "ref": name, "type": "unevaluated", "name": name, "source": "cloudformation",
            "instance_types": [], "volume_types": [], "tags": None, "lifecycle": None, "body": "", "unevaluated": [reason]}



def _module_fact(path: str, label: str, line: int, child: str | None, constructs: list[str]) -> dict:
    """A module block. The scanner never expands a module call: `child` is the local directory its source names (None for
    a remote or expression source), and only the files of that directory are analysed on their own."""
    return {"file": path, "line": line, "ref": f"module.{label}", "type": "module", "name": label, "source": "terraform",
            "instance_types": [], "volume_types": [], "tags": None, "lifecycle": None, "body": "", "local": child,
            "unevaluated": constructs}


def _unevaluated(fact: dict, analysed_dirs: set[str]) -> list[str]:
    """Constructs of one fact that were not evaluated. A module block whose local source directory was analysed is covered
    by that directory's own files; any other module block is an unanalysed input and adds MODULE_REASON."""
    if fact["type"] != "module":
        return fact.get("unevaluated", [])
    if fact["local"] in analysed_dirs:
        return fact["unevaluated"]
    return [MODULE_REASON, *fact["unevaluated"]]


def _tf_json_modules(value) -> tuple[list[tuple[str, dict]], list[str]]:
    """(block entries, other entries) of a top-level `module` value. A value is a Terraform module mapping only when at least one
    entry is a block (a mapping, or a one-element list holding one, with a string `source`): then each block is a module block and
    each other entry is named as an unevaluated module entry. A value with no block entry, such as {"a": 1}, is an ordinary object."""
    if not isinstance(value, dict):
        return [], []
    blocks, others = [], []
    for label, body in value.items():
        block = body[0] if isinstance(body, list) and body else body
        if isinstance(block, dict) and isinstance(block.get("source"), str):
            blocks.append((str(label), block))
        else:
            others.append(str(label))
    return (blocks, others) if blocks else ([], [])


def _doc_facts(path: str, doc) -> list[dict]:
    facts: list[dict] = []
    if not isinstance(doc, dict):
        return facts
    if path.lower().endswith(".tf.json") and isinstance(doc.get("resource"), dict):  # Terraform JSON: read only from *.tf.json files
        for rtype, named in doc["resource"].items():
            for name, attrs in (named.items() if isinstance(named, dict) else []):
                attrs = attrs[0] if isinstance(attrs, list) and attrs else attrs
                attrs = attrs if isinstance(attrs, dict) else {}
                instance_types, volume_types = [], []
                _walk(attrs, ("instance_type", "instance_class", "node_type", "instance_types"), instance_types)
                _walk(attrs, ("volume_type",), volume_types)
                if rtype == "aws_ebs_volume":
                    _walk({"volume_type": attrs.get("type")}, ("volume_type",), volume_types)
                kind = LIFECYCLE_TF.get(rtype)
                lifecycle = None
                if kind == "s3":
                    lifecycle = "lifecycle_rule" in attrs
                elif kind == "logs":
                    lifecycle = _retention_of(attrs, "retention_in_days")
                elif kind == "ecr":
                    lifecycle = False
                tags = None
                if rtype in TAGGABLE_TF:
                    tags = None if _tf_json_tag_key_reasons(rtype, attrs) else _tf_json_tag_values(_declared_tags(attrs, "tags"))
                facts.append({"file": path, "line": 0, "ref": f"{rtype}.{name}", "type": rtype, "name": name, "source": "terraform",
                              "instance_types": instance_types, "volume_types": volume_types, "tags": tags,
                              "provider_alias": _provider_alias(attrs["provider"] if "provider" in attrs else None),
                              "lifecycle": lifecycle, "body": " ".join(_strings(attrs)), "unevaluated": _tf_json_unevaluated(rtype, attrs) + _retention_reason(LIFECYCLE_TF.get(rtype), lifecycle)})
    if path.lower().endswith(".tf.json"):  # Terraform reads JSON configuration only from *.tf.json files: elsewhere a module key is data
        blocks, others = _tf_json_modules(doc.get("module"))
        for label, body in blocks:  # Terraform JSON module blocks: each is not evaluated
            source = body["source"]
            local = posixpath.normpath(posixpath.join(_dir_key(path), source)) if re.fullmatch(r"\.\.?/[^$]*", source) else None
            constructs = [reason for key, reason in (("count", COUNT_REASON), ("for_each", FOR_EACH_REASON)) if key in body]
            facts.append(_module_fact(path, label, 0, local, constructs))
        for label in others:  # a non-block sibling of a Terraform module mapping is named, not silently skipped
            facts.append(_unevaluated_fact(path, f"module.{label}", "module entry is not a Terraform module block and was not evaluated"))
    resources = doc.get("Resources")
    if isinstance(resources, dict):  # CloudFormation
        for name, res in resources.items():
            if not isinstance(res, dict):
                facts.append(_unevaluated_fact(path, name, "resource body is not a mapping and was not evaluated"))
                continue
            if not isinstance(res.get("Type"), str):
                facts.append(_unevaluated_fact(path, name, "resource Type is not a literal string and the resource was not evaluated"))
                continue
            rtype, raw_props = res["Type"], res.get("Properties")
            if raw_props is None or (isinstance(raw_props, dict) and not _is_intrinsic(raw_props)):
                props, unknown = raw_props or {}, None
            elif _is_intrinsic(raw_props):
                props, unknown = {}, "Properties is an intrinsic function and was not evaluated"
            else:
                props, unknown = {}, "Properties is not a mapping and was not evaluated"
            instance_types, volume_types = [], []
            _walk(props, ("InstanceType", "InstanceTypes", "DBInstanceClass", "CacheNodeType"), instance_types)
            _walk(props, ("VolumeType",), volume_types)
            kind = LIFECYCLE_CFN.get(rtype)
            lifecycle, tags = None, None
            if unknown is None:
                if kind == "s3":
                    lifecycle = bool(props.get("LifecycleConfiguration"))
                elif kind == "logs":
                    lifecycle = _retention_of(props, "RetentionInDays")
                elif kind == "ecr":
                    lifecycle = bool(props.get("LifecyclePolicy"))
                tags = _declared_tags(props, "Tags") if rtype in TAGGABLE_CFN else None
                unevaluated = _doc_unevaluated(props, CFN_VALUE_KEYS, structural=False) + _retention_reason(kind, lifecycle)
            else:
                unevaluated = [unknown]
            facts.append({"file": path, "line": 0, "ref": f"{rtype}:{name}", "type": rtype, "name": name, "source": "cloudformation",
                          "instance_types": instance_types, "volume_types": volume_types, "tags": tags,
                          "lifecycle": lifecycle, "body": "", "unevaluated": unevaluated})
    kind = doc.get("kind")
    if isinstance(kind, str) and kind.endswith("List"):  # List and every *List kind, each item judged by its own kind
        label = "List" if kind == "List" else kind
        items = doc.get("items")
        if not isinstance(items, list) and (kind == "List" or kind[:-4] in K8S_WORKLOADS):
            facts.append(_unevaluated_fact(path, label, f"{label} items value is not a list and was not evaluated"))
        for index, item in enumerate(items if isinstance(items, list) else []):
            if isinstance(item, dict):
                if not isinstance(item.get("kind"), str):
                    facts.append(_unevaluated_fact(path, f"{label}[{index}]", f"{label} item has no kind and was not evaluated"))
                facts.extend(_doc_facts(path, item))
            else:
                facts.append(_unevaluated_fact(path, f"{label}[{index}]", f"{label} item is not a mapping and was not evaluated"))
    elif isinstance(kind, str):
        facts.append(_k8s_fact(path, doc))
    return [f for f in facts if f]


def _is_intrinsic(value) -> bool:
    """A JSON or YAML mapping that is a CloudFormation intrinsic (`Ref`, `Fn::...`), not a literal map."""
    return isinstance(value, dict) and any(key == "Ref" or str(key).startswith("Fn::") for key in value)


def _map(value) -> dict:
    return value if isinstance(value, dict) else {}


def _k8s_fact(path: str, doc: dict) -> dict | None:
    kind = doc.get("kind")
    meta = _map(doc.get("metadata"))
    ref = f"{kind}/{meta.get('name', '?')}"
    if kind == "StorageClass":
        volume = _map(doc.get("parameters")).get("type")
        provisioner = doc.get("provisioner")
        ebs = isinstance(provisioner, str) and provisioner in EBS_PROVISIONERS  # `type` names an EBS type only under an AWS EBS provisioner
        return {"file": path, "line": 0, "ref": ref, "type": kind, "name": meta.get("name", "?"), "source": "kubernetes",
                "instance_types": [], "volume_types": [volume] if ebs and isinstance(volume, str) else [], "tags": None, "lifecycle": None, "body": ""}
    if kind not in K8S_WORKLOADS:
        return None
    spec = doc.get("spec")
    if kind == "CronJob":
        spec = _map(spec).get("jobTemplate")
        spec = _map(spec).get("spec") if isinstance(spec, dict) else spec
    if kind != "Pod":
        spec = _map(spec).get("template") if isinstance(spec, dict) else spec
        spec = spec.get("spec") if isinstance(spec, dict) else spec
    if not isinstance(spec, dict):
        return {"file": path, "line": 0, "ref": ref, "type": kind, "name": meta.get("name", "?"), "source": "kubernetes",
                "instance_types": [], "volume_types": [], "tags": None, "lifecycle": None, "body": "", "containers": [],
                "unevaluated": ["pod template or spec is not a mapping and the workload was not evaluated"]}
    containers, bad_entries = [], 0
    for group in ("containers", "initContainers"):
        listed = spec.get(group)
        if listed is not None and not isinstance(listed, list):
            return {"file": path, "line": 0, "ref": ref, "type": kind, "name": meta.get("name", "?"), "source": "kubernetes",
                    "instance_types": [], "volume_types": [], "tags": None, "lifecycle": None, "body": "", "containers": containers,
                    "unevaluated": [f"{group} is not a list and the workload was not evaluated"]}
        for container in listed or []:
            if not isinstance(container, dict):
                bad_entries += 1
                continue
            resources = _map(container.get("resources"))
            requests, limits = _map(resources.get("requests")), _map(resources.get("limits"))
            # A limit with no request is copied into the request by Kubernetes
            # (https://kubernetes.io/docs/concepts/configuration/manage-resources-containers/).
            containers.append({"name": str(container.get("name", "?")), "requests": sorted({str(k) for k in requests} | {str(k) for k in limits}),
                               "limits": sorted(str(k) for k in limits)})
    unevaluated = [f"{bad_entries} container entr{'y' if bad_entries == 1 else 'ies'} not a mapping and not evaluated"] if bad_entries else []
    return {"file": path, "line": 0, "ref": ref, "type": kind, "name": meta.get("name", "?"), "source": "kubernetes",
            "instance_types": [], "volume_types": [], "tags": None, "lifecycle": None, "body": "", "containers": containers,
            "unevaluated": unevaluated}


def _yaml_float(loader, node):
    """A YAML float. A plain decimal is read as a _NumText, which keeps its text; .inf, -.inf and .nan are read as floats; any other
    float form, such as a base-60 value (1:30.5), is read by PyYAML's own float constructor, exactly as PyYAML reads it."""
    text = loader.construct_scalar(node).replace("_", "")
    if text.lower() in _YAML_NONFINITE:
        return _YAML_NONFINITE[text.lower()]
    try:
        return _NumText(text)
    except ValueError:
        return yaml.SafeLoader.construct_yaml_float(loader, node)


def _yaml_int(loader, node):
    """A YAML integer. One longer than MAX_INT_DIGITS is kept as its text (_TooLongInt): Python's int() rejects more than 4300
    digits, which would otherwise lose the whole document. Every other integer form is read by PyYAML's own constructor."""
    text = loader.construct_scalar(node).replace("_", "")
    digits = text.lstrip("+-")
    if len(digits) > MAX_INT_DIGITS and digits.isdigit() and not digits.startswith("0"):  # a decimal literal Python's int() rejects
        return _TooLongInt(text)  # a leading zero is octal in YAML 1.1: PyYAML's own value, read below, never a decimal marker
    return yaml.SafeLoader.construct_yaml_int(loader, node)


_YAML_NONFINITE = {".inf": float("inf"), "+.inf": float("inf"), "-.inf": float("-inf"), ".nan": float("nan")}


def _yaml_loader(found: list):
    """A SafeLoader that records each mapping key written twice. Keys are compared by their CONSTRUCTED value (1 and "1" differ,
    0x1 and 1 are the same key). Each repeat is recorded as (key, is_root): is_root says the mapping is the document root."""

    class Loader(yaml.SafeLoader):
        pass

    def construct_document(self, node):
        self.root_node = node  # the document's root node, so a repeat in the root mapping can be told apart
        return yaml.SafeLoader.construct_document(self, node)

    def construct_mapping(self, node, deep=False):
        if isinstance(node, yaml.MappingNode):
            seen = set()
            for key_node, _ in node.value:
                if key_node.tag == "tag:yaml.org,2002:merge":
                    continue  # a `<<` merge is flattened by PyYAML; it is not a key
                key = self.construct_object(key_node, deep=True)
                try:
                    hash(key)
                except TypeError:
                    continue  # an unhashable key is rejected by PyYAML itself
                if key in seen:
                    found.append((key, node is getattr(self, "root_node", None)))
                seen.add(key)
        return yaml.SafeLoader.construct_mapping(self, node, deep=deep)

    Loader.construct_document = construct_document
    Loader.construct_mapping = construct_mapping
    Loader.add_constructor("tag:yaml.org,2002:float", _yaml_float)
    Loader.add_constructor("tag:yaml.org,2002:int", _yaml_int)
    # CloudFormation short-form intrinsics (!Ref, !Sub, ...) are expressions: they are read as a non-literal intrinsic marker,
    # never evaluated and never mistaken for an absent (None) value
    Loader.add_multi_constructor("!", lambda loader, suffix, node: {f"Fn::{suffix}": True})
    return Loader


# --- rules -----------------------------------------------------------------------------------------------------


def _finding(rule: str, severity: str, summary: str, fact: dict, suffix: str = "") -> dict:
    location = f"{fact['file']}:{fact['line']}" if fact["line"] else fact["file"]
    ident = f"{rule}:{fact['file']}:{fact['ref']}" + (f":{suffix}" if suffix else "")
    return {"id": ident, "severity": severity, "rule": rule, "summary": summary, "file": fact["file"], "location": location}


def _size_rank(instance_type: str) -> tuple[str, str, int, bool] | None:
    """(prefix, family, xlarge multiplier, is_metal) of an AWS-style type such as c5.18xlarge, db.r5.8xlarge, m5.metal,
    t3.micro; the prefix is "", "db", "cache" or "ml". Sizes below xlarge rank 0; xlarge is 1; metal ranks 0 and is
    reported through the flag."""
    match = _INSTANCE.match(instance_type)
    if not match:
        return None
    prefix, family, multiplier, metal = match.groups()
    return prefix or "", family, (int(multiplier) if multiplier else 1) if multiplier is not None else 0, bool(metal)


def _k8s_findings(fact: dict) -> list[dict]:
    out = []
    for container in fact.get("containers", []):
        missing = [r for r in ("cpu", "memory") if r not in container["requests"]]
        if missing:
            out.append(_finding("k8s-missing-requests", "major", f"{fact['ref']} container {container['name']} has no {' or '.join(missing)} request: the scheduler cannot bin-pack it and cost cannot be attributed (a LimitRange default is not evaluated)", fact, container["name"]))
        if "memory" not in container["limits"]:
            out.append(_finding("k8s-missing-limits", "minor", f"{fact['ref']} container {container['name']} has no memory limit: one leak can take the node", fact, container["name"]))
    return out


def _instance_findings(fact: dict, max_xlarge: int) -> list[dict]:
    out = []
    for instance_type in sorted(set(fact["instance_types"])):
        ranked = _size_rank(instance_type)
        if ranked is None:
            continue
        prefix, family, multiplier, metal = ranked
        if (prefix == "" and family in PREVIOUS_GEN_FAMILIES) or (prefix == "cache" and family in PREVIOUS_GEN_CACHE_FAMILIES):
            out.append(_finding("iac-previous-generation-instance", "minor", f"{fact['ref']} uses previous-generation {instance_type}: a current family is cheaper per unit of work", fact, instance_type))
        if metal:
            out.append(_finding("iac-oversized-instance", "minor", f"{fact['ref']} uses bare-metal {instance_type}: confirm the workload needs the whole host", fact, instance_type))
        elif multiplier >= 2 * max_xlarge:
            out.append(_finding("iac-oversized-instance", "major", f"{fact['ref']} uses {instance_type} ({multiplier}xlarge, at least twice the {max_xlarge}xlarge review size): right-size or justify", fact, instance_type))
        elif multiplier >= max_xlarge:
            out.append(_finding("iac-oversized-instance", "minor", f"{fact['ref']} uses {instance_type} ({multiplier}xlarge, review size is {max_xlarge}xlarge): confirm utilization", fact, instance_type))
    return out


def _volume_findings(fact: dict) -> list[dict]:
    return [_finding("iac-previous-generation-volume", "minor", f"{fact['ref']} uses {volume} volumes: {VOLUME_ADVICE[volume]}", fact, volume)
            for volume in sorted(set(fact["volume_types"])) if volume in PREVIOUS_GEN_VOLUMES]


def _lifecycle_findings(facts: list[dict]) -> list[dict]:
    """A bucket or repository is covered by a lifecycle resource that names it in the same directory (a Terraform module). Only a reference to the
    managed resource counts: data.<type>.<name> reads a data source, which is not the resource."""
    covered: set[tuple[str, str, str]] = set()
    for fact in facts:
        folder = os.path.dirname(fact["file"])
        if fact["type"] == "aws_s3_bucket_lifecycle_configuration":
            covered.update((folder, "aws_s3_bucket", name) for name in re.findall(r"(?<![\w.])aws_s3_bucket\.([A-Za-z0-9_-]+)", fact["body"]))
        elif fact["type"] == "aws_ecr_lifecycle_policy":
            covered.update((folder, "aws_ecr_repository", name) for name in re.findall(r"(?<![\w.])aws_ecr_repository\.([A-Za-z0-9_-]+)", fact["body"]))
    out = []
    for fact in facts:
        kind = LIFECYCLE_TF.get(fact["type"]) or LIFECYCLE_CFN.get(fact["type"])
        if kind is None or fact["lifecycle"] is not False:
            continue
        if (os.path.dirname(fact["file"]), fact["type"], fact["name"]) in covered:
            continue
        out.append(_finding("iac-missing-lifecycle", "minor", f"{fact['ref']}: {LIFECYCLE_ADVICE[kind]}", fact))
    return out


def _tag_inheriting_dirs(tagged: dict[str, set[str]], provider_dirs: set[str], module_calls: dict[str, list]) -> dict[str, set[str]]:
    """Per Terraform directory, the provider aliases whose default_tags reach its resources ("" is the default provider): those it declares,
    and "" for each local child module that receives a tagged provider from its parent. A child that declares an unaliased provider of its
    own takes no default from its parent (Terraform passes the default down only without one)."""
    reach = {directory: set(aliases) for directory, aliases in tagged.items()}
    frontier = list(reach)
    while frontier:
        parent = frontier.pop()
        for child, passed in module_calls.get(parent, ()):
            if child in provider_dirs:
                continue
            if passed is None:
                sent = {""} & reach[parent]
            elif passed == "*":
                sent = set(reach[parent])
            else:
                sent = {passed.get("aws", "")} & reach[parent]
            if sent and "" not in reach.get(child, set()):
                reach.setdefault(child, set()).add("")
                frontier.append(child)
    return reach


def _inherits_tags(fact: dict, reach: dict[str, set[str]]) -> bool:
    """Whether a Terraform resource takes its tags from provider default_tags. A resource with no provider argument takes its directory's
    default provider, so it inherits only when "" reaches its directory; one that names a provider inherits when that alias reaches its
    directory (any alias for a reference that is not plain)."""
    reaching = reach.get(_dir_key(fact["file"]), set())
    alias = fact.get("provider_alias")
    if alias is None:
        return "" in reaching
    if alias == "*":
        return bool(reaching)
    return alias in reaching


def _tag_findings(facts: list[dict], required: list[str], reach: dict[str, set[str]]) -> tuple[list[dict], list[dict], list[str]]:
    """Tag findings, the skipped checks, and one reason per taggable resource that was not judged (label and path only)."""
    skipped, out, reasons = [], [], []
    if not required:
        return out, [{"check": "iac-untagged-resource", "status": "skipped:no-required-tags"}], reasons
    candidates = []
    numbered = 0
    for fact in facts:
        where = f"{fact['file']}::{fact['ref']}"
        if fact["tags"] is None:
            if fact["type"] in TAGGABLE_TF or fact["type"] in TAGGABLE_CFN:
                reasons.append(f"{where}: tags are an expression and were not judged")
            continue
        if fact["source"] == "terraform" and _inherits_tags(fact, reach):
            reasons.append(f"{where}: tags inherited from provider default_tags were not judged")
            continue
        if any(fact["tags"].get(key) == _UNKNOWN for key in required):
            reasons.append(f"{where}: a required tag value is an expression and was not judged")
            continue
        numbered += 1
        rid = f"{fact['file']}::{fact['ref']}#{numbered}"
        candidates.append((rid, fact))
    if any(f["source"] == "terraform" and f["tags"] is not None and _inherits_tags(f, reach) for f in facts):
        skipped.append({"check": "iac-untagged-resource", "status": "skipped:provider-default-tags", "detail": "Terraform tags are inherited from provider default_tags and are not evaluated"})
    for start in range(0, len(candidates), TAG_CHUNK):
        chunk = candidates[start:start + TAG_CHUNK]
        resources = [{"id": rid, "monthly_cost": 0, "tags": [{"key": key, "value": value} for key, value in sorted(fact["tags"].items())]} for rid, fact in chunk]
        result = tag_coverage({"resources": resources, "required_keys": required})
        if result["state"] != "SUCCESS":
            raise SwarmError(ErrorCode.E_INPUT, f"tag check refused its input: {result.get('field')}: {result.get('message')}")
        by_id = dict(chunk)
        for item in result["findings"]:
            fact = by_id[item["id"]]
            detail = item["summary"].removeprefix(f"{item['id']} (0/month) ")
            out.append(_finding("iac-untagged-resource", item["severity"], f"{fact['ref']}: {detail}", fact))
    return out, skipped, reasons


# --- scan ------------------------------------------------------------------------------------------------------


def _candidates(root: Path, base: Path, counters: dict, reasons: list[str]):
    """Candidate manifest files below `base`, sorted, never following symlinks, at most MAX_FILES. A directory that cannot
    be listed, or is a symlink, adds one reason naming its path. SILENT_DIRS are never read and add no reason."""
    seen = 0

    def unreadable(error: OSError) -> None:
        target = Path(error.filename) if getattr(error, "filename", None) else base
        reasons.append(f"{target.relative_to(root).as_posix()}: directory could not be read")

    for folder, dirs, files in os.walk(base, followlinks=False, onerror=unreadable):
        kept = []
        for d in sorted(dirs):
            here = os.path.join(folder, d)
            if d in SILENT_DIRS:
                continue
            if os.path.islink(here):
                reasons.append(f"{Path(here).relative_to(root).as_posix()}: symlinked directory not followed")
                continue
            kept.append(d)
        dirs[:] = kept
        for name in sorted(files):
            lower = name.lower()
            if not lower.endswith((".tf", ".json", ".yaml", ".yml", ".template")):
                continue
            full = Path(folder, name)
            if full.is_symlink():
                counters["symlink"] += 1
                reasons.append(f"{full.relative_to(root).as_posix()}: symlink not followed")
                continue
            if seen >= MAX_FILES:
                counters["truncated"] = True
                reasons.append(f"more than {MAX_FILES} candidate files: the rest were not read")
                return
            seen += 1
            yield full


_NOT_JSON = object()  # sentinel: the text is not valid JSON


CFN_IAC_KEY = "Resources"  # a CloudFormation template's top-level mapping: read in any .json or .template file
TF_JSON_IAC_KEY = "resource"  # a Terraform JSON resource mapping: read only in a *.tf.json file, the only JSON Terraform reads


def _is_iac_document(doc: dict, root_repeats: list, terraform_json: bool) -> bool:
    """Whether the scanner analyses `doc` as IaC, decided from its ROOT mapping only: a CloudFormation Resources mapping (any file),
    or, in a *.tf.json file only, a Terraform JSON resource mapping or a Terraform-shaped module; a Kubernetes workload, StorageClass
    or List kind; or a repeated Resources key (any file) or resource key (*.tf.json only) at the root. A key nested at any depth does
    not decide it."""
    markers = (CFN_IAC_KEY, TF_JSON_IAC_KEY) if terraform_json else (CFN_IAC_KEY,)
    if any(isinstance(doc.get(key), dict) for key in markers) or any(key in markers for key in root_repeats):
        return True
    if terraform_json and _tf_json_modules(doc.get("module"))[0]:
        return True
    kind = doc.get("kind")
    return isinstance(kind, str) and (kind in K8S_WORKLOADS or kind == "StorageClass" or (kind.endswith("List") and isinstance(doc.get("items"), list)))


def _json_load(text: str):
    """The JSON value of `text`, and its repeated object keys as (all depths, root only). json.loads keeps only the last of a
    repeated key, so each object's repeats are recorded. Floats are read as _NumText (they keep their text) and integers longer
    than MAX_INT_DIGITS as _TooLongInt, so no number can lose the document."""
    objects = []  # (object, its repeated keys) for every object, so the root's own repeats can be told apart

    def pairs(items):
        obj, found = {}, []
        for key, value in items:
            if key in obj:
                found.append(key)
            obj[key] = value
        objects.append((obj, found))
        return obj

    value = json.loads(text, object_pairs_hook=pairs, parse_float=_NumText, parse_int=_json_int)
    repeats = [key for _, found in objects for key in found]
    root = next((found for obj, found in objects if obj is value), [])
    return value, (repeats, root)


def _json_or_sentinel(text: str):
    """(the parsed JSON value of `text`, its repeated keys), or (_NOT_JSON, ([], [])) when the text is not valid JSON."""
    try:
        return _json_load(text)
    except (ValueError, RecursionError):
        return _NOT_JSON, ([], [])


def _yaml_docs(text: str) -> tuple[list, list[tuple[list, list]]]:
    """Every YAML document of `text`, and for each one its repeated mapping keys as (all depths, root only). Raises
    yaml.YAMLError when PyYAML rejects the text."""
    found: list = []
    docs, per_doc = [], []
    for doc in yaml.load_all(text, Loader=_yaml_loader(found)):  # each document is built before it is yielded
        docs.append(doc)
        per_doc.append(([key for key, _ in found], [key for key, is_root in found if is_root]))
        found.clear()
    return docs, per_doc


def _incomplete(reasons: list[str], analysed: int) -> list[dict]:
    """The shared fail-closed rule (swarm/scan_status.py), in this script's finding shape."""
    return [{**item, "file": "", "location": ""} for item in incomplete_findings(reasons, analysed)]


def scan(root: Path, base: Path, required_tags: list[str], max_xlarge: int) -> dict:
    counters = {"too_large": 0, "binary": 0, "parse_error": 0, "symlink": 0, "yaml_skipped": 0, "truncated": False}
    reasons: list[str] = []  # one label and path per in-scope input that was not analysed or not judged
    facts: list[dict] = []
    tagged: dict[str, set[str]] = {}  # directory -> provider aliases that declare default_tags ("" is the unaliased provider)
    provider_dirs: set[str] = set()  # directories that declare an unaliased provider of their own
    module_calls: dict[str, list] = {}  # directory -> (child directory, providers passed) of each local child module it calls
    unclosed: dict[str, int] = {}
    scanned, passed = 0, 0
    analysed_dirs: set[str] = set()  # directories that had at least one analysed file
    for path in _candidates(root, base, counters, reasons):
        rel = path.relative_to(root).as_posix()
        try:
            info = path.stat()
            if not stat.S_ISREG(info.st_mode):  # a FIFO or device named *.json would block the read
                counters["parse_error"] += 1
                reasons.append(f"{rel}: not a regular file")
                continue
            if info.st_size > MAX_FILE_BYTES:
                counters["too_large"] += 1
                reasons.append(f"{rel}: larger than 1 MiB")
                continue
            data = path.read_bytes()
        except OSError:
            counters["parse_error"] += 1
            reasons.append(f"{rel}: could not be read")
            continue
        if b"\0" in data:
            counters["binary"] += 1
            reasons.append(f"{rel}: binary")
            continue
        try:
            text = data.decode("utf-8-sig")  # a byte-order mark is not part of the first line
        except UnicodeDecodeError:
            counters["binary"] += 1
            reasons.append(f"{rel}: binary")
            continue
        lower = path.name.lower()
        template_doc, template_dupes = _json_or_sentinel(text) if lower.endswith(".template") else (_NOT_JSON, ([], []))  # a .template is JSON when its text is valid JSON
        yaml_format = lower.endswith((".yaml", ".yml")) or (lower.endswith(".template") and template_doc is _NOT_JSON)
        if yaml_format and yaml is None:
            counters["yaml_skipped"] += 1
            reasons.append(f"{rel}: YAML not read, PyYAML is not importable")
            continue
        try:
            if lower.endswith(".tf"):
                tf_facts, (file_tagged, own_provider, calls), not_delimited = _hcl_facts(rel, text)
                facts.extend(tf_facts)
                directory = _dir_key(rel)
                if file_tagged:
                    tagged.setdefault(directory, set()).update(file_tagged)
                if own_provider:
                    provider_dirs.add(directory)
                module_calls.setdefault(directory, []).extend(calls)
                if not_delimited:
                    unclosed[rel] = not_delimited
                scanned += 1
                analysed_dirs.add(directory)
                continue
            if lower.endswith(".json"):
                value, dupes = _json_load(text)
                docs, doc_dupes = [value], [dupes]
            elif template_doc is not _NOT_JSON:
                docs, doc_dupes = [template_doc], [template_dupes]
            else:
                try:
                    docs, doc_dupes = _yaml_docs(text)
                except yaml.YAMLError:
                    value, dupes = _json_or_sentinel(text)  # tab-indented JSON is valid JSON that PyYAML rejects
                    if value is _NOT_JSON:
                        raise
                    docs, doc_dupes = [value], [dupes]
        except (ValueError, RecursionError, yaml.YAMLError if yaml else ValueError):
            counters["parse_error"] += 1
            reasons.append(f"{rel}: could not be parsed")
            continue
        for doc, (repeats, root_repeats) in zip(docs, doc_dupes):  # a repeated key is a finding only in a document analysed as IaC
            if isinstance(doc, dict) and _is_iac_document(doc, root_repeats, lower.endswith(".tf.json")):
                reasons.extend(f'{rel}: duplicate key "{key}" in the document: the earlier value was not evaluated' for key in dict.fromkeys(repeats))
        malformed, mapping_docs = False, 0
        for doc in docs:
            if not isinstance(doc, dict):
                continue  # a top-level array or scalar is not an IaC document, and an empty YAML document holds no content: passed over
            mapping_docs += 1
            try:
                facts.extend(_doc_facts(rel, doc))
            except (AttributeError, TypeError, KeyError, IndexError, ValueError, RecursionError, OverflowError):
                malformed = True  # one malformed document must not abort the scan of the rest of the tree
        if malformed:
            counters["parse_error"] += 1
            reasons.append(f"{rel}: a document could not be evaluated")
        elif mapping_docs:
            scanned += 1
            analysed_dirs.add(_dir_key(rel))
        else:
            passed += 1  # no mapping document (an empty file, or only arrays and scalars): passed over, not in files_scanned
    for fact in facts:
        reasons.extend(f"{fact['file']}::{fact['ref']}: {construct}" for construct in _unevaluated(fact, analysed_dirs))
    findings: list[dict] = []
    for fact in facts:
        findings.extend(_k8s_findings(fact) if fact["source"] == "kubernetes" else [])
        findings.extend(_instance_findings(fact, max_xlarge))
        findings.extend(_volume_findings(fact))
    findings.extend(_lifecycle_findings(facts))
    tag_findings, tag_skipped, tag_reasons = _tag_findings([f for f in facts if f["source"] != "kubernetes"], required_tags, _tag_inheriting_dirs(tagged, provider_dirs, module_calls))
    findings.extend(tag_findings)
    reasons.extend(tag_reasons)
    skipped = list(tag_skipped)
    if unclosed:
        listed = ", ".join(sorted(unclosed)[:5]) + (f" and {len(unclosed) - 5} more" if len(unclosed) > 5 else "")
        skipped.append({"check": "terraform-resources", "status": "skipped:unbalanced-braces",
                        "detail": f"{sum(unclosed.values())} resource block(s) could not be delimited and were not evaluated: {listed}"})
        reasons.extend(f"{where}: {count} resource block(s) could not be delimited and were not evaluated" for where, count in sorted(unclosed.items()))
    if counters["yaml_skipped"]:
        skipped.append({"check": "yaml-manifests", "status": "skipped:tool-missing", "detail": f"PyYAML is not importable: {counters['yaml_skipped']} YAML file(s) not read"})
    findings.extend(_incomplete(reasons, scanned + passed))
    findings.sort(key=lambda f: (-SEVERITY_RANK[f["severity"]], f["file"], f["id"]))
    counts = {severity: sum(1 for f in findings if f["severity"] == severity) for severity in SEVERITY_RANK}
    by_rule: dict[str, int] = {}
    for finding in findings:
        by_rule[finding["rule"]] = by_rule.get(finding["rule"], 0) + 1
    return {
        "status": scan_status(findings),
        "files_scanned": scanned,
        "files_skipped": {"too_large": counters["too_large"], "binary": counters["binary"], "parse_error": counters["parse_error"], "symlink": counters["symlink"], "yaml_tool_missing": counters["yaml_skipped"]},
        "file_limit_reached": counters["truncated"],
        "counts": counts,
        "by_rule": {rule: by_rule[rule] for rule in sorted(by_rule)},
        "findings_truncated": len(findings) > MAX_FINDINGS,
        "findings": findings[:MAX_FINDINGS],
        "skipped": skipped,
        "summary": f"{len(findings)} cost smell(s) in {scanned} file(s): {counts['major'] + counts['blocker']} major or worse, {counts['minor']} minor, {counts['info']} info",
    }


def run(args, ctx) -> dict:
    if ctx.dry_run:
        return {"status": "ok", "dry_run": True, "files_scanned": 0,
                "files_skipped": {"too_large": 0, "binary": 0, "parse_error": 0, "symlink": 0, "yaml_tool_missing": 0},
                "file_limit_reached": False, "counts": dict(CANNED_COUNTS), "by_rule": {}, "findings_truncated": False,
                "findings": [], "skipped": [], "summary": "dry-run: finops_cost_scan would scan IaC and Kubernetes manifests under --root"}
    base = (ctx.root / args.path).resolve()
    if not base.is_relative_to(ctx.root):
        raise SwarmError(ErrorCode.E_INPUT, f"--path {args.path!r} leaves the repository root")
    if not base.is_dir():
        raise SwarmError(ErrorCode.E_INPUT, f"--path {args.path!r} is not a directory under {ctx.root}")
    if args.max_xlarge < 1:
        raise SwarmError(ErrorCode.E_INPUT, "--max-xlarge must be at least 1")
    required = list(dict.fromkeys(key.strip() for key in args.required_tags.split(",") if key.strip()))
    return scan(ctx.root, base, required, args.max_xlarge)


def add_args(p) -> None:
    p.add_argument("--path", default=".", help="directory under --root to scan")
    p.add_argument("--required-tags", default=DEFAULT_REQUIRED_TAGS,
                   help="comma list of allocation tag keys every taggable resource needs (case-sensitive); empty disables the check")
    p.add_argument("--max-xlarge", type=int, default=8, help="xlarge multiplier from which an instance size is reviewed (twice it is major)")


if __name__ == "__main__":
    sys.exit(AgentScript("A20", "finops_cost_scan", run, description=__doc__, add_args=add_args).main())
