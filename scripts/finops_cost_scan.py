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

Reads Terraform (.tf, .tf.json), CloudFormation and Kubernetes manifests (.json, and .yaml/.yml when PyYAML imports;
otherwise the YAML-dependent checks are reported as `skipped:tool-missing` and JSON/Terraform keep working). HCL is
read by a lexer for comments, heredocs and quoted strings with nested `${ }` templates, then regular expressions over
the resource blocks: variables, modules, `count` and dynamic blocks are not evaluated, and a resource block whose
braces do not balance is reported under `skipped` (`terraform-resources`), never dropped silently.
Bounded: files over 1 MiB are skipped, at most 2000 candidate files are read, symlinks are never followed out of
--root, binary and non-regular files are skipped, a malformed manifest is counted under `parse_error`. It reads files
under --root and writes nothing; findings carry paths, resource references, container names and instance or volume
types only, never file contents. Status is `fail` when any finding is major or worse. `--dry-run` prints deterministic
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

try:
    import yaml
except ImportError:  # the YAML-dependent checks are then reported as skipped
    yaml = None

MAX_FILE_BYTES = 1024 * 1024
MAX_FILES = 2000
MAX_FINDINGS = 1000
TAG_CHUNK = 50  # tag_coverage reports one finding per resource, capped at 100
SEVERITY_RANK = {"info": 0, "minor": 1, "major": 2, "blocker": 3}
SKIP_DIRS = {".git", "node_modules", ".terraform", ".venv", "venv", "dist", "build", "__pycache__", ".swarm", ".tox"}
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


def _hcl_tags(masked: str, skeleton: str) -> dict | None:
    """Literal tag map of the block, {} when there is no tags attribute, None when it is not a literal map."""
    raw = _top_level_attr(masked, skeleton, "tags")
    if raw is None:
        return {}
    if not raw.startswith("{"):
        return None
    tags: dict[str, str] = {}
    for entry in re.finditer(r'"?([A-Za-z0-9_.:/-]+)"?[ \t]*=[ \t]*("(?:[^"\\]|\\.)*"|[^,\n}]+)', raw[1:-1]):
        quoted = re.fullmatch(r'"(.*)"', entry.group(2))
        tags[entry.group(1)] = quoted.group(1) if quoted else _UNKNOWN
    return tags


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


def _hcl_scope(directory: str, masked: str, skeleton: str) -> tuple[bool, bool, list[str]]:
    """Whether the file declares provider default_tags, whether it declares an unaliased provider of its own (Terraform
    does not pass the default provider down to such a module), and the local child modules it calls, as directories."""
    own_provider = any(_top_level_attr(bm, bs, "alias") is None for bm, bs in _blocks(masked, skeleton, "provider"))
    calls = []
    for bm, bs in _blocks(masked, skeleton, "module"):
        local = re.fullmatch(r'"(\.\.?/[^"$]*)"', _top_level_attr(bm, bs, "source") or "")
        if local:
            calls.append(posixpath.normpath(posixpath.join(directory, local.group(1))))
    return "default_tags" in masked, own_provider, calls


def _hcl_facts(path: str, text: str) -> tuple[list[dict], tuple[bool, bool, list[str]], int]:
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
            "lifecycle": _hcl_lifecycle(rtype, body_m, body_s, retention),
            "body": body_m,
        })
    return facts, _hcl_scope(_dir_key(path), masked, skeleton), unclosed


def _hcl_lifecycle(rtype: str, body_m: str, body_s: str, retention: str | None) -> bool | None:
    kind = LIFECYCLE_TF.get(rtype)
    if kind == "s3":
        return _has_top_level_block(body_s, "lifecycle_rule")
    if kind == "logs":
        return retention is not None and retention.strip() not in {"0", ""}
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


def _doc_facts(path: str, doc) -> list[dict]:
    facts: list[dict] = []
    if not isinstance(doc, dict):
        return facts
    if isinstance(doc.get("resource"), dict):  # Terraform JSON
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
                    lifecycle = bool(attrs.get("retention_in_days"))
                elif kind == "ecr":
                    lifecycle = False
                tags = None
                if rtype in TAGGABLE_TF:
                    tags = _declared_tags(attrs, "tags")
                facts.append({"file": path, "line": 0, "ref": f"{rtype}.{name}", "type": rtype, "name": name, "source": "terraform",
                              "instance_types": instance_types, "volume_types": volume_types, "tags": tags,
                              "lifecycle": lifecycle, "body": " ".join(_strings(attrs))})
    resources = doc.get("Resources")
    if isinstance(resources, dict):  # CloudFormation
        for name, res in resources.items():
            if not isinstance(res, dict) or not isinstance(res.get("Type"), str):
                continue
            rtype, props = res["Type"], res.get("Properties") if isinstance(res.get("Properties"), dict) else {}
            instance_types, volume_types = [], []
            _walk(props, ("InstanceType", "InstanceTypes", "DBInstanceClass", "CacheNodeType"), instance_types)
            _walk(props, ("VolumeType",), volume_types)
            kind = LIFECYCLE_CFN.get(rtype)
            lifecycle = None
            if kind == "s3":
                lifecycle = bool(props.get("LifecycleConfiguration"))
            elif kind == "logs":
                lifecycle = bool(props.get("RetentionInDays"))
            elif kind == "ecr":
                lifecycle = bool(props.get("LifecyclePolicy"))
            tags = _declared_tags(props, "Tags") if rtype in TAGGABLE_CFN else None
            facts.append({"file": path, "line": 0, "ref": f"{rtype}:{name}", "type": rtype, "name": name, "source": "cloudformation",
                          "instance_types": instance_types, "volume_types": volume_types, "tags": tags,
                          "lifecycle": lifecycle, "body": ""})
    kind = doc.get("kind")
    if kind == "List" and isinstance(doc.get("items"), list):
        for item in doc["items"]:
            facts.extend(_doc_facts(path, item))
    elif isinstance(kind, str):
        facts.append(_k8s_fact(path, doc))
    return [f for f in facts if f]


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
    spec = _map(doc.get("spec"))
    if kind == "CronJob":
        spec = _map(_map(spec.get("jobTemplate")).get("spec"))
    if kind != "Pod":
        spec = _map(_map(spec.get("template")).get("spec"))
    containers = []
    for group in ("containers", "initContainers"):
        listed = spec.get(group)
        for container in listed if isinstance(listed, list) else []:
            if isinstance(container, dict):
                resources = _map(container.get("resources"))
                requests, limits = _map(resources.get("requests")), _map(resources.get("limits"))
                # A limit with no request is copied into the request by Kubernetes
                # (https://kubernetes.io/docs/concepts/configuration/manage-resources-containers/).
                containers.append({"name": str(container.get("name", "?")), "requests": sorted({str(k) for k in requests} | {str(k) for k in limits}),
                                   "limits": sorted(str(k) for k in limits)})
    return {"file": path, "line": 0, "ref": ref, "type": kind, "name": meta.get("name", "?"), "source": "kubernetes",
            "instance_types": [], "volume_types": [], "tags": None, "lifecycle": None, "body": "", "containers": containers}


def _yaml_loader():
    class Loader(yaml.SafeLoader):
        pass

    # CloudFormation short-form intrinsics (!Ref, !Sub, ...) are expressions: they are read as unknown, never evaluated
    Loader.add_multi_constructor("!", lambda loader, suffix, node: None)
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
    """A bucket or repository is covered by a lifecycle resource that names it in the same directory (a Terraform module)."""
    covered: set[tuple[str, str, str]] = set()
    for fact in facts:
        folder = os.path.dirname(fact["file"])
        if fact["type"] == "aws_s3_bucket_lifecycle_configuration":
            covered.update((folder, "aws_s3_bucket", name) for name in re.findall(r"aws_s3_bucket\.([A-Za-z0-9_-]+)", fact["body"]))
        elif fact["type"] == "aws_ecr_lifecycle_policy":
            covered.update((folder, "aws_ecr_repository", name) for name in re.findall(r"aws_ecr_repository\.([A-Za-z0-9_-]+)", fact["body"]))
    out = []
    for fact in facts:
        kind = LIFECYCLE_TF.get(fact["type"]) or LIFECYCLE_CFN.get(fact["type"])
        if kind is None or fact["lifecycle"] is not False:
            continue
        if (os.path.dirname(fact["file"]), fact["type"], fact["name"]) in covered:
            continue
        out.append(_finding("iac-missing-lifecycle", "minor", f"{fact['ref']}: {LIFECYCLE_ADVICE[kind]}", fact))
    return out


def _tag_inheriting_dirs(default_dirs: set[str], provider_dirs: set[str], module_calls: dict[str, set[str]]) -> set[str]:
    """Terraform directories whose resources inherit provider default_tags: those that declare it, and the local child
    modules they call, unless the child declares an unaliased provider of its own (Terraform passes the default down)."""
    covered = set(default_dirs)
    frontier = list(covered)
    while frontier:
        for child in module_calls.get(frontier.pop(), ()):
            if child not in covered and child not in provider_dirs:
                covered.add(child)
                frontier.append(child)
    return covered


def _tag_findings(facts: list[dict], required: list[str], inheriting: set[str]) -> tuple[list[dict], list[dict]]:
    skipped, out = [], []
    if not required:
        return out, [{"check": "iac-untagged-resource", "status": "skipped:no-required-tags"}]
    candidates = []
    numbered = 0
    for fact in facts:
        if fact["tags"] is None:
            continue
        if fact["source"] == "terraform" and _dir_key(fact["file"]) in inheriting:
            continue
        numbered += 1
        rid = f"{fact['file']}::{fact['ref']}#{numbered}"
        candidates.append((rid, fact))
    if any(f["source"] == "terraform" and f["tags"] is not None and _dir_key(f["file"]) in inheriting for f in facts):
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
    return out, skipped


# --- scan ------------------------------------------------------------------------------------------------------


def _candidates(root: Path, base: Path, counters: dict):
    """Candidate manifest files below `base`, sorted, never following symlinks, at most MAX_FILES."""
    seen = 0
    for folder, dirs, files in os.walk(base, followlinks=False):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not os.path.islink(os.path.join(folder, d)))
        for name in sorted(files):
            lower = name.lower()
            if not lower.endswith((".tf", ".json", ".yaml", ".yml")):
                continue
            full = Path(folder, name)
            if full.is_symlink():
                counters["symlink"] += 1
                continue
            if seen >= MAX_FILES:
                counters["truncated"] = True
                return
            seen += 1
            yield full


def scan(root: Path, base: Path, required_tags: list[str], max_xlarge: int) -> dict:
    counters = {"too_large": 0, "binary": 0, "parse_error": 0, "symlink": 0, "yaml_skipped": 0, "truncated": False}
    facts: list[dict] = []
    default_dirs: set[str] = set()  # directories that declare provider default_tags
    provider_dirs: set[str] = set()  # directories that declare an unaliased provider of their own
    module_calls: dict[str, set[str]] = {}  # directory -> the local child module directories it calls
    unclosed: dict[str, int] = {}
    scanned = 0
    for path in _candidates(root, base, counters):
        rel = path.relative_to(root).as_posix()
        try:
            info = path.stat()
            if not stat.S_ISREG(info.st_mode):  # a FIFO or device named *.json would block the read
                counters["parse_error"] += 1
                continue
            if info.st_size > MAX_FILE_BYTES:
                counters["too_large"] += 1
                continue
            data = path.read_bytes()
        except OSError:
            counters["parse_error"] += 1
            continue
        if b"\0" in data:
            counters["binary"] += 1
            continue
        try:
            text = data.decode("utf-8-sig")  # a byte-order mark is not part of the first line
        except UnicodeDecodeError:
            counters["binary"] += 1
            continue
        lower = path.name.lower()
        if lower.endswith((".yaml", ".yml")) and yaml is None:
            counters["yaml_skipped"] += 1
            continue
        try:
            if lower.endswith(".tf"):
                tf_facts, (has_default_tags, own_provider, calls), not_delimited = _hcl_facts(rel, text)
                facts.extend(tf_facts)
                directory = _dir_key(rel)
                if has_default_tags:
                    default_dirs.add(directory)
                if own_provider:
                    provider_dirs.add(directory)
                module_calls.setdefault(directory, set()).update(calls)
                if not_delimited:
                    unclosed[rel] = not_delimited
                scanned += 1
                continue
            docs = [json.loads(text)] if lower.endswith(".json") else list(yaml.load_all(text, Loader=_yaml_loader()))
        except (ValueError, RecursionError, yaml.YAMLError if yaml else ValueError):
            counters["parse_error"] += 1
            continue
        malformed = False
        for doc in docs:
            try:
                facts.extend(_doc_facts(rel, doc))
            except (AttributeError, TypeError, KeyError, IndexError, ValueError, RecursionError):
                malformed = True  # one malformed document must not abort the scan of the rest of the tree
        if malformed:
            counters["parse_error"] += 1
        else:
            scanned += 1
    findings: list[dict] = []
    for fact in facts:
        findings.extend(_k8s_findings(fact) if fact["source"] == "kubernetes" else [])
        findings.extend(_instance_findings(fact, max_xlarge))
        findings.extend(_volume_findings(fact))
    findings.extend(_lifecycle_findings(facts))
    tag_findings, tag_skipped = _tag_findings([f for f in facts if f["source"] != "kubernetes"], required_tags, _tag_inheriting_dirs(default_dirs, provider_dirs, module_calls))
    findings.extend(tag_findings)
    skipped = list(tag_skipped)
    if unclosed:
        listed = ", ".join(sorted(unclosed)[:5]) + (f" and {len(unclosed) - 5} more" if len(unclosed) > 5 else "")
        skipped.append({"check": "terraform-resources", "status": "skipped:unbalanced-braces",
                        "detail": f"{sum(unclosed.values())} resource block(s) could not be delimited and were not evaluated: {listed}"})
    if counters["yaml_skipped"]:
        skipped.append({"check": "yaml-manifests", "status": "skipped:tool-missing", "detail": f"PyYAML is not importable: {counters['yaml_skipped']} YAML file(s) not read"})
    findings.sort(key=lambda f: (-SEVERITY_RANK[f["severity"]], f["file"], f["id"]))
    counts = {severity: sum(1 for f in findings if f["severity"] == severity) for severity in SEVERITY_RANK}
    by_rule: dict[str, int] = {}
    for finding in findings:
        by_rule[finding["rule"]] = by_rule.get(finding["rule"], 0) + 1
    blocking = counts["major"] + counts["blocker"]
    return {
        "status": "fail" if blocking else "ok",
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
